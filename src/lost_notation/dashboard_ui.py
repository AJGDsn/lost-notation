"""Unified local dashboard: Search → Review → Leads → Collection."""
from __future__ import annotations

from datetime import datetime
from html import escape
from http.cookies import CookieError, SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import hmac
import ipaddress
import json
import mimetypes
import os
from pathlib import Path
import secrets
import socket
import threading
import time
from typing import Optional
from urllib.parse import parse_qs, quote, unquote, urlencode, urlparse
import webbrowser

from .corpus import import_corpus
from .desk import (
    add_lead_by_hand, add_requested_site, adopt_earlier_decisions, copy_status, decide, download_copies, inbox, lead, leads,
    load_settings, queue_copies, retry_failed_copies, save_settings, saved_texts, set_aside, stage_counts, update_lead,
)
from .desk_ui import (
    THEME, collection_page, filtered, home_page, leads_page, review_list_page, review_page, search_page, settled_page, texts_page, tools_page, web_search_page,
)
from .handover import mark_requests_done, open_requests, read_imports, scriptorium_status, sync_notes, update_notes
from .search_plan import (
    COLLECTORS, QUERY_FILE, SEARCH_SOURCES, SELECTION_FILE, add_custom_term, archive_progress, brave_available, commons_terms,
    languages_covered, lens_terms, load_custom_terms, load_selection, run_batch, save_plan, searched_texts, selection_from_form,
)
from .site_copy import COPIES_FOLDER, MANIFEST, PAGE_SUFFIXES, copy_folder, copy_progress, copy_site, write_lead_file
from .store import Store
from .workbench_ui import load_queries

PROJECT_ROOT = Path(__file__).resolve().parents[2]
# Copied sites are served with their old scripts able to run but not to reach
# the dashboard: no fetches, no form posts, and nothing loaded from anywhere
# but the copy itself and the Wayback Machine.
COPY_POLICY = "default-src 'self' https://web.archive.org data: 'unsafe-inline' 'unsafe-eval'; connect-src 'none'; form-action 'none'"
__all__ = ["JobManager", "LanAccess", "dashboard", "handler_for", "lens_terms", "query_selection", "serve"]


def query_selection(path: Optional[Path]) -> dict[str, object]:
    """The saved search plan, including terms the researcher added."""
    return load_selection(path, load_custom_terms(path.parent) if path is not None else None)


def connect_page(message: str = "") -> str:
    notice = f"<div class='notice'>{escape(message)}</div>" if message else ""
    return f"""<!doctype html><html><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'>
    <title>Connect to Lost Notation</title>{THEME}<style>
    body{{margin:0;min-height:100vh;display:grid;place-items:center;background:linear-gradient(145deg,#12384d,#176b87);font:16px/1.5 system-ui;color:var(--ink);padding:1rem}}
    main{{width:min(100%,430px);background:white;border-radius:16px;padding:2rem;box-shadow:0 18px 55px #071c2766}}
    .eyebrow{{text-transform:uppercase;letter-spacing:.13em;color:#a96d16;font-weight:750;font-size:.72rem}}h1{{font:700 2.25rem/1 Georgia,serif;color:var(--nav);margin:.55rem 0 1rem}}
    p{{color:var(--muted)}}label{{display:block;font-weight:700;margin:1.25rem 0 .55rem}}input{{width:100%;font:700 1.55rem/1.2 system-ui;letter-spacing:.28em;text-align:center;padding:.8rem;border:2px solid #b9c8cd;border-radius:8px}}
    button{{width:100%;margin-top:.8rem;border:0;border-radius:7px;background:var(--blue);color:white;padding:.85rem;font-weight:750;font-size:1rem}}.notice{{background:#fdebea;border-left:4px solid #b65e55;padding:.7rem .8rem;border-radius:4px}}
    small{{display:block;color:var(--muted);margin-top:1.2rem}}
    </style></head><body><main><div class='eyebrow'>Home network access</div><h1>Lost Notation</h1><p>Enter the six-digit code shown in the launcher window on the PC.</p>{notice}
    <form method='post' action='/connect'><label for='access-code'>Access code</label><input id='access-code' name='code' inputmode='numeric' autocomplete='one-time-code' pattern='[0-9]{{6}}' maxlength='6' required autofocus>
    <button>Connect to the research dashboard</button></form><small>The PC and this device must be on the same private home network.</small></main></body></html>"""


class LanAccess:
    """Short-code gate for a dashboard exposed only to a private home network."""

    cookie_name = "lnp_lan_session"

    def __init__(self, code: Optional[str] = None, session_token: Optional[str] = None) -> None:
        self.code = code or f"{secrets.randbelow(1_000_000):06d}"
        self.session_token = session_token or secrets.token_urlsafe(32)
        self.failures: dict[str, tuple[int, float]] = {}
        self.lock = threading.Lock()

    @staticmethod
    def client_allowed(address: str) -> bool:
        try:
            client = ipaddress.ip_address(address)
        except ValueError:
            return False
        return client.is_loopback or (client.is_private and not client.is_unspecified)

    def authenticated(self, cookie_header: str) -> bool:
        cookie = SimpleCookie()
        try:
            cookie.load(cookie_header or "")
        except CookieError:
            return False
        value = cookie.get(self.cookie_name)
        return bool(value and hmac.compare_digest(value.value, self.session_token))

    def verify(self, supplied: str, address: str) -> tuple[bool, str]:
        now = time.monotonic()
        with self.lock:
            failures, blocked_until = self.failures.get(address, (0, 0.0))
            if blocked_until > now:
                return False, "Too many incorrect attempts. Please wait one minute and try again."
            if hmac.compare_digest(supplied.strip(), self.code):
                self.failures.pop(address, None)
                return True, ""
            failures += 1
            if failures >= 5:
                self.failures[address] = (0, now + 60.0)
                return False, "Too many incorrect attempts. Please wait one minute and try again."
            self.failures[address] = (failures, 0.0)
            return False, "That code was not recognised. Check the PC window and try again."

    def cookie_header(self) -> str:
        return f"{self.cookie_name}={self.session_token}; Path=/; HttpOnly; SameSite=Strict; Max-Age=43200"


def private_lan_addresses() -> list[str]:
    """Return plausible private IPv4 addresses for display in the launcher."""
    found: set[str] = set()
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            found.add(info[4][0])
    except OSError:
        pass
    for target in ("192.0.2.1", "198.51.100.1"):
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            sock.connect((target, 80)); found.add(sock.getsockname()[0])
        except OSError:
            pass
        finally:
            sock.close()
    usable = []
    for address in found:
        try:
            value = ipaddress.ip_address(address)
        except ValueError:
            continue
        if value.is_private and not value.is_loopback and not value.is_link_local:
            usable.append(address)
    def priority(address: str) -> tuple[int, str]:
        if address.startswith("192.168."): return (0, address)
        if address.startswith("10."): return (1, address)
        return (2, address)
    return sorted(set(usable), key=priority)


class JobManager:
    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.running = False
        self.label = ""
        self.result = ""
        self.error = ""
        self.started_at = ""

    def snapshot(self) -> dict[str, object]:
        with self.lock:
            return {"running": self.running, "label": self.label, "result": self.result, "error": self.error, "started_at": self.started_at}

    def start(self, label: str, operation) -> bool:
        with self.lock:
            if self.running:
                return False
            self.running, self.label, self.result, self.error = True, label, "", ""
            self.started_at = datetime.now().strftime("%H:%M")

        def work() -> None:
            try:
                result = str(operation())
                with self.lock: self.result = result
            except Exception as exc:  # The dashboard must retain and display long-job failures.
                with self.lock: self.error = f"{type(exc).__name__}: {exc}"
            finally:
                with self.lock: self.running = False
        threading.Thread(target=work, name="lost-notation-job", daemon=True).start()
        return True

    def progress(self, label: str) -> None:
        """Let a running job say how far it has got."""
        with self.lock: self.label = label


def desk_counts(store: Store, hidden_file: Path, items=None) -> dict[str, int]:
    items = inbox(store, hidden_file) if items is None else items
    counts = {"waiting": sum(not item.later for item in items), "later": sum(item.later for item in items)}
    counts.update(stage_counts(store))
    return counts


def plan_summary(selection: dict[str, object]) -> dict[str, object]:
    used, missing = languages_covered(selection)
    automatic = [source for source in SEARCH_SOURCES if source["automatic"] and source["key"] in selection["sources"] and (not source.get("needs_key") or brave_available())]
    return {"languages": used, "missing": missing, "sources": [source["key"] for source in automatic], "source_names": [source["name"] for source in automatic]}


def dashboard(store: Store, jobs: JobManager, message: str = "", data_dir: Optional[Path] = None, project_root: Path = PROJECT_ROOT) -> str:
    selection = query_selection(data_dir / SELECTION_FILE if data_dir else None)
    counts = desk_counts(store, project_root / "examples" / "review-hidden-sites.txt")
    counts["requests"] = len(open_requests(data_dir)) if data_dir else 0
    return home_page(counts, plan_summary(selection), jobs.snapshot(), message)


def source_statuses(store: Store, data_dir: Path, project_root: Path, selection: dict[str, object], checklist: tuple[int, int]) -> dict[str, str]:
    statuses: dict[str, str] = {}
    with store.connect() as con:
        rows = con.execute("SELECT source_name,state,count(*) n FROM source_items GROUP BY 1,2").fetchall()
    progress: dict[str, dict[str, int]] = {}
    for row in rows:
        progress.setdefault(row["source_name"], {})[row["state"]] = int(row["n"])
    for key in COLLECTORS:
        item = progress.get(key, {})
        statuses[key] = f"{item.get('fetched', 0):,} pages read · {item.get('pending', 0):,} waiting to be read" if item else "Not started yet"
    terms = commons_terms(selection, project_root)
    searched = searched_texts(store, "wikimedia-commons", "srsearch")
    statuses["commons"] = f"{sum(term in searched for term in terms):,} of {len(terms):,} terms searched"
    statuses["archive"] = archive_progress(store, project_root)
    statuses["brave"] = "Key found; ready." if brave_available() else "No BRAVE_SEARCH_API_KEY set, so this is skipped."
    statuses["web"] = f"{checklist[0]:,} of {checklist[1]:,} searches done"
    with store.connect() as con:
        catalogue = int(con.execute("SELECT count(*) FROM triage WHERE kind='catalogue' AND decision='lead'").fetchone()[0])
    statuses["catalogue"] = f"{catalogue:,} catalogue records in Leads" if catalogue else ""
    return statuses


def handler_for(store: Store, data_dir: Path, project_root: Path, jobs: JobManager, lan_access: Optional[LanAccess] = None):
    query_file = data_dir / QUERY_FILE
    selection_file = data_dir / SELECTION_FILE
    hidden_file = project_root / "examples" / "review-hidden-sites.txt"
    adopt_earlier_decisions(store)
    notes_seen: dict = {}

    def checklist() -> tuple[list[str], set[str]]:
        queries = load_queries(query_file) if query_file.exists() else []
        return queries, store.checked_manual_queries()

    class DashboardHandler(BaseHTTPRequestHandler):
        def send_html(self, content: str, status: int = 200) -> None:
            body = content.encode("utf-8"); self.send_response(status); self.send_header("Content-Type", "text/html; charset=utf-8"); self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store, max-age=0"); self.send_header("Pragma", "no-cache"); self.end_headers(); self.wfile.write(body)

        def redirect(self, path: str, message: str = "", cookie: str = "") -> None:
            target = path + (("&" if "?" in path else "?") + urlencode({"message": message}) if message else "")
            self.send_response(303); self.send_header("Location", target)
            self.send_header("Cache-Control", "no-store, max-age=0")
            if cookie: self.send_header("Set-Cookie", cookie)
            self.end_headers()

        def send_copy(self, relative: str) -> None:
            root = (data_dir / COPIES_FOLDER).resolve(); target = (root / relative).resolve()
            if target.is_dir(): target = target / "index.html"
            if root not in target.parents or not target.is_file(): self.send_error(404); return
            content = target.read_bytes()
            kind = "text/html" if target.suffix.lower() in PAGE_SUFFIXES else mimetypes.guess_type(target.name)[0] or "application/octet-stream"
            self.send_response(200); self.send_header("Content-Type", kind); self.send_header("Content-Length", str(len(content)))
            self.send_header("Content-Security-Policy", COPY_POLICY); self.end_headers(); self.wfile.write(content)

        def form(self) -> dict[str, list[str]]:
            size = int(self.headers.get("Content-Length", "0")); return parse_qs(self.rfile.read(size).decode("utf-8"))

        def do_GET(self) -> None:  # noqa: N802
            parsed = urlparse(self.path); query = parse_qs(parsed.query); message = query.get("message", [""])[0]
            if lan_access:
                if not lan_access.client_allowed(self.client_address[0]): self.send_error(403); return
                supplied = query.get("access_code", [""])[0]
                if supplied:
                    accepted, _ = lan_access.verify(supplied, self.client_address[0])
                    if accepted: self.redirect("/", "Home-network access connected.", lan_access.cookie_header()); return
                if not lan_access.authenticated(self.headers.get("Cookie", "")):
                    self.send_html(connect_page()); return
            if parsed.path.startswith("/copies/"): self.send_copy(unquote(parsed.path[len("/copies/"):])); return
            path = parsed.path.rstrip("/") or "/"
            legacy = {"/passages": "/review?filter=passage", "/discoveries": "/review/list", "/documents": "/collection", "/manual": "/search/web", "/terms": "/search"}
            if path in legacy: self.redirect(legacy[path]); return
            if path == "/": self.send_html(dashboard(store, jobs, message, data_dir, project_root)); return
            if path == "/search":
                selection = query_selection(selection_file); queries, checked = checklist()
                counts = desk_counts(store, hidden_file); done = len(set(queries) & checked)
                statuses = source_statuses(store, data_dir, project_root, selection, (done, len(queries)))
                self.send_html(search_page(selection, load_custom_terms(data_dir), statuses, (done, len(queries)), counts, jobs.snapshot(), message)); return
            if path == "/search/web":
                entries = [json.loads(line) for line in query_file.read_text(encoding="utf-8").splitlines() if line.strip()] if query_file.exists() else []
                checked = store.checked_manual_queries(); remaining = [entry for entry in entries if entry["text"] not in checked]
                self.send_html(web_search_page(remaining[:10], len(entries) - len(remaining), len(entries), desk_counts(store, hidden_file), message)); return
            if path in {"/review", "/review/list"}:
                items = inbox(store, hidden_file); counts = desk_counts(store, hidden_file, items); active = query.get("filter", [""])[0]
                if path == "/review/list":
                    self.send_html(review_list_page(items, active, counts, message)); return
                shown = filtered(items, active); wanted = query.get("key", [""])[0]
                current = next((item for item in shown if item.key == wanted), shown[0] if shown else None)
                self.send_html(review_page(items, current, active, counts, stage_counts(store), message)); return
            if path == "/leads": self.send_html(leads_page(leads(store), desk_counts(store, hidden_file), message)); return
            if path in {"/leads/traces", "/leads/set-aside"}:
                decision = "trace" if path == "/leads/traces" else "rejected"
                self.send_html(settled_page(set_aside(store, decision), decision, desk_counts(store, hidden_file), message)); return
            if path == "/collection":
                problem = "" if jobs.snapshot().get("running") else sync_notes(store, data_dir, notes_seen)
                rows = leads(store, ("collection",)); finished = leads(store, ("done",))
                self.send_html(collection_page(rows, {row["item_key"]: copy_status(store, row["item_key"]) for row in rows}, finished, load_settings(data_dir),
                                               desk_counts(store, hidden_file), jobs.snapshot(), message or problem, _site_copies(data_dir, [*rows, *finished]), _requests(data_dir))); return
            if path == "/collection/text":
                row = lead(store, query.get("key", [""])[0])
                if row is None: self.redirect("/collection", "That lead was not found."); return
                self.send_html(texts_page(row, saved_texts(store, row["item_key"]), desk_counts(store, hidden_file))); return
            if path == "/tools": self.send_html(tools_page(desk_counts(store, hidden_file), jobs.snapshot(), message)); return
            if path == "/handbook":
                content = (project_root / "Lost Notation Project Handbook.html").read_bytes()
                self.send_response(200); self.send_header("Content-Type", "text/html; charset=utf-8"); self.send_header("Content-Length", str(len(content))); self.end_headers(); self.wfile.write(content); return
            if path == "/api/status":
                content = json.dumps(jobs.snapshot()).encode(); self.send_response(200); self.send_header("Content-Type", "application/json"); self.send_header("Content-Length", str(len(content))); self.end_headers(); self.wfile.write(content); return
            self.send_error(404)

        def do_POST(self) -> None:  # noqa: N802
            path = urlparse(self.path).path
            if lan_access:
                if not lan_access.client_allowed(self.client_address[0]): self.send_error(403); return
                if path == "/connect":
                    form = self.form(); accepted, message = lan_access.verify(form.get("code", [""])[0], self.client_address[0])
                    if accepted: self.redirect("/", "Connected from this device.", lan_access.cookie_header())
                    else: self.send_html(connect_page(message), 401)
                    return
                if not lan_access.authenticated(self.headers.get("Cookie", "")):
                    self.send_html(connect_page("Please enter the current access code."), 401); return
            origin = self.headers.get("Origin")
            if origin:
                origin_url = urlparse(origin)
                if origin_url.scheme != "http" or origin_url.netloc != self.headers.get("Host", ""):
                    self.send_error(403); return
            form = self.form(); first = lambda name, default="": form.get(name, [default])[0]

            if path in {"/search/save", "/search/run"}:
                message = ""
                if first("plan"):
                    custom = load_custom_terms(data_dir)
                    selection = selection_from_form(form, custom)
                    new_term, new_language = first("new_term").strip(), first("new_language")
                    if new_term and add_custom_term(data_dir, new_language, new_term):
                        selection["concepts"].setdefault(new_language, []).append(" ".join(new_term.split()))
                    message = save_plan(store, data_dir, selection)
                if path == "/search/run":
                    selection = query_selection(selection_file)
                    sources = [first("only")] if first("only") else plan_summary(selection)["sources"]
                    started = jobs.start("Running a search batch", lambda: run_batch(store, data_dir, project_root, sources))
                    message = (message + " " if message else "") + ("Batch started; new finds will appear in Review." if started else "Another job is already running.")
                self.redirect("/search" if first("plan") or first("only") else "/", message); return
            if path == "/search/web/done":
                if first("query"): store.mark_manual_query_checked(first("query"))
                self.redirect("/search/web", "Marked done."); return
            if path == "/search/web/lead":
                url = first("url").strip()
                if not url: self.redirect("/search/web", "Paste an address first."); return
                added = add_lead_by_hand(store, url, first("title") or url, f"Found by searching {first('query')}" if first("query") else "")
                self.redirect("/search/web", "Added to Leads." if added else "That address is already recorded."); return
            if path == "/review/decide":
                items = {item.key: item for item in inbox(store, hidden_file)}; item = items.get(first("key")); active = first("filter")
                if item is None: self.redirect("/review" + (f"?filter={active}" if active else ""), "That find was already decided."); return
                try: decide(store, item, first("decision"), first("notes"))
                except ValueError: self.redirect("/review", "Unknown decision."); return
                said = {"lead": "Kept as a lead.", "trace": "Filed under Too little survives.", "rejected": "Set aside as not relevant.", "later": "Left for later."}[first("decision")]
                self.redirect("/review" + (f"?filter={active}" if active else ""), said); return
            if path == "/review/bulk":
                items = {item.key: item for item in inbox(store, hidden_file)}; decision = first("decision"); active = first("filter"); done = 0
                for key in form.get("key", []):
                    if key in items and decision in {"lead", "trace", "rejected", "later"}:
                        decide(store, items[key], decision); done += 1
                self.redirect("/review/list" + (f"?filter={active}" if active else ""), f"Decided {done} find(s)."); return
            if path == "/leads/add":
                url = first("url").strip()
                if not url: self.redirect("/leads", "An address is required."); return
                added = add_lead_by_hand(store, url, first("title"), first("notes"), first("where") == "catalogue", first("source"))
                self.redirect("/leads", "Added to Leads." if added else "That address is already recorded."); return
            if path == "/leads/update":
                key, action = first("key"), first("action")
                back = first("back") if first("back") in {"/leads/traces", "/leads/set-aside"} else "/leads"
                try:
                    if action == "collect": update_lead(store, key, notes=first("notes"), stage="collection"); message = "Moved to the Collection."
                    elif action == "drop": update_lead(store, key, notes=first("notes"), decision="rejected"); message = "Set aside as not relevant."
                    elif action == "lead": update_lead(store, key, notes=first("notes"), decision="lead", stage="holding"); message = "Moved to Leads."
                    elif action == "trace": update_lead(store, key, notes=first("notes"), decision="trace"); message = "Filed under Too little survives."
                    else: update_lead(store, key, notes=first("notes")); message = "Notes saved."
                except KeyError: message = "That lead was not found."
                _refresh_lead_file(store, data_dir, key)
                self.redirect(back, " ".join(filter(None, [message, update_notes(store, data_dir, [key])]))); return
            if path == "/collection/settings":
                vault = first("vault").strip()
                if vault: save_settings(data_dir, {"vault": vault}); notes_seen.clear()
                self.redirect("/collection", "Vault folder saved." if vault else "Enter a folder path."); return
            if path == "/collection/request":
                request = next((request for request in open_requests(data_dir) if request["address"] == first("address")), None)
                if request is None: self.redirect("/collection", "That request is no longer open."); return
                started = jobs.start("Saving copies", lambda: _save_copies(store, data_dir, [], [add_requested_site(store, request["address"], request["note"])], jobs))
                self.redirect("/collection", "Saving a complete copy in the background; this page shows how far it has got." if started else "Another job is already running."); return
            if path in {"/collection/update", "/collection/all"}:
                action = first("action")
                keys = [first("key")] if path == "/collection/update" else [row["item_key"] for row in leads(store, ("collection",))]
                if lead(store, keys[0] if keys else "") is None and path == "/collection/update": self.redirect("/collection", "That lead was not found."); return
                if action in {"copies", "retry", "sitecopy"}:
                    sites = [key for key in keys if key.startswith("site:")] if action != "retry" else []
                    others = [key for key in keys if not key.startswith("site:")] if action != "sitecopy" else []
                    queued = sum(retry_failed_copies(store, key) if action == "retry" else queue_copies(store, key) for key in others)
                    if not queued and not sites: self.redirect("/collection", "Nothing to download for that."); return
                    started = jobs.start("Saving copies", lambda: _save_copies(store, data_dir, others if queued else [], sites, jobs))
                    said = ("Saving a complete copy in the background; this page shows how far it has got. A large site takes a while, "
                            "because the archive is asked for one file every few seconds.") if sites else f"Saving {queued:,} cop{'y' if queued == 1 else 'ies'} in the background."
                    if sites and all(_copy_finished(data_dir, key) for key in sites):
                        said = "Looking for missing pictures in the background; this page shows how far it has got."
                    self.redirect("/collection", said if started else "Another job is already running."); return
                if action == "open":
                    folder = _own_copy(data_dir, keys[0])
                    if folder is None or not folder.exists(): self.redirect("/collection", "There is no copy to open yet."); return
                    if hasattr(os, "startfile") and ipaddress.ip_address(self.client_address[0]).is_loopback:
                        os.startfile(folder); self.redirect("/collection", "Opened the folder in File Explorer."); return
                    self.redirect("/collection", f"The copy is on the PC, in {folder}"); return
                stages = {"done": ("done", "Marked finished."), "back": ("holding", "Moved back to Leads."), "reopen": ("collection", "Reopened.")}
                if action in stages:
                    for key in keys: update_lead(store, key, stage=stages[action][0])
                    self.redirect("/collection", " ".join(filter(None, [stages[action][1], update_notes(store, data_dir, keys)]))); return
                self.redirect("/collection", "Unknown action."); return
            if path == "/tools/import":
                source = Path(first("path")); name = first("name")
                if not first("path") or not name: self.redirect("/tools", "A path and a name are required."); return
                started = jobs.start(f"Importing {name}", lambda: _import_result(store, source, name)); self.redirect("/tools", "Import started." if started else "Another job is already running."); return
            self.send_error(404)

        def log_message(self, format: str, *args: object) -> None: return
    return DashboardHandler


def _copies_result(store: Store, keys: list[str]) -> str:
    saved, failed = download_copies(store, keys)
    return f"Saved {saved:,} copies" + (f"; {failed:,} could not be saved (you can try them again)" if failed else "") + ". Their text is ready to read in the Collection."


def _own_copy(data_dir: Path, key: str) -> Optional[Path]:
    return copy_folder(data_dir, key.split(":", 1)[1]) if key.startswith("site:") else None


def _copy_finished(data_dir: Path, key: str) -> bool:
    progress = copy_progress(_own_copy(data_dir, key))
    return bool(progress) and not progress["waiting"] and not progress["failed"]


def _refresh_lead_file(store: Store, data_dir: Path, key: str) -> None:
    folder = _own_copy(data_dir, key)
    if folder is not None and (folder / MANIFEST).exists():
        write_lead_file(store, key, folder)


def _requests(data_dir: Path) -> list[dict]:
    return [{**request, "progress": copy_progress(copy_folder(data_dir, request["address"]))} for request in open_requests(data_dir)]


def _site_copies(data_dir: Path, rows) -> dict[str, dict]:
    copies = {}; imports = read_imports(data_dir)
    for row in rows:
        folder = _own_copy(data_dir, row["item_key"])
        if folder is not None:
            copies[row["item_key"]] = {"progress": copy_progress(folder), "folder": str(folder), "browse": f"/copies/{quote(folder.name)}/",
                                       "taken": scriptorium_status(data_dir, row["item_key"], imports)}
    return copies


def _save_copies(store: Store, data_dir: Path, others: list[str], sites: list[str], jobs: JobManager) -> str:
    said = [_copies_result(store, others)] if others else []
    for key in sites:
        site = key.split(":", 1)[1]; row = lead(store, key)
        said.append(copy_site(store, key, site, copy_folder(data_dir, site), source=row["source"] if row else "Internet Archive", progress=jobs.progress))
    mark_requests_done(data_dir)
    said.append(update_notes(store, data_dir, [*others, *sites]))
    return " ".join(filter(None, said))


def _import_result(store: Store, path: Path, name: str) -> str:
    imported, skipped, candidates = import_corpus(store, path, name); return f"Imported {imported} documents; skipped {skipped} unchanged; {candidates} passages sent to Review."


def serve(store: Store, data_dir: Path, project_root: Path, port: int = 8765, open_browser: bool = False, lan: bool = False, access_code: Optional[str] = None) -> None:
    jobs = JobManager(); lan_access = LanAccess(access_code) if lan else None
    host = "0.0.0.0" if lan else "127.0.0.1"
    sync_notes(store, data_dir, {})
    server = ThreadingHTTPServer((host, port), handler_for(store, data_dir, project_root, jobs, lan_access))
    local_url = f"http://127.0.0.1:{port}/"
    if lan_access:
        addresses = private_lan_addresses()
        print("=" * 61)
        print("          LOST NOTATION - iPAD / HOME NETWORK MODE")
        print("=" * 61)
        print("\nOn the iPad, open Safari and enter one of these addresses:\n")
        if addresses:
            for address in addresses: print(f"    http://{address}:{port}/")
        else:
            print(f"    Could not identify this PC's address. Use port {port} with")
            print("    the IPv4 address shown by Windows network settings.")
        print(f"\nAccess code:  {lan_access.code}\n")
        print("The PC and iPad must be on the same private home network.")
        print("If Windows asks, allow Python on PRIVATE networks only.")
        print("Keep this window open. Press Ctrl+C when you have finished.\n", flush=True)
        if open_browser: webbrowser.open(local_url + "?" + urlencode({"access_code": lan_access.code}))
    else:
        print(f"Lost Notation dashboard running at {local_url}")
        print("Keep this window open. Press Ctrl+C here when you have finished.", flush=True)
        if open_browser: webbrowser.open(local_url)
    try: server.serve_forever()
    except KeyboardInterrupt: print("\nDashboard stopped.")
    finally: server.server_close()
