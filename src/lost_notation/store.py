"""SQLite catalogue and immutable content-addressed blob store."""
from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import sqlite3
from urllib.parse import parse_qs, urlencode, urlparse, urlunparse

MIGRATIONS = [
    """
    CREATE TABLE IF NOT EXISTS migrations (version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS candidates (
      id INTEGER PRIMARY KEY, url TEXT NOT NULL UNIQUE, source TEXT NOT NULL,
      discovered_at TEXT NOT NULL, query_text TEXT, title_hint TEXT, archive_url TEXT,
      fetch_state TEXT NOT NULL DEFAULT 'pending', created_at TEXT NOT NULL
    );
    CREATE TABLE IF NOT EXISTS retrievals (
      id INTEGER PRIMARY KEY, candidate_id INTEGER NOT NULL REFERENCES candidates(id),
      retrieved_at TEXT NOT NULL, status_code INTEGER, content_type TEXT, blob_sha256 TEXT,
      final_url TEXT, error TEXT, UNIQUE(candidate_id, retrieved_at)
    );
    CREATE TABLE IF NOT EXISTS artefacts (
      id INTEGER PRIMARY KEY, candidate_id INTEGER NOT NULL REFERENCES candidates(id),
      kind TEXT NOT NULL, producer TEXT NOT NULL, blob_sha256 TEXT NOT NULL,
      confidence REAL, created_at TEXT NOT NULL, UNIQUE(candidate_id, kind, producer, blob_sha256)
    );
    CREATE VIRTUAL TABLE IF NOT EXISTS artefact_fts USING fts5(candidate_id UNINDEXED, text);
    """,
    """
    CREATE TABLE IF NOT EXISTS discovery_runs (
      id INTEGER PRIMARY KEY, source TEXT NOT NULL, request_json TEXT NOT NULL,
      response_sha256 TEXT, started_at TEXT NOT NULL, completed_at TEXT,
      result_count INTEGER NOT NULL DEFAULT 0, error TEXT
    );
    ALTER TABLE candidates ADD COLUMN original_url TEXT;
    CREATE TABLE IF NOT EXISTS projects (
      id TEXT PRIMARY KEY, title TEXT NOT NULL, summary TEXT, author TEXT,
      year INTEGER, categories_json TEXT NOT NULL DEFAULT '[]', status TEXT NOT NULL DEFAULT 'needs-review',
      candidate_id INTEGER REFERENCES candidates(id), created_at TEXT NOT NULL, updated_at TEXT NOT NULL
    );
    """,
    """
    CREATE TABLE IF NOT EXISTS review_marks (
      record_key TEXT PRIMARY KEY, checked_at TEXT NOT NULL
    );
    """,
    """
    CREATE TABLE IF NOT EXISTS manual_query_checks (
      query_text TEXT PRIMARY KEY, checked_at TEXT NOT NULL
    );
    """,
    """
    CREATE TABLE IF NOT EXISTS document_reviews (
      candidate_id INTEGER PRIMARY KEY REFERENCES candidates(id),
      decision TEXT NOT NULL DEFAULT 'unreviewed', notes TEXT NOT NULL DEFAULT '', reviewed_at TEXT NOT NULL
    );
    """,
    """
    CREATE TABLE IF NOT EXISTS corpora (
      id INTEGER PRIMARY KEY, name TEXT NOT NULL UNIQUE, source_path TEXT NOT NULL, created_at TEXT NOT NULL
    );
    CREATE TABLE IF NOT EXISTS corpus_documents (
      id INTEGER PRIMARY KEY, corpus_id INTEGER NOT NULL REFERENCES corpora(id), external_id TEXT NOT NULL,
      title TEXT, url TEXT, author TEXT, published_at TEXT, raw_sha256 TEXT NOT NULL, text_sha256 TEXT NOT NULL,
      created_at TEXT NOT NULL, UNIQUE(corpus_id,external_id,raw_sha256)
    );
    CREATE TABLE IF NOT EXISTS passages (
      id INTEGER PRIMARY KEY, document_id INTEGER NOT NULL REFERENCES corpus_documents(id), ordinal INTEGER NOT NULL,
      text TEXT NOT NULL, score INTEGER NOT NULL, signals_json TEXT NOT NULL, producer TEXT NOT NULL,
      created_at TEXT NOT NULL, UNIQUE(document_id,ordinal,producer)
    );
    CREATE VIRTUAL TABLE IF NOT EXISTS passage_fts USING fts5(passage_id UNINDEXED, text);
    CREATE TABLE IF NOT EXISTS passage_reviews (
      passage_id INTEGER PRIMARY KEY REFERENCES passages(id), decision TEXT NOT NULL DEFAULT 'unreviewed',
      notes TEXT NOT NULL DEFAULT '', reviewed_at TEXT NOT NULL
    );
    """,
    """
    CREATE TABLE IF NOT EXISTS source_items (
      id INTEGER PRIMARY KEY, source_name TEXT NOT NULL, url TEXT NOT NULL, parent_url TEXT,
      depth INTEGER NOT NULL DEFAULT 0, state TEXT NOT NULL DEFAULT 'pending',
      raw_sha256 TEXT, content_type TEXT, final_url TEXT, error TEXT,
      created_at TEXT NOT NULL, updated_at TEXT NOT NULL, UNIQUE(source_name,url)
    );
    CREATE INDEX IF NOT EXISTS source_items_queue ON source_items(source_name,state,id);
    """,
    """
    ALTER TABLE source_items ADD COLUMN priority INTEGER NOT NULL DEFAULT 0;
    CREATE INDEX IF NOT EXISTS source_items_priority_queue ON source_items(source_name,state,priority,id);
    """,
    """
    ALTER TABLE corpus_documents ADD COLUMN group_key TEXT;
    CREATE INDEX IF NOT EXISTS corpus_documents_group_key ON corpus_documents(corpus_id,group_key);
    """,
    """
    ALTER TABLE source_items ADD COLUMN label TEXT;
    """,
    """
    ALTER TABLE passages ADD COLUMN active INTEGER NOT NULL DEFAULT 1;
    CREATE INDEX IF NOT EXISTS passages_active ON passages(active,score,id);
    """,
    """
    ALTER TABLE candidates ADD COLUMN source_id TEXT;
    ALTER TABLE candidates ADD COLUMN access_status TEXT NOT NULL DEFAULT 'unknown';
    ALTER TABLE candidates ADD COLUMN has_fulltext INTEGER;
    ALTER TABLE candidates ADD COLUMN has_graphics INTEGER;
    ALTER TABLE candidates ADD COLUMN provenance_notes TEXT;
    CREATE INDEX IF NOT EXISTS candidates_source_id ON candidates(source,source_id);
    """,
    """
    CREATE TABLE IF NOT EXISTS archive_scans (
      id INTEGER PRIMARY KEY, host TEXT NOT NULL, url_regex TEXT NOT NULL, label TEXT,
      total_pages INTEGER, next_page INTEGER NOT NULL DEFAULT 0, matches INTEGER NOT NULL DEFAULT 0,
      failures INTEGER NOT NULL DEFAULT 0, last_error TEXT,
      created_at TEXT NOT NULL, updated_at TEXT NOT NULL, UNIQUE(host,url_regex)
    );
    """,
    """
    CREATE TABLE IF NOT EXISTS triage (
      item_key TEXT PRIMARY KEY, kind TEXT NOT NULL, source TEXT NOT NULL, title TEXT NOT NULL,
      url TEXT, evidence TEXT NOT NULL DEFAULT '', details_json TEXT NOT NULL DEFAULT '{}',
      decision TEXT NOT NULL, stage TEXT NOT NULL DEFAULT 'holding', notes TEXT NOT NULL DEFAULT '',
      vault_note TEXT, decided_at TEXT NOT NULL, updated_at TEXT NOT NULL
    );
    CREATE INDEX IF NOT EXISTS triage_decision ON triage(decision,stage);
    CREATE TABLE IF NOT EXISTS triage_copies (
      item_key TEXT NOT NULL REFERENCES triage(item_key), candidate_id INTEGER NOT NULL REFERENCES candidates(id),
      PRIMARY KEY(item_key,candidate_id)
    );
    """
]

def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def discussion_group_key(url: object) -> str | None:
    """Return a stable thread identity while retaining the original page URL."""
    if not isinstance(url, str) or not url:
        return None
    parsed = urlparse(url)
    query = parse_qs(parsed.query)
    for key in ("t", "thread", "threadid", "topic", "topic_id"):
        if query.get(key):
            return urlunparse((parsed.scheme.lower(), parsed.netloc.lower(), parsed.path, "", urlencode({key:query[key][0]}), ""))
    if re.search(r"/(?:threads?|topics?|discussions?)/", parsed.path, re.I):
        path = re.sub(r"/(?:page[-/]?\d+|p\d+)/?$", "", parsed.path, flags=re.I)
        kept = {key:values[0] for key,values in query.items() if key.lower() not in {"page","start","offset"} and values}
        return urlunparse((parsed.scheme.lower(), parsed.netloc.lower(), path, "", urlencode(kept), ""))
    return None

class Store:
    def __init__(self, root: Path):
        self.root = root
        self.root.mkdir(parents=True, exist_ok=True)
        self.blobs = root / "blobs"
        self.blobs.mkdir(exist_ok=True)
        self.db = root / "catalogue.sqlite3"
        self.migrate()

    @contextmanager
    def connect(self):
        connection = sqlite3.connect(self.db)
        connection.row_factory = sqlite3.Row
        try:
            yield connection
            connection.commit()
        finally:
            connection.close()

    def migrate(self) -> None:
        with self.connect() as con:
            # Bootstrap the migration ledger before consulting it. The first
            # schema migration also contains this statement so restored or
            # partially initialised catalogues remain harmless to open.
            con.execute("CREATE TABLE IF NOT EXISTS migrations (version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL)")
            for version, migration in enumerate(MIGRATIONS, start=1):
                if not con.execute("SELECT 1 FROM migrations WHERE version=?", (version,)).fetchone():
                    try:
                        con.executescript(migration)
                    except sqlite3.OperationalError as exc:
                        if "duplicate column name" not in str(exc):
                            raise
                    con.execute("INSERT INTO migrations VALUES (?, ?)", (version, now()))
            for row in con.execute("SELECT id,corpus_id,external_id,url FROM corpus_documents WHERE group_key IS NULL OR group_key='' ").fetchall():
                group_key = discussion_group_key(row["url"]) or f"document:{row['corpus_id']}:{row['external_id']}"
                con.execute("UPDATE corpus_documents SET group_key=? WHERE id=?", (group_key, row["id"]))

    def put_blob(self, content: bytes) -> str:
        digest = hashlib.sha256(content).hexdigest()
        destination = self.blobs / digest[:2] / digest
        if not destination.exists():
            destination.parent.mkdir(exist_ok=True)
            destination.write_bytes(content)
        return digest

    def blob(self, digest: str) -> bytes:
        return (self.blobs / digest[:2] / digest).read_bytes()

    def add_candidate(self, record: dict[str, object]) -> bool:
        with self.connect() as con:
            result = con.execute(
                """INSERT OR IGNORE INTO candidates(
                  url,source,discovered_at,query_text,title_hint,archive_url,created_at,original_url,fetch_state,
                  source_id,access_status,has_fulltext,has_graphics,provenance_notes
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    record["url"], record["source"], record["discovered_at"], record.get("query"), record.get("title"),
                    record.get("archive_url"), now(), record.get("original_url"), record.get("fetch_state", "pending"), record.get("source_id"),
                    record.get("access_status", "unknown"), record.get("has_fulltext"), record.get("has_graphics"),
                    record.get("notes"),
                ),
            )
            return result.rowcount == 1

    def record_discovery_run(self, source: str, request: dict[str, object], response: bytes | None, result_count: int, error: str | None = None) -> int:
        digest = self.put_blob(response) if response is not None else None
        with self.connect() as con:
            result = con.execute(
                "INSERT INTO discovery_runs(source,request_json,response_sha256,started_at,completed_at,result_count,error) VALUES(?,?,?,?,?,?,?)",
                (source, json.dumps(request, sort_keys=True), digest, now(), now(), result_count, error),
            )
            return int(result.lastrowid)

    def upsert_project(self, record: dict[str, object]) -> None:
        with self.connect() as con:
            con.execute(
                """INSERT INTO projects(id,title,summary,author,year,categories_json,status,candidate_id,created_at,updated_at)
                VALUES(?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(id) DO UPDATE SET title=excluded.title,summary=excluded.summary,author=excluded.author,
                  year=excluded.year,categories_json=excluded.categories_json,status=excluded.status,
                  candidate_id=excluded.candidate_id,updated_at=excluded.updated_at""",
                (record["id"], record["title"], record.get("summary"), record.get("author"), record.get("year"),
                 json.dumps(record.get("categories", [])), record.get("status", "needs-review"), record.get("candidate_id"), now(), now()),
            )

    def projects_for_vault(self, reviewed_only: bool):
        condition = "WHERE p.status='reviewed'" if reviewed_only else ""
        with self.connect() as con:
            return con.execute(f"""SELECT p.*, c.url, c.original_url, c.archive_url, c.source
                FROM projects p LEFT JOIN candidates c ON c.id=p.candidate_id {condition} ORDER BY p.id""").fetchall()

    def discovery_runs(self, limit: int = 50, with_results_only: bool = False):
        # A paged archive survey logs many empty pages; reviewers need the
        # pages that matched, however long ago they were scanned.
        condition = " AND result_count > 0" if with_results_only else ""
        with self.connect() as con:
            return con.execute(
                "SELECT id,source,request_json,response_sha256,completed_at,result_count,error FROM discovery_runs "
                f"WHERE response_sha256 IS NOT NULL{condition} ORDER BY id DESC LIMIT ?", (limit,)
            ).fetchall()

    def archive_scan(self, host: str, url_regex: str, label: str | None = None, create: bool = True):
        """Progress of one resumable host survey. A changed regex starts afresh,
        because pages already scanned were never tested against it."""
        with self.connect() as con:
            if create:
                con.execute(
                    "INSERT OR IGNORE INTO archive_scans(host,url_regex,label,created_at,updated_at) VALUES(?,?,?,?,?)",
                    (host, url_regex, label, now(), now()),
                )
            return con.execute("SELECT * FROM archive_scans WHERE host=? AND url_regex=?", (host, url_regex)).fetchone()

    def update_archive_scan(self, scan_id: int, **fields: object) -> None:
        columns = {"total_pages", "next_page", "matches", "failures", "last_error"}
        if not fields or not set(fields) <= columns:
            raise ValueError(f"unknown archive-scan fields: {sorted(set(fields) - columns)}")
        assignments = ",".join(f"{name}=?" for name in fields)
        with self.connect() as con:
            con.execute(f"UPDATE archive_scans SET {assignments},updated_at=? WHERE id=?", (*fields.values(), now(), scan_id))

    def checked_review_keys(self) -> set[str]:
        with self.connect() as con:
            return {row[0] for row in con.execute("SELECT record_key FROM review_marks")}

    def save_review_marks(self, visible_keys: set[str], checked_keys: set[str]) -> None:
        """Persist only the checked state for records presently shown in review."""
        with self.connect() as con:
            for key in visible_keys - checked_keys:
                con.execute("DELETE FROM review_marks WHERE record_key=?", (key,))
            for key in checked_keys:
                con.execute(
                    "INSERT INTO review_marks(record_key,checked_at) VALUES(?,?) "
                    "ON CONFLICT(record_key) DO UPDATE SET checked_at=excluded.checked_at", (key, now()),
                )

    def checked_manual_queries(self) -> set[str]:
        with self.connect() as con:
            return {row[0] for row in con.execute("SELECT query_text FROM manual_query_checks")}

    def mark_manual_query_checked(self, query: str) -> None:
        with self.connect() as con:
            con.execute(
                "INSERT INTO manual_query_checks(query_text,checked_at) VALUES(?,?) "
                "ON CONFLICT(query_text) DO UPDATE SET checked_at=excluded.checked_at", (query, now()),
            )

    def reset_manual_query_checks(self, queries: list[str]) -> int:
        """Restart only the active queue while retaining unrelated search history."""
        unique = sorted(set(queries))
        if not unique:
            return 0
        with self.connect() as con:
            before = con.total_changes
            con.executemany("DELETE FROM manual_query_checks WHERE query_text=?", ((query,) for query in unique))
            return con.total_changes - before

    def documents_for_review(self):
        with self.connect() as con:
            return con.execute("""SELECT c.id,c.url,c.original_url,c.title_hint,a.blob_sha256,
                COALESCE(r.decision,'unreviewed') AS decision, COALESCE(r.notes,'') AS notes
                FROM candidates c JOIN artefacts a ON a.candidate_id=c.id AND a.kind='extracted_text'
                LEFT JOIN document_reviews r ON r.candidate_id=c.id ORDER BY c.id""").fetchall()

    def save_document_review(self, candidate_id: int, decision: str, notes: str) -> None:
        if decision not in {"unreviewed", "promising", "irrelevant", "needs-research"}:
            raise ValueError("invalid review decision")
        with self.connect() as con:
            con.execute(
                "INSERT INTO document_reviews(candidate_id,decision,notes,reviewed_at) VALUES(?,?,?,?) "
                "ON CONFLICT(candidate_id) DO UPDATE SET decision=excluded.decision,notes=excluded.notes,reviewed_at=excluded.reviewed_at",
                (candidate_id, decision, notes, now()),
            )

    def get_or_create_corpus(self, name: str, source_path: str) -> int:
        with self.connect() as con:
            con.execute("INSERT OR IGNORE INTO corpora(name,source_path,created_at) VALUES(?,?,?)", (name, source_path, now()))
            return int(con.execute("SELECT id FROM corpora WHERE name=?", (name,)).fetchone()[0])

    def queue_source_item(self, source_name: str, url: str, depth: int, parent_url: str | None = None, priority: int = 0, label: str | None = None) -> bool:
        with self.connect() as con:
            result = con.execute(
                "INSERT OR IGNORE INTO source_items(source_name,url,parent_url,depth,priority,created_at,updated_at,label) VALUES(?,?,?,?,?,?,?,?)",
                (source_name, url, parent_url, depth, priority, now(), now(), label),
            )
            inserted = result.rowcount == 1
            if not inserted:
                con.execute(
                    """UPDATE source_items SET priority=?,label=COALESCE(?,label),
                       parent_url=COALESCE(parent_url,?),updated_at=?
                       WHERE source_name=? AND url=? AND state='pending'""",
                    (priority, label, parent_url, now(), source_name, url),
                )
            return inserted

    def fetched_source_items(self, source_name: str):
        with self.connect() as con:
            return con.execute(
                "SELECT * FROM source_items WHERE source_name=? AND state='fetched' AND raw_sha256 IS NOT NULL ORDER BY id",
                (source_name,),
            ).fetchall()

    def next_source_item(self, source_name: str):
        with self.connect() as con:
            return con.execute(
                "SELECT * FROM source_items WHERE source_name=? AND state='pending' "
                "ORDER BY priority DESC,id LIMIT 1",
                (source_name,),
            ).fetchone()

    def finish_source_item(self, item_id: int, raw_sha256: str, content_type: str, final_url: str) -> None:
        with self.connect() as con:
            con.execute(
                "UPDATE source_items SET state='fetched',raw_sha256=?,content_type=?,final_url=?,error=NULL,updated_at=? WHERE id=?",
                (raw_sha256, content_type, final_url, now(), item_id),
            )

    def fail_source_item(self, item_id: int, error: str) -> None:
        with self.connect() as con:
            con.execute("UPDATE source_items SET state='failed',error=?,updated_at=? WHERE id=?", (error, now(), item_id))

    def reset_source_items(self, source_name: str, include_fetched: bool) -> int:
        condition = "state IN ('failed','fetched')" if include_fetched else "state='failed'"
        with self.connect() as con:
            result = con.execute(
                f"UPDATE source_items SET state='pending',error=NULL,updated_at=? WHERE source_name=? AND {condition}",
                (now(), source_name),
            )
            return result.rowcount

    def source_pending_count(self, source_name: str) -> int:
        with self.connect() as con:
            return int(con.execute("SELECT count(*) FROM source_items WHERE source_name=? AND state='pending'", (source_name,)).fetchone()[0])

    def add_corpus_document(self, corpus_id: int, record: dict[str, object], raw_sha256: str, text_sha256: str, passages: list[dict[str, object]], producer: str) -> bool:
        group_key = discussion_group_key(record.get("url")) or f"document:{corpus_id}:{record['external_id']}"
        with self.connect() as con:
            result = con.execute(
                "INSERT OR IGNORE INTO corpus_documents(corpus_id,external_id,title,url,author,published_at,raw_sha256,text_sha256,created_at,group_key) VALUES(?,?,?,?,?,?,?,?,?,?)",
                (corpus_id, record["external_id"], record.get("title"), record.get("url"), record.get("author"), record.get("published_at"), raw_sha256, text_sha256, now(), group_key),
            )
            if result.rowcount != 1:
                return False
            document_id = int(result.lastrowid)
            for ordinal, passage in enumerate(passages):
                inserted = con.execute(
                    "INSERT INTO passages(document_id,ordinal,text,score,signals_json,producer,created_at) VALUES(?,?,?,?,?,?,?)",
                    (document_id, ordinal, passage["text"], passage["score"], json.dumps(passage["signals"]), producer, now()),
                )
                con.execute("INSERT INTO passage_fts(passage_id,text) VALUES(?,?)", (inserted.lastrowid, passage["text"]))
            return True

    def ranked_passages(self, limit: int = 200):
        with self.connect() as con:
            return con.execute("""WITH representatives AS (
                SELECT p.id,ROW_NUMBER() OVER(PARTITION BY d.group_key ORDER BY p.score DESC,p.id) AS group_rank,
                       COUNT(*) OVER(PARTITION BY d.group_key) AS grouped_matches
                FROM passages p JOIN corpus_documents d ON d.id=p.document_id WHERE p.active=1
              ), group_documents AS (
                SELECT d.group_key,count(DISTINCT d.id) AS grouped_documents
                FROM corpus_documents d JOIN passages p ON p.document_id=d.id WHERE p.active=1 GROUP BY d.group_key
              )
              SELECT p.id,p.text,p.score,p.signals_json,d.title,d.url,d.author,d.published_at,d.external_id,c.name AS corpus_name,
                COALESCE(r.decision,'unreviewed') AS decision,COALESCE(r.notes,'') AS notes,
                representatives.grouped_matches,group_documents.grouped_documents
              FROM representatives JOIN passages p ON p.id=representatives.id
              JOIN corpus_documents d ON d.id=p.document_id JOIN corpora c ON c.id=d.corpus_id
              JOIN group_documents ON group_documents.group_key=d.group_key
              LEFT JOIN passage_reviews r ON r.passage_id=p.id
              WHERE representatives.group_rank=1
              ORDER BY CASE WHEN r.decision IS NULL OR r.decision='unreviewed' THEN 0 ELSE 1 END,p.score DESC,p.id LIMIT ?""", (limit,)).fetchall()

    def passage_review_item(self, passage_id: int | None = None):
        select = """WITH representatives AS (
            SELECT p.id,ROW_NUMBER() OVER(PARTITION BY d.group_key ORDER BY p.score DESC,p.id) AS group_rank,
                   COUNT(*) OVER(PARTITION BY d.group_key) AS grouped_matches
            FROM passages p JOIN corpus_documents d ON d.id=p.document_id WHERE p.active=1
          ), group_documents AS (
            SELECT d.group_key,count(DISTINCT d.id) AS grouped_documents
            FROM corpus_documents d JOIN passages p ON p.document_id=d.id WHERE p.active=1 GROUP BY d.group_key
          )
          SELECT p.id,p.text,p.score,p.signals_json,d.title,d.url,d.author,d.published_at,d.external_id,c.name AS corpus_name,
            COALESCE(r.decision,'unreviewed') AS decision,COALESCE(r.notes,'') AS notes,
            representatives.grouped_matches,group_documents.grouped_documents,representatives.group_rank
          FROM representatives JOIN passages p ON p.id=representatives.id
          JOIN corpus_documents d ON d.id=p.document_id JOIN corpora c ON c.id=d.corpus_id
          JOIN group_documents ON group_documents.group_key=d.group_key
          LEFT JOIN passage_reviews r ON r.passage_id=p.id"""
        with self.connect() as con:
            if passage_id is not None:
                row = con.execute(select + " WHERE p.id=?", (passage_id,)).fetchone()
                if row is not None:
                    return row
            return con.execute(
                select + " WHERE representatives.group_rank=1 AND COALESCE(r.decision,'unreviewed')='unreviewed' ORDER BY p.score DESC,p.id LIMIT 1"
            ).fetchone()

    def next_unreviewed_passage_id(self, passage_id: int) -> int | None:
        with self.connect() as con:
            current = con.execute("SELECT score,id FROM passages WHERE id=?", (passage_id,)).fetchone()
            if current is None:
                return None
            row = con.execute(
                """WITH representatives AS (
                  SELECT p.id,ROW_NUMBER() OVER(PARTITION BY d.group_key ORDER BY p.score DESC,p.id) AS group_rank
                  FROM passages p JOIN corpus_documents d ON d.id=p.document_id WHERE p.active=1
                ) SELECT p.id FROM representatives JOIN passages p ON p.id=representatives.id
                LEFT JOIN passage_reviews r ON r.passage_id=p.id
                WHERE representatives.group_rank=1 AND p.id<>? AND COALESCE(r.decision,'unreviewed')='unreviewed'
                  AND (p.score<? OR (p.score=? AND p.id>?))
                ORDER BY p.score DESC,p.id LIMIT 1""",
                (passage_id, current["score"], current["score"], current["id"]),
            ).fetchone()
            if row is None:
                row = con.execute(
                    """WITH representatives AS (
                      SELECT p.id,ROW_NUMBER() OVER(PARTITION BY d.group_key ORDER BY p.score DESC,p.id) AS group_rank
                      FROM passages p JOIN corpus_documents d ON d.id=p.document_id WHERE p.active=1
                    ) SELECT p.id FROM representatives JOIN passages p ON p.id=representatives.id
                    LEFT JOIN passage_reviews r ON r.passage_id=p.id
                    WHERE representatives.group_rank=1 AND p.id<>? AND COALESCE(r.decision,'unreviewed')='unreviewed'
                    ORDER BY p.score DESC,p.id LIMIT 1""", (passage_id,),
                ).fetchone()
            return int(row[0]) if row else None

    def passage_review_counts(self) -> dict[str, int]:
        with self.connect() as con:
            rows = con.execute(
                """WITH representatives AS (
                  SELECT p.id,ROW_NUMBER() OVER(PARTITION BY d.group_key ORDER BY p.score DESC,p.id) AS group_rank
                  FROM passages p JOIN corpus_documents d ON d.id=p.document_id WHERE p.active=1
                ) SELECT COALESCE(r.decision,'unreviewed') AS decision,count(*) AS amount
                FROM representatives LEFT JOIN passage_reviews r ON r.passage_id=representatives.id
                WHERE representatives.group_rank=1 GROUP BY decision"""
            ).fetchall()
        counts = {"unreviewed": 0, "promising": 0, "needs-research": 0, "irrelevant": 0}
        counts.update({str(row["decision"]): int(row["amount"]) for row in rows})
        counts["total"] = sum(value for key, value in counts.items() if key != "total")
        return counts

    def save_passage_review(self, passage_id: int, decision: str, notes: str) -> None:
        if decision not in {"unreviewed", "promising", "irrelevant", "needs-research"}:
            raise ValueError("invalid passage decision")
        with self.connect() as con:
            con.execute(
                "INSERT INTO passage_reviews(passage_id,decision,notes,reviewed_at) VALUES(?,?,?,?) "
                "ON CONFLICT(passage_id) DO UPDATE SET decision=excluded.decision,notes=excluded.notes,reviewed_at=excluded.reviewed_at",
                (passage_id, decision, notes, now()),
            )

    def pending(self, limit: int):
        with self.connect() as con:
            return con.execute("SELECT * FROM candidates WHERE fetch_state='pending' ORDER BY id LIMIT ?", (limit,)).fetchall()

    def record_retrieval(self, candidate_id: int, *, status_code: int | None, content_type: str | None, blob_sha256: str | None, final_url: str | None, error: str | None) -> None:
        state = "fetched" if blob_sha256 else "failed"
        with self.connect() as con:
            con.execute("INSERT INTO retrievals(candidate_id,retrieved_at,status_code,content_type,blob_sha256,final_url,error) VALUES(?,?,?,?,?,?,?)", (candidate_id, now(), status_code, content_type, blob_sha256, final_url, error))
            con.execute("UPDATE candidates SET fetch_state=? WHERE id=?", (state, candidate_id))

    def add_text(self, candidate_id: int, text: str, producer: str = "html-text/0.1") -> str:
        digest = self.put_blob(text.encode())
        with self.connect() as con:
            con.execute("INSERT OR IGNORE INTO artefacts(candidate_id,kind,producer,blob_sha256,created_at) VALUES(?,?,?,?,?)", (candidate_id, "extracted_text", producer, digest, now()))
            con.execute("INSERT INTO artefact_fts(candidate_id,text) VALUES(?,?)", (candidate_id, text))
        return digest

    def fetched_without_text(self):
        with self.connect() as con:
            return con.execute("""SELECT c.id, r.blob_sha256, r.content_type FROM candidates c JOIN retrievals r ON r.candidate_id=c.id
            WHERE c.fetch_state='fetched' AND r.blob_sha256 IS NOT NULL AND NOT EXISTS (SELECT 1 FROM artefacts a WHERE a.candidate_id=c.id AND a.kind='extracted_text')""").fetchall()

    def search(self, text: str):
        with self.connect() as con:
            return con.execute("SELECT c.id,c.url,c.source,c.title_hint,snippet(artefact_fts,1,'[',']','…',12) AS snippet FROM artefact_fts JOIN candidates c ON c.id=artefact_fts.candidate_id WHERE artefact_fts MATCH ?", (text,)).fetchall()
