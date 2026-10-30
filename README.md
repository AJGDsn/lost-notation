# The Lost Notation Project

This repository is the preservation-first foundation for a digital-humanities archive of independently invented notation systems. It is deliberately a pipeline, not a monolithic crawler: every run is checkpointed, its inputs are retained, and each output can be reused by later stages.

## Minimal prototype

The prototype runs entirely on Python's standard library. It provides:

- multilingual, provenance-labelled query generation;
- an import boundary for pluggable discovery sources (currently JSONL, useful for repeatable harvesting and source adapters);
- URL-deduplicated downloading into a content-addressed preservation store;
- HTML/plain-text extraction with retained raw evidence;
- a SQLite catalogue with FTS5 full-text search and migration tracking;
- a CLI and tests.

### Easiest way to run it on Windows

Double-click [Lost Notation Project.bat](Lost%20Notation%20Project.bat). It opens the Lost Notation research dashboard in your browser, organised as four stages: **Search** (choose terms and sources), **Review** (one inbox for everything found), **Leads** (the holding area for promising finds) and **Collection** (save copies and write Obsidian notes). Keep the small command window open while the dashboard is in use. Python 3.9+ must be installed and available on your PATH.

For an iPad on the same private home network, double-click [Lost Notation Project - iPad.bat](Lost%20Notation%20Project%20-%20iPad.bat). The launcher displays a local Safari address and a six-digit access code while retaining all data on the PC. If Windows asks, allow Python on **Private networks only**. The normal PC-only launcher remains unchanged; use one launcher at a time.

The launcher keeps its active database, cached downloads, and generated query files in `C:\LostNotationData` by default. This keeps the working archive out of OneDrive's synchronisation stream, while the project files and generated research vault can be safely synced.

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -e ".[dev]"
lost-notation generate-queries --output data/queries.jsonl
lost-notation import-discovery --input examples/candidates.jsonl
lost-notation discover-ia-seeds --input examples/wayback-seeds.jsonl --pages 25 --dry-run
lost-notation discover-ia --url-pattern "*.geocities.com/*script*" --limit 20 --dry-run
lost-notation fetch --limit 20
lost-notation extract
lost-notation search "symbolic writing"
pytest
```

`import-discovery` accepts JSON lines with `url`, `source`, `discovered_at`, and optional `query`, `title`, and `archive_url`. It never treats a discovery result as authoritative metadata.

`discover-ia` is a live, rate-limited Internet Archive Wayback CDX URL-index adapter. Its `--url-pattern` is deliberately a URL pattern rather than one of the natural-language search queries: CDX indexes URL/capture metadata, not archived page text. It retains the raw JSON result in the preservation store even in `--dry-run` mode. The CDX API supports JSON output, field selection, filtering, result limits, and duplicate collapse. [Internet Archive's CDX documentation](https://github.com/internetarchive/wayback/blob/master/wayback-cdx-server/README.md)

`discover-ia-seeds` runs the versioned survey list in `examples/wayback-seeds.jsonl`. It covers 22 free hosts, including GeoCities, GeoCities Japan, the OoCities GeoCities mirror, Angelfire, Tripod, Narod.ru, and French, Italian, Brazilian, Polish and German free hosts, arranged so the most promising sections are scanned first:

1. **Named addresses** (139 sections, two requests each): member addresses that begin with a notation word, such as `geocities.com/steno…` or `digilander.libero.it/tachigra…`, on hosts where the first part of the path is the member's chosen name. Each word is matched to the languages the host served. Every HTML page under such an address is kept.
2. **Key topic areas** (5 sections, about 4,500 index pages): GeoCities Athens ('education, philosophy'), Area51 ('sci-fi, fantasy') and TimesSquare ('games, role-playing'); GeoCities Japan Technopolis (science, SF, high technology); and Angelfire's scifi directory. Themes are quoted from the hosts' own directories as archived in 1999–2000.
3. **Other topic areas** (10 sections, about 7,000 index pages): GeoCities SoHo, CollegePark, ResearchTriangle, CapeCanaveral, Tokyo and EnchantedForest; GeoCities Japan Bookend and CollegeLife; Angelfire art and journal.
4. **Whole sites** (22 hosts, about 360,000 index pages): everything else, as long-running background work. This tier re-reads the topic areas, a small overlap.

Tiers 2–4 keep HTML captures whose **original URL** matches a high-precision notation regex. The regex is multilingual (`stenograf`, `kurzschrift`, `taquigraf`, `tachigraf`, `neograf`, `sokki`, `moji`, `pismennost` and others) but still excludes broad terms such as `script`, `writings` and bare `alphabet`.

A single whole-host CDX query times out on the server, because the server must read every capture before it finds the rare matches. The survey therefore uses CDX pagination: each section (a whole host, or an address prefix with `"match": "prefix"`) is split into index pages, and `--pages N` makes the next N index requests, counting each section's one-off size lookup as well as its page scans, so a run takes a predictable time. Progress is stored per section and per regex in the `archive_scans` table after every page, so runs can be stopped and resumed, and changing a seed's regex restarts that section. A tier starts only when every section of the tier before it is finished; within a tier, sections are visited in rotation, least recently scanned first. A seed without a `tier` is treated as a whole site. A page that fails three times is skipped, with its own log entry. Busy replies from the index (HTTP 502, 503 or 504) are retried twice, after 15 and 30 seconds; a page that still fails counts as one failed attempt. Only HTTP 429, the archive's request to slow down, stops the run. Every request, including empty pages, is kept in `discovery_runs`. Matches are counted once per page: the archive lists one page under several spellings (`www.`, `:80`, `http`/`https`), which are merged, and addresses of images and style sheets are skipped, because mirrors sometimes label a missing image as HTML. Pages took 8–25 seconds each in early tests but about a minute each on the first full run, so the whole list is months of background running rather than a single job. The first page or two of each whole site are mostly captures of its own front page. It remains URL-name discovery, not page-text discovery, so every result still needs extraction and review. GeoCities, Angelfire, and Tripod were major personal-homepage hosts, while Xoom, FortuneCity, and Freewebs were additional free-hosting platforms worth targeted recovery work. [Angelfire's history](https://www.angelfire.lycos.com/aboutaf.tmpl), [Microsoft's personal-homepage announcement](https://news.microsoft.com/source/1999/06/30/msn-launches-free-personal-home-pages/), and [a research overview of personal web pages](https://encyclopedia.pub/entry/history/show/77536) provide useful context for this approach.

## Obsidian research vault

The database and blob store remain canonical. The dashboard's Collection writes one note per processed lead (see *The four-stage dashboard*). Separately, formal curator-approved project records can be imported and exported as a Markdown vault:

```powershell
lost-notation import-projects --input examples/projects.jsonl
lost-notation export-vault --vault C:\Research\Lost-Notation-Vault
```

The exporter writes only projects with `status: "reviewed"` by default, generates stable IDs and `[[Category - …]]` links, and preserves text below `## Curatorial notes` when a note is regenerated.

## The four-stage dashboard

1. **Search.** Tick terms in English, French, German, Spanish, Portuguese, Italian, Russian, Japanese and Chinese (a banner names any language left out), add your own terms, and tick the sources to use. **Save and run a batch** runs each ticked automatic source once in the background: the CONLANG mailing list, the Zompist forum, Omniglot, Wikimedia Commons (every ticked term in every ticked language), the old-websites archive survey and, with a key, Brave. The hand-search checklist offers Google, Brave, Yandex, Baidu and Yahoo! Japan links for each search; anything good is pasted straight into Leads.
2. **Review.** Everything found waits in one inbox: mined passages, archived websites (one entry per member site, however many pages, with links to its front page and to everything the Wayback Machine holds), Commons files with a preview, and web results. Decide each with **Keep as a lead** (K), **Too little survives** (T), **Not relevant** (X) or **Decide later** (L), or tick several in the list view. *Too little survives* is for a find that is relevant but too thinly archived to use: it is kept, with your note, in its own list under Leads, with links to look for other copies (for GeoCities sites, the OoCities mirror). Not-relevant finds never come back to Review, but they are listed under **Leads → Set aside as not relevant**, with their notes, where anything can be moved back. Nothing is deleted.
3. **Leads.** The holding area: add notes, add leads found elsewhere (a web page, or a library or catalogue record that needs a visit), then send a lead to the Collection or drop it. Tabs at the top switch to **Too little survives** and **Set aside as not relevant**.
4. **Collection.** **Save a complete copy** of a kept website saves every page, picture and file the Wayback Machine holds for it, from every date it was captured, into `C:\LostNotationData\Site copies\<site>`. The folder is laid out like the original site, its links are changed so it can be read offline, and `_contents.html` lists every file with its capture date. Each file comes from its newest good capture, or an older one when that is broken. Pictures the pages show but the archive never captured at the site's own address are looked for in the archive's captures of the OoCities mirror (GeoCities sites) and, for pictures kept on other sites, at their own addresses; any still missing are listed. A finished copy that has not been searched this way (one made before the search existed, or whose search could not reach the archive) shows **Look for missing pictures**. Other programs can take a finished copy: `_copy.json` says when it is complete, `_lead.json` holds your notes on the lead, and a program can ask for a copy by adding a line to `C:\LostNotationData\requests.txt`, which then appears on the Collection page under **Requested by Scriptorium** for you to start. See [docs/site-copy-format.md](docs/site-copy-format.md). Files are requested one every few seconds, so a site of a few hundred files takes half an hour or so; if the archive asks us to slow down, the copy stops and **Finish the copy** carries on where it left off. **Browse your copy** opens it through the dashboard (its old scripts cannot reach the dashboard), and **Open the folder** opens it in File Explorer. **Save copies** downloads a Commons file's description page and original, or a web page. Text is extracted automatically from every saved page, honouring the character set old pages declare (Shift_JIS, KOI8-R, windows-1251 …). A Markdown note is written automatically for each lead once it has been processed (its copies saved, or marked finished), in a `Lost Notation` folder of your Obsidian vault (set the folder on the page; the default is `C:\LostNotationData\Lost Notation Vault`, which is not backed up by OneDrive), with a `saved pages` subfolder holding one note per saved page. It is kept up to date as the lead changes, and text under `## My notes` is always kept. If Scriptorium has taken the copy, the note says so, and says when the copy has changed since.

Decisions live in the catalogue's `triage` table (`passage:<id>`, `site:<member site>` or `url:<address>`). Decisions from the earlier Passages, Discoveries and manual-search screens are adopted once at start-up: passages marked promising or needing research, and pasted search results, become leads; passages marked irrelevant are set aside. The old screen addresses redirect to their new places.

Some member sites match a notation word only by accident: "neographics" sites are Neopets graphics, `geocities.jp/sokki_battery` sells batteries for surveying instruments (測器 *sokki*, not 速記), and "stenop…" is French pinhole photography (*sténopé*). Such sites are listed in `examples/review-hidden-sites.txt`, one address per line, and never reach Review. Each line hides that address and everything under it, and `*` matches any run of characters. An early probe searched addresses for "script", so addresses that fit it only through "javascript" or "vbscript" are hidden automatically. Hidden pages stay in the catalogue.

If an archival survey encounters an unexpected failure, its complete console output is retained in `C:\LostNotationData\latest-survey.log` and displayed before the launcher pauses.

## Text search with Brave Search

The dashboard can run the first ten generated text queries through Brave Search without scraping any search-engine website. Create an API key in the [Brave Search API dashboard](https://api-dashboard.search.brave.com/app/documentation/web-search/get-started), then set it once in PowerShell (replace the placeholder):

```powershell
[Environment]::SetEnvironmentVariable("BRAVE_SEARCH_API_KEY", "your-key-here", "User")
```

Close and reopen the dashboard and tick **Brave web search** on the Search page; each batch then runs the next ten checklist searches not yet sent to Brave, and the results go to Review. The key is read only from your Windows environment; it is not written to this project, the database, or the Obsidian vault. Each query is sent with its own language (`search_lang`) and `country=ALL`, so a German or Japanese query returns German or Japanese results from anywhere. Brave's API uses the `X-Subscription-Token` header and supports quoted and site/file-type operators in its query parameter. [Brave documentation](https://api-dashboard.search.brave.com/app/documentation/web-search/get-started)

## Free manual search workbench

The **Search** page's terms are divided into specialist, related and broad tiers, plus any terms you add (saved in `C:\LostNotationData\custom-terms.json`). Preset buttons and individual tick boxes control terms, first-person phrases and standalone searches, with a live count. Saving the page creates the hand-search checklist, moves matching CONLANG and Zompist subjects to the front of their pending queues, and re-filters unreviewed passages already mined from those sources. Start with specialist terms, move to related terms when they are exhausted, and keep broad terms for last. Preserved pages and review decisions are never deleted.

Queries cover English, French, German, Spanish, Portuguese, Italian, Russian, Japanese and Chinese. They include each community's own vocabulary (neography, néographie, neografía, 創作文字, 人造文字) and first-person phrases that occur verbatim in natural text. German, for example, uses "selbst entworfen" rather than "ich habe entworfen", because German word order separates the verb. Passage scoring reuses the same vocabulary, so imported non-English corpora are scored too.

Then open **Search engines, by hand** from the Search page. It shows ten unfinished searches at a time; Russian, Chinese and Japanese ones also offer a Yandex, Baidu or Yahoo! Japan link. Each search opens normally in your browser; no search page is scraped or stored. Mark a search done when you have looked through it, and paste any promising result into it to add it to Leads with the originating search recorded.

## Historical and institutional sources

The project also keeps a distinct research stream for historical shorthand, script-reform and phonetic-writing material held by libraries and institutional databases. This supplements—rather than replaces—the core recovery work on independently invented online systems.

The editable Chinese vocabulary is in [`config/search_terms.yaml`](config/search_terms.yaml). Search open Wikimedia Commons file-description pages with:

```powershell
lost-notation --data-dir C:\LostNotationData discover-commons --dry-run
```

Commons asks automated clients to slow down when they search quickly. The adapter waits between requests, honours `Retry-After` and retries, and stops the batch if it is still being refused. Wikimedia also asks clients to identify a contact; set one (an email address or web page) with `[Environment]::SetEnvironmentVariable("LOST_NOTATION_CONTACT", "you@example.org", "User")` and it is added to the User-Agent sent to Commons and the Internet Archive.

The dashboard's Commons source searches this list together with every term ticked on the Search page, each term once. This is discovery only. The raw API response and query are preserved; results appear in **Review**, and nothing is downloaded until a kept lead reaches the Collection. The Commons adapter uses the documented MediaWiki `action=query&list=search` interface, limited to the File namespace. [MediaWiki API documentation](https://www.mediawiki.org/wiki/API:Search_and_discovery)

For a catalogue or restricted database, use **Add a lead you found yourself** on the Leads page and choose *a library, archive or catalogue record*. Such leads are never downloaded automatically: “we know it exists” and “we have preserved the file” remain separate facts.

## Reading saved copies

In the Collection, **Read the saved text** shows the extracted text of every saved page of a lead, with a link to its original address; for a kept passage it shows the whole source page collected with it.

## Corpus import and passage mining

Use **Import a saved corpus** under **Tools** to ingest a file or folder containing `.html`, `.htm`, `.txt`, `.md`, `.json`, or `.jsonl` material. JSON/JSONL forum records may use `text`, `body`, `content`, or `message`, with optional `id`, `title`, `url`, `author`, `username`, `date`, or `created_at` fields. Raw records and full extracted text are preserved as immutable blobs. Re-importing unchanged records is safe and skips them.

The passage miner ranks exact excerpts using transparent signals: first-person creation phrases, specialist notation vocabulary, and their co-occurrence. Passages appear first in **Review**, highest score first. Scores control reading order only; they are not claims about originality or relevance.

Paginated forum threads are grouped for review. All raw pages and extracted passages remain preserved, but only the highest-scoring match from each thread appears in the review queue. The review screen reports how many related matches and pages were grouped, preventing one useful thread from occupying many consecutive review positions.

## Automatic high-value corpus collection

The dashboard can collect small, resumable batches directly from three initial public sources:

- **Omniglot constructed scripts**;
- **the Zompist Bulletin Board's Conlangery forum**; and
- **the independent static CONLANG archive mirror**.

Tick them on the Search page; each batch reads 25 more pages from each. Every accepted URL is put into a persistent priority frontier before retrieval. For Zompist and CONLANG, the current Search vocabulary selection determines which subjects jump to the front and which newly mined passages enter the unreviewed queue. Successfully fetched URLs are not downloaded again on later runs, raw HTML and extracted text are content-addressed, and newly discovered in-scope links remain queued for the next batch. Failed URLs are retained with their error instead of being silently lost. The corresponding CLI command is `lost-notation collect-source omniglot --limit 25` (or `zbb` / `conlang-mirror`). Explicit `--retry-errors` and `--refresh` switches provide controlled rechecking; the dashboard does not use them automatically.

Collection is deliberately sequential and rate-limited. It uses no paid search API. The mined passages flow directly into Review.

Brown University's public CONLANG index remains a valuable lead source, but its `robots.txt` excludes automated access to the message interface. The program therefore does not crawl those pages. Instead it uses the independent read-only static mirror at `archives.conlang.info`, which covers September 1998 through April 2009. Monthly indexes receive priority over generic messages, while message subjects containing notation vocabulary receive the highest priority. Message metadata is retained and HTML-marked quoted replies are omitted from the derived review text while remaining intact in the preserved raw HTML. Saved or subsequently obtained CONLANG exports can also be processed with the general corpus importer.

## Architecture and deliberate choices

See [docs/architecture.md](docs/architecture.md). The initial SQLite database is intentionally local and portable; its repository-facing interface keeps a later PostgreSQL + pgvector/OpenSearch deployment from leaking through the whole codebase.

All operational output belongs under `data/` (ignored by Git). Raw files are immutable blobs named by SHA-256, while the database records URL, retrieval event, and derived artefacts separately. A retry only occurs when explicitly requested with `--refresh`.

## Safety and research practice

The system is designed to record provenance, capture timestamps and archive references, respect retrieval policies, and preserve original bytes. Before scaled crawling, add source-specific rate limits, robots/terms review, a takedown policy, and a versioned human review workflow. OCR, LLM assessment, embeddings, and similarity analysis are intentionally later stages: their results must be versioned, confidence-scored derivatives, never replacements for source material.

## Licence

The Lost Notation Project is free software under the GNU General Public License v3.0; see [LICENSE](LICENSE).
