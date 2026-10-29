"""Small local web interface for reviewing unpromoted archival discoveries."""
from __future__ import annotations

from fnmatch import fnmatchcase
from html import escape
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import re
from urllib.parse import parse_qs, urlencode, urlparse
import webbrowser

from .discovery import address_key, is_page_address, site_key
from .store import Store

ARCHIVE_SOURCE = "internet-archive-cdx-url-regex"
HIDDEN_SITES_FILE = Path(__file__).resolve().parents[2] / "examples" / "review-hidden-sites.txt"
# The "script" inside these words fooled an early probe that searched for "script".
SCRIPT_WORDS = re.compile(r"(?i)(?:(?<=java)|(?<=vb))script")

def load_hidden_sites(path: Path) -> list[str]:
    try:
        lines = path.read_text(encoding="utf-8-sig").splitlines()
    except OSError:
        return []
    return [address_key(line.strip()).strip().rstrip("/") for line in lines if line.strip() and not line.lstrip().startswith("#")]

def is_hidden(url: str, patterns: list[str]) -> bool:
    key = address_key(url)
    return any(fnmatchcase(key, pattern) or fnmatchcase(key, pattern + "/*") for pattern in patterns)

def matched_only_by_script_words(row: dict[str, str]) -> bool:
    """True when an address fits its probe only because "script" sits inside "javascript" or "vbscript"."""
    url_regex, original = row.get("url_regex", ""), row["original_url"]
    if not url_regex or not SCRIPT_WORDS.search(original):
        return False
    try:
        return re.fullmatch(url_regex, SCRIPT_WORDS.sub("", original)) is None
    except re.error:
        return False

def discovery_rows(store: Store, hidden_file: Path = HIDDEN_SITES_FILE) -> tuple[list[dict[str, str]], int]:
    """Rows for the review page, minus hidden sites and accidental "javascript" matches, and how many were hidden."""
    rows = cdx_rows(store)
    patterns = load_hidden_sites(hidden_file)
    shown = [row for row in rows if not is_hidden(row["original_url"], patterns) and not matched_only_by_script_words(row)]
    return shown, len(rows) - len(shown)

def group_by_site(records: list[dict[str, str]], checked_keys: set[str]) -> list[dict[str, object]]:
    """One entry per archived member site (single web or Commons finds stay alone),
    unchecked first and biggest first."""
    groups: dict[str, list[dict[str, str]]] = {}
    for row in records:
        site = site_key(row["original_url"]) if row["source"] == ARCHIVE_SOURCE else row["original_url"]
        groups.setdefault(site, []).append(row)
    entries = [{"site": site, "rows": rows, "checked": all(row["key"] in checked_keys for row in rows)} for site, rows in groups.items()]
    return sorted(entries, key=lambda entry: (entry["checked"], -len(entry["rows"])))

def unchecked_discoveries(store: Store, hidden_file: Path = HIDDEN_SITES_FILE) -> int:
    """How many sites or single finds on the review page have not yet been ticked Checked."""
    rows, _ = discovery_rows(store, hidden_file)
    return sum(not entry["checked"] for entry in group_by_site(rows, store.checked_review_keys()))

def form_keys(form: dict[str, list[str]], name: str) -> set[str]:
    """Each box carries one record key, or a whole site's keys separated by spaces."""
    return {key for value in form.get(name, []) for key in value.split()}

def cdx_rows(store: Store, limit: int = 5000) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    seen_urls: set[str] = set()
    for run in store.discovery_runs(2000, with_results_only=True):
        # The first survey's wildcard runs remain preserved as evidence, but
        # are deliberately excluded: CDX wildcard scope matching returned host
        # landing pages rather than keyword-matched URLs.
        if run["source"] == "brave-web-search":
            try:
                payload = json.loads(store.blob(run["response_sha256"]))
                query = json.loads(run["request_json"]).get("q", "")
            except (OSError, ValueError, TypeError):
                continue
            for index, item in enumerate(payload.get("web", {}).get("results", [])):
                original = item.get("url")
                if not original or original in seen_urls:
                    continue
                seen_urls.add(original)
                rows.append({"key": f"{run['id']}:{index}", "run_id": str(run["id"]), "timestamp": "Live web result", "original_url": original, "archive_url": original, "query": str(query), "discovered_at": run["completed_at"], "title": item.get("title", ""), "source": "brave-web-search"})
                if len(rows) >= limit: return rows
            continue
        if run["source"] == "wikimedia-commons":
            try:
                payload = json.loads(store.blob(run["response_sha256"]))
                query = json.loads(run["request_json"]).get("srsearch", "")
            except (OSError, ValueError, TypeError):
                continue
            for index, item in enumerate(payload.get("query", {}).get("search", [])):
                title = item.get("title")
                if not title:
                    continue
                original = "https://commons.wikimedia.org/wiki/" + title.replace(" ", "_")
                if original in seen_urls:
                    continue
                seen_urls.add(original)
                rows.append({
                    "key": f"{run['id']}:{index}", "run_id": str(run["id"]), "timestamp": "Open Commons file", "original_url": original,
                    "archive_url": original, "query": str(query), "discovered_at": run["completed_at"], "title": title, "source": "wikimedia-commons",
                    "source_id": str(item.get("pageid", "")) or None, "access_status": "open", "has_fulltext": True, "has_graphics": True,
                    "notes": "Open Wikimedia Commons file-description page; download only after review.",
                })
                if len(rows) >= limit:
                    return rows
            continue
        if run["source"] != "internet-archive-cdx-url-regex": continue
        try:
            payload = json.loads(store.blob(run["response_sha256"]))
            headers = payload[0] if payload else []
            request = json.loads(run["request_json"])
            query = request.get("url", "")
            filters = request.get("filter", [])
            filters = [filters] if isinstance(filters, str) else filters
            url_regex = next((item[len("original:"):] for item in filters if item.startswith("original:")), "")
        except (OSError, ValueError, TypeError, AttributeError):
            continue
        for index, values in enumerate(payload[1:]):
            item = dict(zip(headers, values))
            timestamp, original = item.get("timestamp"), item.get("original")
            if not timestamp or not original or not is_page_address(original):
                continue
            if address_key(original) in seen_urls:
                continue
            seen_urls.add(address_key(original))
            replay = f"https://web.archive.org/web/{timestamp}id_/{original}"
            rows.append({
                "key": f"{run['id']}:{index}", "run_id": str(run["id"]), "timestamp": timestamp,
                "original_url": original, "archive_url": replay, "query": str(query),
                "discovered_at": run["completed_at"], "title": "", "source": "internet-archive-cdx-url-regex", "url_regex": url_regex,
            })
            if len(rows) >= limit:
                return rows
    return rows

def link(url: str, text: str) -> str:
    return f"<a href='{escape(url, quote=True)}' target='_blank' rel='noreferrer'>{escape(text)}</a>"

def page_name(row: dict[str, str], site: str) -> str:
    key = address_key(row["original_url"])
    return (key[len(site):] if key.startswith(site) else key).lstrip("/") or "front page"

def site_row(entry: dict[str, object]) -> str:
    rows: list[dict[str, str]] = entry["rows"]  # type: ignore[assignment]
    site, first, keys = str(entry["site"]), rows[0], " ".join(row["key"] for row in rows)
    checked = f"<td><input type='hidden' name='visible' value='{escape(keys)}'><input type='checkbox' name='checked' value='{escape(keys)}' {'checked' if entry['checked'] else ''}></td>"
    found_by = f"<td><code>{escape(first['query'])}</code></td>"
    if len(rows) == 1:
        live = first["timestamp"] in {"Live web result", "Open Commons file"}
        return (f"<tr>{checked}<td><input type='checkbox' name='candidate' value='{escape(first['key'])}'></td>"
                f"<td>{escape(first.get('title') or first['original_url'])}<br>{link(first['archive_url'], 'Open page' if live else 'Open archive')} "
                f"<small>{escape(first['timestamp'] if live else first['timestamp'][:4])}</small></td>{found_by}</tr>")
    names = [page_name(row, site) for row in rows]
    front = next((row for row, name in zip(rows, names) if name in {"front page", "index.htm", "index.html"}), None)
    opener = link(front["archive_url"], "open front page") if front else link(first["archive_url"], "open first page")
    pages ="".join(f"<li><label><input type='checkbox' name='candidate' value='{escape(row['key'])}'> {escape(name)}</label> {link(row['archive_url'], 'open')} <small>{escape(row['timestamp'][:4])}</small></li>" for row, name in zip(rows, names))
    sample = ", ".join(names[:6]) + (", …" if len(names) > 6 else "")
    return (f"<tr>{checked}<td><label><input type='checkbox' name='candidate' value='{escape(keys)}'> all {len(rows)}</label></td>"
            f"<td><strong>{escape(site)}</strong> · {len(rows)} pages · {opener} · "
            f"{link(f'https://web.archive.org/web/*/{site}/*', 'list everything archived')}<br><small>{escape(sample)}</small>"
            f"<details><summary>Choose individual pages</summary><ul>{pages}</ul></details></td>{found_by}</tr>")

def page(records: list[dict[str, str]], checked_keys: set[str], message: str = "", base_path: str = "", hidden: int = 0) -> str:
    escaped_message = f"<p class='message'>{escape(message)}</p>" if message else ""
    hidden_note = f" {hidden} more are hidden as off-topic: sites listed in examples/review-hidden-sites.txt, and addresses that matched only through \"javascript\" or \"vbscript\"." if hidden else ""
    entries = group_by_site(records, checked_keys)
    if not records:
        body = "<p>No archival discoveries have been recorded yet. Run the automatic first survey, then refresh this page.</p>"
    else:
        body = (
            f"<form method='post' action='{base_path}/promote'>"
            "<p>Each archived site is one line; tick <strong>Checked</strong> once you have looked at it, and it moves to the end. Tick <strong>Preserve</strong> for a whole site, or open <em>Choose individual pages</em>. Promotion adds pages to the download queue; it does not download them yet.</p>"
            f"<button type='submit' formaction='{base_path}/checked'>Save checked progress</button> "
            f"<button type='submit' formaction='{base_path}/promote'>Add selected pages to preservation queue</button>"
            "<table><thead><tr><th>Checked</th><th>Preserve</th><th>Site or page</th><th>Found by</th></tr></thead>"
            f"<tbody>{''.join(site_row(entry) for entry in entries)}</tbody></table></form>"
        )
    return f"""<!doctype html><html lang='en'><head><meta charset='utf-8'><title>Lost Notation review</title>
<style>html{{background:#fff}}body{{font-family:system-ui,sans-serif;max-width:1200px;margin:2rem auto;padding:0 1rem;color:#202124;background:#fff}}h1{{margin-bottom:.25rem}}table{{border-collapse:collapse;width:100%;margin-top:1rem}}th,td{{border-bottom:1px solid #ddd;padding:.65rem;text-align:left;vertical-align:top}}th{{background:#f5f5f5}}a{{color:#0759aa}}code{{font-size:.85em;word-break:break-word}}button{{background:#0759aa;color:white;border:0;border-radius:4px;padding:.7rem 1rem;font-size:1rem;cursor:pointer}}.message{{background:#e6f4ea;padding:.75rem;border-radius:4px}}td small{{color:#5f6368;word-break:break-word}}details{{margin-top:.4rem}}summary{{cursor:pointer;color:#0759aa}}details ul{{max-height:22rem;overflow:auto;margin:.4rem 0;padding-left:1.2rem}}details li{{margin:.15rem 0;word-break:break-word}}</style>
</head><body><h1>Discovery review</h1><p>Showing {len(records)} finds as {len(entries)} sites or single pages.{escape(hidden_note)}</p>{escaped_message}{body}</body></html>"""

def handler_for(store: Store):
    class ReviewHandler(BaseHTTPRequestHandler):
        def send_html(self, content: str) -> None:
            encoded = content.encode("utf-8")
            self.send_response(200); self.send_header("Content-Type", "text/html; charset=utf-8"); self.send_header("Content-Length", str(len(encoded))); self.end_headers(); self.wfile.write(encoded)

        def do_GET(self) -> None:  # noqa: N802
            parsed = urlparse(self.path)
            if parsed.path != "/":
                self.send_error(404); return
            rows, hidden = discovery_rows(store)
            self.send_html(page(rows, store.checked_review_keys(), parse_qs(parsed.query).get("message", [""])[0], hidden=hidden))

        def do_POST(self) -> None:  # noqa: N802
            if self.path not in {"/promote", "/checked"}:
                self.send_error(404); return
            size = int(self.headers.get("Content-Length", "0"))
            form = parse_qs(self.rfile.read(size).decode("utf-8"))
            chosen = form_keys(form, "candidate")
            visible = form_keys(form, "visible")
            if self.path == "/checked":
                store.save_review_marks(visible, form_keys(form, "checked"))
                location = "/?" + urlencode({"message": "Checked progress saved."})
                self.send_response(303); self.send_header("Location", location); self.end_headers()
                return
            records = {row["key"]: row for row in cdx_rows(store)}
            added = 0
            for key in chosen:
                row = records.get(key)
                if row and store.add_candidate({
                    "url": row["archive_url"], "original_url": row["original_url"], "archive_url": row["archive_url"],
                    "source": row.get("source", "internet-archive-cdx"), "source_id": row.get("source_id"), "access_status": row.get("access_status", "unknown"),
                    "has_fulltext": row.get("has_fulltext"), "has_graphics": row.get("has_graphics"), "notes": row.get("notes"), "discovered_at": row["discovered_at"], "query": row["query"], "title": row.get("title"),
                }):
                    added += 1
            location = "/?" + urlencode({"message": f"Added {added} page(s) to the preservation queue."})
            self.send_response(303); self.send_header("Location", location); self.end_headers()

        def log_message(self, format: str, *args: object) -> None:
            return
    return ReviewHandler

def serve(store: Store, port: int = 8765, open_browser: bool = False) -> None:
    address = ("127.0.0.1", port)
    server = ThreadingHTTPServer(address, handler_for(store))
    url = f"http://{address[0]}:{port}/"
    print(f"Review interface running at {url}. Leave this window open while reviewing; press Ctrl+C to stop.")
    if open_browser:
        webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nReview interface stopped.")
    finally:
        server.server_close()
