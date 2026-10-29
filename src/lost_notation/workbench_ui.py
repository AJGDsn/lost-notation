"""Local, human-operated search workbench; it never scrapes search engines."""
from __future__ import annotations

from datetime import datetime, timezone
from html import escape
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
from urllib.parse import parse_qs, quote_plus, urlencode, urlparse
import webbrowser

from .store import Store

# Google indexes the Russian, Chinese and Japanese web less thoroughly than
# these regional engines, so their queries get an extra link.
REGIONAL_ENGINES = {
    "ru": ("Try Yandex", "https://yandex.com/search/?text="),
    "zh": ("Try Baidu", "https://www.baidu.com/s?wd="),
    "ja": ("Try Yahoo! Japan", "https://search.yahoo.co.jp/search?p="),
}

def load_queries(path: Path) -> list[str]:
    return [json.loads(line)["text"] for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]

def load_query_languages(path: Path) -> dict[str, str]:
    entries = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    return {entry["text"]: entry["language"] for entry in entries if entry.get("language")}

def regional_link(query: str, languages: dict[str, str]) -> str:
    engine = REGIONAL_ENGINES.get(languages.get(query, ""))
    return f" <a href='{engine[1]}{quote_plus(query)}' target='_blank' rel='noreferrer'>{engine[0]}</a>" if engine else ""

def page(queries: list[str], checked: set[str], message: str = "", base_path: str = "", languages: dict[str, str] | None = None) -> str:
    message_html = f"<p class='message'>{escape(message)}</p>" if message else ""
    languages = languages or {}
    rows = "".join(
        f"<section><h2>{escape(query)}</h2>"
        f"<p><a class='button' href='https://www.google.com/search?q={quote_plus(query)}' target='_blank' rel='noreferrer'>Search the web</a> "
        f"<a href='https://search.brave.com/search?q={quote_plus(query)}' target='_blank' rel='noreferrer'>Try Brave Search</a>{regional_link(query, languages)}</p>"
        f"<form method='post' action='{base_path}/checked'><input type='hidden' name='query' value='{escape(query, quote=True)}'><button type='submit'>{'Checked' if query in checked else 'Mark query checked'}</button></form>"
        f"<form method='post' action='{base_path}/candidate'><input type='hidden' name='query' value='{escape(query, quote=True)}'>"
        "<label>Promising result URL <input name='url' type='url' required placeholder='https://…'></label> "
        "<label>Title or note <input name='title' placeholder='Optional'></label> "
        "<button type='submit'>Add to review queue</button></form></section>"
        for query in queries
    ) or "<p>Every generated query has been marked checked. Generate new queries or add a new query lexicon when ready.</p>"
    return f"""<!doctype html><html lang='en'><head><meta charset='utf-8'><title>Lost Notation search workbench</title>
<style>body{{font-family:system-ui,sans-serif;max-width:980px;margin:2rem auto;padding:0 1rem;color:#202124}}section{{border-top:1px solid #ddd;padding:1rem 0}}h1{{margin-bottom:.25rem}}h2{{font-size:1.05rem;font-weight:600}}form{{margin:.6rem 0}}label{{display:inline-block;margin:.25rem}}input{{padding:.45rem;min-width:15rem}}button,.button{{background:#0759aa;color:#fff;border:0;border-radius:4px;padding:.55rem .75rem;font-size:.95rem;text-decoration:none;cursor:pointer}}.message{{background:#e6f4ea;padding:.75rem;border-radius:4px}}</style>
</head><body><h1>Manual search workbench</h1><p>Showing the next {len(queries)} unchecked queries. Searches open in your normal browser; this tool does not scrape or send search results anywhere.</p>{message_html}{rows}</body></html>"""

def handler_for(store: Store, query_file: Path):
    class WorkbenchHandler(BaseHTTPRequestHandler):
        def render(self, message: str = "") -> None:
            checked = store.checked_manual_queries()
            next_queries = [query for query in load_queries(query_file) if query not in checked][:10]
            content = page(next_queries, checked, message, languages=load_query_languages(query_file)).encode("utf-8")
            self.send_response(200); self.send_header("Content-Type", "text/html; charset=utf-8"); self.send_header("Content-Length", str(len(content))); self.end_headers(); self.wfile.write(content)

        def do_GET(self) -> None:  # noqa: N802
            parsed = urlparse(self.path)
            if parsed.path != "/": self.send_error(404); return
            self.render(parse_qs(parsed.query).get("message", [""])[0])

        def do_POST(self) -> None:  # noqa: N802
            if self.path not in {"/checked", "/candidate"}: self.send_error(404); return
            size = int(self.headers.get("Content-Length", "0"))
            form = parse_qs(self.rfile.read(size).decode("utf-8"))
            query = form.get("query", [""])[0]
            message = ""
            if self.path == "/checked" and query:
                store.mark_manual_query_checked(query); message = "Query marked checked."
            elif self.path == "/candidate" and query and form.get("url", [""])[0]:
                url = form["url"][0]
                added = store.add_candidate({"url": url, "original_url": url, "source": "manual-web-search", "discovered_at": datetime.now(timezone.utc).isoformat(), "query": query, "title": form.get("title", [""])[0]})
                message = "Candidate added to the review queue." if added else "That URL is already in the queue."
            else: message = "A query and URL are required."
            self.send_response(303); self.send_header("Location", "/?" + urlencode({"message": message})); self.end_headers()

        def log_message(self, format: str, *args: object) -> None: return
    return WorkbenchHandler

def serve(store: Store, query_file: Path, port: int = 8766, open_browser: bool = False) -> None:
    if not query_file.exists(): raise FileNotFoundError(f"Generate queries first: {query_file}")
    server = ThreadingHTTPServer(("127.0.0.1", port), handler_for(store, query_file))
    url = f"http://127.0.0.1:{port}/"
    print(f"Manual search workbench running at {url}. Leave this window open; press Ctrl+C to stop.")
    if open_browser: webbrowser.open(url)
    try: server.serve_forever()
    except KeyboardInterrupt: print("\nManual search workbench stopped.")
    finally: server.server_close()
