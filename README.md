# Ebook Library Manager (ebookprep)

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
docker compose run --rm kavita-check --scan                 # 4. rescan Kavita and verify every book
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

**Web UI.** `docker compose up -d review` serves the Ebook Library Manager at
http://localhost:8000:
- **Review Queue:** books waiting for review, in expandable sections per placement. A
  checkbox hides image files (pictures saved as PDF, e.g. `Plan.jpg.pdf`). The Update
  button brings the queue in step with decisions made in the duplicates view.
- **Actions:** a button for every command (identify, re-decide, build, report, Kavita
  check, Kavita rescan + check). Each runs as a background job, one at a time, with a live
  log and a stop button.
- **Files:** the state of every input file (skipped, not identified yet, waiting for
  review, accepted, built, build failed, excluded duplicate) with progress per placement.
- **Report:** the report with a table of contents.
- **Mislabeled:** books whose file contains a different book than its name says. By
  default they are built into `output/_mislabeled/`, outside the libraries. Here you can
  move such a book to any category: the next build puts it there and Kavita shows it,
  while `input/` stays untouched. You can also move it back, or mark it as not mislabeled
  when the detection was wrong.
- **Duplicates** and **Excluded Files:** see below.

**2. Review.** The web UI lists the books waiting for review. On a book page you can:
- open its files in the browser;
- see the first-page text and the ISBNs found in it;
- compare all candidates, with checks for ISBN, title and author;
- copy a candidate into the form with "Use", correct any field, and "Accept";
- type or correct an ISBN and click "Look up ISBN". The sources are asked by that ISBN, the
  results are checked against the book and added to the candidates, and the form is
  filled with the best one, ready to check and accept;
- split a group whose files are different books;
- ignore a book that isn't one (e.g. a picture saved as PDF), which moves its files to the
  skipped files; ignored files can be restored on the Excluded Files page. `build` keeps
  unchanged copies of all ignored files in `output/_ignored/`, with the same paths as in
  `input/` (folder name: `ignored_folder` in `config.toml`).

The UI has no login and only listens on localhost.

**Duplicates.** The review UI's "duplicates" page lists groups of files that may be the
same book, in three sections:
- **Certain:** the files of one book (e.g. a PDF and an EPUB), the same ISBN, or the same
  title and a shared author (any author of either book).
- **Likely:** the same main title (without subtitle, edition and remarks in parentheses,
  e.g. "Serious Cryptography, 2nd Edition: …") and a shared author, or an unknown author.
- **Possible:** a shared author and a title that starts like the other one (e.g. "Windows
  Security Internals" / "… with PowerShell"), or the same main title by different authors.

A group appears in the strictest section that finds it. A looser section lists it again
only when it adds more books.

For each file, a group shows path, format, size, Kavita pages and estimated printed pages.
For an EPUB, Kavita counts one page per HTML document, so an EPUB made of a few large
documents shows only a few pages and coarse reading progress. Printed pages are estimated
at 250 words per page.

Select the file(s) to keep: the group's other files are excluded from the build. If you
keep several files, they stay a group until you keep only one or mark them as not
duplicates. "Not duplicates" on a group whose files were only grouped by similar names
splits it into separate books. Resolving a group also updates the review queue.
Excluded files stay untouched in `input/`, are listed in the report, and can be restored
on the Excluded Files page.

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

The series is the title. Kavita would merge books with the same title and a common format
within a library, so only for such a clash the series becomes "Title (First author)", for
example "Wicca (Scott Cunningham)". Files excluded as duplicates are left out: a book that
loses files is rebuilt, and a book whose files are all excluded is removed from `output/`.

The exit code of `build` is 1 if any book was flagged; details are in the report.

`sources` in `config.toml` selects the online sources and their order. calibre's
Amazon.com source is off by default: it starts a browser for every book and returned no
results from this network.

**4. Check in Kavita.** `kavita-check` logs in to the local Kavita using the login in
`kavita.env` (git-ignored, filled in by you). With `--scan` it rescans the libraries
first. It then compares Kavita's series with the built books:
- every built PDF/EPUB must be there;
- each book must be its own series, named exactly after its title;
- no series may contain files of several books (the original problem);
- nothing unexpected may show up.

Kavita runs only one scan at a time and postpones scans requested meanwhile by hours, so
libraries are scanned one after another.

The Kavita preview's configuration (admin account, libraries) lives in `kavita-config/`
(git-ignored).

The optional `openlibrary_contact` in `config.toml` (e.g. your email address) is sent to
Open Library and raises its rate limit to 3 requests per second.

## Running the tests

Everything runs in Docker with pinned calibre and Python versions:

```sh
docker compose build tests
docker compose run --rm tests
```

The end-to-end test runs the real pipeline into a fresh Kavita 0.9.1.4 (the server's
version) and checks Kavita's series through its API. As a negative control, untreated
PDFs in a category folder must show up merged into a folder-named series:

```sh
docker compose --profile e2e build e2e
docker compose --profile e2e run --rm e2e
docker compose --profile e2e rm -sf kavita-e2e      # removes the test Kavita and its data
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
| `web/` | web UI "Ebook Library Manager" (FastAPI, htmx) |
| `jobs.py` | runs commands as background jobs for the web UI |
| `commands.py` | the commands the web UI can start |
| `progress.py` | state of every input file for the Files page |
| `report.py` | report data, Markdown and web page |
| `series.py` | Kavita series names, "Title (Author)" on title clashes |
| `duplicates.py` | finds groups of possible duplicate files |
| `fileinfo.py` | size, format, Kavita pages and printed pages of a file |
| `kavita.py` | Kavita API client (login, libraries, sequential scans, series) |
| `kavita_verify.py` | compares Kavita's series with the built books |
| `__main__.py` | command line |

## License

MIT, see [LICENSE](LICENSE).
