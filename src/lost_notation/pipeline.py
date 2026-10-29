"""Independently runnable preservation and extraction stages."""
from __future__ import annotations

from dataclasses import dataclass
from html.parser import HTMLParser
import json
from pathlib import Path
import re
from typing import Callable
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from .discovery import BACK_OFF_CODES, DiscoveryResponseError, DiscoverySource
from .store import Store, now
from .term_config import load_term_group

class TextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__(); self.parts: list[str] = []; self._ignore = 0
    def handle_starttag(self, tag, attrs):
        if tag in {"script", "style", "noscript"}: self._ignore += 1
    def handle_endtag(self, tag):
        if tag in {"script", "style", "noscript"}: self._ignore = max(0, self._ignore - 1)
    def handle_data(self, data):
        if not self._ignore and data.strip(): self.parts.append(data.strip())

def import_jsonl(store: Store, path: Path) -> tuple[int, int]:
    added = skipped = 0
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip(): continue
        record = json.loads(line)
        for required in ("url", "source"):
            if not record.get(required): raise ValueError(f"line {number}: missing {required}")
        record.setdefault("discovered_at", now())
        if store.add_candidate(record): added += 1
        else: skipped += 1
    return added, skipped

def discover(store: Store, source: DiscoverySource, query: str, limit: int, dry_run: bool) -> tuple[int, int]:
    records, raw_response, request = source.discover(query, limit)
    store.record_discovery_run(source.name, request, raw_response, len(records))
    if dry_run:
        return len(records), 0
    added = sum(store.add_candidate({
        "url": item.url, "original_url": item.original_url, "archive_url": item.archive_url,
        "source": item.source, "discovered_at": item.discovered_at, "query": item.query,
        "title": item.title, "source_id": item.source_id, "access_status": item.access_status,
        "has_fulltext": item.has_fulltext, "has_graphics": item.has_graphics, "notes": item.notes,
    }) for item in records)
    return len(records), added

SURVEY_SOURCE = "internet-archive-cdx-url-regex"
PAGE_ATTEMPTS = 3
# Seeds are surveyed tier by tier, most promising first: a tier starts only
# once every section of the tier before it is finished. A seed without a tier
# is a whole site.
SURVEY_TIERS = {1: "Named addresses", 2: "Key topic areas", 3: "Other topic areas", 4: "Whole sites"}
WHOLE_SITE_TIER = 4


@dataclass(frozen=True)
class SurveyResult:
    sections: int
    pages: int
    found: int
    added: int
    failed: int
    backed_off: bool


def load_seeds(path: Path) -> list[dict[str, str]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def seed_tier(seed: dict[str, object]) -> int:
    return int(seed.get("tier", WHOLE_SITE_TIER))


def survey_seed_file(
    store: Store, source, path: Path, request_budget: int, dry_run: bool, progress: Callable[[str], None] = lambda _: None,
) -> SurveyResult:
    """Make the next ``request_budget`` archive index requests for the seed list.

    Each seed is a whole host or an address prefix within one (a section),
    divided into index pages; progress is saved after every page, so a survey
    can be stopped and resumed across many runs. Sections are taken tier by
    tier, and within a tier in rotation, least recently scanned first. Every
    request counts towards the budget, including a section's one-off size
    lookup, so a run takes a predictable time.
    """
    seeds = load_seeds(path)
    scans = []
    for seed in seeds:
        scan = dict(store.archive_scan(seed["host"], seed["url_regex"], seed.get("label")))
        scan["tier"], scan["match"] = seed_tier(seed), seed.get("match", "domain")
        scans.append(scan)
    queue = sorted(
        (scan for scan in scans if scan["total_pages"] is None or scan["next_page"] < scan["total_pages"]),
        key=lambda scan: (scan["tier"], scan["updated_at"], scan["id"]),
    )

    def requeue(scan: dict[str, object]) -> None:
        # To the back of its own tier: the sort is stable, so it stays ahead of later tiers.
        queue.append(scan); queue.sort(key=lambda item: item["tier"])

    pages = found = added = failed = requests = 0
    while queue and requests < request_budget:
        scan = queue.pop(0)
        host, regex, match = scan["host"], scan["url_regex"], scan["match"]
        requests += 1
        try:
            if scan["total_pages"] is None:
                scan["total_pages"] = source.count_pages(host, match)
                store.update_archive_scan(scan["id"], total_pages=scan["total_pages"])
                progress(f"{host}: {scan['total_pages']:,} index pages to survey" if scan["total_pages"] else f"{host}: nothing archived")
                if scan["next_page"] < scan["total_pages"]:
                    queue.insert(0, scan)
                continue
            records, raw_response, request = source.scan_page(host, regex, scan["next_page"], match)
        except Exception as exc:
            # Only 429 asks us to slow down. A busy index (5xx) has already
            # been retried by the source, so it counts as one failed attempt.
            if isinstance(exc, HTTPError) and exc.code == 429:
                store.record_discovery_run(SURVEY_SOURCE, {"url": host, "page": scan["next_page"]}, None, 0, error="HTTPError 429: asked to slow down")
                store.update_archive_scan(scan["id"], last_error="HTTP 429: the archive asked us to slow down")
                progress(f"{host}: the Internet Archive asked us to slow down (HTTP 429); stopping this run")
                return SurveyResult(len(seeds), pages, found, added, failed + 1, True)
            failed += 1
            if isinstance(exc, DiscoveryResponseError):
                error, request, body = str(exc), exc.request, exc.body
            else:
                error, request, body = f"{type(exc).__name__}: {exc}", {"url": host, "page": scan["next_page"]}, None
            store.record_discovery_run(SURVEY_SOURCE, request, body, 0, error=error)
            if scan["total_pages"] is None:
                store.update_archive_scan(scan["id"], last_error=f"page count failed: {error}")
                progress(f"{host}: could not count pages ({error}); will retry next run")
                continue
            scan["failures"] += 1
            if scan["failures"] >= PAGE_ATTEMPTS:
                # One unreadable page must not stall a host for ever. The skip
                # gets its own log entry so the page can be found and rescanned.
                skipped = f"page skipped after {PAGE_ATTEMPTS} failed attempts: {error}"
                store.record_discovery_run(SURVEY_SOURCE, {"url": host, "url_regex": regex, "page": scan["next_page"], "skipped": True}, None, 0, error=skipped)
                progress(f"{host}: skipping page {scan['next_page']:,} after {PAGE_ATTEMPTS} failures ({error})")
                scan["next_page"] += 1; scan["failures"] = 0
                store.update_archive_scan(scan["id"], next_page=scan["next_page"], failures=0, last_error=skipped)
            else:
                progress(f"{host}: page {scan['next_page']:,} failed ({error}); will retry")
                store.update_archive_scan(scan["id"], failures=scan["failures"], last_error=error)
            if scan["next_page"] < scan["total_pages"]:
                requeue(scan)
            continue
        store.record_discovery_run(SURVEY_SOURCE, request, raw_response, len(records))
        pages += 1; found += len(records)
        scan["next_page"] += 1; scan["matches"] += len(records); scan["failures"] = 0
        store.update_archive_scan(scan["id"], next_page=scan["next_page"], matches=scan["matches"], failures=0, last_error=None)
        progress(f"{host}: page {scan['next_page']:,} of {scan['total_pages']:,}" + (f" - {len(records)} matching address(es)" if records else ""))
        if not dry_run:
            added += sum(store.add_candidate({
                "url": item.url, "original_url": item.original_url, "archive_url": item.archive_url,
                "source": SURVEY_SOURCE, "discovered_at": item.discovered_at, "query": item.query,
                "title": item.title,
            }) for item in records)
        if scan["next_page"] < scan["total_pages"]:
            requeue(scan)
    return SurveyResult(len(seeds), pages, found, added, failed, False)


def survey_progress(store: Store, path: Path) -> dict[int, dict[str, int]]:
    """Progress of the current seed list per tier, read without any network access."""
    tiers: dict[int, dict[str, int]] = {}
    for seed in load_seeds(path):
        totals = tiers.setdefault(seed_tier(seed), dict.fromkeys(("sections", "counted", "finished", "scanned", "pages", "matches"), 0))
        totals["sections"] += 1
        scan = store.archive_scan(seed["host"], seed["url_regex"], create=False)
        if scan is None or scan["total_pages"] is None:
            continue
        totals["counted"] += 1
        totals["scanned"] += scan["next_page"]; totals["pages"] += scan["total_pages"]; totals["matches"] += scan["matches"]
        totals["finished"] += int(scan["next_page"] >= scan["total_pages"])
    return dict(sorted(tiers.items()))

def discover_queries(store: Store, source: DiscoverySource, path: Path, limit_per_query: int, max_queries: int, dry_run: bool) -> tuple[int, int, int, int]:
    entries = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()][:max_queries]
    return run_query_entries(store, source, entries, limit_per_query, dry_run)


def run_query_entries(store: Store, source: DiscoverySource, entries: list[dict[str, str]], limit_per_query: int, dry_run: bool) -> tuple[int, int, int, int]:
    """Run each ``{"text": ..., "language": ...}`` entry through a text search source."""
    queries = [entry["text"] for entry in entries]
    found = added = failed = 0
    for entry in entries:
        query = entry["text"]
        # A query's language travels with it so that, for example, a German
        # search is not filtered down to English-language results.
        options = {"language": entry["language"]} if entry.get("language") else {}
        try:
            records, raw_response, request = source.discover(query, limit_per_query, **options)
        except DiscoveryResponseError as exc:
            store.record_discovery_run(source.name, exc.request, exc.body, 0, error=str(exc)); failed += 1; continue
        except Exception as exc:
            store.record_discovery_run(source.name, {"q": query}, None, 0, error=f"{type(exc).__name__}: {exc}"); failed += 1
            if isinstance(exc, HTTPError) and exc.code in BACK_OFF_CODES:
                break  # still rate-limited after the adapter's own retries: stop asking
            continue
        store.record_discovery_run(source.name, request, raw_response, len(records))
        found += len(records)
        if not dry_run:
            added += sum(store.add_candidate({
                "url": item.url, "original_url": item.original_url, "archive_url": item.archive_url or None,
                "source": item.source, "discovered_at": item.discovered_at, "query": item.query,
                "title": item.title, "source_id": item.source_id, "access_status": item.access_status,
                "has_fulltext": item.has_fulltext, "has_graphics": item.has_graphics, "notes": item.notes,
            }) for item in records)
    return len(queries), found, added, failed


def discover_term_group(store: Store, source: DiscoverySource, term_file: Path, group: str, limit_per_query: int, max_queries: int, dry_run: bool) -> tuple[int, int, int, int]:
    """Run a source through a named, editable vocabulary group."""
    terms = load_term_group(term_file, group)[:max_queries]
    temporary = term_file.parent / f".{group}.queries.jsonl"
    try:
        temporary.write_text("".join(json.dumps({"text": term}, ensure_ascii=False) + "\n" for term in terms), encoding="utf-8")
        return discover_queries(store, source, temporary, limit_per_query, max_queries, dry_run)
    finally:
        if temporary.exists():
            temporary.unlink()

def import_projects(store: Store, path: Path) -> int:
    count = 0
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        record = json.loads(line)
        if not record.get("id") or not record.get("title"):
            raise ValueError(f"line {number}: project needs id and title")
        store.upsert_project(record)
        count += 1
    return count

def export_vault(store: Store, vault: Path, reviewed_only: bool = True) -> int:
    vault.mkdir(parents=True, exist_ok=True)
    written = 0
    for project in store.projects_for_vault(reviewed_only):
        categories = json.loads(project["categories_json"])
        title = project["title"].replace("\n", " ")
        safe_name = "".join(ch if ch.isalnum() or ch in " -_" else "_" for ch in title).strip() or project["id"]
        links = []
        if project["url"]:
            links.append(f"- Retrieval: {project['url']}")
        if project["original_url"]:
            links.append(f"- Original URL: {project['original_url']}")
        if project["archive_url"]:
            links.append(f"- Archive replay: {project['archive_url']}")
        tags = ", ".join(f"[[Category - {item}]]" for item in categories) or ""
        generated = "\n".join([
            "---", f"id: {project['id']}", f"title: {json.dumps(title, ensure_ascii=False)}",
            f"status: {project['status']}", f"author: {json.dumps(project['author'], ensure_ascii=False)}",
            f"year: {project['year'] or ''}", f"categories: {json.dumps(categories, ensure_ascii=False)}", "---", "",
            f"# {title}", "", "## Summary", "", project["summary"] or "_No curator summary yet._", "",
            "## Classification", "", tags, "", "## Evidence", "", "\n".join(links) or "_No linked source record._", "",
        ])
        target = vault / f"{project['id']} - {safe_name}.md"
        notes = "<!-- Write human notes here. The exporter preserves this section on future runs. -->\n"
        if target.exists() and "## Curatorial notes" in target.read_text(encoding="utf-8"):
            notes = target.read_text(encoding="utf-8").split("## Curatorial notes", 1)[1].lstrip("\r\n")
        target.write_text(generated + "\n## Curatorial notes\n\n" + notes, encoding="utf-8")
        written += 1
    return written

def fetch(store: Store, limit: int, refresh: bool = False) -> int:
    # refresh is reserved for an explicit future state reset; default is strictly cache-first.
    count = 0
    for candidate in store.pending(limit):
        try:
            request = Request(candidate["url"], headers={"User-Agent": "LostNotationResearch/0.1 (preservation prototype)"})
            with urlopen(request, timeout=30) as response:
                body = response.read()
                digest = store.put_blob(body)
                store.record_retrieval(candidate["id"], status_code=response.status, content_type=response.headers.get_content_type(), blob_sha256=digest, final_url=response.url, error=None)
                count += 1
        except Exception as exc:  # store failure evidence; retries require explicit new policy
            store.record_retrieval(candidate["id"], status_code=None, content_type=None, blob_sha256=None, final_url=None, error=f"{type(exc).__name__}: {exc}")
    return count

CHARSET = re.compile(rb"""charset\s*=\s*["']?([A-Za-z0-9_.:-]+)""", re.I)

def decode_page(raw: bytes) -> str:
    """Old personal sites declare Shift_JIS, KOI8-R, windows-1251 and so on in a
    meta tag; honour it, else use UTF-8 if it fits and Windows Latin if not."""
    declared = CHARSET.search(raw[:4096])
    for encoding in ([declared.group(1).decode("ascii")] if declared else []) + ["utf-8"]:
        try:
            return raw.decode(encoding)
        except (LookupError, UnicodeDecodeError):
            continue
    return raw.decode("cp1252", errors="replace")

def extract(store: Store) -> int:
    count = 0
    for row in store.fetched_without_text():
        raw = store.blob(row["blob_sha256"])
        if row["content_type"] not in {"text/html", "text/plain"}: continue
        if row["content_type"] == "text/plain": text = decode_page(raw)
        else:
            parser = TextExtractor(); parser.feed(decode_page(raw)); text = "\n".join(parser.parts)
        store.add_text(row["id"], text)
        count += 1
    return count
