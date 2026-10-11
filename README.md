# ebookprep

Prepares the ebook library in `input/` for Kavita and writes the result to `output/`.
Every book gets its own folder, embedded metadata whose series equals the book title,
a cover image and a calibre `.opf` file, so Kavita lists every book on its own.

## Why books end up in a folder-named series

Kavita (v0.9.1.4) never reads `.opf` files next to a book. It takes the series from
the metadata inside the file:

- **PDF:** calibre series, otherwise the title, otherwise the name of the folder holding the file.
- **EPUB:** calibre series, otherwise the title.

In the current library this goes wrong in three ways:

- calibre's PDF writer fails on about 40% of the PDFs because their structure is damaged.
  It still exits with status 0.
- Kavita itself can't read the damaged PDFs, so it uses the folder name.
- Some EPUBs have the category name embedded as their series (e.g. `Pentesting #14`).

The tool repairs PDFs before writing metadata. It always overwrites the series with the
title, and it checks every output file the way Kavita reads it.

## Layout

Each book goes into `Library/Category/Book/`. Categories listed under
`topic_categories` in `config.toml` keep their topic folders instead, giving
`Library/Category/Topic/Book/` (currently `IT/Security`).

## Workflow

`input/` is mounted read-only. Results go to `output/`. The SQLite state (`state.db`),
temporary files and reports go to `work/`. Add `--only IT/Android` (any Library/Category
path) to any command to limit it.

```sh
docker compose build identify
docker compose run -d --name ebookprep-identify identify    # 1. decide metadata of new books
docker compose run --rm rescore                             #    re-decide after rule changes (offline)
docker compose up review                                    # 2. review at http://localhost:8000
docker compose run --rm build                               # 3. write accepted books to output/
docker compose run --rm report                              # work/report.md, all runs
docker compose up -d kavita                                 # look at output/ in Kavita at :5000
```

**1. Identify.** Every new book (a group of files that are the same book) gets
metadata:
- **calibre `.opf` next to the book:** it's accepted as it is, because you already
  checked those by hand.
- **Otherwise:** the tool reads the text of the first pages and asks the online sources,
  either by ISBN or by title and author. The sources are calibre's built-in ones and the
  Open Library API, throttled to 1 request per second.
- **Auto-accept:** a candidate is accepted automatically only on strict evidence: its ISBN
  is printed in the book (or in the filename or `.opf`), or its title and first author
  appear on the first pages.
- **Review:** everything else waits for review. So does every group formed by similar
  filenames, because those files might be different books.

Files that already belong to a known book are skipped, so your decisions are never
overwritten. `rescore` applies changed rules to the stored books again. It uses the
stored candidates and makes no online requests, and it keeps decisions made by you or
taken from a calibre `.opf`.

When a candidate is accepted, a local author with the same surname keeps the local
spelling, unless the candidate's full name is printed in the book. This protects against
junk author strings from the sources.

Some files contain a different book than their name says. In that case the evidence (an
ISBN, or title and author on the first pages) identifies the real content. Such a book
gets the metadata of its real content and is built into `output/_mislabeled/<original
placement>/`, outside the library folders, so Kavita doesn't show it. The folder name is
set by `mislabeled_folder` in `config.toml`. The report lists these books with their old
title, new title, input file and output folder.

**2. Review.** The web UI lists the books waiting for review. On a book page you can:
- open its files in the browser;
- see the first-page text and the ISBNs found in it;
- compare all candidates, with checks for ISBN, title and author;
- copy a candidate into the form with "Use", correct any field, and "Accept";
- split a group whose files are different books.

The UI has no login and only listens on localhost.

**3. Build.** Only accepted books are written to `output/`, and only when their metadata
changed since the last build. Each build of a book works like this:
1. Damaged PDFs are repaired, and MOBI/AZW files are converted to EPUB (the original is
   kept).
2. The book gets its metadata in its own temporary calibre library, with the series set
   to the title, and is exported as book file(s), `.opf` and `.jpg`.
3. Every exported PDF/EPUB is checked the way Kavita reads it. calibre sometimes fails to
   write metadata without an error, so a failed check is retried once. For a PDF, the
   embedded XMP packet is removed first and logged in the report. A second failure skips
   and flags the book.
4. A rebuild replaces the book's previous folder only after the new build succeeded.

The exit code of `build` is 1 if any book was flagged; details are in the report.

`sources` in `config.toml` selects the online sources and their order. calibre's
Amazon.com source is off by default: it starts a browser for every book and returned no
results from this network.

The optional `openlibrary_contact` in `config.toml` (e.g. your email address) is sent to
Open Library and raises its rate limit to 3 requests per second.

## Running the tests

Everything runs in Docker with pinned calibre and Python versions:

```sh
docker compose build tests
docker compose run --rm tests
```

## Code

| Module | Purpose |
|---|---|
| `config.py` | loads `config.toml` |
| `scan.py` | finds books, sidecar files and skipped files in `input/` |
| `filenames.py` | extracts title, authors, year and ISBN from filenames |
| `grouping.py` | groups copies of the same book into one book folder |
| `naming.py` | builds safe, unique `Main title - First author` names |
| `pdf_repair.py` | rewrites damaged PDFs with pikepdf |
| `kavita_check.py` | predicts the series Kavita shows for a file |
| `metadata.py` | book metadata, `.opf` reading, junk detection, local resolution |
| `calibre.py` | wrapper around calibredb, ebook-meta and ebook-convert |
| `build.py` | builds one book's calibre records with check, retry and flagging |
| `state.py` | SQLite state (books, candidates, build records) and reports |
| `evidence.py` | text and ISBNs from a book's first pages |
| `sources/` | online metadata sources (calibre plugins, Open Library) |
| `scoring.py` | checks candidates against evidence, auto-accept rule |
| `identify.py` | identify stage: decides each book's metadata |
| `pipeline.py` | build stage: writes accepted books to output |
| `web/` | review web UI (FastAPI, htmx) |
| `__main__.py` | command line |
