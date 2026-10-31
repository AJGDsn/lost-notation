# Taking a site copy into another program

A complete copy of a website lives in `<data folder>\Site copies\<site>\`, where `<site>` is the address with `/` replaced by a space (`geocities.com example`). Another program may read these folders; it should not open the catalogue or the `blobs` folder. This page is what such a program can rely on.

## When a copy is ready

A copy is ready when `_copy.json` has a `complete` timestamp. It is written last, after every file, link and note in the folder, and the file is replaced in one step, so a reader never sees half of it.

- `complete` is removed before anything in the folder changes and written again when the work is done, so a copy without it is either unfinished or being changed.
- A new `complete` value means the copy has changed since it was last taken. Running a finished copy again with nothing to do leaves it alone.
- `complete` does not mean every file was saved: files the archive could not supply stay as `state: "failed"`. Take only files with `state: "saved"`.

## `_copy.json`

| Field | Meaning |
|---|---|
| `format` | Raised only for a change that would trip up a reader. Currently `1`. New fields may appear without a new number, so ignore fields you do not know. |
| `site` | The address, e.g. `geocities.com/example`: lower case, no scheme, no `www.`. |
| `lead` | The lead this copy belongs to, e.g. `site:geocities.com/example`. This is the name to use for it in other programs. |
| `listed` | When the archive was first asked what it holds. |
| `updated` | When this file was last saved. It changes while a copy is under way, so compare `complete` instead. |
| `complete` | When the copy was finished. Absent if it is not ready. |
| `missing` | Addresses of pictures the pages show that were not found anywhere. |
| `aliases` | Optional. Other addresses of the same site, in the same form as `site`. See below. |
| `files` | One entry per file, below. |

Each entry in `files`:

| Field | Meaning |
|---|---|
| `path` | Where the file is, relative to the folder, with `/` between folders. Lower case and safe for Windows. Folders become `index.html`. |
| `original` | The address the file was captured from. |
| `timestamp` | The capture, as the Wayback Machine's 14 digits. |
| `mimetype` | As the archive recorded it. |
| `state` | `saved`, `failed` or `waiting`. |
| `found` | Optional. `mirror` if the file came from the OoCities mirror, `elsewhere` if from another site; absent if from the site's own captures. |
| `as` | Optional. For a file from the mirror, the address it stands in for. |
| `stripped` | Optional. `true` for a page from the mirror whose additions were removed (see below). |

The other fields (`searched`, `left_out`, and `older` and `error` on a file) are for this program's own use and may change.

## `_lead.json`

Beside `_copy.json`: what the researcher knows about the lead. It is rewritten when the notes change, without changing `complete`, so read it each time.

| Field | Meaning |
|---|---|
| `format` | As above. |
| `lead` | As above. |
| `title` | The lead's title. |
| `source` | Where it was found, e.g. `Old websites (Internet Archive)` or `Requested by Scriptorium`. |
| `found_by` | The search or address that turned it up, if known. |
| `notes` | The researcher's notes. |
| `decided` | When it was kept as a lead. |

## What the pages are

The pages are reading copies, not the archived bytes. The original bytes of every page stay in the catalogue.

- **Links are changed.** A link to a file in the copy becomes a relative path. Anything else, including links to other sites, points at `https://web.archive.org/web/<timestamp>/<address>`. Do not follow those when working on a page's text.
- **`<base>` tags are commented out.** The rest of the page, including its text and encoding, is unchanged.
- **Pages from the OoCities mirror** (`found: "mirror"`) have the mirror's header, footer and scripts removed, so they begin where the author's page began. `stripped: true` confirms it. Should the mirror ever use a form this program does not recognise, the page is left as captured and `stripped` is absent.
- **Pages from the site's own captures keep the host's additions**, such as the scripts and tracking image GeoCities added to every page.
- Files from other sites are under `_elsewhere/<host>/`. `_contents.html` lists every file for a person to read.

## Aliases

`aliases` lists addresses the program learned of while copying:

- the mirror, e.g. `oocities.org/example`, when a page came from it;
- an address where a link names a page the copy holds, under a name two sites would not share by chance, and at least three of the site's pages link there. A site whose front page links to `www.example.org/m1.htm` and whose other pages link to `www.example.org` gets `example.org`.

Links to an alias are noted, not changed: they still point at the Wayback Machine. To treat one as local, unwrap the address, drop the alias from its front, and look for the rest as a `path`.

## Saying what was taken

Append one tab-separated line per import to `<data folder>\imported.txt`, creating it if it does not exist:

```
site:geocities.com/example	2026-01-15	2026-01-15T09:30:00.000000+00:00	Websites/example
```

The fields are the lead key, the date, the `complete` value that was imported, and where it was shelved. Only ever append; the last line for a lead is the current one. A refresh that brings in nothing new need add no line.

This program never writes to the file. It reads it to say on the lead's vault note, and on the Collection page, what was taken, and it flags a copy whose `complete` no longer matches. A lead with no line is not taken to be untaken: it just says nothing.

## Asking for a copy

Append a line to `<data folder>\requests.txt`, creating it if it does not exist:

```
geocities.com/example The author's own site
```

The address comes first, then optionally a space and a note. Use UTF-8 and end the line with a line break. Lines beginning with `#` are ignored. Only ever append. Letter case does not matter: `geocities.com/athens/forum/1234` and `geocities.com/Athens/Forum/1234` are the same request, the archive's index ignores case too, and each file is fetched under the capitals it was captured with.

Only sites can be asked for. A line beginning `book:`, and the address of a single file such as `example.org/some_book.pdf` or a wiki's `File:` page, is ignored and left as it is. [book-export-design.md](book-export-design.md) has the plan for books.

The request appears on the Collection page under "Requested by Scriptorium", where the researcher starts the copy; nothing starts by itself, because a copy takes half an hour or more of polite requests to the archive. When the copy is complete, this program adds `[done <date>]` to the end of the line:

```
geocities.com/example The author's own site [done 2026-01-20]
```

A request for a site whose copy is already complete is marked done the next time the Collection page is opened.
