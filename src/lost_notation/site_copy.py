"""A complete, browsable copy of an archived website, made from the Wayback Machine.

Every page, picture, sound and download the archive holds for a kept website,
from every date it was captured, is saved into a folder laid out like the
original site, and the links are changed so the copy can be read offline.
Different captures often hold different pictures, so each file comes from its
newest good capture, falling back to older captures when that one is broken.
Pictures the pages show but the archive never captured at the site's own
address are then looked for elsewhere: in the archive's captures of the
OoCities mirror (for GeoCities sites), and, for pictures kept on other sites,
at their own addresses. Links to anything still missing, or to other sites,
open in the Wayback Machine instead.

Each page is also saved into the catalogue, so its text can be read in the
Collection and appears in the Obsidian note. ``_copy.json`` in the folder
records where each file came from; running the copy again finishes anything
left undone and tries failed files again.
"""
from __future__ import annotations

import hashlib
from html import escape, unescape
import json
from pathlib import Path
import posixpath
import re
import time
from typing import Callable, Iterator, Optional
from urllib.error import HTTPError
from urllib.parse import quote, unquote, urljoin, urlsplit, urlunsplit
from urllib.request import Request, urlopen

from .desk import note_name
from .discovery import BACK_OFF_CODES, InternetArchiveCdx, address_key, user_agent
from .pipeline import extract
from .store import Store, now

COPIES_FOLDER = "Site copies"
MANIFEST = "_copy.json"
CONTENTS = "_contents.html"
ELSEWHERE = "_elsewhere"
MAX_FILES = 3000
MAX_FILE_BYTES = 60 * 1024 * 1024
# Older captures tried when a file's newest capture is broken.
MAX_OLDER_CAPTURES = 4
# Pictures from other sites looked up one by one, at most.
MAX_LOOKUPS = 60
PAGE_SUFFIXES = (".htm", ".html", ".shtml", ".asp", ".php", ".cgi", ".pl", ".jsp", ".cfm")
PICTURE_SUFFIXES = (".gif", ".jpg", ".jpeg", ".png", ".bmp", ".ico", ".tif", ".tiff")
# How long to wait when the archive asks us to slow down or stops answering.
BACK_OFF_SECONDS = (60.0, 120.0)
WAYBACK_LINK = re.compile(r"(?i)^(?:https?:)?//web\.archive\.org/web/\d+[a-z_]*/(.+)$")
SKIPPED_LINK = re.compile(r"(?i)^(?:#|mailto:|javascript:|data:|about:|news:|ftp:|tel:)")
TAG = re.compile(r"<[a-zA-Z][^>]*>")
ATTRIBUTE = re.compile(r"""(?is)\s(href|src|background|lowsrc|data)(\s*=\s*)("[^"]*"|'[^']*'|[^\s>"']+)""")
EMBEDDING = {"src", "background", "lowsrc", "data"}
BASE_TAG = re.compile(r"(?is)<base\s[^>]*>")
BASE_HREF = re.compile(r"""(?i)href\s*=\s*("[^"]*"|'[^']*'|[^\s>]+)""")
REFRESH = re.compile(r"""(?i)(content\s*=\s*["']?\s*\d*\s*;\s*url\s*=\s*)([^"'>\s]+)""")
CSS_URL = re.compile(r"""(?i)(url\(\s*)("[^"]*"|'[^']*'|[^)\s]*)(\s*\))""")
# Visitor counters, tracking pixels and the hosts' own advertising: never worth looking for.
NOT_CONTENT = re.compile(r"(?i)^https?://(?:[^/]*@)?(?:visit\.|[^/]*counter|[^/]*\.hitbox\.com|[^/]*yimg\.com|geo\.yahoo\.com)|/visit\.gif|/counter")
UNSAFE_NAME = re.compile(r'[<>:"\\|?*\x00-\x1f]')
RESERVED_NAMES = {"con", "prn", "aux", "nul"} | {f"{port}{n}" for port in ("com", "lpt") for n in range(1, 10)}
FOUND_IN = {"mirror": "from the OoCities mirror", "elsewhere": "from another site"}


class ArchiveUnavailable(RuntimeError):
    """The archive keeps asking us to slow down, or stopped answering."""


def copy_folder(data_dir: Path, site: str) -> Path:
    return data_dir / COPIES_FOLDER / note_name(site)


# Where each file goes ---------------------------------------------------------

def _safe_name(part: str) -> str:
    try:
        part = unquote(part, errors="strict")
    except UnicodeDecodeError:  # not UTF-8: keep the escaped form, which links quote back
        pass
    part = UNSAFE_NAME.sub("_", part).rstrip(". ") or "_"
    if part.split(".", 1)[0].lower() in RESERVED_NAMES:
        part = "_" + part
    return part[:120]


def local_path(url: str, site: str) -> Optional[str]:
    """Where an address of the site goes inside its copy, or None when it belongs to another site.

    Folders become ``index.html``; letter case is ignored, as the old hosts did.
    """
    key = address_key(url)
    path, _, query = key.partition("?")
    if path != site and not path.startswith(site + "/"):
        return None
    parts: list[str] = []
    for part in path[len(site):].split("/"):
        if part == "..":
            parts = parts[:-1]
        elif part not in {"", "."}:
            parts.append(part)
    if not parts or path.endswith("/") or "." not in parts[-1]:
        parts.append("index.html")
    if query:
        stem, dot, suffix = parts[-1].rpartition(".")
        tag = hashlib.sha1(query.encode("utf-8")).hexdigest()[:8]
        parts[-1] = f"{stem}_{tag}.{suffix}" if dot else f"{parts[-1]}_{tag}"
    return "/".join(_safe_name(part) for part in parts)


def elsewhere_path(url: str) -> str:
    """Where a file kept on another site goes inside the copy."""
    host = address_key(url).split("?", 1)[0].split("/", 1)[0]
    return f"{ELSEWHERE}/{_safe_name(host)}/{local_path(url, host)}"


def is_page(entry: dict[str, str]) -> bool:
    return entry.get("mimetype", "").lower() == "text/html" or entry["path"].lower().endswith(PAGE_SUFFIXES)


def is_picture(entry: dict[str, str]) -> bool:
    return entry.get("mimetype", "").lower().startswith("image/") or entry["path"].lower().endswith(PICTURE_SUFFIXES)


# What the archive holds -------------------------------------------------------

def choose_captures(rows: list[dict[str, str]], site: str) -> list[dict]:
    """The newest good capture of each file of the site, with a few older ones to fall back on.

    Front page first, then the other pages, then everything else.
    """
    captures: dict[str, list[dict[str, str]]] = {}
    for row in rows:
        path = local_path(row["original"], site)
        mime = (row.get("mimetype") or "").lower()
        if path is None or mime == "warc/revisit":
            continue
        if path.lower().endswith(PICTURE_SUFFIXES) and mime == "text/html":
            continue  # an error page captured in place of a missing picture
        captures.setdefault(path, []).append(row)
    chosen = []
    for path, found in captures.items():
        found.sort(key=lambda row: row["timestamp"], reverse=True)
        newest = found[0]
        chosen.append({"path": path, "original": newest["original"], "timestamp": newest["timestamp"], "mimetype": (newest.get("mimetype") or "").lower(),
                       "older": [[row["timestamp"], row["original"]] for row in found[1:1 + MAX_OLDER_CAPTURES]], "state": "waiting", "error": ""})
    return sorted(chosen, key=lambda entry: (entry["path"] != "index.html", not is_page(entry), entry["path"]))


def list_site(cdx: InternetArchiveCdx, site: str) -> list[dict[str, str]]:
    rows = cdx.captures(site + "/", "prefix")
    if "/" in site:  # the bare address, e.g. geocities.com/taquigra without its slash
        rows += cdx.captures(site, "exact")
    return rows


def raw_capture(timestamp: str, original: str) -> str:
    return f"https://web.archive.org/web/{timestamp}id_/{original}"


def load_manifest(folder: Path) -> dict:
    try:
        return json.loads((folder / MANIFEST).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _save_manifest(folder: Path, manifest: dict) -> None:
    manifest["updated"] = now()
    (folder / MANIFEST).write_text(json.dumps(manifest, ensure_ascii=False, indent=1), encoding="utf-8")


def copy_progress(folder: Path) -> Optional[dict[str, int]]:
    manifest = load_manifest(folder); files = manifest.get("files") or []
    if not files:
        return None
    saved = [entry for entry in files if entry["state"] == "saved"]
    return {
        "total": len(files), "saved": len(saved), "failed": sum(entry["state"] == "failed" for entry in files),
        "waiting": sum(entry["state"] == "waiting" for entry in files), "pages": sum(is_page(entry) for entry in saved),
        "pictures": sum(is_picture(entry) and not is_page(entry) for entry in saved), "found_elsewhere": sum(bool(entry.get("found")) for entry in saved),
        "missing": len(manifest.get("missing", [])), "left_out": int(manifest.get("left_out", 0)), "front_page": int((folder / "index.html").exists()),
    }


# Downloading ------------------------------------------------------------------

def _fetch(url: str, opener: Callable[..., object], sleeper: Callable[[float], None]) -> bytes:
    request = Request(url, headers={"User-Agent": user_agent("keeping a researcher's own copy of an archived site")})
    for attempt in range(len(BACK_OFF_SECONDS) + 1):
        try:
            with opener(request, timeout=60) as response:
                body = response.read(MAX_FILE_BYTES + 1)
            if len(body) > MAX_FILE_BYTES:
                raise ValueError(f"larger than {MAX_FILE_BYTES // (1024 * 1024)} MB; not saved")
            return body
        except HTTPError as exc:
            if exc.code not in BACK_OFF_CODES:
                raise
            problem = f"the archive asked us to slow down (HTTP {exc.code})"
        except (OSError, TimeoutError) as exc:  # refused, reset or timed out
            problem = f"the archive stopped answering ({type(exc).__name__})"
        if attempt == len(BACK_OFF_SECONDS):
            raise ArchiveUnavailable(problem)
        sleeper(BACK_OFF_SECONDS[attempt])
    raise AssertionError("unreachable")


def _fetch_entry(entry: dict, opener: Callable[..., object], sleeper: Callable[[float], None], delay_seconds: float) -> bytes:
    """The file from its newest capture, or from an older one when that is broken."""
    picture = entry["path"].lower().endswith(PICTURE_SUFFIXES)
    problem: Exception = ValueError("no capture to fetch")
    for index, (timestamp, original) in enumerate([(entry["timestamp"], entry["original"])] + [tuple(older) for older in entry.get("older", [])]):
        if index:
            sleeper(delay_seconds)
        try:
            body = _fetch(raw_capture(timestamp, original), opener, sleeper)
        except ArchiveUnavailable:
            raise
        except Exception as exc:
            problem = exc; continue
        if not body or (picture and body.lstrip()[:1] == b"<"):
            problem = ValueError("the archive returned an error page instead of the picture"); continue
        entry["timestamp"], entry["original"] = timestamp, original
        return body
    raise problem


def _register_page(store: Store, key: str, source: str, entry: dict, body: bytes) -> None:
    """Keep a copied page in the catalogue too, so its text can be read and noted."""
    url = raw_capture(entry["timestamp"], entry["original"])
    store.add_candidate({"url": url, "original_url": entry.get("as") or entry["original"], "archive_url": url, "source": source, "discovered_at": now(),
                         "query": key, "title": "front page" if entry["path"] == "index.html" else entry["path"]})
    with store.connect() as con:
        candidate = con.execute("SELECT id,fetch_state FROM candidates WHERE url=?", (url,)).fetchone()
        con.execute("INSERT OR IGNORE INTO triage_copies(item_key,candidate_id) VALUES(?,?)", (key, candidate["id"]))
    if candidate["fetch_state"] != "fetched":
        store.record_retrieval(int(candidate["id"]), status_code=200, content_type="text/html", blob_sha256=store.put_blob(body), final_url=url, error=None)


def _download(
    store: Store, key: str, source: str, folder: Path, manifest: dict, entries: list[dict], site: str, *,
    opener: Callable[..., object], sleeper: Callable[[float], None], delay_seconds: float, progress: Optional[Callable[[str], None]],
) -> tuple[int, str]:
    """Download these entries; returns how many were saved, and why it stopped early if it did."""
    root = folder.resolve(); saved = 0
    for index, entry in enumerate(entries):
        if index:
            sleeper(delay_seconds)
        if progress:
            progress(f"Copying {site}: file {index + 1:,} of {len(entries):,}")
        target = (folder / entry["path"]).resolve()
        if root not in target.parents:
            entry.update(state="failed", error="unsafe file name"); continue
        try:
            body = _fetch_entry(entry, opener, sleeper, delay_seconds)
            target.parent.mkdir(parents=True, exist_ok=True); target.write_bytes(body)
        except ArchiveUnavailable as exc:
            _save_manifest(folder, manifest); return saved, str(exc)
        except Exception as exc:  # recorded, and tried again next time
            entry.update(state="failed", error=f"{type(exc).__name__}: {exc}"[:200]); continue
        entry.update(state="saved", error=""); saved += 1
        if is_page(entry):
            _register_page(store, key, source, entry, body)
        if index % 10 == 9:
            _save_manifest(folder, manifest)
    _save_manifest(folder, manifest)
    return saved, ""


# Links ------------------------------------------------------------------------

def _base(text: str, entry: dict) -> str:
    """The address a page's relative links were written against."""
    address = entry.get("as") or entry["original"]  # a mirror's copy stands in for the original page
    parts = urlsplit(address)
    if entry["path"].endswith("index.html") and not parts.path.endswith("/") and "." not in parts.path.rsplit("/", 1)[-1]:
        address = urlunsplit(parts._replace(path=parts.path + "/"))  # a folder addressed without its slash
    declared = BASE_TAG.search(text)
    href = BASE_HREF.search(declared.group(0)) if declared else None
    return urljoin(address, unescape(href.group(1).strip("'\""))) if href else address


def _resolve(value: str, base: str) -> Optional[tuple[str, str]]:
    """The absolute address a link points to, and its #fragment; None for links to leave alone."""
    raw = unescape(value.strip().strip("'\""))
    if not raw or SKIPPED_LINK.match(raw):
        return None
    archived = WAYBACK_LINK.match(raw)
    if archived:  # a link this copier wrote earlier; work out the original again
        raw = archived.group(1)
    target, _, fragment = urljoin(base, raw).partition("#")
    return (target, fragment) if urlsplit(target).scheme in {"http", "https"} else None


def _path_for(target: str, site: str) -> str:
    return local_path(target, site) or elsewhere_path(target)


def _references(text: str, entry: dict, site: str) -> Iterator[tuple[str, bool]]:
    """Every address a copied page links to, and whether it is shown in the page (a picture) rather than linked."""
    base = _base(text, entry)
    for tag in TAG.findall(text):
        for name, _, value in ATTRIBUTE.findall(tag):
            resolved = _resolve(value, base)
            if resolved:
                yield resolved[0], name.lower() in EMBEDDING
    for match in CSS_URL.finditer(text):
        resolved = _resolve(match.group(2), base)
        if resolved:
            yield resolved[0], True


def rewrite_page(text: str, entry: dict, site: str, available: set[str], css: bool = False) -> str:
    """Point a copied page's links at the copy, or at the Wayback Machine for anything not copied."""
    base = _base(text, entry)

    def replace(value: str) -> Optional[str]:
        resolved = _resolve(value, base)
        if resolved is None:
            return None
        target, fragment = resolved; path = _path_for(target, site)
        if path in available:
            new = quote(posixpath.relpath(path, posixpath.dirname(entry["path"]) or "."), safe="/")
        else:
            new = f"https://web.archive.org/web/{entry['timestamp']}/{target}"
        return new + ("#" + fragment if fragment else "")

    def css_url(match: re.Match) -> str:
        new = replace(match.group(2))
        return match.group(0) if new is None else f"{match.group(1)}{new.replace(' ', '%20').replace('(', '%28').replace(')', '%29')}{match.group(3)}"

    if not css:
        text = BASE_TAG.sub(lambda match: "<!-- " + match.group(0).replace("--", "- -") + " -->", text)

        def attribute(match: re.Match) -> str:
            new = replace(match.group(3))
            return match.group(0) if new is None else f' {match.group(1)}{match.group(2)}"{escape(new, quote=True)}"'

        def refresh(match: re.Match) -> str:
            new = replace(match.group(2))
            return match.group(0) if new is None else match.group(1) + new

        def tag(match: re.Match) -> str:
            rewritten = ATTRIBUTE.sub(attribute, match.group(0))
            return REFRESH.sub(refresh, rewritten) if rewritten[:5].lower() == "<meta" else rewritten

        text = TAG.sub(tag, text)
    return CSS_URL.sub(css_url, text)


def _rewritable(entry: dict) -> bool:
    return entry["state"] == "saved" and (is_page(entry) or entry["path"].lower().endswith(".css"))


def _read(folder: Path, entry: dict) -> str:
    return (folder / entry["path"]).read_bytes().decode("latin-1")  # byte for byte, whatever the page's own encoding


def rewrite_copy(folder: Path, site: str, manifest: dict) -> None:
    """Point every copied page at the copy. Safe to repeat: links written earlier are worked out again."""
    available = {entry["path"] for entry in manifest["files"] if entry["state"] == "saved"}
    for entry in manifest["files"]:
        if _rewritable(entry):
            original = _read(folder, entry)
            rewritten = rewrite_page(original, entry, site, available, entry["path"].lower().endswith(".css"))
            if rewritten != original:
                (folder / entry["path"]).write_bytes(rewritten.encode("latin-1", errors="xmlcharrefreplace"))
    _write_contents(folder, site, manifest)


# Looking for missing pictures -------------------------------------------------

def wanted_files(folder: Path, site: str, manifest: dict) -> dict[str, str]:
    """Files the copied pages use that the copy lacks: anything of the site's own, and pictures shown from other sites."""
    have = {entry["path"] for entry in manifest["files"]}
    wanted: dict[str, str] = {}
    for entry in manifest["files"]:
        if not _rewritable(entry):
            continue
        for target, shown in _references(_read(folder, entry), entry, site):
            if NOT_CONTENT.search(target):
                continue
            own = local_path(target, site)
            if own is None and not (shown and urlsplit(target).path.lower().endswith(PICTURE_SUFFIXES)):
                continue
            path = own or elsewhere_path(target)
            if path not in have:
                wanted.setdefault(path, target)
    return wanted


def find_missing(cdx: InternetArchiveCdx, folder: Path, site: str, manifest: dict, max_lookups: int = MAX_LOOKUPS) -> list[dict]:
    """Look for files the pages use beyond the site's own captures; returns new entries to download.

    A GeoCities site's own files are looked for in the archive's captures of the
    OoCities mirror; pictures kept on other sites are looked up at their own addresses.
    """
    wanted = wanted_files(folder, site, manifest); found: list[dict] = []
    host, _, member = site.partition("/")
    if host == "geocities.com" and member and any(not path.startswith(ELSEWHERE + "/") for path in wanted):
        mirror = f"oocities.org/{member}"
        for entry in choose_captures(list_site(cdx, mirror), mirror):
            if entry["path"] in wanted:
                found.append({**entry, "as": wanted[entry["path"]], "found": "mirror"})
    got = {entry["path"] for entry in found}
    elsewhere = [(path, url) for path, url in wanted.items() if path.startswith(ELSEWHERE + "/") and path not in got]
    for path, url in elsewhere[:max_lookups]:
        pictures = [row for row in cdx.captures(url, "exact") if not (row.get("mimetype") or "").lower().startswith("text/")]
        for entry in choose_captures(pictures, address_key(url).split("?", 1)[0].split("/", 1)[0])[:1]:
            found.append({**entry, "path": path, "found": "elsewhere"}); got.add(path)
    manifest["missing"] = sorted(url for path, url in wanted.items() if path not in got and path.lower().endswith(PICTURE_SUFFIXES))
    return found


def _write_contents(folder: Path, site: str, manifest: dict) -> None:
    files = manifest["files"]
    saved = [entry for entry in files if entry["state"] == "saved"]
    pages = sum(is_page(entry) for entry in saved); pictures = sum(is_picture(entry) and not is_page(entry) for entry in saved)
    rows = []
    for entry in files:
        name = escape(entry["path"])
        shown = f"<a href='{escape(quote(entry['path'], safe='/'), quote=True)}'>{name}</a>" if entry["state"] == "saved" else name
        status = {"saved": FOUND_IN.get(entry.get("found", ""), ""), "failed": f"not saved: {escape(entry['error'])}", "waiting": "not saved yet"}[entry["state"]]
        captured = f"{entry['timestamp'][:4]}-{entry['timestamp'][4:6]}-{entry['timestamp'][6:8]}"
        rows.append(f"<tr><td>{shown}</td><td><a href='https://web.archive.org/web/{entry['timestamp']}/{escape(entry['original'], quote=True)}'>{captured}</a></td><td>{status}</td></tr>")
    left_out = f" {manifest['left_out']:,} more files were left out to keep the copy a manageable size." if manifest.get("left_out") else ""
    front = "<a href='index.html'>Open the front page</a>." if (folder / "index.html").exists() else "The archive holds no front page for this site; start from any page below."
    missing = manifest.get("missing", [])
    missing_list = (f"<h2>Pictures not found</h2><p>The pages show {len(missing):,} picture(s) that no capture holds, at the site's own address or anywhere else looked. "
                    "Each link lists every capture of that address, in case one turns up later.</p><ul>"
                    + "".join(f"<li><a href='https://web.archive.org/web/*/{escape(url, quote=True)}'>{escape(url)}</a></li>" for url in missing) + "</ul>") if missing else ""
    (folder / CONTENTS).write_text(f"""<!doctype html><html lang='en'><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'>
<title>Copy of {escape(site)}</title><style>body{{font:15px/1.5 system-ui,sans-serif;margin:2rem auto;max-width:62rem;padding:0 1rem;color:#1d2b33;background:#fff}}
table{{border-collapse:collapse;width:100%}}td,th{{padding:.3rem .6rem;text-align:left;border-bottom:1px solid #dde4e6;vertical-align:top}}td:first-child,li{{word-break:break-all}}</style></head><body>
<h1>Your copy of {escape(site)}</h1>
<p>Made from the Internet Archive's Wayback Machine by the Lost Notation Project, last updated {escape(manifest['updated'][:10])}. {len(saved):,} of {len(files):,} files saved: {pages:,} pages, {pictures:,} pictures and {len(saved) - pages - pictures:,} other files.{left_out} {front}</p>
<p>Captures from every date are combined. Each file comes from its newest good capture, or an older one when that is broken; its date links to that capture in the Wayback Machine. Pictures the archive never captured at the site's own address were looked for in other places, noted beside each. Links to pages the archive does not hold, and to other sites, open in the Wayback Machine.</p>
<table><thead><tr><th>File</th><th>Captured</th><th></th></tr></thead><tbody>{''.join(rows)}</tbody></table>{missing_list}</body></html>""", encoding="utf-8")


def copy_site(
    store: Store, key: str, site: str, folder: Path, *, source: str = "Internet Archive", cdx: Optional[InternetArchiveCdx] = None,
    opener: Callable[..., object] = urlopen, sleeper: Callable[[float], None] = time.sleep, delay_seconds: float = 4.0,
    progress: Optional[Callable[[str], None]] = None, max_files: int = MAX_FILES,
) -> str:
    """Save (or finish saving) a complete copy of an archived site into ``folder``."""
    folder.mkdir(parents=True, exist_ok=True)
    cdx = cdx or InternetArchiveCdx(opener=opener, sleeper=sleeper)
    manifest = load_manifest(folder)
    if not manifest.get("files"):
        if progress:
            progress(f"Listing everything archived for {site}")
        try:
            found = choose_captures(list_site(cdx, site), site)
        except (OSError, ValueError) as exc:
            return f"Could not list what the archive holds for {site} ({type(exc).__name__}: {exc}). Try again later."
        if not found:
            return f"The Wayback Machine holds no copied files for {site}."
        manifest = {"site": site, "lead": key, "listed": now(), "left_out": max(0, len(found) - max_files), "files": found[:max_files]}
    steps = dict(opener=opener, sleeper=sleeper, delay_seconds=delay_seconds, progress=progress)
    todo = [entry for entry in manifest["files"] if entry["state"] != "saved" or not (folder / entry["path"]).exists()]
    saved, stopped = _download(store, key, source, folder, manifest, todo, site, **steps)
    if not stopped and not manifest.get("searched"):
        if progress:
            progress(f"Looking for pictures missing from {site}")
        try:
            extra = find_missing(cdx, folder, site, manifest)
        except (OSError, ValueError):
            extra = None  # looked for again next time
        if extra is not None:
            manifest["files"] += extra; manifest["searched"] = True
            more, stopped = _download(store, key, source, folder, manifest, extra, site, **steps); saved += more
    if progress:
        progress(f"Linking the pages of {site} together")
    rewrite_copy(folder, site, manifest)
    extract(store)
    status = copy_progress(folder) or {}
    said = f"Copy of {site}: {status.get('saved', 0):,} of {status.get('total', 0):,} files saved ({saved:,} new"
    said += f", {status['found_elsewhere']:,} found outside the site's own captures)" if status.get("found_elsewhere") else ")"
    if status.get("failed"):
        said += f"; {status['failed']:,} could not be saved"
    if status.get("missing"):
        said += f"; {status['missing']:,} pictures the pages show were not found anywhere"
    if stopped:
        said += f". Stopped because {stopped}; save the complete copy again later to finish it"
    return said + "."
