"""Pluggable discovery interfaces and an Internet Archive CDX adapter."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import gzip
import json
import os
from time import sleep
from typing import Callable, Protocol
from urllib.error import HTTPError
from urllib.parse import quote, urlencode
from urllib.request import Request, urlopen

CDX_ENDPOINT = "https://web.archive.org/cdx/search/cdx"
BRAVE_ENDPOINT = "https://api.search.brave.com/res/v1/web/search"
COMMONS_ENDPOINT = "https://commons.wikimedia.org/w/api.php"
# Status codes a service uses to ask a client to slow down.
BACK_OFF_CODES = {429, 503}
# Wayback's index answers these when it is briefly overloaded. They are not a
# request to slow down: the same query usually succeeds seconds later.
CDX_BUSY_CODES = {502, 503, 504}
# Addresses that are never pages. Mirrors sometimes answer a missing image
# with an HTML error page, so the archive's MIME type alone does not rule them out.
NON_PAGE_SUFFIXES = (".gif", ".jpg", ".jpeg", ".png", ".bmp", ".ico", ".css", ".js", ".mid", ".wav")


def address_key(url: str) -> str:
    """One key per archived page however its address was written.

    The archive lists ``http://www.geocities.com:80/x/`` and ``http://geocities.com/x/``
    separately; like the archive's own index key, this ignores the scheme, ``www.``,
    the default port, a fragment, a bare ``?`` and letter case.
    """
    rest = url.split("#", 1)[0].rstrip("?").split("://", 1)[-1]
    host, slash, path = rest.partition("/")
    host = host.lower().removeprefix("www.")
    if host.endswith((":80", ":443")):
        host = host.rsplit(":", 1)[0]
    return (host + slash + path).lower()


def is_page_address(url: str) -> bool:
    return not url.split("#", 1)[0].split("?", 1)[0].lower().endswith(NON_PAGE_SUFFIXES)


# Hosts that file each member under a topic or state, e.g. angelfire.com/scifi/<member>.
TWO_LEVEL_HOSTS = {"angelfire.com"}


def site_key(url: str) -> str:
    """The member site an archived page belongs to, e.g. ``geocities.com/taquigra``.

    Usually the host plus the first directory. Themed areas number their members
    (``geocities.com/athens/acropolis/1234``), so a numbered directory within the
    first three ends the key.
    """
    host, _, path = address_key(url).split("?", 1)[0].partition("/")
    parts = [part for part in path.split("/") if part]
    if parts and not path.endswith("/") and "." in parts[-1]:
        parts = parts[:-1]
    depth = next((index + 1 for index, part in enumerate(parts[:3]) if part.isdigit()), 2 if host in TWO_LEVEL_HOSTS else 1)
    return "/".join([host] + parts[:depth])


def user_agent(purpose: str) -> str:
    """Identify the project. Wikimedia asks automated clients for a contact
    address; set LOST_NOTATION_CONTACT (an email or web page) to supply one."""
    contact = os.environ.get("LOST_NOTATION_CONTACT", "").strip()
    return f"LostNotationResearch/0.2 ({purpose}{'; ' + contact if contact else ''}) python-urllib"

class DiscoveryResponseError(RuntimeError):
    """A response was received and preserved but could not be interpreted."""
    def __init__(self, message: str, body: bytes, request: dict[str, object]) -> None:
        super().__init__(message)
        self.body = body
        self.request = request

@dataclass(frozen=True)
class DiscoveryRecord:
    """A candidate with enough evidence to reconstruct how it was found."""
    url: str
    original_url: str
    archive_url: str
    source: str
    discovered_at: str
    title: str | None = None
    query: str | None = None
    source_id: str | None = None
    access_status: str = "unknown"
    has_fulltext: bool | None = None
    has_graphics: bool | None = None
    notes: str | None = None

class DiscoverySource(Protocol):
    name: str
    def discover(self, query: str, limit: int) -> tuple[list[DiscoveryRecord], bytes, dict[str, object]]: ...

def parse_cdx_json(body: bytes, query: str) -> list[DiscoveryRecord]:
    if body.startswith(b"\x1f\x8b"):
        body = gzip.decompress(body)
    if not body.strip():
        return []
    rows = json.loads(body)
    if not rows:
        return []
    headers = rows[0]
    records: list[DiscoveryRecord] = []
    for values in rows[1:]:
        row = dict(zip(headers, values))
        timestamp, original = row.get("timestamp"), row.get("original")
        if not timestamp or not original:
            continue
        replay = f"https://web.archive.org/web/{timestamp}id_/{original}"
        records.append(DiscoveryRecord(
            url=replay, original_url=original, archive_url=replay,
            source="internet-archive-cdx", discovered_at=datetime.now(timezone.utc).isoformat(), query=query,
        ))
    return records

class InternetArchiveCdx:
    """Discover archived URLs using Wayback's URL index.

    CDX does not full-text search archived pages. Its query is therefore a URL
    pattern such as ``*.geocities.com/*script*``, not a prose search phrase.
    """
    name = "internet-archive-cdx"

    def __init__(
        self,
        min_interval_seconds: float = 2.0,
        request_timeout_seconds: float = 90.0,
        max_retries: int = 2,
        opener: Callable[..., object] = urlopen,
        sleeper: Callable[[float], None] = sleep,
    ) -> None:
        self.min_interval_seconds = min_interval_seconds
        self.request_timeout_seconds = request_timeout_seconds
        self.max_retries = max_retries
        self.opener = opener
        self.sleeper = sleeper

    def _get(self, params: list[tuple[str, object]] | dict[str, object]) -> bytes:
        """Fetch politely, waiting and trying again while the index is busy."""
        request = Request(f"{CDX_ENDPOINT}?{urlencode(params)}", headers={"User-Agent": user_agent("archival research")})
        for attempt in range(self.max_retries + 1):
            try:
                with self.opener(request, timeout=self.request_timeout_seconds) as response:
                    return response.read()
            except HTTPError as exc:
                if exc.code not in CDX_BUSY_CODES or attempt == self.max_retries:
                    raise
                self.sleeper(15.0 * 2 ** attempt)
            finally:
                self.sleeper(self.min_interval_seconds)
        raise AssertionError("unreachable")

    def discover(self, query: str, limit: int) -> tuple[list[DiscoveryRecord], bytes, dict[str, object]]:
        params: dict[str, object] = {
            "url": query, "output": "json", "fl": "timestamp,original,statuscode,mimetype,digest",
            "filter": "statuscode:200", "collapse": "digest", "limit": limit, "gzip": "false",
        }
        body = self._get(params)
        try:
            records = parse_cdx_json(body, query)
        except (ValueError, OSError) as exc:
            raise DiscoveryResponseError(f"CDX response was not usable JSON: {exc}", body, params) from exc
        return records, body, params

    def captures(self, url: str, match: str = "prefix") -> list[dict[str, str]]:
        """Every good capture at or under an address, with its timestamp and MIME type."""
        body = self._get([("url", url), ("matchType", match), ("output", "json"), ("fl", "timestamp,original,mimetype,digest"),
                          ("filter", "statuscode:200"), ("collapse", "digest")])
        rows = json.loads(body.decode("utf-8") or "[]")
        return [dict(zip(rows[0], row)) for row in rows[1:]] if rows else []

    def count_pages(self, host: str, match: str = "domain") -> int:
        """Number of index pages a scan of a whole host, or of an address prefix, is divided into."""
        request_record: dict[str, object] = {"url": host, "matchType": match, "showNumPages": True}
        body = self._get([("url", host), ("matchType", match), ("showNumPages", "true")])
        try:
            return int(body.strip() or 0)
        except ValueError as exc:
            raise DiscoveryResponseError(f"CDX page count was not a number: {exc}", body, request_record) from exc

    def scan_page(
        self, host: str, url_regex: str, page: int, match: str = "domain",
    ) -> tuple[list[DiscoveryRecord], bytes, dict[str, object]]:
        """Find HTML captures on one index page whose original URL matches a Java regex.

        A single request over a whole host times out on the server, because it
        must read every capture before finding rare matches. One page is a
        bounded slice of the index, so a host is surveyed page by page.
        ``match`` is ``domain`` for a whole host, or ``prefix`` for one section
        of it such as ``geocities.com/athens/``.

        This is URL-name discovery only. It deliberately does not pretend to be
        a full-text search of archived page contents.
        """
        filters = ["statuscode:200", "mimetype:text/html", f"original:{url_regex}"]
        params: list[tuple[str, object]] = [
            ("url", host), ("matchType", match), ("page", page), ("output", "json"),
            ("fl", "timestamp,original,statuscode,mimetype,digest"),
        ] + [("filter", item) for item in filters]
        request_record: dict[str, object] = {"url": host, "matchType": match, "page": page, "filter": filters}
        body = self._get(params)
        try:
            records = parse_cdx_json(body, f"{host} / {url_regex}")
        except (ValueError, OSError) as exc:
            raise DiscoveryResponseError(f"CDX response was not usable JSON: {exc}", body, request_record) from exc
        # A page may list several captures of one address, written several
        # ways; keep the first, and leave out images labelled as HTML.
        unique: dict[str, DiscoveryRecord] = {}
        for record in records:
            if is_page_address(record.original_url):
                unique.setdefault(address_key(record.original_url), record)
        return list(unique.values()), body, request_record

class BraveWebSearch:
    """Text-query discovery via the Brave Search API; credentials stay in env vars."""
    name = "brave-web-search"
    # Project language code -> Brave ``search_lang``. Brave's own codes differ
    # for Chinese and Portuguese. ``country=ALL`` keeps results worldwide.
    SEARCH_LANGUAGES = {"en": "en", "fr": "fr", "de": "de", "es": "es", "it": "it", "pt": "pt-br", "ru": "ru", "ja": "ja", "zh": "zh-hans"}

    def __init__(self, api_key: str | None = None, min_interval_seconds: float = 1.0) -> None:
        self.api_key = api_key or os.environ.get("BRAVE_SEARCH_API_KEY")
        self.min_interval_seconds = min_interval_seconds
        if not self.api_key:
            raise ValueError("BRAVE_SEARCH_API_KEY is not set. Create a Brave Search API key, then set it as a Windows environment variable.")

    def discover(self, query: str, limit: int, language: str | None = None) -> tuple[list[DiscoveryRecord], bytes, dict[str, object]]:
        params: dict[str, object] = {"q": query, "count": min(limit, 20), "country": "ALL"}
        if language in self.SEARCH_LANGUAGES:
            params["search_lang"] = self.SEARCH_LANGUAGES[language]
        request = Request(
            f"{BRAVE_ENDPOINT}?{urlencode(params)}",
            headers={"Accept": "application/json", "Accept-Encoding": "identity", "X-Subscription-Token": self.api_key},
        )
        with urlopen(request, timeout=20) as response:
            body = response.read()
        sleep(self.min_interval_seconds)
        try:
            payload = json.loads(body)
        except ValueError as exc:
            raise DiscoveryResponseError(f"Brave response was not usable JSON: {exc}", body, params) from exc
        discovered_at = datetime.now(timezone.utc).isoformat()
        records = [DiscoveryRecord(
            url=result["url"], original_url=result["url"], archive_url="", source=self.name,
            discovered_at=discovered_at, title=result.get("title"), query=query,
        ) for result in payload.get("web", {}).get("results", []) if result.get("url")]
        return records, body, params


class WikimediaCommonsSearch:
    """Discover openly accessible file-description pages on Wikimedia Commons.

    This intentionally performs discovery only. The normal preservation queue
    decides whether a researcher-selected file should be downloaded.
    """
    name = "wikimedia-commons"

    def __init__(
        self,
        min_interval_seconds: float = 2.0,
        request_timeout_seconds: float = 20.0,
        max_retries: int = 3,
        opener: Callable[..., object] = urlopen,
        sleeper: Callable[[float], None] = sleep,
    ) -> None:
        self.min_interval_seconds = min_interval_seconds
        self.request_timeout_seconds = request_timeout_seconds
        self.max_retries = max_retries
        self.opener = opener
        self.sleeper = sleeper

    def _get(self, params: dict[str, object]) -> bytes:
        """Fetch politely: when told to slow down, wait and try again."""
        request = Request(
            f"{COMMONS_ENDPOINT}?{urlencode(params)}",
            headers={"Accept": "application/json", "User-Agent": user_agent("open cultural heritage discovery")},
        )
        for attempt in range(self.max_retries + 1):
            try:
                with self.opener(request, timeout=self.request_timeout_seconds) as response:
                    body = response.read()
                self.sleeper(self.min_interval_seconds)
                return body
            except HTTPError as exc:
                if exc.code not in BACK_OFF_CODES or attempt == self.max_retries:
                    raise
                retry_after = exc.headers.get("Retry-After", "") if exc.headers else ""
                self.sleeper(min(120.0, float(retry_after)) if retry_after.isdigit() else 10.0 * 3 ** attempt)
        raise AssertionError("unreachable")

    def discover(self, query: str, limit: int) -> tuple[list[DiscoveryRecord], bytes, dict[str, object]]:
        params: dict[str, object] = {
            "action": "query", "list": "search", "srsearch": query,
            "srnamespace": 6, "srlimit": min(limit, 50), "format": "json", "formatversion": 2,
        }
        body = self._get(params)
        try:
            payload = json.loads(body)
        except ValueError as exc:
            raise DiscoveryResponseError(f"Wikimedia Commons response was not usable JSON: {exc}", body, params) from exc
        discovered_at = datetime.now(timezone.utc).isoformat()
        records: list[DiscoveryRecord] = []
        for result in payload.get("query", {}).get("search", []):
            title = result.get("title")
            if not title:
                continue
            page_url = "https://commons.wikimedia.org/wiki/" + quote(title.replace(" ", "_"), safe=":_")
            records.append(DiscoveryRecord(
                url=page_url, original_url=page_url, archive_url="", source=self.name,
                discovered_at=discovered_at, title=title, query=query, source_id=str(result.get("pageid", "")) or None,
                access_status="open", has_fulltext=True, has_graphics=True,
                notes="Open Wikimedia Commons file-description page; download only after review.",
            ))
        return records, body, params
