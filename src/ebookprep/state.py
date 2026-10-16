"""About this file: the SQLite store of the pipeline and the Markdown reports.

Books: the identify stage stores every book (group of source files) with its local
metadata, the evidence read from its first pages, the online candidates and their checks,
and its status: "accepted" (calibre sidecar, automatic on strict evidence, or approved by
the user) or "review". The build stage builds accepted books whose metadata differs from
what was last built, and records what it built.

Duplicates: when the reviewer keeps one file of a group of possible duplicates, the other
files are recorded as excluded (with the kept file); the build leaves them out, and they
can be restored. Dismissed duplicate groups are remembered by their key. Files the
reviewer ignores (e.g. pictures saved as PDF) are recorded as ignored: they leave the
review, are not built and count as skipped; they can be restored as well. Categories the
reviewer flags as not books (magazines, blueprints) leave all their files out of
identify, review and build, including subfolders; the build copies them unchanged to a
separate folder. The flag can be removed again.

Moved files: the content hash (SHA-256) of every input file is kept with its size and
modification time, so a file that turns up under another path can be recognised (see
moves.py). A move renames the path everywhere (books, built sources, excluded and ignored
files, hashes) and is recorded.

Damaged downloads: the result of inspecting each input file for download damage, with its
size and modification time so it is only inspected again after a change (see damaged.py).

Kavita: the issues of the last Kavita check are kept (one per affected file, see
kavita_verify.py), and per built book when it was checked and its open problems. The
reviewer can accept an issue (remembered by kind, file and names, so it stays accepted in
later checks) or mark it fixed (after renaming the series in Kavita, until the next
check). A book passed when it was checked after its last build without open problems. Output folders the user
copied to the live library are marked with the date (see folders.py).

Jobs: commands started from the web UI (identify, build, ...) with status, exit code
and log file.

Records: every build run is recorded with the outcome of each calibre record it built
(sources, outputs, metadata used, repairs, conversions, retries with the removed XMP
packets, errors) and the input files it skipped. The review web UI reads and writes the
same database; WAL mode lets it do that while a run is in progress. Human-readable reports
are generated from the database (see report.py).
"""

import json
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import asdict, dataclass, fields
from datetime import UTC, datetime
from pathlib import Path

from ebookprep.filenames import parse_filename
from ebookprep.metadata import BookMetadata

_JOBS_SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('running', 'succeeded', 'flagged', 'failed', 'stopped', 'interrupted')),
    log_path TEXT NOT NULL,
    exit_code INTEGER,
    started_at TEXT NOT NULL,
    finished_at TEXT
);
"""

_SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
    id INTEGER PRIMARY KEY,
    input_root TEXT NOT NULL,
    output_root TEXT NOT NULL,
    started_at TEXT NOT NULL,
    finished_at TEXT
);
CREATE TABLE IF NOT EXISTS records (
    id INTEGER PRIMARY KEY,
    run_id INTEGER NOT NULL REFERENCES runs(id),
    placement TEXT NOT NULL,
    book_name TEXT NOT NULL,
    record_name TEXT NOT NULL,
    sources TEXT NOT NULL,
    outputs TEXT NOT NULL,
    title TEXT NOT NULL,
    authors TEXT NOT NULL,
    metadata_source TEXT NOT NULL,
    notes TEXT NOT NULL,
    needs_review INTEGER NOT NULL,
    group_reason TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('done', 'failed')),
    retried INTEGER NOT NULL,
    removed_xmp TEXT NOT NULL,
    repaired TEXT NOT NULL,
    converted TEXT NOT NULL,
    error TEXT,
    processed_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS books (
    id INTEGER PRIMARY KEY,
    key TEXT NOT NULL UNIQUE,
    placement TEXT NOT NULL,
    group_reason TEXT NOT NULL,
    book_folder TEXT,
    sources TEXT NOT NULL,
    local_metadata TEXT NOT NULL,
    local_source TEXT NOT NULL,
    local_notes TEXT NOT NULL,
    evidence_excerpt TEXT NOT NULL,
    evidence_isbns TEXT NOT NULL,
    lookup_errors TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('accepted', 'review')),
    decided_by TEXT NOT NULL,
    review_reasons TEXT NOT NULL,
    metadata TEXT NOT NULL,
    mislabeled INTEGER NOT NULL DEFAULT 0,
    built_metadata TEXT,
    built_folder TEXT,
    built_series TEXT,
    built_sources TEXT,
    target_placement TEXT,
    evidence_back_isbns TEXT NOT NULL DEFAULT '[]',
    built_at TEXT,
    kavita_checked_at TEXT,
    kavita_problem TEXT,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS candidates (
    id INTEGER PRIMARY KEY,
    book_id INTEGER NOT NULL REFERENCES books(id),
    rank INTEGER NOT NULL,
    source TEXT NOT NULL,
    metadata TEXT NOT NULL,
    cover_url TEXT,
    score REAL NOT NULL,
    isbn_match INTEGER NOT NULL,
    title_in_text INTEGER NOT NULL,
    author_in_text INTEGER NOT NULL,
    auto_acceptable INTEGER NOT NULL,
    back_isbn_match INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS excluded_sources (
    path TEXT PRIMARY KEY,
    kept TEXT NOT NULL,
    duplicate_group TEXT NOT NULL,
    excluded_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS ignored_sources (
    path TEXT PRIMARY KEY,
    ignored_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS not_book_categories (
    placement TEXT PRIMARY KEY,
    flagged_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS file_hashes (
    path TEXT PRIMARY KEY,
    size INTEGER NOT NULL,
    mtime_ns INTEGER NOT NULL,
    sha256 TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS moved_files (
    id INTEGER PRIMARY KEY,
    old_path TEXT NOT NULL,
    new_path TEXT NOT NULL,
    method TEXT NOT NULL,
    book_id INTEGER,
    moved_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS download_checks (
    path TEXT PRIMARY KEY,
    size INTEGER NOT NULL,
    mtime_ns INTEGER NOT NULL,
    damage TEXT,
    repairable INTEGER NOT NULL,
    note TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS kavita_issues (
    id INTEGER PRIMARY KEY,
    kind TEXT NOT NULL,
    path TEXT NOT NULL,
    book_id INTEGER,
    expected TEXT NOT NULL,
    actual TEXT NOT NULL,
    series_id INTEGER,
    fixed_at TEXT
);
CREATE TABLE IF NOT EXISTS kavita_acceptances (
    kind TEXT NOT NULL,
    path TEXT NOT NULL,
    expected TEXT NOT NULL,
    actual TEXT NOT NULL,
    accepted_at TEXT NOT NULL,
    PRIMARY KEY (kind, path, expected, actual)
);
CREATE TABLE IF NOT EXISTS live_folders (
    path TEXT PRIMARY KEY,
    marked_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS dismissed_duplicates (
    duplicate_group TEXT PRIMARY KEY,
    dismissed_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS skipped (
    run_id INTEGER NOT NULL REFERENCES runs(id),
    path TEXT NOT NULL,
    reason TEXT NOT NULL
);
""" + _JOBS_SCHEMA

# RecordEntry fields stored as JSON arrays.
_JSON_FIELDS = ("sources", "outputs", "authors", "notes", "removed_xmp", "repaired", "converted")


@dataclass(frozen=True)
class RecordEntry:
    """The stored result of one calibre record. Paths are relative to input/output root."""

    placement: str
    book_name: str
    record_name: str
    sources: tuple[str, ...]
    outputs: tuple[str, ...]
    title: str
    authors: tuple[str, ...]
    metadata_source: str
    notes: tuple[str, ...]
    needs_review: bool
    group_reason: str
    status: str
    retried: bool
    removed_xmp: tuple[tuple[str, str], ...]
    repaired: tuple[str, ...]
    converted: tuple[str, ...]
    error: str | None

    @property
    def flagged(self) -> bool:
        return self.status == "failed"


@dataclass(frozen=True)
class StoredCandidate:
    source: str
    metadata: BookMetadata
    cover_url: str | None
    score: float
    isbn_match: bool
    title_in_text: bool
    author_in_text: bool
    auto_acceptable: bool
    # its ISBN is printed on the book's last pages (weaker than isbn_match)
    back_isbn_match: bool = False


@dataclass(frozen=True)
class NewBook:
    """A book as found by the identify stage. Paths are relative to the input root."""

    key: str
    placement: str
    group_reason: str
    book_folder: str | None
    sources: tuple[str, ...]
    local_metadata: BookMetadata
    local_source: str
    local_notes: tuple[str, ...]
    evidence_excerpt: str
    evidence_isbns: tuple[str, ...]
    lookup_errors: tuple[tuple[str, str], ...]
    status: str
    decided_by: str
    review_reasons: tuple[str, ...]
    metadata: BookMetadata
    mislabeled: bool = False
    candidates: tuple[StoredCandidate, ...] = ()
    # ISBNs after the word "ISBN" on the last pages (see evidence.py)
    evidence_back_isbns: tuple[str, ...] = ()


@dataclass(frozen=True)
class BookRow:
    id: int
    key: str
    placement: str
    group_reason: str
    book_folder: str | None
    sources: tuple[str, ...]
    local_metadata: BookMetadata
    local_source: str
    local_notes: tuple[str, ...]
    evidence_excerpt: str
    evidence_isbns: tuple[str, ...]
    lookup_errors: tuple[tuple[str, str], ...]
    status: str
    decided_by: str
    review_reasons: tuple[str, ...]
    # the accepted metadata, or the proposal while the book waits for review
    metadata: BookMetadata
    # the file contains a different book than its filename or .opf says
    mislabeled: bool
    built_metadata: BookMetadata | None
    built_folder: str | None
    # the series name written by the last build (title, or "Title (Author)" on a clash)
    built_series: str | None = None
    # the source files the last build used (all sources minus excluded duplicates)
    built_sources: tuple[str, ...] | None = None
    # where a mislabeled book was moved to in the review (Library/Category), None if not moved
    target_placement: str | None = None
    # ISBNs after the word "ISBN" on the last pages (see evidence.py)
    evidence_back_isbns: tuple[str, ...] = ()
    # when the last build finished, and the result of the last Kavita check
    built_at: str | None = None
    kavita_checked_at: str | None = None
    kavita_problem: str | None = None

    @property
    def kavita_passed(self) -> bool:
        """Checked in Kavita after its last build, without a problem."""
        checked_after_build = self.kavita_checked_at is not None and (
            self.built_at is None or self.kavita_checked_at >= self.built_at
        )
        return self.built_folder is not None and checked_after_build and self.kavita_problem is None

    @property
    def needs_build(self) -> bool:
        return self.status == "accepted" and self.built_metadata != self.metadata


_BOOK_JSON_FIELDS = ("sources", "local_notes", "evidence_isbns", "lookup_errors", "review_reasons", "evidence_back_isbns")
_BOOK_OPTIONAL_JSON_FIELDS = ("built_sources",)


@dataclass(frozen=True)
class JobRow:
    id: int
    name: str
    status: str
    log_path: str
    exit_code: int | None
    started_at: str
    finished_at: str | None


@dataclass(frozen=True)
class IgnoredSource:
    path: str
    ignored_at: str


@dataclass(frozen=True)
class FileHash:
    path: str
    size: int
    mtime_ns: int
    sha256: str


@dataclass(frozen=True)
class DownloadCheck:
    """The result of inspecting an input file for download damage (see damaged.py)."""

    path: str
    size: int
    mtime_ns: int
    # None when the file is fine
    damage: str | None
    repairable: bool
    note: str


@dataclass(frozen=True)
class DownloadDamage:
    path: str
    damage: str
    repairable: bool
    # what a repair restores, or why it can't and what to do
    note: str


KAVITA_PROBLEM_KINDS = ("missing", "wrong_name", "spacing", "merged", "unexpected")


@dataclass(frozen=True)
class KavitaIssue:
    """Something a Kavita check found for one file. Kinds: the problems in
    KAVITA_PROBLEM_KINDS, and "case" (Kavita shows a name that only differs in case or
    punctuation; a note, never a problem)."""

    kind: str
    path: str
    book_id: int | None = None
    expected: str = ""
    actual: str = ""
    series_id: int | None = None
    id: int | None = None
    accepted: bool = False
    fixed: bool = False

    @property
    def open(self) -> bool:
        return self.kind in KAVITA_PROBLEM_KINDS and not self.accepted and not self.fixed

    @property
    def text(self) -> str:
        name = self.path.rsplit("/", 1)[-1]
        return {
            "missing": f"missing in Kavita: {name}",
            "wrong_name": f"wrong name: in series {self.actual!r}, expected {self.expected!r}",
            "spacing": f"Kavita shows {self.actual!r} instead of {self.expected!r} (spaces differ)",
            "merged": f"merged: series {self.actual!r} contains files of several books",
            "unexpected": f"in Kavita but not built: {self.path}",
            "case": f"Kavita shows {self.actual!r} instead of {self.expected!r} (only case or punctuation differ)",
        }[self.kind]


@dataclass(frozen=True)
class MovedFile:
    old_path: str
    new_path: str
    # how the move was recognised: "same content" or "same filename"
    method: str
    book_id: int | None
    moved_at: str


@dataclass(frozen=True)
class NotBookCategory:
    placement: str
    flagged_at: str


def is_under(placement: str, categories: set[str]) -> bool:
    """Whether a placement is one of the categories or a folder inside one."""
    return any(placement == c or placement.startswith(c + "/") for c in categories)


@dataclass(frozen=True)
class ExcludedSource:
    path: str
    kept: str
    group: str
    excluded_at: str
_BOOK_METADATA_FIELDS = ("local_metadata", "metadata", "built_metadata")


def metadata_to_json(metadata: BookMetadata | None) -> str | None:
    return None if metadata is None else json.dumps(asdict(metadata), ensure_ascii=False)


def metadata_from_json(text: str | None) -> BookMetadata | None:
    if text is None:
        return None
    data = json.loads(text)
    return BookMetadata(**{k: tuple(v) if isinstance(v, list) else v for k, v in data.items()})


class StateStore:
    def __init__(self, path: Path):
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as db:
            db.execute("PRAGMA journal_mode=WAL")
            db.executescript(_SCHEMA)
            _migrate(db)

    def start_run(self, input_root: str, output_root: str) -> int:
        with self._connect() as db:
            cursor = db.execute(
                "INSERT INTO runs (input_root, output_root, started_at) VALUES (?, ?, ?)",
                (input_root, output_root, _now()),
            )
            return int(cursor.lastrowid)

    def finish_run(self, run_id: int) -> None:
        with self._connect() as db:
            db.execute("UPDATE runs SET finished_at = ? WHERE id = ?", (_now(), run_id))

    def add_record(self, run_id: int, entry: RecordEntry) -> None:
        values = {f.name: getattr(entry, f.name) for f in fields(RecordEntry)}
        for name in _JSON_FIELDS:
            values[name] = json.dumps(values[name], ensure_ascii=False)
        values["needs_review"] = int(entry.needs_review)
        values["retried"] = int(entry.retried)
        columns = ["run_id", *values, "processed_at"]
        with self._connect() as db:
            db.execute(
                f"INSERT INTO records ({', '.join(columns)}) VALUES ({', '.join('?' for _ in columns)})",
                (run_id, *values.values(), _now()),
            )

    def add_skipped(self, run_id: int, path: str, reason: str) -> None:
        with self._connect() as db:
            db.execute("INSERT INTO skipped (run_id, path, reason) VALUES (?, ?, ?)", (run_id, path, reason))

    def records(self, run_id: int) -> list[RecordEntry]:
        with self._connect() as db:
            rows = db.execute("SELECT * FROM records WHERE run_id = ? ORDER BY id", (run_id,)).fetchall()
        return [_entry_from_row(row) for row in rows]

    def skipped(self, run_id: int) -> list[tuple[str, str]]:
        with self._connect() as db:
            rows = db.execute("SELECT path, reason FROM skipped WHERE run_id = ? ORDER BY path", (run_id,)).fetchall()
        return [(row["path"], row["reason"]) for row in rows]

    def latest_records(self) -> list[RecordEntry]:
        """The newest record for each set of sources, across all runs."""
        with self._connect() as db:
            rows = db.execute("SELECT * FROM records ORDER BY id").fetchall()
        latest = {row["sources"]: row for row in rows}
        return [_entry_from_row(row) for row in latest.values()]

    def all_skipped(self) -> list[tuple[str, str]]:
        with self._connect() as db:
            rows = db.execute("SELECT DISTINCT path, reason FROM skipped ORDER BY path").fetchall()
        return [(row["path"], row["reason"]) for row in rows]

    # --- books

    def known_sources(self) -> set[str]:
        """Every source file that belongs to a stored book."""
        with self._connect() as db:
            rows = db.execute("SELECT sources FROM books").fetchall()
        return {source for row in rows for source in json.loads(row["sources"])}

    def add_book(self, book: NewBook) -> int:
        values = {f.name: getattr(book, f.name) for f in fields(NewBook) if f.name != "candidates"}
        for name in _BOOK_JSON_FIELDS:
            values[name] = json.dumps(values[name], ensure_ascii=False)
        for name in ("local_metadata", "metadata"):
            values[name] = metadata_to_json(values[name])
        values["mislabeled"] = int(book.mislabeled)
        values["updated_at"] = _now()
        with self._connect() as db:
            cursor = db.execute(
                f"INSERT INTO books ({', '.join(values)}) VALUES ({', '.join('?' for _ in values)})",
                tuple(values.values()),
            )
            book_id = int(cursor.lastrowid)
            _insert_candidates(db, book_id, book.candidates)
        return book_id

    def books(self, status: str | None = None) -> list[BookRow]:
        query, params = "SELECT * FROM books", ()
        if status is not None:
            query, params = query + " WHERE status = ?", (status,)
        with self._connect() as db:
            rows = db.execute(query + " ORDER BY placement, id", params).fetchall()
        return [_book_from_row(row) for row in rows]

    def book(self, book_id: int) -> BookRow:
        with self._connect() as db:
            row = db.execute("SELECT * FROM books WHERE id = ?", (book_id,)).fetchone()
        if row is None:
            raise KeyError(book_id)
        return _book_from_row(row)

    def candidates(self, book_id: int) -> list[StoredCandidate]:
        with self._connect() as db:
            rows = db.execute("SELECT * FROM candidates WHERE book_id = ? ORDER BY rank", (book_id,)).fetchall()
        return [
            StoredCandidate(
                source=row["source"],
                metadata=metadata_from_json(row["metadata"]),
                cover_url=row["cover_url"],
                score=row["score"],
                isbn_match=bool(row["isbn_match"]),
                title_in_text=bool(row["title_in_text"]),
                author_in_text=bool(row["author_in_text"]),
                auto_acceptable=bool(row["auto_acceptable"]),
                back_isbn_match=bool(row["back_isbn_match"]),
            )
            for row in rows
        ]

    def add_candidates(self, book_id: int, candidates: tuple[StoredCandidate, ...]) -> int:
        """Adds candidates (e.g. from an ISBN lookup in the review) in front of the stored ones;
        candidates already stored are not added twice. Returns how many were added."""
        existing = {(c.source, c.metadata) for c in self.candidates(book_id)}
        new = tuple(c for c in candidates if (c.source, c.metadata) not in existing)
        if new:
            with self._connect() as db:
                db.execute("UPDATE candidates SET rank = rank + ? WHERE book_id = ?", (len(new), book_id))
                _insert_candidates(db, book_id, new)
        return len(new)

    def decide(self, book_id: int, metadata: BookMetadata, decided_by: str) -> None:
        """Accepts the book with this metadata (e.g. approved in the review UI)."""
        with self._connect() as db:
            db.execute(
                "UPDATE books SET status = 'accepted', metadata = ?, decided_by = ?, updated_at = ? WHERE id = ?",
                (metadata_to_json(metadata), decided_by, _now(), book_id),
            )

    def split_book(self, book_id: int) -> list[int]:
        """Replaces a book made of several files by one book per file, each waiting for review
        with metadata from its filename. Used when a group joined files of different books."""
        book = self.book(book_id)
        new_ids = []
        for source in book.sources:
            info = parse_filename(Path(source).stem)
            metadata = BookMetadata(
                title=info.title, authors=info.authors, isbn=info.isbn, pubdate=str(info.year) if info.year else None
            )
            new_ids.append(
                self.add_book(
                    NewBook(
                        key=json.dumps([source], ensure_ascii=False),
                        placement=book.placement,
                        group_reason="single",
                        book_folder=None,
                        sources=(source,),
                        local_metadata=metadata,
                        local_source="filename",
                        local_notes=(),
                        evidence_excerpt="",
                        evidence_isbns=(),
                        lookup_errors=(),
                        status="review",
                        decided_by="proposal: local",
                        review_reasons=("split from a group in the review",),
                        metadata=metadata,
                    )
                )
            )
        with self._connect() as db:
            db.execute("DELETE FROM candidates WHERE book_id = ?", (book_id,))
            db.execute("DELETE FROM books WHERE id = ?", (book_id,))
        return new_ids

    def replace_decision(
        self,
        book_id: int,
        status: str,
        decided_by: str,
        review_reasons: tuple[str, ...],
        metadata: BookMetadata,
        mislabeled: bool,
        candidates: tuple[StoredCandidate, ...],
        evidence_back_isbns: tuple[str, ...] | None = None,
        lookup_errors: tuple[tuple[str, str], ...] | None = None,
    ) -> None:
        """Stores a new automatic decision for a book (see identify.rescore and relookup),
        and the ISBNs on its last pages and the errors of a new lookup when given."""
        with self._connect() as db:
            if lookup_errors is not None:
                db.execute(
                    "UPDATE books SET lookup_errors = ? WHERE id = ?",
                    (json.dumps(lookup_errors, ensure_ascii=False), book_id),
                )
            if evidence_back_isbns is not None:
                db.execute(
                    "UPDATE books SET evidence_back_isbns = ? WHERE id = ?", (json.dumps(evidence_back_isbns), book_id)
                )
            db.execute(
                "UPDATE books SET status = ?, decided_by = ?, review_reasons = ?, metadata = ?, mislabeled = ?, "
                "updated_at = ? WHERE id = ?",
                (status, decided_by, json.dumps(review_reasons, ensure_ascii=False), metadata_to_json(metadata),
                 int(mislabeled), _now(), book_id),
            )
            db.execute("DELETE FROM candidates WHERE book_id = ?", (book_id,))
            _insert_candidates(db, book_id, candidates)

    def update_review(self, book_id: int, status: str, review_reasons: tuple[str, ...]) -> None:
        with self._connect() as db:
            db.execute(
                "UPDATE books SET status = ?, review_reasons = ?, updated_at = ? WHERE id = ?",
                (status, json.dumps(review_reasons, ensure_ascii=False), _now(), book_id),
            )

    def move_book(self, book_id: int, placement: str | None) -> None:
        """Sets the placement a (mislabeled) book is built into; None builds it where found."""
        with self._connect() as db:
            db.execute(
                "UPDATE books SET target_placement = ?, updated_at = ? WHERE id = ?", (placement, _now(), book_id)
            )

    def set_mislabeled(self, book_id: int, mislabeled: bool) -> None:
        with self._connect() as db:
            db.execute(
                "UPDATE books SET mislabeled = ?, updated_at = ? WHERE id = ?", (int(mislabeled), _now(), book_id)
            )

    def mark_built(
        self,
        book_id: int,
        metadata: BookMetadata,
        folder: str,
        series: str | None = None,
        sources: tuple[str, ...] | None = None,
    ) -> None:
        if sources is None:
            sources = self.book(book_id).sources
        with self._connect() as db:
            db.execute(
                "UPDATE books SET built_metadata = ?, built_folder = ?, built_series = ?, built_sources = ?, "
                "built_at = ?, updated_at = ? WHERE id = ?",
                (metadata_to_json(metadata), folder, series or metadata.title,
                 json.dumps(list(sources), ensure_ascii=False), _precise_now(), _now(), book_id),
            )

    def record_kavita_check(self, checked_books: set[int], issues: list[KavitaIssue]) -> None:
        """Stores a Kavita check: its issues (replacing those of the previous check) and, for
        every checked book, the time and its open problems."""
        checked_at = _precise_now()
        with self._connect() as db:
            db.execute("DELETE FROM kavita_issues")
            db.executemany(
                "INSERT INTO kavita_issues (kind, path, book_id, expected, actual, series_id) VALUES (?, ?, ?, ?, ?, ?)",
                [(i.kind, i.path, i.book_id, i.expected, i.actual, i.series_id) for i in issues],
            )
            db.executemany("UPDATE books SET kavita_checked_at = ? WHERE id = ?", [(checked_at, b) for b in checked_books])
        for book_id in checked_books:
            self._refresh_kavita_problem(book_id)

    def kavita_issues(self) -> list[KavitaIssue]:
        with self._connect() as db:
            rows = db.execute(
                "SELECT i.*, a.accepted_at FROM kavita_issues i LEFT JOIN kavita_acceptances a "
                "ON a.kind = i.kind AND a.path = i.path AND a.expected = i.expected AND a.actual = i.actual "
                "ORDER BY i.kind, i.path"
            ).fetchall()
        return [
            KavitaIssue(r["kind"], r["path"], r["book_id"], r["expected"], r["actual"], r["series_id"], r["id"],
                        accepted=r["accepted_at"] is not None, fixed=r["fixed_at"] is not None)
            for r in rows
        ]

    def kavita_issue(self, issue_id: int) -> KavitaIssue:
        issue = next((i for i in self.kavita_issues() if i.id == issue_id), None)
        if issue is None:
            raise KeyError(issue_id)
        return issue

    def accept_kavita_issue(self, issue_id: int) -> None:
        issue = self.kavita_issue(issue_id)
        with self._connect() as db:
            db.execute(
                "INSERT OR IGNORE INTO kavita_acceptances (kind, path, expected, actual, accepted_at) VALUES (?, ?, ?, ?, ?)",
                (issue.kind, issue.path, issue.expected, issue.actual, _now()),
            )
        self._refresh_kavita_problem(issue.book_id)

    def unaccept_kavita_issue(self, issue_id: int) -> None:
        issue = self.kavita_issue(issue_id)
        with self._connect() as db:
            db.execute(
                "DELETE FROM kavita_acceptances WHERE kind = ? AND path = ? AND expected = ? AND actual = ?",
                (issue.kind, issue.path, issue.expected, issue.actual),
            )
        self._refresh_kavita_problem(issue.book_id)

    def mark_kavita_issue_fixed(self, issue_id: int) -> None:
        issue = self.kavita_issue(issue_id)
        with self._connect() as db:
            db.execute("UPDATE kavita_issues SET fixed_at = ? WHERE id = ?", (_now(), issue_id))
        self._refresh_kavita_problem(issue.book_id)

    def kavita_unexpected(self) -> list[str]:
        """Files of the last check Kavita showed that weren't built, unless accepted."""
        return [i.path for i in self.kavita_issues() if i.kind == "unexpected" and i.open]

    def _refresh_kavita_problem(self, book_id: int | None) -> None:
        if book_id is None:
            return
        problems = [i.text for i in self.kavita_issues() if i.book_id == book_id and i.open]
        with self._connect() as db:
            db.execute("UPDATE books SET kavita_problem = ? WHERE id = ?", ("; ".join(problems) or None, book_id))

    def mark_live(self, path: str) -> None:
        with self._connect() as db:
            db.execute("INSERT OR REPLACE INTO live_folders (path, marked_at) VALUES (?, ?)", (path, _precise_now()))

    def unmark_live(self, path: str) -> None:
        with self._connect() as db:
            db.execute("DELETE FROM live_folders WHERE path = ?", (path,))

    def live_folders(self) -> dict[str, str]:
        """Output folders marked as copied to the live library, with the time of the mark."""
        with self._connect() as db:
            return {r["path"]: r["marked_at"] for r in db.execute("SELECT * FROM live_folders ORDER BY path")}

    def clear_built(self, book_id: int) -> None:
        """Forgets the book's build, after its folder was removed from the output."""
        with self._connect() as db:
            db.execute(
                "UPDATE books SET built_metadata = NULL, built_folder = NULL, built_series = NULL, "
                "built_sources = NULL, updated_at = ? WHERE id = ?",
                (_now(), book_id),
            )

    # --- jobs

    def add_job(self, name: str, log_path: str) -> int:
        with self._connect() as db:
            cursor = db.execute(
                "INSERT INTO jobs (name, status, log_path, started_at) VALUES (?, 'running', ?, ?)",
                (name, log_path, _now()),
            )
            return int(cursor.lastrowid)

    def set_job_log(self, job_id: int, log_path: str) -> None:
        with self._connect() as db:
            db.execute("UPDATE jobs SET log_path = ? WHERE id = ?", (log_path, job_id))

    def finish_job(self, job_id: int, status: str, exit_code: int | None) -> None:
        with self._connect() as db:
            db.execute(
                "UPDATE jobs SET status = ?, exit_code = ?, finished_at = ? WHERE id = ?",
                (status, exit_code, _now(), job_id),
            )

    def job(self, job_id: int) -> JobRow:
        with self._connect() as db:
            row = db.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
        if row is None:
            raise KeyError(job_id)
        return JobRow(**dict(row))

    def jobs(self, limit: int = 50) -> list[JobRow]:
        with self._connect() as db:
            rows = db.execute("SELECT * FROM jobs ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
        return [JobRow(**dict(row)) for row in rows]

    # --- duplicates

    def exclude_sources(self, paths: list[str], kept: str, group: str) -> None:
        with self._connect() as db:
            db.executemany(
                "INSERT OR REPLACE INTO excluded_sources (path, kept, duplicate_group, excluded_at) VALUES (?, ?, ?, ?)",
                [(path, kept, group, _now()) for path in paths],
            )

    def restore_source(self, path: str) -> None:
        with self._connect() as db:
            db.execute("DELETE FROM excluded_sources WHERE path = ?", (path,))

    def excluded_sources(self) -> list[ExcludedSource]:
        with self._connect() as db:
            rows = db.execute("SELECT * FROM excluded_sources ORDER BY path").fetchall()
        return [ExcludedSource(r["path"], r["kept"], r["duplicate_group"], r["excluded_at"]) for r in rows]

    def excluded_paths(self) -> set[str]:
        return {e.path for e in self.excluded_sources()}

    def ignore_sources(self, paths: list[str]) -> None:
        with self._connect() as db:
            db.executemany(
                "INSERT OR REPLACE INTO ignored_sources (path, ignored_at) VALUES (?, ?)",
                [(path, _now()) for path in paths],
            )

    def restore_ignored(self, path: str) -> None:
        with self._connect() as db:
            db.execute("DELETE FROM ignored_sources WHERE path = ?", (path,))

    def ignored_sources(self) -> list[IgnoredSource]:
        with self._connect() as db:
            rows = db.execute("SELECT * FROM ignored_sources ORDER BY path").fetchall()
        return [IgnoredSource(r["path"], r["ignored_at"]) for r in rows]

    def ignored_paths(self) -> set[str]:
        return {i.path for i in self.ignored_sources()}

    def flag_not_books(self, placement: str) -> None:
        with self._connect() as db:
            db.execute(
                "INSERT OR IGNORE INTO not_book_categories (placement, flagged_at) VALUES (?, ?)", (placement, _now())
            )

    def restore_books(self, placement: str) -> None:
        with self._connect() as db:
            db.execute("DELETE FROM not_book_categories WHERE placement = ?", (placement,))

    def not_book_categories(self) -> list[NotBookCategory]:
        with self._connect() as db:
            rows = db.execute("SELECT * FROM not_book_categories ORDER BY placement").fetchall()
        return [NotBookCategory(r["placement"], r["flagged_at"]) for r in rows]

    def not_book_paths(self) -> set[str]:
        """Source files of the stored books in categories flagged as not books."""
        categories = {c.placement for c in self.not_book_categories()}
        if not categories:
            return set()
        return {s for b in self.books() if is_under(b.placement, categories) for s in b.sources}

    def unused_paths(self) -> set[str]:
        """Source files that are not built: excluded duplicates, ignored files and files in
        categories that are not books."""
        return self.excluded_paths() | self.ignored_paths() | self.not_book_paths()

    def file_hashes(self) -> dict[str, FileHash]:
        with self._connect() as db:
            rows = db.execute("SELECT * FROM file_hashes").fetchall()
        return {r["path"]: FileHash(r["path"], r["size"], r["mtime_ns"], r["sha256"]) for r in rows}

    def save_file_hashes(self, hashes: list[FileHash]) -> None:
        with self._connect() as db:
            db.executemany(
                "INSERT OR REPLACE INTO file_hashes (path, size, mtime_ns, sha256) VALUES (?, ?, ?, ?)",
                [(h.path, h.size, h.mtime_ns, h.sha256) for h in hashes],
            )

    def download_checks(self) -> dict[str, DownloadCheck]:
        with self._connect() as db:
            rows = db.execute("SELECT * FROM download_checks").fetchall()
        return {
            r["path"]: DownloadCheck(r["path"], r["size"], r["mtime_ns"], r["damage"], bool(r["repairable"]), r["note"])
            for r in rows
        }

    def save_download_checks(self, checks: list[DownloadCheck]) -> None:
        with self._connect() as db:
            db.executemany(
                "INSERT OR REPLACE INTO download_checks (path, size, mtime_ns, damage, repairable, note) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                [(c.path, c.size, c.mtime_ns, c.damage, int(c.repairable), c.note) for c in checks],
            )

    def download_damage(self) -> dict[str, DownloadDamage]:
        """The input files found to be damaged downloads."""
        return {
            path: DownloadDamage(path, c.damage, c.repairable, c.note)
            for path, c in sorted(self.download_checks().items())
            if c.damage is not None
        }

    def move_source(self, old: str, new: str) -> None:
        """Renames an input path everywhere it is stored."""

        def renamed(paths):
            return [new if p == old else p for p in paths]

        with self._connect() as db:
            for row in db.execute("SELECT id, sources, built_sources FROM books").fetchall():
                sources = json.loads(row["sources"])
                built = json.loads(row["built_sources"]) if row["built_sources"] is not None else None
                if old not in sources and old not in (built or ()):
                    continue
                db.execute(
                    "UPDATE books SET sources = ?, built_sources = ?, key = ? WHERE id = ?",
                    (
                        json.dumps(renamed(sources), ensure_ascii=False),
                        json.dumps(renamed(built), ensure_ascii=False) if built is not None else None,
                        json.dumps(sorted(renamed(sources)), ensure_ascii=False),
                        row["id"],
                    ),
                )
            for row in db.execute("SELECT path, kept FROM excluded_sources").fetchall():
                kept = ", ".join(renamed(row["kept"].split(", ")))
                db.execute(
                    "UPDATE excluded_sources SET path = ?, kept = ? WHERE path = ?",
                    (new if row["path"] == old else row["path"], kept, row["path"]),
                )
            db.execute("UPDATE ignored_sources SET path = ? WHERE path = ?", (new, old))
            db.execute("UPDATE OR IGNORE file_hashes SET path = ? WHERE path = ?", (new, old))
            db.execute("DELETE FROM file_hashes WHERE path = ?", (old,))

    def record_move(self, old: str, new: str, method: str, book_id: int | None) -> None:
        with self._connect() as db:
            db.execute(
                "INSERT INTO moved_files (old_path, new_path, method, book_id, moved_at) VALUES (?, ?, ?, ?, ?)",
                (old, new, method, book_id, _now()),
            )

    def moved_files(self) -> list[MovedFile]:
        with self._connect() as db:
            rows = db.execute("SELECT * FROM moved_files ORDER BY id").fetchall()
        return [MovedFile(r["old_path"], r["new_path"], r["method"], r["book_id"], r["moved_at"]) for r in rows]

    def set_placement(self, book_id: int, placement: str) -> None:
        with self._connect() as db:
            db.execute("UPDATE books SET placement = ?, updated_at = ? WHERE id = ?", (placement, _now(), book_id))

    def require_review(self, book_id: int, reason: str) -> None:
        """Puts a book back into the review queue with an additional reason."""
        reasons = self.book(book_id).review_reasons
        if reason not in reasons:
            reasons = (*reasons, reason)
        with self._connect() as db:
            db.execute(
                "UPDATE books SET status = 'review', review_reasons = ?, updated_at = ? WHERE id = ?",
                (json.dumps(reasons, ensure_ascii=False), _now(), book_id),
            )

    def dismiss_duplicates(self, group: str) -> None:
        with self._connect() as db:
            db.execute(
                "INSERT OR IGNORE INTO dismissed_duplicates (duplicate_group, dismissed_at) VALUES (?, ?)",
                (group, _now()),
            )

    def dismissed_duplicates(self) -> set[str]:
        with self._connect() as db:
            return {r[0] for r in db.execute("SELECT duplicate_group FROM dismissed_duplicates")}

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.path)
        connection.row_factory = sqlite3.Row
        try:
            with connection:
                yield connection
        finally:
            connection.close()


def _book_from_row(row: sqlite3.Row) -> BookRow:
    values = {}
    for f in fields(BookRow):
        value = row[f.name]
        if f.name in _BOOK_JSON_FIELDS:
            value = tuple(tuple(v) if isinstance(v, list) else v for v in json.loads(value))
        elif f.name in _BOOK_OPTIONAL_JSON_FIELDS:
            value = tuple(json.loads(value)) if value is not None else None
        elif f.name in _BOOK_METADATA_FIELDS:
            value = metadata_from_json(value)
        elif f.name == "mislabeled":
            value = bool(value)
        values[f.name] = value
    return BookRow(**values)


def _insert_candidates(db: sqlite3.Connection, book_id: int, candidates: tuple[StoredCandidate, ...]) -> None:
    for rank, candidate in enumerate(candidates):
        db.execute(
            "INSERT INTO candidates (book_id, rank, source, metadata, cover_url, score, isbn_match, "
            "title_in_text, author_in_text, auto_acceptable, back_isbn_match) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                book_id, rank, candidate.source, metadata_to_json(candidate.metadata), candidate.cover_url,
                candidate.score, int(candidate.isbn_match), int(candidate.title_in_text),
                int(candidate.author_in_text), int(candidate.auto_acceptable), int(candidate.back_isbn_match),
            ),
        )


def _migrate(db: sqlite3.Connection) -> None:
    """Adds columns and values introduced after a database was created."""
    jobs_sql = db.execute("SELECT sql FROM sqlite_master WHERE name = 'jobs'").fetchone()[0]
    if "'flagged'" not in jobs_sql:
        # SQLite can't change a CHECK constraint: the table is copied into the current schema.
        db.execute("ALTER TABLE jobs RENAME TO jobs_before_flagged")
        db.executescript(_JOBS_SCHEMA)
        db.execute("INSERT INTO jobs SELECT * FROM jobs_before_flagged")
        db.execute("DROP TABLE jobs_before_flagged")
    columns = {row[1] for row in db.execute("PRAGMA table_info(books)")}
    if "mislabeled" not in columns:
        db.execute("ALTER TABLE books ADD COLUMN mislabeled INTEGER NOT NULL DEFAULT 0")
    if "target_placement" not in columns:
        db.execute("ALTER TABLE books ADD COLUMN target_placement TEXT")
    if "built_sources" not in columns:
        db.execute("ALTER TABLE books ADD COLUMN built_sources TEXT")
        if "built_metadata" in columns:
            # books built before exclusions existed were built from all their sources
            db.execute("UPDATE books SET built_sources = sources WHERE built_metadata IS NOT NULL")
    db.execute("DROP TABLE IF EXISTS kavita_unexpected")
    for column in ("built_at", "kavita_checked_at", "kavita_problem"):
        if column not in columns:
            db.execute(f"ALTER TABLE books ADD COLUMN {column} TEXT")
    if "evidence_back_isbns" not in columns:
        db.execute("ALTER TABLE books ADD COLUMN evidence_back_isbns TEXT NOT NULL DEFAULT '[]'")
    candidate_columns = {row[1] for row in db.execute("PRAGMA table_info(candidates)")}
    if "back_isbn_match" not in candidate_columns:
        db.execute("ALTER TABLE candidates ADD COLUMN back_isbn_match INTEGER NOT NULL DEFAULT 0")
    if "built_series" not in columns:
        db.execute("ALTER TABLE books ADD COLUMN built_series TEXT")
        if "built_metadata" in columns:
            # books built before series names existed were built with series = title
            db.execute(
                "UPDATE books SET built_series = json_extract(built_metadata, '$.title') WHERE built_metadata IS NOT NULL"
            )


def _entry_from_row(row: sqlite3.Row) -> RecordEntry:
    values = {}
    for f in fields(RecordEntry):
        value = row[f.name]
        if f.name in _JSON_FIELDS:
            value = tuple(tuple(v) if isinstance(v, list) else v for v in json.loads(value))
        elif f.name in ("needs_review", "retried"):
            value = bool(value)
        values[f.name] = value
    return RecordEntry(**values)


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def _precise_now() -> str:
    """Time with microseconds, for comparing a build with a later Kavita check."""
    return datetime.now(UTC).isoformat(timespec="microseconds")
