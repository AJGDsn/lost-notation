import json
from pathlib import Path
from http.cookiejar import CookieJar
from http.server import ThreadingHTTPServer
import sqlite3
import threading
from urllib.error import HTTPError
from urllib.parse import parse_qs, urlencode, urlparse
from urllib.request import build_opener, HTTPCookieProcessor, Request, urlopen

from lost_notation.discovery import InternetArchiveCdx, parse_cdx_json
from lost_notation.pipeline import discover_queries, discover_term_group, export_vault, extract, import_jsonl, survey_progress, survey_seed_file
from lost_notation.review_ui import cdx_rows
from lost_notation.workbench_ui import load_queries, load_query_languages, page as workbench_page
from lost_notation.corpus import apply_passage_lens, import_corpus, score_passage
from lost_notation.queries import generate_queries
from lost_notation.store import MIGRATIONS, Store
from lost_notation.collector import Download, SOURCES, conlang_message_record, harvest, page_details, rebuild_source_frontier
from lost_notation.corpus_ui import page as passage_review_page
from lost_notation.dashboard_ui import JobManager, LanAccess, dashboard, handler_for
from lost_notation.desk import (
    Item, add_lead_by_hand, adopt_earlier_decisions, copy_status, copy_targets, decide, download_copies, inbox, lead, leads,
    queue_copies, saved_texts, set_aside, stage_counts, update_lead, write_vault_note,
)
from lost_notation.desk_ui import _site_copy_buttons, review_page, search_page, settled_page
from lost_notation.site_copy import copy_folder, copy_progress, copy_site, elsewhere_path, local_path
from lost_notation.search_plan import add_custom_term, default_selection, load_custom_terms, load_selection, run_commons, save_plan, selection_from_form
from lost_notation.term_config import load_term_group

def test_query_generation_has_first_person_and_languages():
    queries = generate_queries(["en", "fr"])
    assert any("I designed" in item.text for item in queries)
    assert {item.language for item in queries} == {"en", "fr"}
    assert all(item.tier == "focused" for item in queries)
    assert not any(item.concept == "constructed script" for item in queries)

def test_query_generation_covers_new_languages_and_natural_german():
    queries = generate_queries(); texts = {item.text for item in queries}
    assert '"selbst entworfen" "Kurzschrift"' in texts
    assert not any("ich habe entworfen" in text for text in texts)
    assert '"自分で作った" "創作文字"' in texts and '"я придумал" "стенография"' in texts
    assert '"neography"' in texts and {item.language for item in queries} >= {"pt", "it", "ru", "ja", "zh"}

def test_passage_scoring_recognises_non_english_evidence():
    assert score_passage("Ich habe diese Kurzschrift selbst entworfen.") is not None
    assert score_passage("これは自分で作った創作文字です。") is not None
    assert score_passage("Я придумал свой алфавит.") is not None

def test_manual_search_offers_regional_engines(tmp_path: Path):
    query_file = tmp_path / "queries.jsonl"
    query_file.write_text(json.dumps({"text": '"стенография"', "language": "ru"}) + "\n" + json.dumps({"text": '"shorthand"', "language": "en"}) + "\n", encoding="utf-8")
    html = workbench_page(load_queries(query_file), set(), languages=load_query_languages(query_file))
    assert "yandex.com/search" in html and html.count("Try Yandex") == 1

def test_query_generation_accepts_a_narrow_custom_selection():
    queries = generate_queries(
        ["en"], {"en":["constructed script"]}, {"en":["I invented"]}, False
    )
    assert [item.text for item in queries] == ['"I invented" "constructed script"']
    assert queries[0].tier == "broad"

def test_import_is_idempotent(tmp_path: Path):
    store = Store(tmp_path / "data")
    source = tmp_path / "candidates.jsonl"
    source.write_text('{"url":"https://example.test/a","source":"fixture"}\n')
    assert import_jsonl(store, source) == (1, 0)
    assert import_jsonl(store, source) == (0, 1)

def test_extract_html_and_search(tmp_path: Path):
    store = Store(tmp_path / "data")
    store.add_candidate({"url":"https://example.test/a", "source":"fixture", "discovered_at":"now"})
    digest = store.put_blob(b"<html><body>My symbolic writing system<script>ignore</script></body></html>")
    store.record_retrieval(1, status_code=200, content_type="text/html", blob_sha256=digest, final_url="https://example.test/a", error=None)
    assert extract(store) == 1
    assert len(store.search("symbolic")) == 1

def test_cdx_parsing_and_vault_export(tmp_path: Path):
    records = parse_cdx_json(b'[["timestamp","original"],["20010101000000","http://example.test/a"]]', "*.example.test/*")
    assert records[0].archive_url.startswith("https://web.archive.org/web/200101")
    store = Store(tmp_path / "data")
    store.upsert_project({"id": "lnp-1", "title": "A Script", "categories": ["ideography"], "status": "reviewed"})
    vault = tmp_path / "vault"
    assert export_vault(store, vault) == 1
    assert "[[Category - ideography]]" in next(vault.glob("*.md")).read_text(encoding="utf-8")

class PagedArchiveFixture:
    """Stands in for the Wayback CDX index: hosts and prefixes are divided into pages."""
    def __init__(self, pages, matches=None, broken=None):
        self.pages, self.matches, self.broken, self.calls, self.match_types = pages, matches or {}, broken or {}, [], []
    def count_pages(self, host, match="domain"):
        return self.pages[host]
    def scan_page(self, host, url_regex, page, match="domain"):
        from lost_notation.discovery import parse_cdx_json
        self.calls.append((host, page)); self.match_types.append(match)
        if (host, page) in self.broken:
            raise self.broken[(host, page)]
        rows = [["timestamp", "original"]] + [["20010101000000", url] for url in self.matches.get((host, page), [])]
        body = json.dumps(rows).encode()
        return parse_cdx_json(body, host), body, {"url": host, "page": page}

def write_seeds(tmp_path: Path, *hosts):
    seeds = tmp_path / "seeds.jsonl"
    seeds.write_text("".join(json.dumps({"host": host, "url_regex": ".*shorthand.*"}) + "\n" for host in hosts))
    return seeds

def test_archive_survey_rotates_hosts_and_resumes_where_it_stopped(tmp_path: Path):
    store = Store(tmp_path / "data"); seeds = write_seeds(tmp_path, "a.test", "b.test")
    source = PagedArchiveFixture({"a.test": 3, "b.test": 1}, {("a.test", 1): ["http://a.test/shorthand.html"]})
    first = survey_seed_file(store, source, seeds, 4, True)  # two size lookups and two pages
    assert source.calls == [("a.test", 0), ("b.test", 0)] and first.pages == 2 and first.found == 0
    second = survey_seed_file(store, source, seeds, 10, True)
    assert source.calls[2:] == [("a.test", 1), ("a.test", 2)] and second.found == 1
    assert survey_progress(store, seeds) == {4: {"sections": 2, "counted": 2, "finished": 2, "scanned": 4, "pages": 4, "matches": 1}}
    assert cdx_rows(store)[0]["original_url"] == "http://a.test/shorthand.html"
    assert survey_seed_file(store, source, seeds, 10, True).pages == 0 and len(source.calls) == 4

def test_archive_survey_logs_bad_pages_and_skips_one_that_keeps_failing(tmp_path: Path):
    from lost_notation.discovery import DiscoveryResponseError
    store = Store(tmp_path / "data"); seeds = write_seeds(tmp_path, "a.test")
    source = PagedArchiveFixture({"a.test": 2}, broken={("a.test", 0): DiscoveryResponseError("bad response", b"not-json", {"url": "a.test"})})
    result = survey_seed_file(store, source, seeds, 5, True)
    assert source.calls == [("a.test", 0)] * 3 + [("a.test", 1)]
    assert result.failed == 3 and result.pages == 1 and not result.backed_off
    with store.connect() as con:
        errors = [row[0] for row in con.execute("SELECT error FROM discovery_runs WHERE error IS NOT NULL ORDER BY id")]
    assert errors[:3] == ["bad response"] * 3 and errors[3].startswith("page skipped after 3 failed attempts")

def test_archive_survey_stops_when_asked_to_slow_down(tmp_path: Path):
    from urllib.error import HTTPError
    store = Store(tmp_path / "data"); seeds = write_seeds(tmp_path, "a.test", "b.test")
    source = PagedArchiveFixture({"a.test": 2, "b.test": 2}, broken={("a.test", 0): HTTPError("u", 429, "Too Many Requests", {}, None)})
    result = survey_seed_file(store, source, seeds, 10, True)
    assert result.backed_off and source.calls == [("a.test", 0)]
    assert store.archive_scan("a.test", ".*shorthand.*")["next_page"] == 0

def test_archive_survey_moves_on_when_the_index_stays_busy(tmp_path: Path):
    from urllib.error import HTTPError
    store = Store(tmp_path / "data"); seeds = write_seeds(tmp_path, "a.test", "b.test")
    source = PagedArchiveFixture({"a.test": 2, "b.test": 1}, broken={("a.test", 0): HTTPError("u", 503, "Service Unavailable", {}, None)})
    result = survey_seed_file(store, source, seeds, 6, True)
    assert not result.backed_off and source.calls == [("a.test", 0), ("b.test", 0), ("a.test", 0), ("a.test", 0)]
    assert result.pages == 1 and result.failed == 3

def test_archive_index_requests_wait_and_retry_while_busy():
    from urllib.error import HTTPError
    from lost_notation.discovery import InternetArchiveCdx
    attempts, waits = [], []
    class Response:
        def __enter__(self): return self
        def __exit__(self, *args): return False
        def read(self): return b"42\n"
    def opener(request, timeout):
        attempts.append(request.full_url)
        if len(attempts) < 3: raise HTTPError(request.full_url, 503, "Service Unavailable", {}, None)
        return Response()
    assert InternetArchiveCdx(opener=opener, sleeper=waits.append).count_pages("a.test") == 42
    assert len(attempts) == 3 and waits == [15.0, 2.0, 30.0, 2.0, 2.0]

def test_changing_a_seed_regex_restarts_that_host(tmp_path: Path):
    store = Store(tmp_path / "data"); seeds = write_seeds(tmp_path, "a.test")
    survey_seed_file(store, PagedArchiveFixture({"a.test": 1}), seeds, 5, True)
    seeds.write_text(json.dumps({"host": "a.test", "url_regex": ".*stenograf.*"}) + "\n")
    assert survey_progress(store, seeds)[4]["counted"] == 0

def test_archive_survey_finishes_promising_tiers_before_whole_sites(tmp_path: Path):
    store = Store(tmp_path / "data"); seeds = tmp_path / "seeds.jsonl"
    seeds.write_text("".join(json.dumps(seed) + "\n" for seed in [
        {"host": "a.test", "url_regex": ".*steno.*"},
        {"host": "a.test/athens/", "url_regex": ".*steno.*", "match": "prefix", "tier": 2},
        {"host": "a.test/steno", "url_regex": ".*", "match": "prefix", "tier": 1},
        {"host": "a.test/conlang", "url_regex": ".*", "match": "prefix", "tier": 1},
        {"host": "a.test/shorthand", "url_regex": ".*", "match": "prefix", "tier": 1},
    ]))
    source = PagedArchiveFixture({"a.test": 1, "a.test/athens/": 1, "a.test/steno": 2, "a.test/conlang": 0, "a.test/shorthand": 1})
    survey_seed_file(store, source, seeds, 20, True)
    assert source.calls == [("a.test/steno", 0), ("a.test/shorthand", 0), ("a.test/steno", 1), ("a.test/athens/", 0), ("a.test", 0)]
    assert source.match_types == ["prefix"] * 4 + ["domain"]
    progress = survey_progress(store, seeds)
    assert progress[1] == {"sections": 3, "counted": 3, "finished": 3, "scanned": 3, "pages": 3, "matches": 0}
    assert list(progress) == [1, 2, 4] and progress[4]["finished"] == 1

def test_one_page_written_several_ways_is_listed_once_and_images_are_dropped(tmp_path: Path):
    from lost_notation.discovery import address_key
    assert address_key("http://www.geocities.com:80/Steno/a.htm#top") == address_key("https://geocities.com/steno/a.htm?") == "geocities.com/steno/a.htm"
    store = Store(tmp_path / "data"); seeds = write_seeds(tmp_path, "a.test")
    variants = ["http://www.a.test:80/steno/", "http://a.test/steno/", "https://a.test/steno/pen.gif", "http://a.test/steno/lesson.htm"]
    survey_seed_file(store, PagedArchiveFixture({"a.test": 1}, {("a.test", 0): variants}), seeds, 5, True)
    assert [row["original_url"] for row in cdx_rows(store)] == ["http://www.a.test:80/steno/", "http://a.test/steno/lesson.htm"]

def test_sites_on_the_hidden_list_drop_out_of_review(tmp_path: Path):
    from lost_notation.review_ui import HIDDEN_SITES_FILE, discovery_rows, is_hidden, load_hidden_sites, page
    store = Store(tmp_path / "data")
    addresses = ["http://www.geocities.com/Steno83/poem.htm", "http://geocities.com/steno834/a.htm", "http://digilander.libero.it/stenoo/x.htm", "http://geocities.com/taquigra/marti.htm"]
    payload = json.dumps([["timestamp", "original"]] + [["20010101000000", url] for url in addresses]).encode()
    store.record_discovery_run("internet-archive-cdx-url-regex", {"url": "geocities.com"}, payload, len(addresses))
    hidden_file = tmp_path / "hidden.txt"
    hidden_file.write_text("# off-topic\nhttp://www.GeoCities.com/steno83/\n\n  digilander.libero.it/steno*  # names\n", encoding="utf-8")
    rows, hidden = discovery_rows(store, hidden_file)
    assert [row["original_url"] for row in rows] == ["http://geocities.com/steno834/a.htm", "http://geocities.com/taquigra/marti.htm"] and hidden == 2
    assert "2 more are hidden as off-topic" in page(rows, set(), hidden=hidden)
    assert discovery_rows(store, tmp_path / "missing.txt") == (cdx_rows(store), 0)
    shipped = load_hidden_sites(HIDDEN_SITES_FILE)
    assert is_hidden("http://www.geocities.jp/sokki_battery/bank.html", shipped)
    assert not any(is_hidden(url, shipped) for url in ["http://www.geocities.com/taquigra/marti.htm", "http://www.geocities.com/stenomelin/", "http://www.geocities.jp/steno/hayato.htm", "http://freewebs.com/conlanger56/avetglaiso_home.htm"])

def test_addresses_that_matched_only_through_javascript_are_hidden(tmp_path: Path):
    from lost_notation.review_ui import discovery_rows
    store = Store(tmp_path / "data")
    addresses = ["http://a.test/JavaScript/help.htm", "http://a.test/vbscript/x.htm", "http://a.test/javascript/shorthand.htm", "http://a.test/invented-script.htm"]
    payload = json.dumps([["timestamp", "original"]] + [["20010101000000", url] for url in addresses]).encode()
    store.record_discovery_run("internet-archive-cdx-url-regex", {"url": "a.test", "filter": ["statuscode:200", "original:(?i).*(shorthand|script).*"]}, payload, len(addresses))
    rows, hidden = discovery_rows(store, tmp_path / "none.txt")
    assert [row["original_url"] for row in rows] == ["http://a.test/javascript/shorthand.htm", "http://a.test/invented-script.htm"] and hidden == 2

def test_member_site_keys_follow_each_host_layout():
    from lost_notation.discovery import site_key
    assert site_key("http://www.geocities.com:80/Taquigra/marti.htm") == "geocities.com/taquigra"
    assert site_key("http://geocities.com/steno83") == site_key("http://geocities.com/steno83/") == "geocities.com/steno83"
    assert site_key("http://www.geocities.com/Athens/Acropolis/1234/a/b.htm") == "geocities.com/athens/acropolis/1234"
    assert site_key("http://www.angelfire.com/80s/elvira/writings/x.htm") == "angelfire.com/80s/elvira"
    assert site_key("http://www.tripod.com/bin/profile/view?Scriptese") == "tripod.com/bin"
    assert site_key("http://a.test/index.html") == "a.test"

def test_discoveries_show_one_line_per_site_and_act_on_all_its_pages(tmp_path: Path):
    from lost_notation.review_ui import form_keys, group_by_site, page
    store = Store(tmp_path / "data")
    addresses = ["http://a.test/taquigra/", "http://a.test/taquigra/marti.htm", "http://a.test/taquigra/pitman.htm", "http://a.test/steno83/poem.htm"]
    payload = json.dumps([["timestamp", "original"]] + [["20010101000000", url] for url in addresses]).encode()
    store.record_discovery_run("internet-archive-cdx-url-regex", {"url": "a.test"}, payload, len(addresses))
    rows = cdx_rows(store); keys = [row["key"] for row in rows]
    entries = group_by_site(rows, set())
    assert [(entry["site"], len(entry["rows"])) for entry in entries] == [("a.test/taquigra", 3), ("a.test/steno83", 1)]
    html = page(rows, set())
    assert html.count("name='checked'") == 2 and f"value='{' '.join(keys[:3])}'> all 3" in html and "open front page" in html
    assert "marti.htm" in html and "https://web.archive.org/web/*/a.test/taquigra/*" in html
    form = {"visible": [" ".join(keys[:3]), keys[3]], "checked": [" ".join(keys[:3])]}
    store.save_review_marks(form_keys(form, "visible"), form_keys(form, "checked"))
    assert store.checked_review_keys() == set(keys[:3])
    assert [entry["site"] for entry in group_by_site(rows, store.checked_review_keys())] == ["a.test/steno83", "a.test/taquigra"]

def test_a_skipped_last_page_finishes_its_section(tmp_path: Path):
    from lost_notation.discovery import DiscoveryResponseError
    store = Store(tmp_path / "data"); seeds = write_seeds(tmp_path, "a.test")
    source = PagedArchiveFixture({"a.test": 1}, broken={("a.test", 0): DiscoveryResponseError("bad response", b"", {"url": "a.test"})})
    survey_seed_file(store, source, seeds, 10, True)
    assert source.calls == [("a.test", 0)] * 3 and survey_progress(store, seeds)[4]["finished"] == 1

def test_text_queries_are_cached_as_discovery_runs(tmp_path: Path):
    from lost_notation.discovery import DiscoveryRecord
    class FixtureSearch:
        name = "brave-web-search"
        def discover(self, query, limit):
            result = DiscoveryRecord("https://example.test/a", "https://example.test/a", "", self.name, "now", title="Example", query=query)
            return [result], b'{"web":{"results":[]}}', {"q": query}
    queries = tmp_path / "queries.jsonl"
    queries.write_text('{"text":"my notation"}\n')
    assert discover_queries(Store(tmp_path / "data"), FixtureSearch(), queries, 3, 1, True) == (1, 1, 0, 0)

def test_text_queries_carry_their_language_to_the_search_source(tmp_path: Path):
    seen = []
    class FixtureSearch:
        name = "brave-web-search"
        def discover(self, query, limit, language=None):
            seen.append(language); return [], b'{"web":{"results":[]}}', {"q": query}
    queries = tmp_path / "queries.jsonl"
    queries.write_text(json.dumps({"text": '"Kurzschrift"', "language": "de"}) + "\n" + json.dumps({"text": '"速记"', "language": "zh"}) + "\n", encoding="utf-8")
    discover_queries(Store(tmp_path / "data"), FixtureSearch(), queries, 3, 5, True)
    assert seen == ["de", "zh"]

def test_brave_requests_use_the_query_language_and_worldwide_results(monkeypatch):
    import lost_notation.discovery as discovery
    requests = []
    class Response:
        def __enter__(self): return self
        def __exit__(self, *args): return False
        def read(self): return b'{"web":{"results":[]}}'
    def fake_urlopen(request, timeout):
        requests.append(request.full_url); return Response()
    monkeypatch.setattr(discovery, "urlopen", fake_urlopen); monkeypatch.setattr(discovery, "sleep", lambda _: None)
    source = discovery.BraveWebSearch(api_key="test-key")
    source.discover('"pasigraphie"', 5, "fr"); source.discover('"速记"', 5, "zh"); source.discover('"x"', 5)
    assert "search_lang=fr" in requests[0] and "country=ALL" in requests[0]
    assert "search_lang=zh-hans" in requests[1] and "search_lang" not in requests[2]

def test_commons_waits_and_retries_when_rate_limited():
    from urllib.error import HTTPError
    from lost_notation.discovery import WikimediaCommonsSearch
    attempts, waits = [], []
    class Response:
        def __enter__(self): return self
        def __exit__(self, *args): return False
        def read(self): return b'{"query":{"search":[{"pageid":9,"title":"File:A.png"}]}}'
    def opener(request, timeout):
        attempts.append(request.get_header("User-agent"))
        if len(attempts) < 3: raise HTTPError(request.full_url, 429, "Too Many Requests", {"Retry-After": "7"}, None)
        return Response()
    records, _, _ = WikimediaCommonsSearch(opener=opener, sleeper=waits.append).discover("速记", 5)
    assert len(records) == 1 and len(attempts) == 3 and waits[:2] == [7.0, 7.0]
    assert attempts[0].startswith("LostNotationResearch/")

def test_text_queries_stop_when_a_source_stays_rate_limited(tmp_path: Path):
    from urllib.error import HTTPError
    calls = []
    class LimitedSearch:
        name = "wikimedia-commons"
        def discover(self, query, limit):
            calls.append(query); raise HTTPError("u", 429, "Too Many Requests", {}, None)
    queries = tmp_path / "queries.jsonl"
    queries.write_text('{"text":"one"}\n{"text":"two"}\n')
    assert discover_queries(Store(tmp_path / "data"), LimitedSearch(), queries, 3, 5, True) == (2, 0, 0, 1)
    assert calls == ["one"]

def test_editable_term_group_drives_a_discovery_source(tmp_path: Path):
    terms = tmp_path / "terms.yaml"
    terms.write_text("commons_chinese:\n  - 速记\n  - 注音字母\n", encoding="utf-8")
    assert load_term_group(terms, "commons_chinese") == ["速记", "注音字母"]
    class FixtureCommons:
        name = "wikimedia-commons"
        def discover(self, query, limit):
            from lost_notation.discovery import DiscoveryRecord
            record = DiscoveryRecord("https://commons.wikimedia.org/wiki/File:Example", "https://commons.wikimedia.org/wiki/File:Example", "", self.name, "now", title="File:Example", query=query, source_id="12", access_status="open", has_fulltext=True, has_graphics=True)
            return [record], b'{"query":{"search":[]}}', {"srsearch": query}
    assert discover_term_group(Store(tmp_path / "data"), FixtureCommons(), terms, "commons_chinese", 3, 1, True) == (1, 1, 0, 0)

def test_review_rows_rehydrate_saved_cdx_response(tmp_path: Path):
    store = Store(tmp_path / "data")
    store.record_discovery_run("internet-archive-cdx-url-regex", {"url": "example.test"}, b'[["timestamp","original"],["20010101000000","http://example.test/a"]]', 1)
    rows = cdx_rows(store)
    assert rows[0]["original_url"] == "http://example.test/a"

def test_review_rows_rehydrate_saved_commons_response(tmp_path: Path):
    store = Store(tmp_path / "data")
    store.record_discovery_run("wikimedia-commons", {"srsearch": "速记"}, '{"query":{"search":[{"pageid":9,"title":"File:速记.png"}]}}'.encode("utf-8"), 1)
    rows = cdx_rows(store)
    assert rows[0]["source"] == "wikimedia-commons"
    assert rows[0]["source_id"] == "9" and rows[0]["access_status"] == "open"

def test_manual_finding_aid_is_held_out_of_downloads(tmp_path: Path):
    store = Store(tmp_path / "data")
    assert store.add_candidate({"url":"https://catalogue.example/item/1", "source":"Library catalogue", "discovered_at":"now", "fetch_state":"manual-acquisition", "access_status":"institutional", "source_id":"call-1", "notes":"Read in library."})
    assert store.pending(20) == []
    with store.connect() as con:
        saved = con.execute("SELECT source_id,access_status,provenance_notes,fetch_state FROM candidates").fetchone()
    assert tuple(saved) == ("call-1", "institutional", "Read in library.", "manual-acquisition")
    assert add_lead_by_hand(store, "https://catalogue.example/item/2", "Call 2", catalogue=True, source="A library")
    assert copy_targets(store, lead(store, "url:catalogue.example/item/2")) == []

def test_review_marks_are_persisted_and_can_be_cleared(tmp_path: Path):
    store = Store(tmp_path / "data")
    store.save_review_marks({"1:0", "1:1"}, {"1:0"})
    assert store.checked_review_keys() == {"1:0"}
    store.save_review_marks({"1:0", "1:1"}, set())
    assert store.checked_review_keys() == set()

def test_manual_workbench_tracks_checked_queries(tmp_path: Path):
    query_file = tmp_path / "queries.jsonl"
    query_file.write_text('{"text":"my shorthand"}\n')
    assert load_queries(query_file) == ["my shorthand"]
    store = Store(tmp_path / "data")
    store.mark_manual_query_checked("my shorthand")
    assert store.checked_manual_queries() == {"my shorthand"}

def test_manual_queue_reset_only_clears_the_current_selection(tmp_path: Path):
    store = Store(tmp_path / "data")
    store.mark_manual_query_checked("my shorthand")
    store.mark_manual_query_checked("older ideography search")
    assert store.reset_manual_query_checks(["my shorthand", "not checked"]) == 1
    assert store.checked_manual_queries() == {"older ideography search"}

def test_document_review_is_persisted(tmp_path: Path):
    store = Store(tmp_path / "data")
    store.add_candidate({"url": "https://example.test/a", "source": "test", "discovered_at": "now"})
    store.save_document_review(1, "promising", "Likely original shorthand.")
    with store.connect() as con:
        row = con.execute("SELECT decision,notes FROM document_reviews WHERE candidate_id=1").fetchone()
    assert tuple(row) == ("promising", "Likely original shorthand.")

def test_cli_exposes_fetch_command():
    import lost_notation.cli as cli
    assert callable(cli.fetch)

def test_corpus_import_is_idempotent_and_mines_passages(tmp_path: Path):
    corpus = tmp_path / "forum"
    corpus.mkdir()
    (corpus / "thread.jsonl").write_text('{"id":"p1","author":"Ada","url":"https://forum.test/p1","text":"I invented my writing system to communicate using semantic glyphs."}\n')
    store = Store(tmp_path / "data")
    assert import_corpus(store, corpus, "fixture forum") == (1, 0, 1)
    assert import_corpus(store, corpus, "fixture forum") == (0, 1, 0)
    rows = store.ranked_passages()
    assert rows[0]["author"] == "Ada" and rows[0]["score"] >= 10

def test_passage_scoring_rejects_generic_script_mentions():
    assert score_passage("Run this JavaScript file to load the page.") is None
    assert score_passage("I designed my shorthand notation for faster writing.") is not None

def test_passage_scoring_can_use_the_selected_research_lens():
    assert score_passage(
        "I designed my notation for rapid writing.",
        [], ["pasigraphy"], focus_required=True,
    ) is None
    assert score_passage(
        "This pasigraphy was designed for international communication.",
        [], ["pasigraphy"], focus_required=True,
    ) is not None

def test_applying_a_lens_refilters_without_deleting_passages(tmp_path: Path):
    corpus = tmp_path / "lens.jsonl"
    corpus.write_text(
        '{"id":"one","text":"I invented my shorthand notation for rapid writing."}\n'
        '{"id":"two","text":"I designed my symbolic language using semantic glyphs."}\n'
    )
    store = Store(tmp_path / "data"); import_corpus(store, corpus, "lens fixture")
    assert apply_passage_lens(store, ["shorthand"], []) == (1, 1)
    assert len(store.ranked_passages()) == 1 and "shorthand" in store.ranked_passages()[0]["text"]
    assert apply_passage_lens(store, ["symbolic language"], []) == (1, 1)
    assert len(store.ranked_passages()) == 1 and "symbolic language" in store.ranked_passages()[0]["text"]
    with store.connect() as con:
        assert con.execute("SELECT count(*) FROM passages").fetchone()[0] == 2

def test_omniglot_harvest_is_resumable_and_mines_pages(tmp_path: Path):
    pages = {
        "https://www.omniglot.com/conscripts/": b'<a href="example.htm">Example</a>',
        "https://www.omniglot.com/conscripts/example.htm": b'<title>Example</title><p>I invented my writing system using semantic glyphs.</p>',
    }
    calls = []
    def fixture_fetch(url):
        calls.append(url)
        return Download(pages[url], url, "text/html")
    store = Store(tmp_path / "data")
    first = harvest(store, "omniglot", 2, fetcher=fixture_fetch, sleep=lambda _: None)
    assert first.fetched == 2 and first.passages == 1 and first.pending == 0
    second = harvest(store, "omniglot", 2, fetcher=fixture_fetch, sleep=lambda _: None)
    assert second.fetched == 0 and len(calls) == 2
    assert store.ranked_passages()[0]["title"] == "Example"

def test_zbb_links_drop_session_ids_and_exclude_other_forums():
    raw = b'''<a href="./viewtopic.php?t=20&amp;sid=temporary">Topic</a>
        <a href="./viewforum.php?f=1&amp;sid=temporary">Other forum</a>'''
    _, links = page_details(raw, "https://www.verduria.org/viewforum.php?f=3", SOURCES["zbb"])
    assert [link.url for link in links] == ["https://www.verduria.org/viewtopic.php?t=20"]

def test_selected_terms_reprioritise_an_existing_zbb_frontier(tmp_path: Path):
    store = Store(tmp_path / "data")
    root = "https://www.verduria.org/viewforum.php?f=3"
    store.queue_source_item("zbb", root, 0)
    item = store.next_source_item("zbb")
    body = b'''<a href="./viewtopic.php?t=10">General discussion</a>
        <a href="./viewtopic.php?t=20">My pasigraphy project</a>'''
    raw_hash = store.put_blob(body)
    store.finish_source_item(item["id"], raw_hash, "text/html", root)
    assert rebuild_source_frontier(store, "zbb", ["pasigraphy"]) == 2
    next_item = store.next_source_item("zbb")
    assert next_item["url"].endswith("t=20") and next_item["priority"] >= 1000

def test_conlang_mirror_prioritises_notation_subjects_and_extracts_metadata(tmp_path: Path):
    root = "http://archives.conlang.info/"
    month = root + "2009-04.html"
    message = root + "zhae/kuate/faefaphal.html"
    pages = {
        root: b'<a href="2009-04.html">April</a><a href="2009-03.html">March</a>',
        month: b'<a href="zhae/kuate/faefaphal.html">My new ideographic script</a><a href="wha/ganqhei/qhuengaphal.html">Meeting</a>',
        message: b'''<title>Conlang: My new notation (Ada, Apr 8 '09, 2:23)</title>
            <p>April 2009 Index</p><p>I invented my writing system using semantic glyphs.</p>
            <blockquote>Quoted earlier discussion about another script.</blockquote>''',
    }
    calls = []
    def fixture_fetch(url):
        calls.append(url)
        return Download(pages[url], url, "text/html")
    store = Store(tmp_path / "data")
    result = harvest(store, "conlang-mirror", 3, fetcher=fixture_fetch, sleep=lambda _: None)
    assert calls == [root, month, message]
    assert result.passages == 1
    row = store.ranked_passages()[0]
    assert row["author"] == "Ada" and row["published_at"] == "Apr 8 '09, 2:23"
    assert "Quoted earlier" not in row["text"]

def test_passage_review_has_next_result_and_progress(tmp_path: Path):
    corpus = tmp_path / "forum.jsonl"
    corpus.write_text(
        '{"id":"one","text":"I invented my shorthand notation for rapid writing."}\n'
        '{"id":"two","text":"I designed my writing system with semantic glyphs."}\n'
    )
    store = Store(tmp_path / "data")
    import_corpus(store, corpus, "review fixture")
    current = store.passage_review_item()
    next_id = store.next_unreviewed_passage_id(current["id"])
    assert next_id is not None and next_id != current["id"]
    html = passage_review_page(store, current["id"])
    assert "Save and show next" in html and "Next result without saving" in html
    assert "0 reviewed · 2 remaining · 2 total" in html
    store.save_passage_review(current["id"], "promising", "Good lead")
    assert store.passage_review_item()["id"] == next_id

def cdx_payload(*addresses: str) -> bytes:
    return json.dumps([["timestamp", "original"]] + [[f"200{index + 1}0101000000", url] for index, url in enumerate(addresses)]).encode()

def test_home_page_shows_the_four_stages_and_points_to_waiting_finds(tmp_path: Path):
    store = Store(tmp_path / "data")
    html = dashboard(store, JobManager())
    assert all(f"{number}</span>{name}" in html for number, name in [("1", "Search"), ("2", "Review"), ("3", "Leads"), ("4", "Collection")])
    assert "action='/search/run'" in html and "Find more material" in html
    store.record_discovery_run("internet-archive-cdx-url-regex", {"url": "a.test"}, cdx_payload("http://a.test/steno/", "http://a.test/shorthand/"), 2)
    html = dashboard(store, JobManager())
    assert "Review the new finds" in html and "<span class='step'>2</span>Review<span class='badge'>2</span>" in html

def test_search_page_lists_every_language_and_names_the_ones_left_out():
    selection = default_selection()
    selection["concepts"] = {language: terms if language in {"en", "fr"} else [] for language, terms in selection["concepts"].items()}
    html = search_page(selection, {"ru": ["скоропись"]}, {"web": "3 of 13 searches done"}, (3, 13), {}, {})
    assert "Left out:</strong> German, Spanish, Portuguese, Italian, Russian, Japanese, Chinese" in html
    assert "стенография" in html and "速記" in html and "скоропись" in html and "Yandex" in html
    assert "name='source' value='commons' checked" in html and "3 of 13 searches done" in html

def test_search_plan_keeps_added_terms_and_ticked_sources(tmp_path: Path):
    data = tmp_path / "data"; store = Store(data)
    assert add_custom_term(data, "ru", "скоропись") and not add_custom_term(data, "ru", "скоропись") and not add_custom_term(data, "en", "shorthand")
    custom = load_custom_terms(data)
    form = {"concept": ["ru|скоропись", "en|shorthand", "en|not offered"], "phrase": ["en|I invented"], "source": ["commons", "bogus"], "include_standalone": ["on"]}
    selection = selection_from_form(form, custom)
    assert selection["concepts"]["ru"] == ["скоропись"] and selection["concepts"]["en"] == ["shorthand"] and selection["sources"] == ["commons"]
    assert "2 terms in 2 languages" in save_plan(store, data, selection)
    assert load_selection(data / "query-selection.json", custom)["concepts"]["ru"] == ["скоропись"]
    texts = [json.loads(line)["text"] for line in (data / "queries.jsonl").read_text(encoding="utf-8").splitlines()]
    assert '"скоропись"' in texts and '"I invented" "shorthand"' in texts

def test_commons_batches_search_each_term_once(tmp_path: Path):
    class FakeCommons:
        name = "wikimedia-commons"
        def __init__(self): self.calls = []
        def discover(self, query, limit):
            self.calls.append(query); return [], b'{"query":{"search":[]}}', {"srsearch": query}
    store = Store(tmp_path / "data"); source = FakeCommons()
    assert "searched 2 terms" in run_commons(store, ["速记", "стенография"], 10, source)
    assert "every ticked term" in run_commons(store, ["速记", "стенография"], 10, source)
    assert source.calls == ["速记", "стенография"]

def test_review_inbox_gathers_every_source_and_decisions_move_finds_on(tmp_path: Path):
    store = Store(tmp_path / "data"); corpus = tmp_path / "one.jsonl"
    corpus.write_text('{"id":"one","text":"I invented my shorthand notation for rapid writing."}\n'); import_corpus(store, corpus, "fixture")
    store.record_discovery_run("internet-archive-cdx-url-regex", {"url": "a.test/taquigra"}, cdx_payload("http://a.test/taquigra/", "http://a.test/taquigra/marti.htm"), 2)
    store.record_discovery_run("wikimedia-commons", {"srsearch": "速记"}, '{"query":{"search":[{"pageid":9,"title":"File:速记.png"}]}}'.encode("utf-8"), 1)
    items = inbox(store, tmp_path / "none.txt")
    assert [item.kind for item in items] == ["passage", "site", "file"]
    assert items[1].key == "site:a.test/taquigra" and items[1].details["pages"] == 2 and items[1].details["years"] == "2001–2002"
    assert "Special:FilePath/%E9%80%9F%E8%AE%B0.png?width=480" in review_page(items, items[2], "", {}, {})
    decide(store, items[0], "lead", "Strong first-person claim"); decide(store, items[1], "rejected"); decide(store, items[2], "later")
    assert [(item.kind, item.later) for item in inbox(store, tmp_path / "none.txt")] == [("file", True)]
    assert store.passage_review_counts()["promising"] == 1
    assert [(row["item_key"], row["notes"]) for row in leads(store)] == [(items[0].key, "Strong first-person claim")]
    assert stage_counts(store)["rejected"] == 1

def test_earlier_decisions_become_leads_once(tmp_path: Path):
    store = Store(tmp_path / "data"); corpus = tmp_path / "one.jsonl"
    corpus.write_text('{"id":"one","text":"I invented my shorthand notation for rapid writing."}\n'); import_corpus(store, corpus, "fixture")
    passage = store.passage_review_item()["id"]; store.save_passage_review(passage, "needs-research", "check the thread")
    store.add_candidate({"url": "https://example.test/my-script", "source": "manual-web-search", "discovered_at": "now", "query": '"neography"', "title": "My script"})
    assert adopt_earlier_decisions(store) >= 2
    assert {row["item_key"] for row in leads(store)} == {f"passage:{passage}", "url:example.test/my-script"}
    assert adopt_earlier_decisions(store) == 0
    update_lead(store, f"passage:{passage}", decision="rejected"); adopt_earlier_decisions(store)
    assert lead(store, f"passage:{passage}")["decision"] == "rejected"

class FakeArchive:
    """Answers index queries and raw (id_) capture requests the way the Wayback Machine does."""
    def __init__(self, listings, bodies):
        self.listings = listings; self.bodies = bodies; self.fetched = []
    def __call__(self, request, timeout=None):
        url = request.full_url
        if url.startswith("https://web.archive.org/cdx/"):
            query = parse_qs(urlparse(url).query)
            rows = self.listings.get((query["url"][0], query["matchType"][0]), [])
            return FakeBody(json.dumps([["timestamp", "original", "mimetype", "digest"]] + [list(row) + ["x"] for row in rows] if rows else []).encode())
        timestamp, original = url.split("/web/", 1)[1].split("id_/", 1)
        self.fetched.append((timestamp, original))
        if (timestamp, original) not in self.bodies:
            raise HTTPError(url, 404, "Not Found", {}, None)
        return FakeBody(self.bodies[(timestamp, original)])

class FakeBody:
    status = 200; url = ""
    class headers:
        @staticmethod
        def get_content_type(): return "text/html"
    def __init__(self, body): self.body = body
    def __enter__(self): return self
    def __exit__(self, *exc): return False
    def read(self, size=-1): return self.body

TAQUIGRA_INDEX = ('<html><head><link rel=stylesheet href="style.css"></head><body background="fondo.gif">'
                  '<a href="marti.htm">Martí</a> <a href="http://www.geocities.com/taquigra/gone.htm">gone</a> <img src="fotos/marti.jpg">'
                  "<img src='fotos/lost.gif'> <img src=\"http://pics.example.test/glyph.gif\"> <a href=\"mailto:x@example.test\">mail</a>"
                  ' <a href="#top">top</a> <a href="http://other.example.test/">elsewhere</a> <img src="http://visit.geocities.yahoo.com/visit.gif?us125">'
                  '</body></html>')

def taquigra_archive() -> FakeArchive:
    site = "http://www.geocities.com:80/taquigra/"
    return FakeArchive(
        {("geocities.com/taquigra/", "prefix"): [
            ("20010101000000", site, "text/html"), ("20050101000000", site, "text/html"),
            ("20020101000000", site + "marti.htm", "text/html"), ("20020101000000", site + "style.css", "text/css"),
            ("20030101000000", site + "fotos/marti.jpg", "image/jpeg"), ("20060101000000", site + "fotos/marti.jpg", "image/jpeg"),
            ("20040101000000", site + "fotos/lost.gif", "text/html")],
         ("geocities.com/taquigra", "exact"): [("20000101000000", "http://geocities.com/taquigra", "text/html")],
         ("oocities.org/taquigra/", "prefix"): [("20190101000000", "http://www.oocities.org/taquigra/fotos/lost.gif", "image/gif")],
         ("http://pics.example.test/glyph.gif", "exact"): [("20030505000000", "http://pics.example.test/glyph.gif", "image/gif")]},
        {("20050101000000", site): TAQUIGRA_INDEX.encode("latin-1"),
         ("20020101000000", site + "marti.htm"): '<html><head><meta charset="iso-8859-1"></head><body><p>Taquigrafía de Martí</p><a href="./">home</a></body></html>'.encode("latin-1"),
         ("20020101000000", site + "style.css"): b"body{background:url('fotos/marti.jpg')}",
         ("20060101000000", site + "fotos/marti.jpg"): b"<html>error</html>",
         ("20030101000000", site + "fotos/marti.jpg"): b"\xff\xd8JPEG",
         ("20190101000000", "http://www.oocities.org/taquigra/fotos/lost.gif"): b"GIF89a-from-the-mirror",
         ("20030505000000", "http://pics.example.test/glyph.gif"): b"GIF89a-glyph"},
    )

def kept_site(store: Store, tmp_path: Path, site: str = "geocities.com/taquigra"):
    store.record_discovery_run("internet-archive-cdx-url-regex", {"url": site}, cdx_payload(f"http://www.{site}/"), 1)
    item = inbox(store, tmp_path / "none.txt")[0]; decide(store, item, "lead", "Spanish shorthand society")
    update_lead(store, item.key, stage="collection")
    return item

def test_a_complete_copy_combines_every_capture_and_links_the_pages_together(tmp_path: Path):
    store = Store(tmp_path / "data"); item = kept_site(store, tmp_path); archive = taquigra_archive()
    folder = copy_folder(tmp_path / "data", "geocities.com/taquigra")
    said = copy_site(store, item.key, "geocities.com/taquigra", folder, cdx=InternetArchiveCdx(opener=archive, sleeper=lambda s: None), opener=archive, sleeper=lambda s: None)
    assert "6 of 6 files saved" in said and "2 found outside the site's own captures" in said and "1 pictures the pages show were not found" in said
    assert (folder / "fotos" / "marti.jpg").read_bytes() == b"\xff\xd8JPEG"  # the newest capture was an error page, so an older one was used
    assert (folder / "fotos" / "lost.gif").read_bytes() == b"GIF89a-from-the-mirror"
    assert (folder / "_elsewhere" / "pics.example.test" / "glyph.gif").read_bytes() == b"GIF89a-glyph"
    page = (folder / "index.html").read_text(encoding="latin-1")
    assert 'href="marti.htm"' in page and 'src="fotos/marti.jpg"' in page and 'src="fotos/lost.gif"' in page and 'src="_elsewhere/pics.example.test/glyph.gif"' in page
    assert 'href="https://web.archive.org/web/20050101000000/http://www.geocities.com/taquigra/gone.htm"' in page
    assert 'href="https://web.archive.org/web/20050101000000/http://other.example.test/"' in page and 'href="mailto:x@example.test"' in page and 'href="#top"' in page
    assert 'href="index.html"' in (folder / "marti.htm").read_text(encoding="latin-1") and "url(fotos/marti.jpg)" in (folder / "style.css").read_text()
    contents = (folder / "_contents.html").read_text(encoding="utf-8")
    assert "from the OoCities mirror" in contents and "Pictures not found" in contents and "taquigra/fondo.gif" in contents
    progress = copy_progress(folder)
    assert (progress["saved"], progress["pages"], progress["pictures"], progress["missing"]) == (6, 2, 3, 1)
    assert copy_status(store, item.key)["texts"] == 2 and any("Taquigrafía de Martí" in text["text"] for text in saved_texts(store, item.key))
    fetched = len(archive.fetched)
    assert "(0 new" in copy_site(store, item.key, "geocities.com/taquigra", folder, opener=archive, sleeper=lambda s: None)
    assert len(archive.fetched) == fetched and (folder / "index.html").read_text(encoding="latin-1") == page
    note = write_vault_note(store, item.key, tmp_path / "vault", folder); body = note.read_text(encoding="utf-8")
    assert "Spanish shorthand society" in body and "Your own copy: [front page](<file:" in body and "## My notes" in body
    assert len(list((note.parent / f"{note.stem} - saved pages").glob("*.md"))) == 2
    note.write_text(body.replace("_Write your own notes here; they are kept when this note is refreshed._", "Compare with Martí 1803."), encoding="utf-8")
    assert "Compare with Martí 1803." in write_vault_note(store, item.key, tmp_path / "vault", folder).read_text(encoding="utf-8")

def test_a_copy_stops_politely_and_finishes_later_when_the_archive_asks_to_slow_down(tmp_path: Path):
    store = Store(tmp_path / "data"); item = kept_site(store, tmp_path); archive = taquigra_archive(); waits = []
    folder = copy_folder(tmp_path / "data", "geocities.com/taquigra")
    def busy(request, timeout=None):
        if "id_/" in request.full_url and request.full_url.endswith("marti.htm"):
            raise HTTPError(request.full_url, 429, "Too Many Requests", {}, None)
        return archive(request, timeout)
    said = copy_site(store, item.key, "geocities.com/taquigra", folder, cdx=InternetArchiveCdx(opener=archive, sleeper=lambda s: None), opener=busy, sleeper=waits.append)
    assert "Stopped because the archive asked us to slow down" in said and 60.0 in waits and 120.0 in waits
    assert copy_progress(folder)["waiting"] == 3 and (folder / "index.html").exists()
    assert "6 of 6 files saved" in copy_site(store, item.key, "geocities.com/taquigra", folder, cdx=InternetArchiveCdx(opener=archive, sleeper=lambda s: None), opener=archive, sleeper=lambda s: None)

def test_a_finished_copy_that_was_never_searched_can_look_for_missing_pictures(tmp_path: Path):
    store = Store(tmp_path / "data"); item = kept_site(store, tmp_path); archive = taquigra_archive()
    folder = copy_folder(tmp_path / "data", "geocities.com/taquigra")
    def no_mirror(request, timeout=None):
        if "url=oocities.org" in request.full_url:
            raise HTTPError(request.full_url, 500, "Server Error", {}, None)
        return archive(request, timeout)
    said = copy_site(store, item.key, "geocities.com/taquigra", folder, cdx=InternetArchiveCdx(opener=no_mirror, sleeper=lambda s: None), opener=archive, sleeper=lambda s: None)
    assert "4 of 4 files saved" in said and "look for them again later" in said
    progress = copy_progress(folder); copy = {"progress": progress}
    assert progress["searched"] == 0 and "value='sitecopy'" in _site_copy_buttons(copy, "") and "Look for missing pictures" in _site_copy_buttons(copy, "")
    said = copy_site(store, item.key, "geocities.com/taquigra", folder, cdx=InternetArchiveCdx(opener=archive, sleeper=lambda s: None), opener=archive, sleeper=lambda s: None)
    assert "6 of 6 files saved (2 new, 2 found outside the site's own captures)" in said and "again later" not in said
    progress = copy_progress(folder)
    assert (progress["searched"], progress["missing"]) == (1, 1) and "sitecopy" not in _site_copy_buttons({"progress": progress}, "")
    assert 'src="fotos/lost.gif"' in (folder / "index.html").read_text(encoding="latin-1")

def test_web_page_leads_are_saved_and_their_text_extracted(tmp_path: Path):
    store = Store(tmp_path / "data"); key = "url:example.test/my-script"
    add_lead_by_hand(store, "https://example.test/my-script", "My script"); update_lead(store, key, stage="collection")
    assert queue_copies(store, key) == 1
    page = FakeBody(b"<html><body><p>My own script, invented in 1998</p></body></html>")
    assert download_copies(store, [key], opener=lambda request, timeout: page, sleeper=lambda seconds: None) == (1, 0)
    assert "invented in 1998" in saved_texts(store, key)[0]["text"]

def test_copied_addresses_stay_inside_the_copy_folder():
    assert local_path("http://www.geocities.com:80/Taquigra/Fotos/A%20B.JPG", "geocities.com/taquigra") == "fotos/a b.jpg"
    assert local_path("http://geocities.com/taquigra", "geocities.com/taquigra") == "index.html"
    assert local_path("http://geocities.com/taquigra/../../etc/x.htm", "geocities.com/taquigra") == "etc/x.htm"
    assert local_path("http://geocities.com/taquigrafia/x.htm", "geocities.com/taquigra") is None
    assert local_path("http://geocities.com/taquigra/con.htm", "geocities.com/taquigra") == "_con.htm"
    assert local_path("http://geocities.com/taquigra/list.cgi?a=1", "geocities.com/taquigra").startswith("list_")
    assert elsewhere_path("http://Pics.Example.test:80/a/b.gif") == "_elsewhere/pics.example.test/a/b.gif"

def test_too_little_survives_keeps_thin_finds_in_their_own_list(tmp_path: Path):
    store = Store(tmp_path / "data"); corpus = tmp_path / "one.jsonl"
    corpus.write_text('{"id":"one","text":"I invented my shorthand notation for rapid writing."}\n'); import_corpus(store, corpus, "fixture")
    store.record_discovery_run("internet-archive-cdx-url-regex", {"url": "geocities.com"}, cdx_payload("http://geocities.com/taquigra/", "http://geocities.com/stenox/"), 2)
    items = {item.key: item for item in inbox(store, tmp_path / "none.txt")}
    passage = next(key for key in items if key.startswith("passage:"))
    decide(store, items["site:geocities.com/taquigra"], "trace", "Only the front page survives")
    decide(store, items["site:geocities.com/stenox"], "rejected", "Not enough archived"); decide(store, items[passage], "trace")
    assert inbox(store, tmp_path / "none.txt") == [] and store.passage_review_counts()["needs-research"] == 1
    assert (stage_counts(store)["trace"], stage_counts(store)["rejected"], leads(store)) == (2, 1, [])
    html = settled_page(set_aside(store, "trace"), "trace", stage_counts(store))
    assert "Only the front page survives" in html and "https://www.oocities.org/taquigra/" in html and "Keep as a lead after all" in html
    update_lead(store, "site:geocities.com/stenox", decision="trace")
    assert [row["item_key"] for row in set_aside(store, "trace")][0] == "site:geocities.com/stenox" and set_aside(store, "rejected") == []
    find = Item("url:x.test", "web", "Brave web search", "x", "http://x.test/")
    assert "value='trace'" in review_page([find], find, "", {}, {}) and "t:'trace'" in review_page([find], find, "", {}, {})

def test_dashboard_files_thin_finds_and_serves_a_copy_safely(tmp_path: Path):
    store = Store(tmp_path / "data"); item = kept_site(store, tmp_path); archive = taquigra_archive()
    copy_site(store, item.key, "geocities.com/taquigra", copy_folder(tmp_path / "data", "geocities.com/taquigra"),
              cdx=InternetArchiveCdx(opener=archive, sleeper=lambda s: None), opener=archive, sleeper=lambda s: None)
    store.record_discovery_run("internet-archive-cdx-url-regex", {"url": "a.test"}, cdx_payload("http://a.test/steno/"), 1)
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler_for(store, tmp_path / "data", tmp_path, JobManager()))
    thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
    base = f"http://127.0.0.1:{server.server_port}"
    try:
        data = urlencode({"key": "site:a.test/steno", "decision": "trace", "notes": "only the front page"}).encode("ascii")
        with urlopen(Request(base + "/review/decide", data=data), timeout=5) as response:
            assert "Filed under Too little survives" in response.read().decode("utf-8")
        with urlopen(base + "/leads/traces", timeout=5) as response:
            assert "only the front page" in response.read().decode("utf-8")
        data = urlencode({"key": "site:a.test/steno", "action": "lead", "notes": "worth another look", "back": "/leads/traces"}).encode("ascii")
        with urlopen(Request(base + "/leads/update", data=data), timeout=5) as response:
            assert response.geturl().startswith(base + "/leads/traces") and "Moved to Leads" in response.read().decode("utf-8")
        with urlopen(base + "/collection", timeout=5) as response:
            page = response.read().decode("utf-8")
            assert "Complete copy: 6 of 6 files" in page and "/copies/geocities.com%20taquigra/index.html" in page and "1 pictures not found anywhere" in page
        with urlopen(base + "/copies/geocities.com%20taquigra/index.html", timeout=5) as response:
            assert "connect-src 'none'" in response.headers["Content-Security-Policy"] and response.headers.get_content_type() == "text/html"
            assert 'src="fotos/marti.jpg"' in response.read().decode("latin-1")
        with urlopen(base + "/copies/geocities.com%20taquigra/fotos/marti.jpg", timeout=5) as response:
            assert response.headers.get_content_type() == "image/jpeg"
        for escape_attempt in ("/copies/..%2Fcatalogue.sqlite3", "/copies/geocities.com%20taquigra/..%2F..%2Fcatalogue.sqlite3"):
            try:
                urlopen(base + escape_attempt, timeout=5); assert False, escape_attempt
            except HTTPError as exc:
                assert exc.code == 404
    finally:
        server.shutdown(); server.server_close(); thread.join(timeout=5)

def test_dashboard_routes_carry_a_find_from_review_to_leads(tmp_path: Path):
    store = Store(tmp_path / "data")
    store.record_discovery_run("internet-archive-cdx-url-regex", {"url": "a.test"}, cdx_payload("http://a.test/steno/"), 1)
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler_for(store, tmp_path / "data", tmp_path, JobManager()))
    thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
    base = f"http://127.0.0.1:{server.server_port}"
    try:
        with urlopen(base + "/review", timeout=5) as response:
            assert "a.test/steno" in response.read().decode("utf-8")
        data = urlencode({"key": "site:a.test/steno", "decision": "lead", "notes": "check it"}).encode("ascii")
        with urlopen(Request(base + "/review/decide", data=data), timeout=5) as response:
            assert "Kept as a lead" in response.read().decode("utf-8")
        with urlopen(base + "/leads", timeout=5) as response:
            assert "a.test/steno" in response.read().decode("utf-8")
        with urlopen(base + "/discoveries", timeout=5) as response:
            assert response.geturl().endswith("/review/list")
        with urlopen(base + "/search", timeout=5) as response:
            assert "Where to look" in response.read().decode("utf-8")
    finally:
        server.shutdown(); server.server_close(); thread.join(timeout=5)

def test_integrated_passage_page_uses_dashboard_routes(tmp_path: Path):
    corpus = tmp_path / "one.jsonl"
    corpus.write_text('{"id":"one","text":"I invented my shorthand notation for rapid writing."}\n')
    store = Store(tmp_path / "data"); import_corpus(store, corpus, "fixture")
    html = passage_review_page(store, base_path="/passages")
    assert "action='/passages/save-next'" in html and "formaction='/passages/save'" in html

def test_paginated_forum_thread_shows_only_its_strongest_match(tmp_path: Path):
    store = Store(tmp_path / "data"); corpus_id = store.get_or_create_corpus("Forum", "fixture")
    signals = {"first_person":["i invented"],"specialist":["shorthand"],"supporting":[]}
    first = {"external_id":"page-1","title":"A thread page 1","url":"https://www.verduria.org/viewtopic.php?t=42"}
    second = {"external_id":"page-2","title":"A thread page 2","url":"https://www.verduria.org/viewtopic.php?t=42&start=25"}
    store.add_corpus_document(corpus_id, first, store.put_blob(b"one"), store.put_blob(b"one text"), [{"text":"I invented a shorthand.","score":10,"signals":signals}], "fixture")
    store.add_corpus_document(corpus_id, second, store.put_blob(b"two"), store.put_blob(b"two text"), [{"text":"I invented a detailed shorthand notation.","score":18,"signals":signals}], "fixture")
    rows = store.ranked_passages()
    assert len(rows) == 1 and rows[0]["url"].endswith("start=25")
    assert rows[0]["grouped_documents"] == 2
    assert store.passage_review_counts()["total"] == 1
    assert "Thread grouped:" in passage_review_page(store)
    with store.connect() as con:
        assert con.execute("SELECT count(*) FROM corpus_documents").fetchone()[0] == 2

def test_thread_group_migration_backfills_existing_forum_pages(tmp_path: Path):
    root = tmp_path / "old-data"; root.mkdir(); database = root / "catalogue.sqlite3"
    connection = sqlite3.connect(database)
    try:
        for version,migration in enumerate(MIGRATIONS[:-1], start=1):
            connection.executescript(migration)
            connection.execute("INSERT INTO migrations(version,applied_at) VALUES(?,?)", (version,"before-upgrade"))
        connection.execute("INSERT INTO corpora(id,name,source_path,created_at) VALUES(1,'Old forum','fixture','before-upgrade')")
        connection.execute("""INSERT INTO corpus_documents(corpus_id,external_id,title,url,raw_sha256,text_sha256,created_at)
            VALUES(1,'old-page','Old page','https://www.verduria.org/viewtopic.php?t=77&start=25','raw','text','before-upgrade')""")
        connection.commit()
    finally:
        connection.close()
    store = Store(root)
    with store.connect() as migrated:
        group_key = migrated.execute("SELECT group_key FROM corpus_documents").fetchone()[0]
    assert group_key == "https://www.verduria.org/viewtopic.php?t=77"

def test_lan_access_allows_only_private_clients_and_uses_a_six_digit_code():
    access = LanAccess(code="123456", session_token="test-session")
    assert access.client_allowed("127.0.0.1") and access.client_allowed("192.168.1.20")
    assert not access.client_allowed("8.8.8.8")
    assert access.verify("123456", "192.168.1.20")[0]
    assert access.authenticated("lnp_lan_session=test-session")
    assert not access.authenticated("lnp_lan_session=wrong")

def test_lan_dashboard_requires_code_then_remembers_device(tmp_path: Path):
    store = Store(tmp_path / "data")
    access = LanAccess(code="123456", session_token="test-session-token")
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler_for(store, tmp_path, tmp_path, JobManager(), access))
    thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
    base = f"http://127.0.0.1:{server.server_port}"
    try:
        with urlopen(base + "/", timeout=5) as response:
            assert "Enter the six-digit code" in response.read().decode("utf-8")
        cookies = CookieJar(); opener = build_opener(HTTPCookieProcessor(cookies))
        request = Request(base + "/connect", data=urlencode({"code":"123456"}).encode("ascii"))
        with opener.open(request, timeout=5) as response:
            html = response.read().decode("utf-8")
        assert "Digital archaeology field station" in html
        assert any(cookie.name == "lnp_lan_session" for cookie in cookies)
    finally:
        server.shutdown(); server.server_close(); thread.join(timeout=5)

def test_desktop_dashboard_still_opens_without_a_code(tmp_path: Path):
    store = Store(tmp_path / "data")
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler_for(store, tmp_path, tmp_path, JobManager()))
    thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
    try:
        with urlopen(f"http://127.0.0.1:{server.server_port}/", timeout=5) as response:
            html = response.read().decode("utf-8")
            assert response.headers["Cache-Control"] == "no-store, max-age=0"
        assert "Digital archaeology field station" in html
        assert "Enter the six-digit code" not in html
    finally:
        server.shutdown(); server.server_close(); thread.join(timeout=5)

def test_cli_keeps_desktop_dashboard_and_adds_optional_lan_mode():
    import lost_notation.cli as cli
    assert not cli.parser().parse_args(["dashboard"]).lan
    assert cli.parser().parse_args(["dashboard", "--lan"]).lan
