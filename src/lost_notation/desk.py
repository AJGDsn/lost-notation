"""The research desk: one Review inbox, a Leads holding area and a Collection.

Everything a search turns up waits in the Review inbox until it is kept as a
lead, filed as a trace (relevant, but too little survives to use), set aside
as not relevant, or left for later. Leads wait in the holding
area; when one is worth processing it moves to the Collection, where copies are
saved, their text extracted and an Obsidian note written.

Each decision is one row of the ``triage`` table, keyed by what was reviewed:
``passage:<id>`` for a mined passage, ``site:<member site>`` for an archived
website and ``url:<address>`` for a Commons file, web page or hand-added lead.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import hashlib
from html.parser import HTMLParser
import json
from pathlib import Path
import re
import time
from typing import Callable, Optional
from urllib.error import HTTPError
from urllib.parse import quote, urlsplit, urlunsplit
from urllib.request import Request, urlopen

from .discovery import address_key, user_agent
from .pipeline import extract
from .review_ui import ARCHIVE_SOURCE, HIDDEN_SITES_FILE, discovery_rows, group_by_site, page_name
from .store import Store, now

SOURCE_NAMES = {
    ARCHIVE_SOURCE: "Old websites (Internet Archive)",
    "wikimedia-commons": "Wikimedia Commons",
    "brave-web-search": "Brave web search",
}
KIND_NAMES = {"passage": "Passage", "site": "Archived website", "file": "Commons file", "web": "Web page", "catalogue": "Catalogue record"}
# Review filters, in the order the inbox presents them.
FILTERS = {"passage": "Passages", "site": "Old websites", "web": "Web pages", "file": "Commons files"}
DECISIONS = {"lead", "trace", "rejected", "later"}
SETTLED = {"lead", "trace", "rejected"}
# How each settled decision is recorded on the older passage-review table.
PASSAGE_DECISIONS = {"lead": "promising", "trace": "needs-research", "rejected": "irrelevant"}
STAGES = {"holding", "collection", "done"}
FRONT_PAGES = {"front page", "index.htm", "index.html"}
MAX_COPY_BYTES = 60 * 1024 * 1024
SETTINGS_FILE = "desk-settings.json"


@dataclass
class Item:
    """One thing waiting in the Review inbox."""
    key: str
    kind: str
    source: str
    title: str
    url: str
    evidence: str = ""
    details: dict = field(default_factory=dict)
    weight: int = 0
    later: bool = False


def viewer_url(archive_url: str) -> str:
    """The Wayback Machine's normal viewer for a raw (``id_``) capture: links and
    images then load from the archive too. Downloads keep the raw capture."""
    return archive_url.replace("id_/", "/", 1)


def commons_file_name(title: str) -> str:
    return title.split(":", 1)[-1]


def commons_thumbnail(title: str, width: int = 480) -> str:
    return f"https://commons.wikimedia.org/wiki/Special:FilePath/{quote(commons_file_name(title).replace(' ', '_'))}?width={width}"


def decisions(store: Store) -> dict[str, str]:
    with store.connect() as con:
        return {row[0]: row[1] for row in con.execute("SELECT item_key,decision FROM triage")}


def inbox(store: Store, hidden_file: Path = HIDDEN_SITES_FILE) -> list[Item]:
    """Everything awaiting a decision: fresh items first, then those set aside for later."""
    decided = decisions(store)
    items: list[Item] = []
    for row in store.ranked_passages(1_000_000):
        key = f"passage:{row['id']}"
        if row["decision"] != "unreviewed" or decided.get(key) in SETTLED:
            continue
        signals = json.loads(row["signals_json"])
        items.append(Item(
            key, "passage", row["corpus_name"], row["title"] or row["external_id"], row["url"] or "", row["text"],
            {"signals": signals["first_person"] + signals["specialist"] + signals["supporting"], "author": row["author"],
             "published": row["published_at"], "matches": int(row["grouped_matches"]), "documents": int(row["grouped_documents"])},
            int(row["score"]), decided.get(key) == "later",
        ))
    rows, _ = discovery_rows(store, hidden_file)
    for entry in group_by_site(rows, store.checked_review_keys()):
        pages: list[dict[str, str]] = entry["rows"]  # type: ignore[assignment]
        first = pages[0]
        if first["source"] == ARCHIVE_SOURCE:
            site = str(entry["site"]); key = f"site:{site}"
            names = [page_name(row, site) for row in pages]
            front = next((row for row, name in zip(pages, names) if name in FRONT_PAGES), first)
            years = sorted(row["timestamp"][:4] for row in pages)
            item = Item(key, "site", SOURCE_NAMES[ARCHIVE_SOURCE], site, viewer_url(front["archive_url"]), ", ".join(names[:12]), {
                "pages": len(pages), "listing": f"https://web.archive.org/web/*/{site}/*", "found_by": first["query"],
                "years": f"{years[0]}–{years[-1]}" if years[0] != years[-1] else years[0],
                "page_list": [[name, viewer_url(row["archive_url"])] for name, row in zip(names, pages)][:400],
            }, len(pages))
        else:
            key = f"url:{address_key(first['original_url'])}"
            kind = "file" if first["source"] == "wikimedia-commons" else "web"
            item = Item(key, kind, SOURCE_NAMES.get(first["source"], first["source"]), first.get("title") or first["original_url"],
                        first["original_url"], "", {"found_by": first["query"]})
        if decided.get(item.key) in SETTLED:
            continue
        item.later = bool(entry["checked"]) or decided.get(item.key) == "later"
        items.append(item)
    order = list(FILTERS)
    items.sort(key=lambda item: (item.later, order.index(item.kind), -item.weight))
    return items


def decide(store: Store, item: Item, decision: str, notes: str = "") -> None:
    if decision not in DECISIONS:
        raise ValueError(f"unknown decision: {decision}")
    stamp = now()
    with store.connect() as con:
        con.execute(
            """INSERT INTO triage(item_key,kind,source,title,url,evidence,details_json,decision,stage,notes,decided_at,updated_at)
               VALUES(?,?,?,?,?,?,?,?,'holding',?,?,?)
               ON CONFLICT(item_key) DO UPDATE SET decision=excluded.decision,stage='holding',
                 notes=CASE WHEN excluded.notes<>'' THEN excluded.notes ELSE triage.notes END,updated_at=excluded.updated_at""",
            (item.key, item.kind, item.source, item.title, item.url, item.evidence, json.dumps(item.details, ensure_ascii=False),
             decision, notes.strip(), stamp, stamp),
        )
    if item.kind == "passage" and decision in SETTLED:
        store.save_passage_review(int(item.key.split(":", 1)[1]), PASSAGE_DECISIONS[decision], notes.strip())


def add_lead_by_hand(store: Store, url: str, title: str, notes: str = "", catalogue: bool = False, source: str = "") -> bool:
    """Record something found outside the program straight into Leads."""
    key = f"url:{address_key(url)}"; stamp = now()
    kind = "catalogue" if catalogue else "web"
    with store.connect() as con:
        result = con.execute(
            """INSERT OR IGNORE INTO triage(item_key,kind,source,title,url,evidence,details_json,decision,stage,notes,decided_at,updated_at)
               VALUES(?,?,?,?,?,'','{}','lead','holding',?,?,?)""",
            (key, kind, source.strip() or ("Library or catalogue" if catalogue else "Your own web search"), title.strip() or url, url, notes.strip(), stamp, stamp),
        )
        return result.rowcount == 1


def add_requested_site(store: Store, site: str, note: str = "", asked_by: str = "Requested by Scriptorium") -> str:
    """Bring a site another project asked for into the Collection, keeping any notes it already has."""
    key = f"site:{site}"; stamp = now(); listing = f"https://web.archive.org/web/*/{site}/*"
    with store.connect() as con:
        con.execute(
            """INSERT INTO triage(item_key,kind,source,title,url,evidence,details_json,decision,stage,notes,decided_at,updated_at)
               VALUES(?,'site',?,?,?,'',?,'lead','collection',?,?,?)
               ON CONFLICT(item_key) DO UPDATE SET decision='lead',stage='collection',
                 notes=CASE WHEN COALESCE(triage.notes,'')='' THEN excluded.notes ELSE triage.notes END,updated_at=excluded.updated_at""",
            (key, asked_by, site, listing, json.dumps({"listing": listing, "found_by": asked_by}), note.strip(), stamp, stamp),
        )
    return key


def leads(store: Store, stages: tuple[str, ...] = ("holding",)):
    marks = ",".join("?" * len(stages))
    with store.connect() as con:
        return con.execute(f"SELECT * FROM triage WHERE decision='lead' AND stage IN ({marks}) ORDER BY updated_at DESC", stages).fetchall()


def set_aside(store: Store, decision: str):
    """Finds filed as traces, or set aside as not relevant: most recently decided first."""
    with store.connect() as con:
        return con.execute("SELECT * FROM triage WHERE decision=? ORDER BY updated_at DESC", (decision,)).fetchall()


def lead(store: Store, key: str):
    with store.connect() as con:
        return con.execute("SELECT * FROM triage WHERE item_key=?", (key,)).fetchone()


def update_lead(store: Store, key: str, *, notes: Optional[str] = None, stage: Optional[str] = None, decision: Optional[str] = None) -> None:
    if stage is not None and stage not in STAGES:
        raise ValueError(f"unknown stage: {stage}")
    if decision is not None and decision not in DECISIONS:
        raise ValueError(f"unknown decision: {decision}")
    with store.connect() as con:
        row = con.execute("SELECT * FROM triage WHERE item_key=?", (key,)).fetchone()
        if row is None:
            raise KeyError(key)
        con.execute(
            "UPDATE triage SET notes=?,stage=?,decision=?,updated_at=? WHERE item_key=?",
            (row["notes"] if notes is None else notes.strip(), stage or row["stage"], decision or row["decision"], now(), key),
        )
    if decision in SETTLED and key.startswith("passage:"):
        store.save_passage_review(int(key.split(":", 1)[1]), PASSAGE_DECISIONS[decision], row["notes"] if notes is None else notes.strip())


def stage_counts(store: Store) -> dict[str, int]:
    counts = {"holding": 0, "collection": 0, "done": 0, "trace": 0, "rejected": 0}
    with store.connect() as con:
        for row in con.execute("SELECT decision,stage,count(*) n FROM triage WHERE decision IN ('lead','trace','rejected') GROUP BY 1,2"):
            counts[row["stage"] if row["decision"] == "lead" else row["decision"]] += int(row["n"])
    return counts


def adopt_earlier_decisions(store: Store) -> int:
    """Bring decisions made on the older review screens into the desk, once.

    Passages marked promising or needing research, and pages pasted in from a
    web search or recorded as finding aids, become leads; passages marked
    irrelevant are set aside. Existing desk decisions are never overwritten.
    """
    adopted = 0
    with store.connect() as con:
        before = con.total_changes
        con.execute(
            """INSERT OR IGNORE INTO triage(item_key,kind,source,title,url,evidence,details_json,decision,stage,notes,decided_at,updated_at)
               SELECT 'passage:'||p.id,'passage',c.name,COALESCE(d.title,d.external_id),d.url,p.text,
                 '{"earlier_decision": "'||r.decision||'"}',
                 CASE r.decision WHEN 'irrelevant' THEN 'rejected' ELSE 'lead' END,'holding',r.notes,r.reviewed_at,r.reviewed_at
               FROM passage_reviews r JOIN passages p ON p.id=r.passage_id JOIN corpus_documents d ON d.id=p.document_id
               JOIN corpora c ON c.id=d.corpus_id WHERE r.decision IN ('promising','needs-research','irrelevant')"""
        )
        hand_added = con.execute(
            """SELECT c.*,COALESCE(r.notes,'') AS review_notes FROM candidates c LEFT JOIN document_reviews r ON r.candidate_id=c.id
               WHERE c.source='manual-web-search' OR c.fetch_state='manual-acquisition'"""
        ).fetchall()
        for row in hand_added:
            url = row["original_url"] or row["url"]; key = f"url:{address_key(url)}"
            catalogue = row["fetch_state"] == "manual-acquisition"
            notes = "\n\n".join(part for part in (row["provenance_notes"], row["review_notes"]) if part)
            if row["query_text"] and not catalogue:
                notes = (notes + "\n\n" if notes else "") + f"Found by searching {row['query_text']}"
            con.execute(
                """INSERT OR IGNORE INTO triage(item_key,kind,source,title,url,evidence,details_json,decision,stage,notes,decided_at,updated_at)
                   VALUES(?,?,?,?,?,'','{}','lead','holding',?,?,?)""",
                (key, "catalogue" if catalogue else "web", row["source"] if catalogue else "Your own web search",
                 row["title_hint"] or url, url, notes, row["created_at"], row["created_at"]),
            )
            if not catalogue:
                con.execute("INSERT OR IGNORE INTO triage_copies(item_key,candidate_id) VALUES(?,?)", (key, row["id"]))
        adopted = con.total_changes - before
    return adopted


# Collection: saving copies --------------------------------------------------

def copy_targets(store: Store, row) -> list[dict[str, Optional[str]]]:
    """What "save copies" downloads for a lead. Websites get a complete copy instead (site_copy.py)."""
    if row["kind"] == "file":
        name = commons_file_name(row["title"])
        return [
            {"url": row["url"], "original_url": row["url"], "archive_url": None, "title": "Commons description page"},
            {"url": f"https://commons.wikimedia.org/wiki/Special:FilePath/{quote(name.replace(' ', '_'))}", "original_url": row["url"], "archive_url": None, "title": name},
        ]
    if row["kind"] == "web":
        return [{"url": row["url"], "original_url": row["url"], "archive_url": None, "title": row["title"]}]
    return []  # passages were saved when collected; catalogue records need a visit; websites are copied whole


def queue_copies(store: Store, key: str) -> int:
    row = lead(store, key)
    if row is None:
        raise KeyError(key)
    targets = copy_targets(store, row)
    for target in targets:
        store.add_candidate({
            "url": target["url"], "original_url": target["original_url"], "archive_url": target["archive_url"],
            "source": row["source"], "discovered_at": row["decided_at"], "query": key, "title": target["title"],
        })
    with store.connect() as con:
        for target in targets:
            candidate = con.execute("SELECT id FROM candidates WHERE url=?", (target["url"],)).fetchone()
            con.execute("INSERT OR IGNORE INTO triage_copies(item_key,candidate_id) VALUES(?,?)", (key, candidate["id"]))
    return len(targets)


def retry_failed_copies(store: Store, key: str) -> int:
    with store.connect() as con:
        return con.execute(
            "UPDATE candidates SET fetch_state='pending' WHERE fetch_state='failed' AND id IN (SELECT candidate_id FROM triage_copies WHERE item_key=?)", (key,)
        ).rowcount


def _ascii_address(url: str) -> str:
    """An address as a request must carry it. Saved addresses keep their own letters, as a lead's key is made from them."""
    parts = urlsplit(url); safe = "%/:;=&?~+!$,'@()*[]"
    return urlunsplit(parts._replace(path=quote(parts.path, safe=safe), query=quote(parts.query, safe=safe)))


def download_copies(
    store: Store, keys: list[str], limit: Optional[int] = None, *, delay_seconds: float = 1.5,
    opener: Callable[..., object] = urlopen, sleeper: Callable[[float], None] = time.sleep,
) -> tuple[int, int]:
    """Download the queued copies of these leads politely, then extract their text."""
    if not keys:
        return 0, 0
    marks = ",".join("?" * len(keys))
    with store.connect() as con:
        pending = con.execute(
            f"""SELECT DISTINCT c.id,c.url FROM triage_copies t JOIN candidates c ON c.id=t.candidate_id
                WHERE t.item_key IN ({marks}) AND c.fetch_state='pending' ORDER BY c.id""", keys,
        ).fetchall()
    saved = failed = 0
    for index, row in enumerate(pending[:limit] if limit else pending):
        if index:
            sleeper(delay_seconds)
        try:
            request = Request(_ascii_address(row["url"]), headers={"User-Agent": user_agent("preserving a page a researcher selected")})
            with opener(request, timeout=60) as response:
                body = response.read(MAX_COPY_BYTES + 1)
                if len(body) > MAX_COPY_BYTES:
                    raise ValueError(f"larger than {MAX_COPY_BYTES // (1024 * 1024)} MB; not saved")
                store.record_retrieval(int(row["id"]), status_code=response.status, content_type=response.headers.get_content_type(),
                                       blob_sha256=store.put_blob(body), final_url=response.url, error=None)
                saved += 1
        except Exception as exc:  # the failure is recorded so it can be retried
            store.record_retrieval(int(row["id"]), status_code=getattr(exc, "code", None), content_type=None, blob_sha256=None, final_url=None,
                                   error=f"{type(exc).__name__}: {exc}")
            failed += 1
            if isinstance(exc, HTTPError) and exc.code == 429:
                break
    extract(store)
    return saved, failed


def copy_status(store: Store, key: str) -> dict[str, int]:
    status = {"total": 0, "fetched": 0, "failed": 0, "pending": 0, "texts": 0}
    with store.connect() as con:
        for row in con.execute(
            "SELECT c.fetch_state,count(*) n FROM triage_copies t JOIN candidates c ON c.id=t.candidate_id WHERE t.item_key=? GROUP BY 1", (key,)
        ):
            status[row["fetch_state"]] = status.get(row["fetch_state"], 0) + int(row["n"]); status["total"] += int(row["n"])
        status["texts"] = int(con.execute(
            "SELECT count(DISTINCT a.candidate_id) FROM triage_copies t JOIN artefacts a ON a.candidate_id=t.candidate_id AND a.kind='extracted_text' WHERE t.item_key=?", (key,)
        ).fetchone()[0])
    return status


def saved_texts(store: Store, key: str) -> list[dict[str, str]]:
    """Readable text saved for a lead: its downloaded pages, or a passage's whole source page."""
    with store.connect() as con:
        if key.startswith("passage:"):
            rows = con.execute(
                """SELECT COALESCE(d.title,d.external_id) AS title,d.url AS original_url,d.url,d.text_sha256 AS blob_sha256
                   FROM passages p JOIN corpus_documents d ON d.id=p.document_id WHERE p.id=?""", (int(key.split(":", 1)[1]),)
            ).fetchall()
        else:
            rows = con.execute(
                """SELECT c.title_hint AS title,COALESCE(c.original_url,c.url) AS original_url,c.url,a.blob_sha256
                   FROM triage_copies t JOIN candidates c ON c.id=t.candidate_id
                   JOIN artefacts a ON a.candidate_id=c.id AND a.kind='extracted_text' WHERE t.item_key=? ORDER BY c.id""", (key,)
            ).fetchall()
    texts = []
    for row in rows:
        try:
            text = store.blob(row["blob_sha256"]).decode("utf-8", errors="replace")
        except OSError:
            continue
        texts.append({"title": row["title"] or row["original_url"], "original_url": row["original_url"], "url": row["url"], "text": text})
    return texts


# Collection: Obsidian notes -------------------------------------------------

def load_settings(data_dir: Path) -> dict[str, str]:
    try:
        saved = json.loads((data_dir / SETTINGS_FILE).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        saved = {}
    return {"vault": str(saved.get("vault") or data_dir / "Lost Notation Vault")}


def save_settings(data_dir: Path, settings: dict[str, str]) -> None:
    (data_dir / SETTINGS_FILE).write_text(json.dumps(settings, ensure_ascii=False, indent=2), encoding="utf-8")


def note_name(text: str) -> str:
    cleaned = re.sub(r'[\\/:*?"<>|#^\[\]]+', " ", text)
    return re.sub(r"\s+", " ", cleaned).strip(" .")[:90] or "Untitled"


class _Markdownish(HTMLParser):
    """Strip any HTML that slipped into a title or note."""
    def __init__(self) -> None:
        super().__init__(); self.parts: list[str] = []
    def handle_data(self, data: str) -> None:
        self.parts.append(data)


def _plain(text: str) -> str:
    parser = _Markdownish(); parser.feed(text or ""); return "".join(parser.parts)


def _as_written(text: str) -> str:
    """Text from a source: its double brackets are its author's, so they read as written and do not link.
    Only a line where they could form a link is changed."""
    escape = lambda brackets: "".join("\\" + bracket for bracket in brackets[0])
    return re.sub(r"^.*\[\[.*\]\].*$", lambda line: re.sub(r"\[\[+|\]\]+", escape, line[0]), text, flags=re.M)


def _yaml(value: object) -> str:
    return json.dumps(value, ensure_ascii=False)


def _write_if_changed(path: Path, text: str) -> None:
    """A vault may sync to the cloud or be open in Obsidian, so a file is left alone unless it has changed."""
    body = text.encode("utf-8")
    if not path.exists() or path.read_bytes() != body:
        path.write_bytes(body)


def write_vault_note(store: Store, key: str, vault: Path, own_copy: Optional[Path] = None, taken: Optional[dict] = None) -> Path:
    """Write (or refresh) a lead's Obsidian note and one note per saved page.

    Anything written below the "## My notes" heading in Obsidian is kept when
    the note is refreshed, even if the vault has moved since. ``taken`` is what
    Scriptorium has taken of this lead, if it has said (see handover.py).
    """
    row = lead(store, key)
    if row is None:
        raise KeyError(key)
    details = json.loads(row["details_json"] or "{}")
    folder = vault / "Lost Notation"; folder.mkdir(parents=True, exist_ok=True)
    previous = Path(row["vault_note"]) if row["vault_note"] else None
    target = previous
    if target is None or target.parent != folder:
        stem = note_name(_plain(row["title"]))
        target = folder / f"{stem}.md"
        if target.exists() and f"lost-notation-key: {_yaml(key)}" not in target.read_text(encoding="utf-8"):
            target = folder / f"{stem} ({hashlib.sha1(key.encode()).hexdigest()[:6]}).md"
    kept = "_Write your own notes here; they are kept when this note is refreshed._\n"
    for source in (target, previous):
        existing = source.read_text(encoding="utf-8") if source is not None and source.exists() else ""
        if "\n## My notes\n" in existing:
            kept = existing.split("\n## My notes\n", 1)[1].lstrip("\n"); break

    where = []
    if row["kind"] == "site":
        where += [f"- Original address: `{row['title']}`", f"- Front page in the Wayback Machine: {row['url']}", f"- Everything archived: {details.get('listing', '')}"]
        if details.get("years"):
            where.append(f"- Captured: {details['years']}")
        if own_copy is not None and (own_copy / "_contents.html").exists():
            front = f"[front page](<{(own_copy / 'index.html').as_uri()}>) · " if (own_copy / "index.html").exists() else ""
            where.append(f"- Your own copy: {front}[every file](<{(own_copy / '_contents.html').as_uri()}>) · folder `{own_copy}`")
    elif row["url"]:
        where.append(f"- Address: {row['url']}")
    if row["kind"] == "file":
        where.append(f"- Preview: ![]({commons_thumbnail(row['title'], 800)})")
    if taken:
        shelf = f"[{taken['shelf']}]({taken['page']})" if taken.get("page") else taken["shelf"]
        where.append(f"- In Scriptorium: taken {taken['date']}" + (f", shelved in {shelf}" if shelf else "") + ("; this copy has changed since." if taken["changed"] else "."))

    evidence = ""
    if row["kind"] == "passage" and row["evidence"]:
        evidence = "\n".join("> " + line for line in _as_written(_plain(row["evidence"])).splitlines()) + "\n"
    elif row["kind"] == "site" and row["evidence"]:
        evidence = f"{details.get('pages', '')} archived pages, including: {_as_written(row['evidence'])}\n"

    texts = saved_texts(store, key)
    page_links = []
    if texts and row["kind"] != "passage":
        pages_folder = folder / f"{target.stem} - saved pages"; pages_folder.mkdir(exist_ok=True)
        used: set[str] = set()
        for text in texts:
            name = note_name(text["title"] or text["original_url"])
            while name.lower() in used:
                name += " 2"
            used.add(name.lower())
            _write_if_changed(pages_folder / f"{name}.md", "\n".join([
                "---", f"source: {_yaml(text['original_url'])}", f"copy: {_yaml(text['url'])}", f"lead: {_yaml('[[' + target.stem + ']]')}", "---", "",
                f"# {name}", "", f"From [[{target.stem}]] · original address: {text['original_url']}", "", _as_written(text["text"]), "",
            ]))
            page_links.append(f"- [[{pages_folder.name}/{name}|{name}]]")
    status = copy_status(store, key)
    if row["kind"] == "passage":
        copies = "The whole source page was saved when it was collected; its text is in the program's catalogue."
    elif row["kind"] == "catalogue":
        copies = "Not downloadable: this record needs a library visit or request."
    elif status["total"]:
        copies = f"{status['fetched']} of {status['total']} saved; {status['texts']} with readable text." + (f" {status['failed']} could not be saved." if status["failed"] else "")
    else:
        copies = "No copies saved yet."

    body = "\n".join([
        "---", f"title: {_yaml(_plain(row['title']))}", f"kind: {_yaml(KIND_NAMES.get(row['kind'], row['kind']))}",
        f"source: {_yaml(row['source'])}", f"found: {row['decided_at'][:10]}", f"status: {_yaml('finished' if row['stage'] == 'done' else 'processing')}",
        *([f"scriptorium: {_yaml(('changed since taken ' if taken['changed'] else 'taken ') + taken['date'])}"] if taken else []),
        f"lost-notation-key: {_yaml(key)}", "tags: [lost-notation]", "---", "",
        f"# {_plain(row['title'])}", "",
        "## Why it's a lead", "", _plain(row["notes"]) or "_No notes yet._", "",
        "## Where it is", "", "\n".join(where) or "_No address recorded._", "",
        *(["## Evidence", "", evidence] if evidence else []),
        *([f"Found by: `{details['found_by']}`", ""] if details.get("found_by") else []),
        "## Saved copies", "", copies, "", "\n".join(page_links), "",
        "## My notes", "", kept,
    ])
    _write_if_changed(target, body)
    with store.connect() as con:
        con.execute("UPDATE triage SET vault_note=? WHERE item_key=? AND COALESCE(vault_note,'')<>?", (str(target), key, str(target)))
    return target
