"""Local interface for curatorial reading of extracted source text."""
from __future__ import annotations

from html import escape
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlencode, urlparse
import webbrowser

from .store import Store

DECISIONS = (("unreviewed", "Unreviewed"), ("promising", "Promising"), ("needs-research", "Needs more research"), ("irrelevant", "Irrelevant"))

def page(store: Store, message: str = "", base_path: str = "") -> str:
    message_html = f"<p class='message'>{escape(message)}</p>" if message else ""
    documents = []
    for row in store.documents_for_review():
        text = store.blob(row["blob_sha256"]).decode("utf-8", errors="replace")
        choices = "".join(f"<label><input type='radio' name='decision' value='{value}' {'checked' if row['decision'] == value else ''}> {label}</label>" for value, label in DECISIONS)
        title = row["title_hint"] or row["original_url"] or row["url"]
        documents.append(
            f"<section><h2>{escape(title)}</h2><p><a href='{escape(row['url'], quote=True)}' target='_blank' rel='noreferrer'>Open preserved page</a></p>"
            f"<pre>{escape(text)}</pre><form method='post' action='{base_path}/save'><input type='hidden' name='candidate_id' value='{row['id']}'>"
            f"<fieldset><legend>Research decision</legend>{choices}</fieldset><label>Curatorial notes<br><textarea name='notes' rows='4'>{escape(row['notes'])}</textarea></label><br><button type='submit'>Save review</button></form></section>"
        )
    body = "".join(documents) or "<p>No extracted documents are ready for review.</p>"
    return f"""<!doctype html><html lang='en'><head><meta charset='utf-8'><title>Lost Notation document review</title>
<style>body{{font-family:system-ui,sans-serif;max-width:1050px;margin:2rem auto;padding:0 1rem;color:#202124}}section{{border-top:1px solid #ddd;padding:1.25rem 0}}h2{{font-size:1.15rem}}pre{{white-space:pre-wrap;word-break:break-word;background:#f6f7f8;padding:1rem;max-height:30rem;overflow:auto}}fieldset{{border:0;padding:0;margin:.75rem 0}}fieldset label{{margin-right:.8rem}}textarea{{width:min(100%,48rem);font:inherit;padding:.5rem}}button{{background:#0759aa;color:#fff;border:0;border-radius:4px;padding:.55rem .75rem;font-size:.95rem;cursor:pointer}}.message{{background:#e6f4ea;padding:.75rem;border-radius:4px}}</style>
</head><body><h1>Document review</h1><p>Read preserved text, record your judgement, and keep notes grounded in the source. Project creation remains separate.</p>{message_html}{body}</body></html>"""

def handler_for(store: Store):
    class DocumentHandler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802
            parsed = urlparse(self.path)
            if parsed.path != "/": self.send_error(404); return
            content = page(store, parse_qs(parsed.query).get("message", [""])[0]).encode("utf-8")
            self.send_response(200); self.send_header("Content-Type", "text/html; charset=utf-8"); self.send_header("Content-Length", str(len(content))); self.end_headers(); self.wfile.write(content)
        def do_POST(self) -> None:  # noqa: N802
            if self.path != "/save": self.send_error(404); return
            size = int(self.headers.get("Content-Length", "0")); form = parse_qs(self.rfile.read(size).decode("utf-8"))
            try:
                store.save_document_review(int(form.get("candidate_id", ["0"])[0]), form.get("decision", ["unreviewed"])[0], form.get("notes", [""])[0])
                message = "Document review saved."
            except (ValueError, TypeError): message = "Could not save that review."
            self.send_response(303); self.send_header("Location", "/?" + urlencode({"message": message})); self.end_headers()
        def log_message(self, format: str, *args: object) -> None: return
    return DocumentHandler

def serve(store: Store, port: int = 8767, open_browser: bool = False) -> None:
    server = ThreadingHTTPServer(("127.0.0.1", port), handler_for(store)); url = f"http://127.0.0.1:{port}/"
    print(f"Document review running at {url}. Leave this window open; press Ctrl+C to stop.")
    if open_browser: webbrowser.open(url)
    try: server.serve_forever()
    except KeyboardInterrupt: print("\nDocument review stopped.")
    finally: server.server_close()
