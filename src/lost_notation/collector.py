"""Small, resumable harvesters for high-value public research corpora."""
from __future__ import annotations

from dataclasses import dataclass
from html.parser import HTMLParser
import re
import time
from typing import Callable
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, urlencode, urldefrag, urljoin, urlparse, urlunparse
from urllib.request import Request, urlopen

from .corpus import html_text, ingest_corpus_document, score_passage, split_passages
from .store import Store, discussion_group_key

USER_AGENT = "LostNotationProject/0.1 (digital-humanities preservation research)"
MAX_PAGE_BYTES = 12 * 1024 * 1024


@dataclass(frozen=True)
class SourceSpec:
    key: str
    corpus_name: str
    seed_url: str
    max_depth: int
    delay_seconds: float
    accepts: Callable[[str], bool]
    normalizes: Callable[[str], str]
    prioritizes: Callable[[str, str], int]


@dataclass(frozen=True)
class DiscoveredLink:
    url: str
    label: str
    priority: int


@dataclass(frozen=True)
class Download:
    body: bytes
    final_url: str
    content_type: str


@dataclass(frozen=True)
class HarvestResult:
    fetched: int
    failed: int
    skipped: int
    passages: int
    pending: int


class PageParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.links: list[tuple[str, str]] = []
        self.title_parts: list[str] = []
        self.in_title = False
        self.current_href: str | None = None
        self.current_label: list[str] = []

    def handle_starttag(self, tag, attrs):
        if tag.lower() == "title":
            self.in_title = True
        if tag.lower() == "a":
            href = dict(attrs).get("href")
            if href:
                self.current_href = href
                self.current_label = []

    def handle_endtag(self, tag):
        if tag.lower() == "title":
            self.in_title = False
        if tag.lower() == "a" and self.current_href:
            self.links.append((self.current_href, " ".join(self.current_label).strip()))
            self.current_href = None
            self.current_label = []

    def handle_data(self, data):
        if self.in_title:
            self.title_parts.append(data.strip())
        if self.current_href:
            self.current_label.append(data.strip())


def _omniglot_url(url: str) -> bool:
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or parsed.netloc.lower() not in {"omniglot.com", "www.omniglot.com"}:
        return False
    path = parsed.path.lower()
    if not path.startswith("/conscripts/"):
        return False
    return not path.endswith((".jpg", ".jpeg", ".png", ".gif", ".svg", ".webp", ".pdf", ".zip"))


def _identity_url(url: str) -> str:
    return url


def _normal_priority(url: str, label: str) -> int:
    return 0


def _normalize_zbb(url: str) -> str:
    parsed = urlparse(url)
    query = parse_qs(parsed.query)
    kept: dict[str, str] = {}
    for key in ("f", "t", "start"):
        if query.get(key):
            kept[key] = query[key][0]
    return urlunparse(("https", "www.verduria.org", parsed.path, "", urlencode(kept), ""))


def _zbb_url(url: str) -> bool:
    parsed = urlparse(url)
    if parsed.scheme != "https" or parsed.netloc.lower() != "www.verduria.org":
        return False
    query = parse_qs(parsed.query)
    if parsed.path.endswith("/viewforum.php"):
        return query.get("f") == ["3"]
    return parsed.path.endswith("/viewtopic.php") and bool(query.get("t"))


def _zbb_priority(url: str, label: str) -> int:
    return 20 if urlparse(url).path.endswith("/viewtopic.php") else 5


CONLANG_SUBJECT_TERMS = (
    "script", "writing system", "orthograph", "alphabet", "glyph", "logograph",
    "ideograph", "shorthand", "pasigraph", "syllab", "abugida", "abjad",
    "notation", "neograph", "conscrip", "transliter", "writing direction",
)


def _normalize_conlang_mirror(url: str) -> str:
    parsed = urlparse(url)
    return urlunparse(("http", "archives.conlang.info", parsed.path or "/", "", "", ""))


def _conlang_mirror_url(url: str) -> bool:
    parsed = urlparse(url)
    if parsed.scheme != "http" or parsed.netloc.lower() != "archives.conlang.info":
        return False
    path = parsed.path
    if path == "/" or re.fullmatch(r"/\d{4}-\d{2}\.html", path):
        return True
    return bool(re.fullmatch(r"/[a-z0-9]+/[a-z0-9]+/(?!tree\.html)[a-z0-9]+\.html", path, re.I))


def _conlang_mirror_priority(url: str, label: str) -> int:
    path = urlparse(url).path
    if re.fullmatch(r"/\d{4}-\d{2}\.html", path):
        return 50
    if path != "/":
        lower = label.lower()
        return 100 if any(term in lower for term in CONLANG_SUBJECT_TERMS) else 10
    return 60


SOURCES = {
    "omniglot": SourceSpec(
        "omniglot", "Omniglot constructed scripts", "https://www.omniglot.com/conscripts/", 1, 1.5,
        _omniglot_url, _identity_url, _normal_priority
    ),
    "zbb": SourceSpec(
        "zbb", "Zompist Bulletin Board - Conlangery", "https://www.verduria.org/viewforum.php?f=3", 2, 5.0,
        _zbb_url, _normalize_zbb, _zbb_priority
    ),
    "conlang-mirror": SourceSpec(
        "conlang-mirror", "CONLANG static archive mirror", "http://archives.conlang.info/", 2, 3.0,
        _conlang_mirror_url, _normalize_conlang_mirror, _conlang_mirror_priority
    ),
}


def download(url: str, timeout: int = 30) -> Download:
    request = Request(url, headers={"User-Agent": USER_AGENT, "Accept": "text/html,application/xhtml+xml;q=0.9,text/plain;q=0.8"})
    with urlopen(request, timeout=timeout) as response:
        body = response.read(MAX_PAGE_BYTES + 1)
        if len(body) > MAX_PAGE_BYTES:
            raise ValueError("page exceeded the 12 MB safety limit")
        return Download(body, response.geturl(), response.headers.get_content_type())


def page_details(body: bytes, base_url: str, spec: SourceSpec) -> tuple[str | None, list[DiscoveredLink]]:
    parser = PageParser()
    parser.feed(body.decode("utf-8", errors="replace"))
    title = " ".join(part for part in parser.title_parts if part).strip() or None
    links: list[DiscoveredLink] = []
    seen: set[str] = set()
    for href, label in parser.links:
        absolute = spec.normalizes(urldefrag(urljoin(base_url, href))[0])
        if absolute not in seen and spec.accepts(absolute):
            seen.add(absolute)
            links.append(DiscoveredLink(absolute, re.sub(r"\s+", " ", label), spec.prioritizes(absolute, label)))
    return title, links


def _focused_priority(link: DiscoveredLink, focus_terms: list[str] | tuple[str, ...] | None, parent_url: str = "", parent_priority: int = 0) -> int:
    if not focus_terms:
        return link.priority
    label = link.label.lower()
    matched = sum(1 for term in focus_terms if term.lower() in label)
    inherited = bool(
        parent_priority >= 1000
        and discussion_group_key(parent_url)
        and discussion_group_key(parent_url) == discussion_group_key(link.url)
    )
    return link.priority + (1000 + matched * 20 if matched else (1000 if inherited else 0))


def rebuild_source_frontier(store: Store, source_key: str, focus_terms: list[str]) -> int:
    """Re-read preserved index pages and reprioritise still-pending links."""
    spec = SOURCES[source_key]; updated = 0
    with store.connect() as con:
        pending_urls = {row[0] for row in con.execute(
            "SELECT url FROM source_items WHERE source_name=? AND state='pending'", (source_key,)
        )}
    for item in store.fetched_source_items(source_key):
        if int(item["depth"]) >= spec.max_depth:
            continue
        try:
            _, links = page_details(store.blob(item["raw_sha256"]), item["final_url"] or item["url"], spec)
        except (OSError, ValueError):
            continue
        for link in links:
            priority = _focused_priority(link, focus_terms, item["url"], int(item["priority"]))
            inserted = store.queue_source_item(spec.key, link.url, int(item["depth"]) + 1, item["url"], priority, link.label)
            if inserted or link.url in pending_urls:
                updated += 1
                pending_urls.add(link.url)
    return updated


class ArchiveMessageText(HTMLParser):
    """Extract message text while omitting HTML-marked quoted replies."""
    def __init__(self) -> None:
        super().__init__()
        self.parts: list[str] = []
        self.ignored = 0
        self.quoted = 0

    def handle_starttag(self, tag, attrs):
        tag = tag.lower()
        if tag in {"script", "style", "noscript"}: self.ignored += 1
        if tag == "blockquote": self.quoted += 1
        if tag in {"p", "div", "li", "br", "pre", "tr"}: self.parts.append("\n")

    def handle_endtag(self, tag):
        tag = tag.lower()
        if tag in {"script", "style", "noscript"}: self.ignored = max(0, self.ignored - 1)
        if tag == "blockquote": self.quoted = max(0, self.quoted - 1)
        if tag in {"p", "div", "li", "pre", "tr"}: self.parts.append("\n")

    def handle_data(self, data):
        if not self.ignored and not self.quoted and data.strip(): self.parts.append(data.strip() + " ")


def conlang_message_record(body: bytes, title: str | None, url: str) -> tuple[dict[str, object], str]:
    parser = ArchiveMessageText(); parser.feed(body.decode("utf-8", errors="replace"))
    full_text = re.sub(r"[ \t]+", " ", "".join(parser.parts)).strip()
    display_title = re.sub(r"^Conlang:\s*", "", title or "", flags=re.I)
    metadata = re.search(r"\(([^,()]+),\s*([^()]+)\)\s*$", display_title)
    author = published_at = None
    if metadata:
        author, published_at = metadata.group(1).strip(), metadata.group(2).strip()
        display_title = display_title[:metadata.start()].strip()
    month_index = list(re.finditer(
        r"(?:January|February|March|April|May|June|July|August|September|October|November|December)\s+\d{4}\s+Index",
        full_text,
    ))
    if len(month_index) >= 2:
        message_text = full_text[month_index[0].end():month_index[1].start()].strip()
    elif month_index:
        message_text = full_text[month_index[0].end():].strip()
    else:
        message_text = full_text
    message_text = re.sub(r"^\s*---\s*", "", message_text)
    message_text = "\n".join(line for line in message_text.splitlines() if not line.lstrip().startswith(">"))
    return {
        "external_id": url, "title": display_title or title, "url": url,
        "author": author, "published_at": published_at,
    }, message_text


def harvest(
    store: Store,
    source_key: str,
    limit: int = 25,
    *,
    refresh: bool = False,
    retry_errors: bool = False,
    fetcher: Callable[[str], Download] = download,
    sleep: Callable[[float], None] = time.sleep,
    focus_terms: list[str] | None = None,
    first_person_terms: list[str] | None = None,
) -> HarvestResult:
    """Fetch the next bounded batch and leave a persistent URL frontier behind."""
    if source_key not in SOURCES:
        raise ValueError(f"unknown source: {source_key}")
    spec = SOURCES[source_key]
    corpus_id = store.get_or_create_corpus(spec.corpus_name, spec.seed_url)
    store.queue_source_item(spec.key, spec.seed_url, depth=0)
    if refresh:
        store.reset_source_items(spec.key, include_fetched=True)
    elif retry_errors:
        store.reset_source_items(spec.key, include_fetched=False)

    fetched = failed = skipped = passages = 0
    for attempt in range(max(0, limit)):
        item = store.next_source_item(spec.key)
        if item is None:
            break
        if attempt:
            sleep(spec.delay_seconds)
        try:
            result = fetcher(item["url"])
            content_type = result.content_type.lower()
            if "html" not in content_type and "text" not in content_type:
                raise ValueError(f"unsupported content type: {content_type}")
            title, links = page_details(result.body, result.final_url, spec) if "html" in content_type else (None, [])
            text = html_text(result.body) if "html" in content_type else result.body.decode("utf-8", errors="replace")
            record: dict[str, object] = {"external_id": item["url"], "title": title, "url": result.final_url}
            if spec.key == "conlang-mirror" and int(item["depth"]) == 2:
                record, text = conlang_message_record(result.body, title, result.final_url)
            should_mine = spec.key != "conlang-mirror" or int(item["depth"]) == 2
            mined = [match for passage in split_passages(text) if (match := score_passage(
                passage, first_person_terms, focus_terms, None, focus_terms is not None
            ))] if should_mine else []
            raw_hash = store.put_blob(result.body)
            text_hash = store.put_blob(text.encode("utf-8"))
            added = ingest_corpus_document(
                store,
                corpus_id,
                record,
                raw_hash,
                text_hash,
                mined,
            )
            passages += len(mined) if added else 0
            skipped += 0 if added else 1
            if int(item["depth"]) < spec.max_depth:
                for link in links:
                    priority = _focused_priority(link, focus_terms, item["url"], int(item["priority"]))
                    store.queue_source_item(spec.key, link.url, int(item["depth"]) + 1, item["url"], priority, link.label)
            store.finish_source_item(int(item["id"]), raw_hash, content_type, result.final_url)
            fetched += 1
        except (HTTPError, URLError, TimeoutError, ValueError, OSError) as exc:
            store.fail_source_item(int(item["id"]), f"{type(exc).__name__}: {exc}")
            failed += 1
            if isinstance(exc, HTTPError) and exc.code in {429, 503}:
                break
    return HarvestResult(fetched, failed, skipped, passages, store.source_pending_count(spec.key))
