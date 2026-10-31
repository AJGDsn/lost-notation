"""What passes between this project and another that takes its site copies.

``requests.txt`` in the data folder holds one site address per line, optionally
followed by a space and a note. Another project only ever appends to it. The
researcher starts each copy from the Collection; when the copy is complete this
module adds ``[done <date>]`` to the end of the line.

``imported.txt`` is that project's record of what it took, one tab-separated line
per import: lead key, date, the copy's ``complete`` value and where it was shelved.
A lead's vault note says so, and says when the copy has changed since.
See docs/site-copy-format.md.

Vault notes are kept up to date from here too: a lead gets one once it has been
processed through the Collection, and it changes when the lead does.
"""
from __future__ import annotations

from pathlib import Path
import re
from typing import Optional

from .desk import copy_status, lead, load_settings, write_vault_note
from .discovery import address_key
from .site_copy import _write_atomically, copy_folder, load_manifest
from .store import Store, now

REQUESTS_FILE = "requests.txt"
IMPORTED_FILE = "imported.txt"
DONE_MARK = re.compile(r"\s*\[done (\d{4}-\d{2}-\d{2})\]\s*$")
SITE_ADDRESS = re.compile(r"^[a-z0-9-]+(?:\.[a-z0-9-]+)+(?:/[^\s?#]*)?$")
# Only sites can be asked for. See docs/book-export-design.md for books.
FILE_ADDRESS = re.compile(r"/(?:wiki/file:|[^/]*\.(?:pdf|djvu?|epub|tiff?|cbz|zip|png|jpe?g|gif)$)")


def _parse(line: str) -> Optional[dict[str, str]]:
    """The request on a line of the file; None for a note, a blank line or anything that is not a site's address, such as a book line."""
    line = line.strip()
    if not line or line.startswith("#"):
        return None
    done = DONE_MARK.search(line)
    words = (line[:done.start()] if done else line).split(None, 1)
    address = address_key(words[0]).rstrip("/")
    if words[0].lower().startswith("book:") or FILE_ADDRESS.search(address) or not SITE_ADDRESS.match(address):
        return None
    return {"address": address, "note": words[1].strip() if len(words) > 1 else "", "done": done.group(1) if done else ""}


def _read(path: Path) -> str:
    return path.read_bytes().decode("utf-8-sig", errors="replace")


def read_requests(data_dir: Path) -> list[dict[str, str]]:
    try:
        text = _read(data_dir / REQUESTS_FILE)
    except OSError:
        return []
    return [request for request in map(_parse, text.splitlines()) if request]


def mark_requests_done(data_dir: Path) -> int:
    """Mark each open request whose copy is complete; returns how many.

    The other project may append at any moment, so the file is read again just
    before it is replaced. If it cannot be written now, the next call tries again.
    """
    path = data_dir / REQUESTS_FILE
    try:
        for _ in range(3):
            text = _read(path); lines = []; marked = 0
            for line in text.splitlines(keepends=True):
                request = _parse(line)
                body = line.rstrip("\r\n")
                if request and not request["done"] and load_manifest(copy_folder(data_dir, request["address"])).get("complete"):
                    line = f"{body} [done {now()[:10]}]{line[len(body):]}"; marked += 1
                lines.append(line)
            if not marked:
                return 0
            new = "".join(lines)
            if _read(path) == text:
                _write_atomically(path, new if new.endswith("\n") else new + "\n")
                return marked
    except OSError:
        pass
    return 0


def open_requests(data_dir: Path) -> list[dict[str, str]]:
    """Requests still waiting for a copy, once each."""
    mark_requests_done(data_dir)
    return list({request["address"]: request for request in read_requests(data_dir) if not request["done"]}.values())


def read_imports(data_dir: Path) -> dict[str, dict[str, str]]:
    """What Scriptorium says it has taken, by lead. A later line for a lead replaces an earlier one."""
    try:
        text = _read(data_dir / IMPORTED_FILE)
    except OSError:
        return {}
    taken: dict[str, dict[str, str]] = {}
    for line in text.splitlines():
        fields = [field.strip() for field in line.split("\t")]
        if len(fields) >= 3 and fields[0]:
            taken[fields[0]] = {"date": fields[1], "complete": fields[2], "shelf": fields[3] if len(fields) > 3 else ""}
    return taken


def _copy_of(data_dir: Path, key: str) -> Optional[Path]:
    return copy_folder(data_dir, key.split(":", 1)[1]) if key.startswith("site:") else None


def scriptorium_status(data_dir: Path, key: str, imports: Optional[dict[str, dict[str, str]]] = None) -> Optional[dict]:
    """What Scriptorium took of a lead, and whether the copy has changed since. None means it has not said,
    which does not prove it took nothing: an import it could not record still stands."""
    taken = (read_imports(data_dir) if imports is None else imports).get(key)
    if taken is None:
        return None
    folder = _copy_of(data_dir, key)
    return {**taken, "changed": folder is not None and load_manifest(folder).get("complete") != taken["complete"]}


def write_note(store: Store, data_dir: Path, key: str) -> Path:
    return write_vault_note(store, key, Path(load_settings(data_dir)["vault"]), _copy_of(data_dir, key), scriptorium_status(data_dir, key))


def _note_is_due(store: Store, data_dir: Path, key: str) -> bool:
    """A note is written once a lead has been processed: its copies saved, or marked finished."""
    row = lead(store, key)
    if row is None:
        return False
    if row["vault_note"] or (row["decision"] == "lead" and row["stage"] == "done"):
        return True
    folder = _copy_of(data_dir, key)
    return bool(load_manifest(folder).get("complete")) if folder is not None else copy_status(store, key)["fetched"] > 0


def update_notes(store: Store, data_dir: Path, keys: list[str]) -> str:
    """Write the notes of these leads that are due one; says what happened, for the page or job that asked."""
    due = [key for key in keys if _note_is_due(store, data_dir, key)]
    try:
        for key in due:
            write_note(store, data_dir, key)
    except OSError as exc:
        return f"The vault could not be written ({type(exc).__name__}: {exc}); check the folder at the top of the Collection."
    return f"Vault note{'s' if len(due) > 1 else ''} up to date." if due else ""


def sync_notes(store: Store, data_dir: Path, seen: dict) -> str:
    """Bring every note up to date, the first time and whenever Scriptorium has reported an import. Returns any problem."""
    try:
        stat = (data_dir / IMPORTED_FILE).stat(); signature = (stat.st_mtime_ns, stat.st_size)
    except OSError:
        signature = None
    if "imports" in seen and seen["imports"] == signature:
        return ""
    with store.connect() as con:
        keys = [row[0] for row in con.execute("SELECT item_key FROM triage WHERE COALESCE(vault_note,'')<>'' OR (decision='lead' AND stage IN ('collection','done'))")]
    said = update_notes(store, data_dir, keys)
    if not said.startswith("The vault"):
        seen["imports"] = signature; return ""
    return said
