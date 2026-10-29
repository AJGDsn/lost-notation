"""Focused local review interface for mined corpus passages."""
from __future__ import annotations

from html import escape
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from urllib.parse import parse_qs, urlencode, urlparse
import webbrowser

from .store import Store

DECISIONS = (("unreviewed", "Unreviewed"), ("promising", "Promising"), ("needs-research", "Needs more research"), ("irrelevant", "Irrelevant"))


def page(store: Store, passage_id: int | None = None, message: str = "", base_path: str = "") -> str:
    row = store.passage_review_item(passage_id)
    counts = store.passage_review_counts()
    reviewed = counts["total"] - counts["unreviewed"]
    progress = f"{reviewed} reviewed · {counts['unreviewed']} remaining · {counts['total']} total"
    message_html = f"<p class='message'>{escape(message)}</p>" if message else ""

    if row is None:
        body = """<section class='complete'><h2>All caught up</h2>
        <p>There are no unreviewed candidate passages at present. Collect or import another batch, then return here.</p></section>"""
    else:
        signals = json.loads(row["signals_json"])
        labels = signals["first_person"] + signals["specialist"] + signals["supporting"]
        signal_html = " ".join(f"<code>{escape(label)}</code>" for label in labels)
        choices = "".join(
            f"<label><input type='radio' name='decision' value='{value}' {'checked' if row['decision'] == value else ''}> {label}</label>"
            for value, label in DECISIONS
        )
        source = f"<a href='{escape(row['url'], quote=True)}' target='_blank' rel='noreferrer'>Open original source</a>" if row["url"] else escape(row["external_id"])
        next_id = store.next_unreviewed_passage_id(int(row["id"]))
        next_link = f"<a class='next-link' href='{base_path}/?passage_id={next_id}'>Next result without saving →</a>" if next_id else "<span class='muted'>No other unreviewed result</span>"
        hidden_next = f"<input type='hidden' name='next_id' value='{next_id}'>" if next_id else ""
        grouped_documents = int(row["grouped_documents"])
        grouped_matches = int(row["grouped_matches"])
        group_note = ""
        if grouped_documents > 1:
            group_note = f"<p class='group-note'><strong>Thread grouped:</strong> showing the strongest of {grouped_matches} matches found across {grouped_documents} preserved pages. The other pages remain safely archived.</p>"
        elif grouped_matches > 1:
            group_note = f"<p class='group-note'><strong>Page grouped:</strong> showing the strongest of {grouped_matches} matches from this page.</p>"
        details = " · ".join(part for part in (row["corpus_name"], row["author"], row["published_at"]) if part)
        body = f"""<article>
        <div class='navigation'>{next_link}</div>
        <header><span class='score'>score {row['score']}</span><h2>{escape(row['title'] or row['external_id'])}</h2>
        <p>{escape(details)} · {source}</p></header>
        {group_note}<p class='signals'>{signal_html}</p>
        <blockquote>{escape(row['text'])}</blockquote>
        <form method='post' action='{base_path}/save-next'>
          <input type='hidden' name='passage_id' value='{row['id']}'>{hidden_next}
          <fieldset><legend>Research decision</legend>{choices}</fieldset>
          <label class='notes'>Notes<textarea name='notes' rows='4'>{escape(row['notes'])}</textarea></label>
          <div class='actions'><button type='submit'>Save and show next</button>
          <button class='secondary' type='submit' formaction='{base_path}/save'>Save without moving</button></div>
        </form></article>"""

    return f"""<!doctype html><html lang='en'><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'>
<title>Lost Notation passage review</title><style>
body{{font-family:system-ui,sans-serif;max-width:980px;margin:2rem auto;padding:0 1rem;color:#202124;background:#f5f6f6}}
main{{background:white;border:1px solid #dde1e3;border-radius:8px;padding:1.4rem 1.7rem;box-shadow:0 4px 18px #1d2b3510}}
h1{{margin:.1rem 0}}.progress{{color:#59636b;margin:.3rem 0 1.4rem}}article{{border-top:1px solid #ddd;padding-top:1rem}}
.navigation{{text-align:right;min-height:1.7rem}}.next-link{{font-weight:650}}header h2{{margin:.4rem 0}}header p{{color:#59636b}}
.score{{float:right;background:#e9f2f7;color:#155b8f;border-radius:999px;padding:.25rem .55rem;font-size:.85rem}}
.group-note{{background:#e9f4f3;border-left:4px solid #248b85;padding:.75rem .9rem;border-radius:4px;color:#31565b}}
blockquote{{border-left:4px solid #71808b;margin:1.2rem 0;padding:1rem 1.2rem;background:#f6f7f8;white-space:pre-wrap;font-family:Georgia,serif;line-height:1.55}}
code{{background:#e9edef;padding:.15rem .3rem;margin-right:.25rem;border-radius:3px}}fieldset{{border:0;padding:0;margin:1.1rem 0}}fieldset label{{display:inline-block;margin:.35rem 1rem .35rem 0}}
.notes{{font-weight:650}}textarea{{display:block;width:100%;font:inherit;padding:.6rem;margin-top:.35rem;border:1px solid #aeb7bd;border-radius:4px}}
.actions{{display:flex;gap:.6rem;flex-wrap:wrap;margin-top:1rem}}button{{background:#0759aa;color:#fff;border:0;border-radius:4px;padding:.65rem .9rem;font-size:1rem;cursor:pointer}}
button.secondary{{background:#68747c}}.message{{background:#e6f4ea;padding:.75rem;border-radius:4px}}.complete{{padding:2rem 0}}.muted{{color:#7a8389}}
a{{color:#0759aa}}@media(max-width:600px){{main{{padding:1rem}}.score{{float:none;display:inline-block}}}}
</style></head><body><main><h1>Mined passage review</h1><p class='progress'>{escape(progress)}</p>
<p>One result at a time. Scores prioritise reading; they are not originality judgements.</p>{message_html}{body}</main></body></html>"""


def handler_for(store: Store):
    class PassageHandler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802
            parsed = urlparse(self.path)
            if parsed.path != "/": self.send_error(404); return
            query = parse_qs(parsed.query)
            try: passage_id = int(query["passage_id"][0]) if query.get("passage_id") else None
            except (ValueError, TypeError): passage_id = None
            content = page(store, passage_id, query.get("message", [""])[0]).encode("utf-8")
            self.send_response(200); self.send_header("Content-Type", "text/html; charset=utf-8"); self.send_header("Content-Length", str(len(content))); self.end_headers(); self.wfile.write(content)

        def do_POST(self) -> None:  # noqa: N802
            if self.path not in {"/save", "/save-next"}: self.send_error(404); return
            size = int(self.headers.get("Content-Length", "0")); form = parse_qs(self.rfile.read(size).decode("utf-8"))
            try:
                passage_id = int(form.get("passage_id", ["0"])[0])
                store.save_passage_review(passage_id, form.get("decision", ["unreviewed"])[0], form.get("notes", [""])[0])
                target_id = form.get("next_id", [""])[0] if self.path == "/save-next" else str(passage_id)
                params = {"message": "Passage review saved."}
                if target_id: params["passage_id"] = target_id
            except (ValueError, TypeError):
                params = {"message": "Could not save that review."}
            self.send_response(303); self.send_header("Location", "/?" + urlencode(params)); self.end_headers()

        def log_message(self, format: str, *args: object) -> None: return
    return PassageHandler


def serve(store: Store, port: int = 8768, open_browser: bool = False) -> None:
    server = ThreadingHTTPServer(("127.0.0.1", port), handler_for(store)); url = f"http://127.0.0.1:{port}/"
    print(f"Passage review running at {url}. Leave this window open; press Ctrl+C to stop.")
    if open_browser: webbrowser.open(url)
    try: server.serve_forever()
    except KeyboardInterrupt: print("\nPassage review stopped.")
    finally: server.server_close()
