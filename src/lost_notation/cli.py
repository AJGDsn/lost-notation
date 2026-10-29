from __future__ import annotations

import argparse
from pathlib import Path

from .discovery import BraveWebSearch, InternetArchiveCdx, WikimediaCommonsSearch
from .pipeline import SURVEY_TIERS, discover, discover_queries, discover_term_group, export_vault, extract, fetch, import_jsonl, import_projects, survey_progress, survey_seed_file
from .review_ui import serve
from .document_ui import serve as serve_documents
from .corpus import import_corpus
from .collector import SOURCES, harvest
from .corpus_ui import serve as serve_corpus
from .workbench_ui import serve as serve_workbench
from .dashboard_ui import lens_terms, query_selection, serve as serve_dashboard
from .queries import generate_queries, write_queries
from .store import Store

def survey_progress_lines(tiers: dict[int, dict[str, int]]) -> list[str]:
    lines = []
    for tier, totals in tiers.items():
        share = f" ({totals['scanned'] / totals['pages']:.1%})" if totals["pages"] else ""
        uncounted = totals["sections"] - totals["counted"]
        pending = f"; {uncounted} not yet sized" if uncounted else ""
        lines.append(f"{SURVEY_TIERS.get(tier, f'Tier {tier}')}: {totals['finished']} of {totals['sections']} sections finished; "
                     f"{totals['scanned']:,} of {totals['pages']:,} index pages scanned{share}; {totals['matches']:,} matching addresses{pending}")
    return lines

def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(prog="lost-notation")
    root.add_argument("--data-dir", type=Path, default=Path("data"))
    commands = root.add_subparsers(dest="command", required=True)
    query = commands.add_parser("generate-queries"); query.add_argument("--output", type=Path, required=True); query.add_argument("--language", action="append")
    importer = commands.add_parser("import-discovery"); importer.add_argument("--input", type=Path, required=True)
    cdx = commands.add_parser("discover-ia", help="Discover archive captures by Wayback URL pattern")
    cdx.add_argument("--url-pattern", required=True); cdx.add_argument("--limit", type=int, default=50); cdx.add_argument("--dry-run", action="store_true")
    seeds = commands.add_parser("discover-ia-seeds", help="Scan the curated Wayback sections, most promising tier first (resumable)")
    seeds.add_argument("--input", type=Path, required=True)
    seeds.add_argument("--pages", type=int, default=25, help="archive index requests to make in this run (page scans and one-off size lookups)")
    seeds.add_argument("--dry-run", action="store_true"); seeds.add_argument("--log", type=Path, help="also append progress lines to this file")
    brave = commands.add_parser("discover-brave", help="Run generated text queries through Brave Search")
    brave.add_argument("--queries", type=Path, required=True); brave.add_argument("--limit-per-query", type=int, default=10); brave.add_argument("--max-queries", type=int, default=10); brave.add_argument("--dry-run", action="store_true")
    commons = commands.add_parser("discover-commons", help="Search open Wikimedia Commons file-description pages")
    commons.add_argument("--terms", type=Path, default=Path("config/search_terms.yaml")); commons.add_argument("--group", default="commons_chinese")
    commons.add_argument("--limit-per-query", type=int, default=10); commons.add_argument("--max-queries", type=int, default=20); commons.add_argument("--dry-run", action="store_true")
    downloader = commands.add_parser("fetch"); downloader.add_argument("--limit", type=int, default=20)
    commands.add_parser("extract")
    project = commands.add_parser("import-projects"); project.add_argument("--input", type=Path, required=True)
    vault = commands.add_parser("export-vault"); vault.add_argument("--vault", type=Path, required=True); vault.add_argument("--include-unreviewed", action="store_true")
    review = commands.add_parser("review", help="Open the local discovery-review interface")
    review.add_argument("--port", type=int, default=8765); review.add_argument("--open", action="store_true")
    workbench = commands.add_parser("workbench", help="Open the local manual web-search workbench")
    workbench.add_argument("--queries", type=Path, required=True); workbench.add_argument("--port", type=int, default=8766); workbench.add_argument("--open", action="store_true")
    documents = commands.add_parser("documents", help="Open the local extracted-document review interface")
    documents.add_argument("--port", type=int, default=8767); documents.add_argument("--open", action="store_true")
    corpus = commands.add_parser("import-corpus", help="Import local HTML, text, Markdown, JSON or JSONL corpus material")
    corpus.add_argument("--input", type=Path, required=True); corpus.add_argument("--name", required=True)
    passages = commands.add_parser("passages", help="Open the mined-passage review interface")
    passages.add_argument("--port", type=int, default=8768); passages.add_argument("--open", action="store_true")
    collect = commands.add_parser("collect-source", help="Collect the next resumable batch from a supported public corpus")
    collect.add_argument("source", choices=sorted(SOURCES)); collect.add_argument("--limit", type=int, default=25)
    collect.add_argument("--retry-errors", action="store_true"); collect.add_argument("--refresh", action="store_true")
    search = commands.add_parser("search"); search.add_argument("text")
    dashboard = commands.add_parser("dashboard", help="Open the unified local research dashboard")
    dashboard.add_argument("--port", type=int, default=8765); dashboard.add_argument("--open", action="store_true")
    dashboard.add_argument("--lan", action="store_true", help="Allow access from an iPad on the private home network")
    return root

def main() -> None:
    args = parser().parse_args()
    if args.command == "generate-queries":
        queries = generate_queries(args.language); write_queries(args.output, queries); print(f"wrote {len(queries)} queries to {args.output}"); return
    store = Store(args.data_dir)
    if args.command == "import-discovery":
        added, skipped = import_jsonl(store, args.input); print(f"imported {added}; skipped existing {skipped}")
    elif args.command == "discover-ia":
        found, added = discover(store, InternetArchiveCdx(), args.url_pattern, args.limit, args.dry_run)
        print(f"found {found} archived URL records; {'dry run only' if args.dry_run else f'imported {added}'}")
    elif args.command == "discover-ia-seeds":
        def report(line: str) -> None:
            print(line, flush=True)
            if args.log:
                with args.log.open("a", encoding="utf-8") as log: log.write(line + "\n")
        result = survey_seed_file(store, InternetArchiveCdx(), args.input, args.pages, args.dry_run, report)
        status = "dry run only" if args.dry_run else f"imported {result.added}"
        errors = f"; {result.failed} request(s) logged an error" if result.failed else ""
        report(f"scanned {result.pages} index pages; found {result.found} matching archived addresses; {status}{errors}")
        for line in survey_progress_lines(survey_progress(store, args.input)): report(line)
    elif args.command == "discover-brave":
        queries, found, added, failed = discover_queries(store, BraveWebSearch(), args.queries, args.limit_per_query, args.max_queries, args.dry_run)
        status = "dry run only" if args.dry_run else f"imported {added}"
        errors = f"; {failed} query error(s) logged" if failed else ""
        print(f"ran {queries} Brave text queries; found {found} web results; {status}{errors}")
    elif args.command == "discover-commons":
        queries, found, added, failed = discover_term_group(store, WikimediaCommonsSearch(), args.terms, args.group, args.limit_per_query, args.max_queries, args.dry_run)
        status = "dry run only" if args.dry_run else f"added {added} discovery candidates"
        errors = f"; {failed} query error(s) logged" if failed else ""
        print(f"ran {queries} Wikimedia Commons searches; found {found} file records; {status}{errors}")
    elif args.command == "fetch": print(f"fetched {fetch(store, args.limit)} candidates")
    elif args.command == "extract": print(f"extracted {extract(store)} documents")
    elif args.command == "import-projects": print(f"upserted {import_projects(store, args.input)} projects")
    elif args.command == "export-vault": print(f"wrote {export_vault(store, args.vault, not args.include_unreviewed)} vault notes")
    elif args.command == "review": serve(store, args.port, args.open)
    elif args.command == "workbench": serve_workbench(store, args.queries, args.port, args.open)
    elif args.command == "documents": serve_documents(store, args.port, args.open)
    elif args.command == "import-corpus":
        imported, skipped, candidates = import_corpus(store, args.input, args.name); print(f"imported {imported} corpus documents; skipped existing {skipped}; mined {candidates} candidate passages")
    elif args.command == "passages": serve_corpus(store, args.port, args.open)
    elif args.command == "collect-source":
        lens = {}
        if args.source in {"zbb", "conlang-mirror"}:
            concepts, phrases = lens_terms(query_selection(args.data_dir / "query-selection.json"))
            lens = {"focus_terms": concepts, "first_person_terms": phrases}
        result = harvest(store, args.source, args.limit, refresh=args.refresh, retry_errors=args.retry_errors, **lens)
        print(f"collected {result.fetched} pages; {result.failed} failed; {result.skipped} unchanged; mined {result.passages} candidate passages; {result.pending} URLs queued for a later run")
    elif args.command == "dashboard": serve_dashboard(store, args.data_dir, Path(__file__).resolve().parents[2], args.port, args.open, args.lan)
    else:
        for row in store.search(args.text): print(f"{row['id']}  {row['url']}\n  {row['snippet']}")

if __name__ == "__main__": main()
