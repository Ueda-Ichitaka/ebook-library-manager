"""About this file: the SQLite store of the pipeline and the Markdown reports.

Books: the identify stage stores every book (group of source files) with its local
metadata, the evidence read from its first pages, the online candidates and their checks,
and its status: "accepted" (calibre sidecar, automatic on strict evidence, or approved by
the user) or "review". The build stage builds accepted books whose metadata differs from
what was last built, and records what it built.

Records: every build run is recorded with the outcome of each calibre record it built
(sources, outputs, metadata used, repairs, conversions, retries with the removed XMP
packets, errors) and the input files it skipped. The review web UI reads and writes the
same database; WAL mode lets it do that while a run is in progress. Human-readable reports
are generated from the database: one per run, and a summary of the newest result of
every book across all runs.
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
    auto_acceptable INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS skipped (
    run_id INTEGER NOT NULL REFERENCES runs(id),
    path TEXT NOT NULL,
    reason TEXT NOT NULL
);
"""

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

    @property
    def needs_build(self) -> bool:
        return self.status == "accepted" and self.built_metadata != self.metadata


_BOOK_JSON_FIELDS = ("sources", "local_notes", "evidence_isbns", "lookup_errors", "review_reasons")
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
            )
            for row in rows
        ]

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
    ) -> None:
        """Stores a new automatic decision for a book (see identify.rescore)."""
        with self._connect() as db:
            db.execute(
                "UPDATE books SET status = ?, decided_by = ?, review_reasons = ?, metadata = ?, mislabeled = ?, "
                "updated_at = ? WHERE id = ?",
                (status, decided_by, json.dumps(review_reasons, ensure_ascii=False), metadata_to_json(metadata),
                 int(mislabeled), _now(), book_id),
            )
            db.execute("DELETE FROM candidates WHERE book_id = ?", (book_id,))
            _insert_candidates(db, book_id, candidates)

    def set_mislabeled(self, book_id: int, mislabeled: bool) -> None:
        with self._connect() as db:
            db.execute(
                "UPDATE books SET mislabeled = ?, updated_at = ? WHERE id = ?", (int(mislabeled), _now(), book_id)
            )

    def mark_built(self, book_id: int, metadata: BookMetadata, folder: str) -> None:
        with self._connect() as db:
            db.execute(
                "UPDATE books SET built_metadata = ?, built_folder = ?, updated_at = ? WHERE id = ?",
                (metadata_to_json(metadata), folder, _now(), book_id),
            )

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
            "title_in_text, author_in_text, auto_acceptable) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                book_id, rank, candidate.source, metadata_to_json(candidate.metadata), candidate.cover_url,
                candidate.score, int(candidate.isbn_match), int(candidate.title_in_text),
                int(candidate.author_in_text), int(candidate.auto_acceptable),
            ),
        )


def _migrate(db: sqlite3.Connection) -> None:
    """Adds columns introduced after a database was created."""
    columns = {row[1] for row in db.execute("PRAGMA table_info(books)")}
    if "mislabeled" not in columns:
        db.execute("ALTER TABLE books ADD COLUMN mislabeled INTEGER NOT NULL DEFAULT 0")


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


def write_report(store: StateStore, run_id: int, path: Path) -> Path:
    """Report of one run."""
    return _write(f"ebookprep report, run {run_id}", store.records(run_id), store.skipped(run_id), path)


def write_summary_report(store: StateStore, path: Path) -> Path:
    """Report of the current state: the newest result of every book across all runs."""
    mislabeled = [b for b in store.books() if b.mislabeled]
    return _write("ebookprep report, all runs", store.latest_records(), store.all_skipped(), path, mislabeled)


def _write(
    heading: str,
    records: list[RecordEntry],
    skipped: list[tuple[str, str]],
    path: Path,
    mislabeled: list[BookRow] = (),
) -> Path:
    done = [r for r in records if r.status == "done"]
    failed = [r for r in records if r.flagged]
    review = [r for r in done if r.needs_review]
    retried = [r for r in records if r.retried]

    lines = [
        f"# {heading}",
        "",
        "| Result | Count |",
        "|---|---|",
        f"| Done | {len(done)} |",
        f"| Failed (flagged) | {len(failed)} |",
        f"| Needs review | {len(review)} |",
        f"| Retried after silent calibre failure | {len(retried)} |",
        f"| Repaired PDFs | {sum(len(r.repaired) for r in records)} |",
        f"| Converted to EPUB | {sum(len(r.converted) for r in records)} |",
        f"| Skipped input files | {len(skipped)} |",
        "",
    ]

    lines += ["## Failed (flagged)", ""]
    for r in failed:
        lines += [f"- **{r.placement}/{r.record_name}**: {r.error}", *[f"  - source: `{s}`" for s in r.sources]]
    lines.append("")

    lines += ["## Needs review", ""]
    for r in review:
        reasons = ([f"grouped as {r.group_reason}"] if r.group_reason == "similar" else []) + list(r.notes)
        lines += [f"- **{r.placement}/{r.record_name}** ({r.title}): {'; '.join(reasons)}"]
        lines += [f"  - source: `{s}`" for s in r.sources]
    lines.append("")

    lines += ["## Retried, embedded XMP metadata removed", ""]
    for r in retried:
        lines.append(f"- **{r.placement}/{r.record_name}**: {r.status}")
        for file_name, packet in r.removed_xmp:
            lines += [f"  - removed from `{file_name}`:", "", "```xml", packet.strip(), "```", ""]
    lines.append("")

    lines += ["## Repaired PDFs", ""]
    lines += [f"- `{source}`" for r in records for source in r.repaired]
    lines.append("")

    lines += ["## Converted to EPUB", ""]
    lines += [f"- `{source}`" for r in records for source in r.converted]
    lines.append("")

    if mislabeled:
        lines += ["## Content differs from the filename", ""]
        lines += ["| Title from filename / .opf | Real content | Input file | Output folder |", "|---|---|---|---|"]
        for book in mislabeled:
            sources = "<br>".join(f"`{s}`" for s in book.sources)
            built = f"`{book.built_folder}`" if book.built_folder else "not built yet"
            lines.append(f"| {book.local_metadata.title} | {book.metadata.title} | {sources} | {built} |")
        lines.append("")

    lines += ["## Skipped input files", ""]
    lines += [f"- `{p}`: {reason}" for p, reason in skipped]
    lines.append("")

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines), encoding="utf-8")
    return path
