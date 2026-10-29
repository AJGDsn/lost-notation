"""Import local corpora and rank passage-level notation-system evidence."""
from __future__ import annotations

from html.parser import HTMLParser
import json
from pathlib import Path
import re
from typing import Iterator

from .queries import CONCEPT_TIERS, FIRST_PERSON as QUERY_FIRST_PERSON
from .store import Store

PRODUCER = "passage-miner/0.2"
# English signals are hand-tuned. Other languages reuse the reviewed search
# vocabulary, so a French or Japanese forum is not scored as if it were silent.
_OTHER_LANGUAGES = [language for language in QUERY_FIRST_PERSON if language != "en"]
FIRST_PERSON = ("i designed", "i invented", "i created", "i developed", "my writing system", "my notation", "my symbols", "my glyphs", "i've been working on", "i have been working on") + tuple(dict.fromkeys(
    term.lower() for language in _OTHER_LANGUAGES for term in QUERY_FIRST_PERSON[language]
))
SPECIALIST = ("pasigraphy", "pasigraphic", "shorthand", "speedwriting", "stenography", "ideography", "ideographic", "semantic writing", "symbolic language", "visual language", "writing system", "neograph", "constructed script", "logograph") + tuple(dict.fromkeys(
    term.lower() for language in _OTHER_LANGUAGES for tier in ("focused", "expanded") for term in CONCEPT_TIERS[tier][language]
))
SUPPORTING = (
    "glyph", "symbol", "notation", "alphabet", "script", "international communication", "universal language", "compositional",
    "alfabet", "símbolo", "simbol", "glifo", "glyphe", "schrift", "notación", "notação", "notazione",
    "алфавит", "символ", "письменност", "文字", "記号", "符号", "字母",
)

class HTMLText(HTMLParser):
    def __init__(self) -> None:
        super().__init__(); self.parts: list[str] = []; self.ignored = 0
    def handle_starttag(self, tag, attrs):
        if tag in {"script", "style", "noscript"}: self.ignored += 1
        if tag in {"p", "div", "li", "br", "article", "blockquote"}: self.parts.append("\n")
    def handle_endtag(self, tag):
        if tag in {"script", "style", "noscript"}: self.ignored = max(0, self.ignored - 1)
        if tag in {"p", "div", "li", "article", "blockquote"}: self.parts.append("\n")
    def handle_data(self, data):
        if not self.ignored and data.strip(): self.parts.append(data.strip() + " ")

def html_text(raw: bytes) -> str:
    parser = HTMLText(); parser.feed(raw.decode("utf-8", errors="replace")); return "".join(parser.parts)

def records(path: Path) -> Iterator[tuple[dict[str, object], bytes, str]]:
    files = [path] if path.is_file() else sorted(item for item in path.rglob("*") if item.is_file())
    for file in files:
        suffix = file.suffix.lower(); raw = file.read_bytes(); relative = file.name if path.is_file() else str(file.relative_to(path))
        if suffix in {".html", ".htm"}:
            yield {"external_id": relative, "title": file.stem}, raw, html_text(raw)
        elif suffix in {".txt", ".md"}:
            yield {"external_id": relative, "title": file.stem}, raw, raw.decode("utf-8", errors="replace")
        elif suffix in {".jsonl", ".json"}:
            try:
                payload = json.loads(raw) if suffix == ".json" else [json.loads(line) for line in raw.decode("utf-8", errors="replace").splitlines() if line.strip()]
            except ValueError:
                continue
            items = payload if isinstance(payload, list) else payload.get("posts", payload.get("items", [payload]))
            for index, item in enumerate(items):
                if not isinstance(item, dict): continue
                text = next((item.get(key) for key in ("text", "body", "content", "message") if isinstance(item.get(key), str)), "")
                if not text: continue
                record = {"external_id": f"{relative}#{item.get('id', index)}", "title": item.get("title"), "url": item.get("url"), "author": item.get("author") or item.get("username"), "published_at": item.get("date") or item.get("created_at")}
                yield record, json.dumps(item, ensure_ascii=False).encode("utf-8"), text

def split_passages(text: str) -> list[str]:
    cleaned = re.sub(r"[ \t]+", " ", text.replace("\r", "\n"))
    blocks = [part.strip() for part in re.split(r"\n{2,}", cleaned) if len(part.strip()) >= 20]
    if len(blocks) <= 1:
        sentences = [item.strip() for item in re.split(r"(?<=[.!?])\s+|\n+", cleaned) if len(item.strip()) >= 20]
        blocks = [" ".join(sentences[i:i + 3]) for i in range(0, len(sentences), 3)]
    return [block[:2400] for block in blocks if block]

def score_passage(
    text: str,
    first_person_terms: tuple[str, ...] | list[str] | None = None,
    specialist_terms: tuple[str, ...] | list[str] | None = None,
    supporting_terms: tuple[str, ...] | list[str] | None = None,
    focus_required: bool = False,
) -> dict[str, object] | None:
    first_terms = FIRST_PERSON if first_person_terms is None else tuple(term.lower() for term in first_person_terms)
    specialist_vocabulary = SPECIALIST if specialist_terms is None else tuple(term.lower() for term in specialist_terms)
    support_vocabulary = SUPPORTING if supporting_terms is None else tuple(term.lower() for term in supporting_terms)
    lower = text.lower(); first = [term for term in first_terms if term in lower]; specialist = [term for term in specialist_vocabulary if term in lower]; supporting = [term for term in support_vocabulary if term in lower]
    if focus_required and not specialist and not (first and supporting):
        return None
    score = len(first) * 4 + len(specialist) * 3 + min(len(supporting), 3)
    if first and (specialist or supporting): score += 6
    if score < 3: return None
    return {"text": text, "score": score, "signals": {"first_person": first, "specialist": specialist, "supporting": supporting}}


def apply_passage_lens(store: Store, concepts: list[str], phrases: list[str]) -> tuple[int, int]:
    """Re-score unreviewed evidence without deleting passages or review history."""
    active = inactive = 0
    with store.connect() as con:
        rows = con.execute("""SELECT p.id,p.text FROM passages p LEFT JOIN passage_reviews r ON r.passage_id=p.id
            WHERE COALESCE(r.decision,'unreviewed')='unreviewed'""").fetchall()
        for row in rows:
            match = score_passage(row["text"], phrases, concepts, SUPPORTING, True)
            if match:
                con.execute("UPDATE passages SET active=1,score=?,signals_json=? WHERE id=?", (match["score"], json.dumps(match["signals"]), row["id"]))
                active += 1
            else:
                con.execute("UPDATE passages SET active=0 WHERE id=?", (row["id"],))
                inactive += 1
    return active, inactive

def import_corpus(store: Store, path: Path, name: str) -> tuple[int, int, int]:
    corpus_id = store.get_or_create_corpus(name, str(path.resolve())); imported = skipped = candidates = 0
    for record, raw, text in records(path):
        raw_hash = store.put_blob(raw); text_hash = store.put_blob(text.encode("utf-8")); mined = [result for passage in split_passages(text) if (result := score_passage(passage))]
        if store.add_corpus_document(corpus_id, record, raw_hash, text_hash, mined, PRODUCER): imported += 1; candidates += len(mined)
        else: skipped += 1
    return imported, skipped, candidates


def ingest_corpus_document(
    store: Store,
    corpus_id: int,
    record: dict[str, object],
    raw_hash: str,
    text_hash: str,
    mined: list[dict[str, object]],
) -> bool:
    """Shared ingestion boundary used by local and network corpus adapters."""
    return store.add_corpus_document(corpus_id, record, raw_hash, text_hash, mined, PRODUCER)
