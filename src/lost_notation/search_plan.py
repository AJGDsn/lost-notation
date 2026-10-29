"""The search plan: which terms to look for, in which languages, and where.

The plan is saved in the data folder (``query-selection.json``) together with
any terms the researcher has added (``custom-terms.json``). Saving it rebuilds
the hand-search checklist and re-ranks the forum and mailing-list queues; a
"batch" then runs every ticked automatic source once.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Optional

from .collector import harvest, rebuild_source_frontier
from .corpus import apply_passage_lens
from .discovery import BraveWebSearch, InternetArchiveCdx, WikimediaCommonsSearch
from .pipeline import SURVEY_TIERS, run_query_entries, survey_progress, survey_seed_file
from .queries import CONCEPT_TIERS, CONCEPTS, FIRST_PERSON, Query, generate_queries, write_queries
from .store import Store
from .term_config import load_term_group

LANGUAGE_NAMES = {"en": "English", "fr": "French", "de": "German", "es": "Spanish", "pt": "Portuguese", "it": "Italian", "ru": "Russian", "ja": "Japanese", "zh": "Chinese"}
SELECTION_FILE = "query-selection.json"
CUSTOM_TERMS_FILE = "custom-terms.json"
QUERY_FILE = "queries.jsonl"

# Every place the program can look, in the order the Search page lists them.
SEARCH_SOURCES = [
    {"key": "conlang-mirror", "name": "CONLANG mailing list, 1998–2009", "automatic": True,
     "how": "Reads the conlang mailing-list archive, subjects that use your terms first, and pulls out passages where someone describes a system of their own."},
    {"key": "zbb", "name": "Zompist forum (Conlangery)", "automatic": True,
     "how": "Reads forum threads, matching subjects first, and pulls out passages that use your terms or phrases."},
    {"key": "omniglot", "name": "Omniglot invented scripts", "automatic": True,
     "how": "Reads Omniglot's pages on constructed scripts and pulls out what their makers wrote about them."},
    {"key": "commons", "name": "Wikimedia Commons", "automatic": True,
     "how": "Searches file titles and descriptions for each ticked term, in every ticked language, plus a Chinese script-reform list. Finds scans, charts and specimens."},
    {"key": "archive", "name": "Old personal websites (Internet Archive)", "automatic": True,
     "how": "Looks through the archive's index of GeoCities, GeoCities Japan, Angelfire, Tripod, Narod (Russian), Libero (Italian), Wanadoo (French), Lycos (German), iG (Brazilian), Republika (Polish) and others for site addresses that contain a notation word. It matches addresses, not page text, and works through the most promising sections first."},
    {"key": "brave", "name": "Brave web search (automatic)", "automatic": True, "needs_key": True,
     "how": "Runs your searches through Brave's search service, each in its own language. Needs your own Brave Search API key, which costs money; without one it is skipped."},
    {"key": "web", "name": "Search engines, by hand", "automatic": False,
     "how": "A checklist of your searches with links to Google and Brave, plus Yandex for Russian, Baidu for Chinese and Yahoo! Japan for Japanese. You read the results and paste anything good straight into Leads."},
    {"key": "catalogue", "name": "Libraries and catalogues, by hand", "automatic": False,
     "how": "Record a book, manuscript, thesis or catalogue entry you found elsewhere; it goes straight into Leads and is never downloaded automatically."},
]
SOURCE_KEYS = [source["key"] for source in SEARCH_SOURCES]
DEFAULT_SOURCES = ["conlang-mirror", "zbb", "omniglot", "commons", "archive"]
COLLECTORS = {"conlang-mirror", "zbb", "omniglot"}


def brave_available() -> bool:
    return bool(os.environ.get("BRAVE_SEARCH_API_KEY"))


def load_custom_terms(data_dir: Path) -> dict[str, list[str]]:
    try:
        saved = json.loads((data_dir / CUSTOM_TERMS_FILE).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        saved = {}
    return {language: [term for term in saved.get(language, []) if isinstance(term, str) and term.strip()] for language in FIRST_PERSON}


def add_custom_term(data_dir: Path, language: str, term: str) -> bool:
    term = " ".join(term.split())
    if language not in FIRST_PERSON or not term or term in CONCEPTS[language]:
        return False
    custom = load_custom_terms(data_dir)
    if term in custom[language]:
        return False
    custom[language].append(term)
    (data_dir / CUSTOM_TERMS_FILE).write_text(json.dumps(custom, ensure_ascii=False, indent=2), encoding="utf-8")
    return True


def known_terms(custom: Optional[dict[str, list[str]]] = None) -> dict[str, list[str]]:
    custom = custom or {}
    return {language: CONCEPTS[language] + [term for term in custom.get(language, []) if term not in CONCEPTS[language]] for language in FIRST_PERSON}


def default_selection() -> dict[str, object]:
    return {
        "concepts": {language: list(CONCEPT_TIERS["focused"][language]) for language in FIRST_PERSON},
        "phrases": {language: list(phrases) for language, phrases in FIRST_PERSON.items()},
        "include_standalone": True,
        "sources": list(DEFAULT_SOURCES),
    }


def load_selection(path: Optional[Path], custom: Optional[dict[str, list[str]]] = None) -> dict[str, object]:
    if path is None or not path.exists():
        return default_selection()
    try:
        saved = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default_selection()
    terms = known_terms(custom)
    return {
        "concepts": {language: [term for term in saved.get("concepts", {}).get(language, []) if term in terms[language]] for language in FIRST_PERSON},
        "phrases": {language: [term for term in saved.get("phrases", {}).get(language, []) if term in FIRST_PERSON[language]] for language in FIRST_PERSON},
        "include_standalone": bool(saved.get("include_standalone", True)),
        "sources": [key for key in saved.get("sources", DEFAULT_SOURCES) if key in SOURCE_KEYS],
    }


def selection_from_form(form: dict[str, list[str]], custom: Optional[dict[str, list[str]]] = None) -> dict[str, object]:
    terms = known_terms(custom)
    concepts: dict[str, list[str]] = {language: [] for language in FIRST_PERSON}
    phrases: dict[str, list[str]] = {language: [] for language in FIRST_PERSON}
    for value in form.get("concept", []):
        language, separator, term = value.partition("|")
        if separator and language in terms and term in terms[language] and term not in concepts[language]:
            concepts[language].append(term)
    for value in form.get("phrase", []):
        language, separator, term = value.partition("|")
        if separator and language in FIRST_PERSON and term in FIRST_PERSON[language] and term not in phrases[language]:
            phrases[language].append(term)
    sources = [key for key in form.get("source", []) if key in SOURCE_KEYS]
    return {"concepts": concepts, "phrases": phrases, "include_standalone": "include_standalone" in form, "sources": sources}


def queries_for(selection: dict[str, object]) -> list[Query]:
    return generate_queries(concepts=selection["concepts"], phrases=selection["phrases"], include_standalone=bool(selection["include_standalone"]))


def lens_terms(selection: dict[str, object]) -> tuple[list[str], list[str]]:
    concepts = list(dict.fromkeys(term for language in FIRST_PERSON for term in selection["concepts"].get(language, [])))
    phrases = list(dict.fromkeys(term for language in FIRST_PERSON for term in selection["phrases"].get(language, [])))
    return concepts, phrases


def languages_covered(selection: dict[str, object]) -> tuple[list[str], list[str]]:
    used = [language for language in FIRST_PERSON if selection["concepts"].get(language)]
    return used, [language for language in FIRST_PERSON if language not in used]


def save_plan(store: Store, data_dir: Path, selection: dict[str, object]) -> str:
    """Save the plan, rebuild the hand-search checklist and re-rank the reading queues."""
    queries = queries_for(selection)
    write_queries(data_dir / QUERY_FILE, queries)
    (data_dir / SELECTION_FILE).write_text(json.dumps(selection, ensure_ascii=False, indent=2), encoding="utf-8")
    concepts, phrases = lens_terms(selection)
    if concepts:
        apply_passage_lens(store, concepts, phrases)
        for source in ("zbb", "conlang-mirror"):
            rebuild_source_frontier(store, source, concepts)
    used, _ = languages_covered(selection)
    return f"Saved: {sum(len(terms) for terms in selection['concepts'].values())} terms in {len(used)} languages, {len(queries)} searches on the hand-search checklist."


def searched_texts(store: Store, source_name: str, field: str) -> set[str]:
    with store.connect() as con:
        rows = con.execute("SELECT request_json FROM discovery_runs WHERE source=? AND error IS NULL", (source_name,)).fetchall()
    found = set()
    for row in rows:
        try:
            found.add(str(json.loads(row[0]).get(field, "")))
        except ValueError:
            continue
    return found


def commons_terms(selection: dict[str, object], project_root: Path) -> list[str]:
    concepts, _ = lens_terms(selection)
    try:
        chinese = load_term_group(project_root / "config" / "search_terms.yaml", "commons_chinese")
    except (OSError, ValueError):
        chinese = []
    return list(dict.fromkeys(concepts + chinese))


def run_commons(store: Store, terms: list[str], max_terms: int, source=None) -> str:
    done = searched_texts(store, "wikimedia-commons", "srsearch")
    todo = [term for term in terms if term not in done][:max_terms]
    if not todo:
        return "Wikimedia Commons: every ticked term has been searched."
    ran, found, _, failed = run_query_entries(store, source or WikimediaCommonsSearch(), [{"text": term} for term in todo], 20, True)
    return f"Wikimedia Commons: searched {ran} terms, {found} files found" + (f", {failed} errors" if failed else "") + "."


def run_brave(store: Store, query_file: Path, max_queries: int, source=None) -> str:
    if source is None and not brave_available():
        return "Brave: skipped (no BRAVE_SEARCH_API_KEY set)."
    entries = [json.loads(line) for line in query_file.read_text(encoding="utf-8").splitlines() if line.strip()] if query_file.exists() else []
    done = searched_texts(store, "brave-web-search", "q")
    todo = [entry for entry in entries if entry["text"] not in done][:max_queries]
    if not todo:
        return "Brave: every search on the checklist has been run."
    ran, found, _, failed = run_query_entries(store, source or BraveWebSearch(), todo, 10, True)
    return f"Brave: ran {ran} searches, {found} results" + (f", {failed} errors" if failed else "") + "."


def archive_progress(store: Store, project_root: Path) -> str:
    seeds = project_root / "examples" / "wayback-seeds.jsonl"
    if not seeds.exists():
        return ""
    parts = []
    for tier, totals in survey_progress(store, seeds).items():
        if totals["finished"] < totals["sections"] or tier == 1:
            parts.append(f"{SURVEY_TIERS.get(tier, f'Tier {tier}')}: {totals['finished']} of {totals['sections']} sections done")
    return "; ".join(parts[:2])


def run_batch(store: Store, data_dir: Path, project_root: Path, sources: list[str], *, pages: int = 25, archive_requests: int = 10, terms: int = 10) -> str:
    """Run every ticked automatic source once; results land in the Review inbox."""
    selection = load_selection(data_dir / SELECTION_FILE, load_custom_terms(data_dir))
    concepts, phrases = lens_terms(selection)
    lines = []
    for key in [key for key in SOURCE_KEYS if key in sources]:
        try:
            if key in COLLECTORS:
                lens = {"focus_terms": concepts, "first_person_terms": phrases} if key != "omniglot" and concepts else {}
                result = harvest(store, key, pages, **lens)
                name = next(source["name"] for source in SEARCH_SOURCES if source["key"] == key)
                lines.append(f"{name}: read {result.fetched} pages, {result.passages} passages for Review.")
            elif key == "commons":
                lines.append(run_commons(store, commons_terms(selection, project_root), terms))
            elif key == "archive":
                result = survey_seed_file(store, InternetArchiveCdx(), project_root / "examples" / "wayback-seeds.jsonl", archive_requests, True)
                slowed = " The archive asked us to slow down; try later." if result.backed_off else ""
                lines.append(f"Old websites: {result.pages} index pages scanned, {result.found} addresses found.{slowed}")
            elif key == "brave":
                lines.append(run_brave(store, data_dir / QUERY_FILE, terms))
        except Exception as exc:  # one source failing must not stop the others
            lines.append(f"{key}: stopped ({type(exc).__name__}: {exc}).")
    return " ".join(lines) or "No automatic sources are ticked."
