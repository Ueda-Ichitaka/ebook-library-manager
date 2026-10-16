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
docker compose run --rm detect-moves                        #    recognise moved/renamed input files
docker compose run --rm relookup                            #    ask the sources again for review books
docker compose run --rm rescore                             #    re-decide after rule changes (offline)
docker compose up review                                    # 2. review at http://localhost:8000
docker compose run --rm build                               # 3. write accepted books to output/
docker compose run --rm report                              # work/report.md, all runs
docker compose up -d kavita                                 # look at output/ in Kavita at :5000
docker compose run --rm kavita-check --scan                 # 4. rescan Kavita and check every book
```

**1. Identify.** Every new book (a group of files that are the same book) gets
metadata:
- **calibre `.opf` next to the book:** it's accepted as it is, because you already
  checked those by hand.
- **Otherwise:** the tool reads the text of the first pages and asks the online sources,
  either by ISBN or by title and author (see "Online sources" below).
- **ISBNs on the last pages:** many books (often German ones and EPUBs) print the
  copyright page at the back. Without an ISBN on the first 10 pages, the last 5 pages
  (the end of an EPUB) are searched for numbers right after the word "ISBN". The back
  pages also advertise other books, so these ISBNs are looked up together with title and
  author, and count as evidence only together with the title on the first pages.
- **Auto-accept:** a candidate is accepted automatically only on strict evidence: its ISBN
  is printed on the first pages (or in the filename or `.opf`), its ISBN is printed on
  the last pages and its title on the first pages, or its title and first author appear
  on the first pages.
- **Review:** everything else waits for review. So does every group formed by similar
  filenames, because those files might be different books.

Files that already belong to a known book are skipped, so your decisions are never
overwritten. `relookup` ("Look up review books again" on the Actions page) asks the
sources again for the books waiting for review, e.g. after a source was added; earlier
candidates are kept and decided on together with the new ones. `rescore` applies changed
rules to the stored books again. It uses the stored candidates and makes no online
requests, and it keeps decisions made by you or taken from a calibre `.opf`.

**Moved files.** Re-sorting the input (moving or renaming files) doesn't lose anything.
`detect-moves` ("Detect moved files" on the Actions page) recognises a known file under a
new path, and identify and build run it first, too:
- **same content:** the SHA-256 of every input file is stored (computed once, and again
  only when a file's size or date changes; the first run over 15 GB takes about 10 s), so
  a moved or renamed file is recognised by its content;
- **same filename:** a file that moved before its hash was stored is recognised by its
  filename, but only when exactly one new file has that name.

The book keeps its metadata and review decision, and excluded, ignored and dismissed
states follow the file. The book's category follows its files, and the next build moves
its output folder. If a book's files end up in different categories, it goes back to the
review queue to be split or sorted back. Moves are listed on the Files page and in the
report; known files that are gone without a match show as "missing from the input".

**Online sources.** `sources` in `config.toml` selects them and their order:
- **calibre: Google, calibre: Amazon.com, calibre: Edelweiss:** calibre's built-in
  sources. Amazon.com is off by default: it starts a browser for every book and returned
  no results from this network.
- **Open Library:** its search API, and for an ISBN the search doesn't know, its ISBN
  API. 1 request per second (3 with `openlibrary_contact` set).
- **DNB:** the German national library (free, no registration, CC0 data). Finds German
  books the others miss.
- **Google Books API:** needs your own free API key; without one Google answers every
  request with "429" (the shared quota is zero). Create a key in the Google Cloud console
  (enable "Books API", then Credentials > Create credentials > API key) and put it into
  `api-keys.env` as `GOOGLE_BOOKS_API_KEY=...`. That file is git-ignored and read by
  identify, relookup and the web UI.

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
- **Folder overview** (next to the review queue), to see which folders can go back into
  the live library:
  - *Input Folders:* every input folder and subfolder with its files done/total. ✅ means
    complete: every file is done (built, excluded, ignored, not books, skipped), no book
    from it is mislabeled without your decision or still to be moved by the next build,
    and every book built from it passed the last Kavita check after its build. Otherwise
    the open points are listed.
  - *Output Folders:* every output folder down to the category/topic (book folders are
    counted) with its books and files. ✅ means ready: its input folder is complete, every
    book in it passed Kavita, no book is still to be built into it, and Kavita showed no
    leftover file in it. The "live" checkbox remembers that you copied the folder to the
    live library; if anything in it changes afterwards, it says so.
  - *Moved:* re-sorted input folders (old -> new folder, with the files), mislabeled books
    you moved to another category, and what is in `_mislabeled`, `_ignored` and
    `_misplaced`, grouped by the input folder it came from.

  Kavita results come from "Check in Kavita" / "Rescan Kavita and check", which store the
  result per book.
- **Actions:** a button for every command (identify, detect moved files, look up review books again,
  re-decide, build, report, Kavita rescan + check). Each runs as a background job, one at a time, with a live
  log and a stop button.
- **Files:** the state of every input file (skipped, not identified yet, waiting for
  review, accepted, built, build failed, excluded duplicate, missing from the input) with
  progress per placement, and the files recognised as moved.
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

**Not books.** A whole category without books (magazines, woodwork blueprints) can be
flagged with the "Not books" button in its section of the review queue or on the Files
page. All its files, subfolders included, leave identify, review, duplicates and build;
`build` removes books already built from it and copies all its files unchanged to
`output/_misplaced/`, with the same paths as in `input/` (folder name: `misplaced_folder`
in `config.toml`). Flagged categories are listed on the Excluded Files page, where they
can be restored.

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

**Damaged downloads.** Some downloads are broken: the download site's rate-limit message
("Too many downloads at the same time from the same IP address...") was written over the
first 154 bytes of the file. Identify, build and "Detect moved files" check the input for
this, and for files whose name carries an MD5 (Anna's Archive) that differs from their
content while the file can't be read. The input is never changed; the build repairs a
work copy when nothing is lost:
- **PDF:** the header and the start of the overwritten object are rebuilt; what was lost
  there (e.g. a print color profile entry) is dropped. All pages stay.
- **EPUB:** the zip is rebuilt; a lost `mimetype` or `META-INF/container.xml` is restored.
  If any other entry was hit (e.g. a chapter), the book is not built: re-download it.

Books that can't be restored are skipped by the build without a new attempt until the
file changes. The Files page shows them as "damaged download" with the MD5 to
re-download; the report has a "Damaged downloads" section.

The exit code of `build` is 0 when everything was built and 3 when it ran through but
some books were flagged (details in the report); 1 means it crashed. On the Actions page
exit code 3 shows as "done with flagged books" instead of "failed".

**4. Check in Kavita.** "Rescan Kavita and check" (`kavita-check --scan`) logs in to the
local Kavita using the login in `kavita.env` (git-ignored, filled in by you), rescans all
libraries, and then compares Kavita's series with the built books. Without the rescan,
Kavita would still show the state of its last scan (new books missing, removed ones still
there), so the check always rescans first. Kavita runs only one scan at a time and
postpones scans requested meanwhile by hours, so libraries are scanned one after another.

The problems go to the **Kavita** page of the web UI, per book:
- **Missing:** a built file Kavita doesn't show;
- **Wrong series name:** the series isn't named after the book's title;
- **Spaces differ:** Kavita kept a name from an earlier scan whose words are run together
  (e.g. "FormDerWissenschaftlichenAusarbeitung"). Kavita matches series by name without
  spaces, case and punctuation, so a rebuild doesn't change it;
- **Merged:** one series contains files of several books (the original problem);
- **Not built:** a file in Kavita the build didn't write.

Each problem can be **accepted** (it stays accepted in later checks, and can be undone),
the book can be opened to **edit** its metadata (then build and check again), and a wrong
or run-together series name can be **renamed in Kavita** (through Kavita's API). Names
that only differ in case or punctuation are listed as notes, not problems. A book passes
when it was checked after its last build without open problems; the folder overview uses
this. With open problems the check exits with code 3 ("done with flagged books").

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
| `evidence.py` | text and ISBNs from a book's first pages, ISBNs from its last pages |
| `sources/` | online metadata sources (calibre plugins, Open Library, DNB, Google Books API) |
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
| `moves.py` | recognises known input files that were moved or renamed |
| `damaged.py` | recognises damaged downloads and repairs what can be fully restored |
| `folders.py` | folder overview: input/output trees, complete/ready marks, moved files |
| `fileinfo.py` | size, format, Kavita pages and printed pages of a file |
| `kavita.py` | Kavita API client (login, libraries, sequential scans, series) |
| `kavita_verify.py` | compares Kavita's series with the built books |
| `__main__.py` | command line |

## License

MIT, see [LICENSE](LICENSE).
