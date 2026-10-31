# Book export: the parked design

Nothing on this page is built, apart from the two fixes listed below. It records what was worked out with Scriptorium about handing over PDFs and book scans, so the work can be picked up later. What Scriptorium can rely on today is in [site-copy-format.md](site-copy-format.md). All examples here are invented.

## Why it is parked

In October 2026 the owner chose to make only two small fixes and leave the export until there is a book Scriptorium can read, or until they decide what to do about Chinese and Japanese. Neither version below has been approved.

- **Scriptorium can't read Chinese past import.** Most of the PDFs held as leads are in Chinese, so there is little to gain from sending them yet.
- **A Japanese book is held only in part.** The catalogue has part 3 of four, and that part should not go alone. Scriptorium ties corrections to page numbers, so importing it as one book and merging the other parts into it later would be awkward.

## The two fixes made

- **Addresses outside ASCII.** Save copies could not fetch a Commons description page whose file name was not ASCII. It now percent-encodes the address when it makes the request. The saved address and the lead key stay as they were.
- **Book lines in `requests.txt`.** A line beginning `book:`, or the address of a single file, was misread as a request for a site. Such lines are now ignored and left as they are. Scriptorium won't send any for now.

## The small version

This is the version recommended for when the export comes back. It covers leads with one file. It follows the same contract as the full design, so anything added later only adds to it, and `format` stays 1.

- A "Send to Scriptorium" button on a Collection card, shown only when that lead has a saved PDF or scan. Nothing exports by itself.
- It writes `<data folder>\Book copies\<short title> (<hash>)\`, holding the original file, `_lead.json`, the description page in `_source\`, and `_book.json` written last. With no hints sent, the description page is the only place the author and licence arrive.
- `_book.json` has only the required fields, with `relation: "single"` and one entry in `files` with `order: 1` and `state: "saved"`, plus `mimetype` and `download_url`, which are already recorded.
- Sending again with the same bytes does nothing. If the bytes ever differ, Send refuses and tells the owner, so nothing is overwritten and `_earlier` isn't needed.
- The Collection card and vault note read `complete` from `_book.json` and say "taken", not "shelved".

The rough estimate is 100 lines, a few tests and a short section in the format document, against several times that for the full design. Scriptorium would need to confirm `single`.

If the Japanese book is wanted, add one thing: "Add another file" with `relation: "parts"`, the order taken from the part numbers for the owner to check, and the hand-added Commons fix below. That needs no relation choice, no `_earlier` and no new reader, because `parts` needs only one line in `imported.txt`.

## The full design

### Scriptorium's requirements

Lost Notation agreed to these, except where the four differences below say otherwise.

- **Manifest.** `_book.json`, not `_copy.json`, with its own format number and no HTML at the folder's top level, because Scriptorium sends any folder with `_copy.json` or a top-level `*.htm*` file to its website importer.
- **Several files.** `relation` says how the files of a lead belong together, and each file has an explicit `order`, because file name order isn't safe.
- **Archive captures.** The original address and the archive address are given separately, along with what the host says about the item.
- **Hints.** Optional, sent as found and marked as guesses. `year` is the publication year and is never filled in from `captured`. Library and subject aren't Lost Notation's to set.
- **Files exactly as captured.** No OCR layer, recompression, deskewing or removal of pages. If an original PDF exists, send that, not images made from it. No claim that a set is complete: Scriptorium checks that itself.
- **After export.** The owner starts each import. Scriptorium reports exports that are ready and not yet taken. Books are flagged, not refreshed, and Scriptorium compares `sha256`. Nothing that has been taken is overwritten in place.
- **`imported.txt`.** The line means taken, not published or reviewed. The shelf folder doesn't exist until Scriptorium runs generate, so notes must not imply the book is ready to read.

### The folder

`<data folder>\Book copies\<short title> (<hash>)\`, beside Site copies, where the hash is six characters made from the lead key.

| Item | Meaning |
|---|---|
| The book's files | The original bytes, copied from the blob store and checked against their SHA-256. A copy, not a link, so nothing done to it can alter the catalogue. |
| `_lead.json` | The same file and fields as for site copies. |
| `_source\` | Description pages, listed apart from `files` so they can't be taken as a part. |
| `_earlier\<date>\` | Before a folder's book files change, the previous version moves here with its own `_book.json`. Scanning the top level finds only the current one. |
| `_book.json` | Written last. |

The export covers any saved file on a `file` or `web` lead whose type isn't text or HTML. There is no list of formats, because the owner's click on each lead is the filter. That leaves out single web pages. Passages and catalogue records have no file to send. PDFs inside site copies stay where they are, with their address and date in `_copy.json`.

### `_book.json`

| Field | Required | Meaning |
|---|---|---|
| `format` | Yes | Its own number, starting at `1`. |
| `lead` | Yes | The lead key. Treat it as an opaque name and copy it from here. |
| `relation` | Yes | `single`, `parts` (one book in ordered pieces), `volumes` (separate books, several imports from one lead), `alternatives` (two scans of one thing; the owner picks) or `unknown` (goes to the owner; the default for several files). |
| `complete` | Yes | Written last and removed before anything in the folder changes. It means the folder is finished, not the book. |
| `files` | Yes | One entry per book file, below. |
| `updated` | | When this file was last saved. |
| `hints` | | `title`, `author`, `year`, `language`, as found. |
| `source_pages` | | `path`, `source_url`, `captured` and `sha256` for each file in `_source\`. |

Each entry in `files`:

| Field | Required | Meaning |
|---|---|---|
| `path` | Yes | Where the file is, relative to the folder. |
| `order` | Yes | Its place among the lead's files. |
| `state` | Yes | Only `saved` is taken. A part that couldn't be saved is listed as `failed` with no `sha256`, so the gap shows. |
| `sha256` | Yes | Of the file's bytes. |
| `source_url` | Yes | The address to cite. For a Wayback capture, the address after the timestamp. |
| `captured` | Yes | The capture time for a Wayback file; otherwise when Lost Notation downloaded it. |
| `archive_url`, `timestamp` | | The archive address and its 14 digits. |
| `download_url` | | Where the bytes came from, if somewhere other than `source_url`, as on Commons. |
| `source_date` | | The server's Last-Modified header. Keeping it needs one new database column. |
| `mimetype`, `size` | | As recorded. |
| `host` | | What the host says, as given. On Commons: name, author, credit, licence, upload date and sha1, from one API call when the file is saved. |

### The lead key

A book's key is `url:` plus its address in lower case, without scheme, `www.` or default port, such as `url:commons.wikimedia.org/wiki/file:example_book.pdf`.

- **Stable for a lead.** Once a lead exists its key never changes, so "export differs" works by comparing `sha256` under the same key.
- **Not stable for a book.** These all get different keys today: the same item on Commons, archive.org and its author's own site; one Commons page written with `_` and with `%20`; and two Wayback captures of one file.

So Scriptorium's duplicate check should use `sha256`, which matches identical bytes from any host. Two different scans of one book match on nothing automatically; only the owner can link them, by putting both on one lead as `alternatives`.

Two additions on Lost Notation's side: new book leads get a tidied key (percent-encoding decoded, `_` for spaces on Commons, and the original address for a Wayback one), and Send warns, without blocking, if the same `sha256` has already gone under another lead. Existing keys are never rewritten.

### The four differences from Scriptorium's requirements

1. **`notes` stays out of `_book.json`.** Notes live in `_lead.json`, which changes without touching `complete`, so a second copy could disagree with it.
2. **`_lead.json` is the one exception** to "removed before anything in the folder changes", the same as for site copies.
3. **Two forms of book request.** Keys are lower case and these addresses are case-sensitive, so the address can't be rebuilt from a key, and a key alone can't ask for something new. Both forms take the usual `[done <date>]` suffix:
   - `book:url:<key> note` for a lead already held;
   - `book:https://<full address, capitals kept> note` for a new one.
4. **`complete` keeps its old value if the bytes didn't change**, as site copies already do. A new `complete` from Lost Notation therefore always means different hashes. Scriptorium's own check is still worth keeping.

### The lists

`imported.txt` keeps its first four fields. Scriptorium would add a fifth, `library` (`steno` or `pasi`), and a sixth, the paths taken in that line, separated by `|` and written exactly as `path`. No sixth field means the whole export. Today's reader already tolerates extra fields, but it keeps only the last line for a lead. For book leads it would:

- read every line for the lead and match its paths against `files`;
- count `volumes` as fully taken only when every file has been seen, and otherwise say "taken 1 of 2 volumes";
- count any line as taken for the other relations;
- show "changed since" where a line's `complete` differs from the current one.

Sites keep last-line-wins. The note reads "In Scriptorium: taken \<date\> for \<shelf\>; this does not mean it is ready to read yet", with no link to the shelf.

In `requests.txt`, book requests would appear in the existing panel with a "Save and send" button, and a line without the prefix that plainly names a file would become a book request instead of being ignored.

### What the owner sees

- "Send to Scriptorium" on a Collection card, shown only when the lead has a qualifying saved file. Save copies, "Save copies of every lead", Mark finished and note syncs never export, and there is no send-all.
- "Add another file to this lead". Once a lead has more than one book file, a relation choice and order numbers, with the order proposed from the file names for the owner to check. A book in parts is one lead with `relation: "parts"`.
- When it was sent and whether Scriptorium took it, on the card and the vault note. There is no new table: the folder is the record.

### Example

```json
{"format": 1, "lead": "url:commons.wikimedia.org/wiki/file:example_book_part1.pdf", "relation": "parts", "updated": "...", "complete": "...",
 "hints": {"title": "Example Book", "author": "A. Author"},
 "files": [{"path": "Example Book part1.pdf", "order": 1, "state": "saved", "sha256": "...", "source_url": "https://commons.wikimedia.org/wiki/File:Example_Book_part1.pdf",
            "download_url": "https://upload.wikimedia.org/...", "captured": "...", "source_date": "...", "mimetype": "application/pdf", "size": 123,
            "host": {"name": "...", "author": "...", "credit": "...", "licence": "...", "uploaded": "...", "sha1": "..."}}],
 "source_pages": [{"path": "_source/Example Book part1.html", "source_url": "...", "captured": "...", "sha256": "..."}]}
```

## What brings each later part back

| Part | What brings it back |
|---|---|
| The small version itself | A book Scriptorium can read, or the owner's decision on Chinese and Japanese. |
| "Add another file", relation and order choices | The owner wants a book in parts or volumes. The Japanese book would be the first. |
| Fix: a Commons address added by hand becomes a `web` lead, which saves the description page but not the file | The same work: adding the other parts of a book needs it. No current lead is affected. |
| `_earlier` | A sent file really needs replacing. The bytes come from immutable storage, so this shouldn't arise with single files. |
| Reader for several `imported.txt` lines per lead | The first `volumes` lead. |
| Tidied keys and the duplicate warning | Book requests are built, or a duplicate lead actually appears. |
| `book:` requests | Scriptorium needs a book Lost Notation doesn't hold. Until then the reader ignores those lines. |
| `hints`, `host`, `source_date` | The description page proves not to be enough. |
| A `lost-notation export` command | Someone wants it. The owner works from the dashboard. |
| Fix: downloads are capped at 60 MB and read into memory | A wanted file is larger. Every held file fits today, and a larger one fails with a clear message. Then stream it and raise the cap. |

## What Scriptorium would add on its side

Its minimum:

- a `_book.json` branch in its import;
- a required Latin title and language;
- a `lost_notation:` block in `book.yaml`;
- the fifth `imported.txt` field, `library`;
- a `sha256` duplicate check at import.

## Still open

- **Who supplies the Latin title and language.** Scriptorium requires them, but the small version sends no hints and Lost Notation holds neither as fact. In the full design `language` is sent only if the host states it, and a title may not be in Latin.
- **Chinese and Japanese folder and file names.** They stay unless Scriptorium objects. It hasn't answered.
- **PDFs in site folders.** They stay where they are. Scriptorium hasn't answered.
- **`single` and the `|` separator.** Proposed by Lost Notation, not yet confirmed.
- **For the owner, when it comes back:** the small or the full design and the four differences; the button name ("Send to Scriptorium" is recommended); whether to add the other parts of the Japanese book; and which leads to move into the Collection, which only the owner does.
