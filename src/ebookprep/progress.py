"""About this file: how far every input file is, for the progress overview in the web UI.

Each file of the input gets one state:
- skipped: not a book (archive, junk, unsupported format, outside a category),
- not identified yet: identify hasn't seen it,
- waiting for review: its book needs a decision in the review,
- accepted, not built yet: decided, the next build writes it,
- built: in the output,
- build failed: the last build of its book failed (see the report),
- excluded duplicate: another file of the same book was kept.
Totals are counted per state and per placement (Library/Category[/Topic]); a file is done
when it is built, excluded or skipped.
"""

from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

from ebookprep.config import Config
from ebookprep.scan import scan_input
from ebookprep.state import StateStore

SKIPPED = "skipped"
NOT_IDENTIFIED = "not identified yet"
REVIEW = "waiting for review"
ACCEPTED = "accepted, not built yet"
BUILT = "built"
BUILD_FAILED = "build failed"
EXCLUDED = "excluded duplicate"
STATES = (REVIEW, NOT_IDENTIFIED, BUILD_FAILED, ACCEPTED, BUILT, EXCLUDED, SKIPPED)
DONE_STATES = frozenset({BUILT, EXCLUDED, SKIPPED})


@dataclass(frozen=True)
class FileProgress:
    path: str
    placement: str
    state: str
    detail: str = ""
    book_id: int | None = None
    mislabeled: bool = False


@dataclass
class Totals:
    total: int = 0
    counts: Counter = field(default_factory=Counter)

    @property
    def done_percent(self) -> int:
        done = sum(self.counts[s] for s in DONE_STATES)
        return round(100 * done / self.total) if self.total else 100


@dataclass
class Summary(Totals):
    by_placement: dict[str, Totals] = field(default_factory=dict)


def file_progress(input_root: Path, store: StateStore, config: Config) -> list[FileProgress]:
    scan = scan_input(input_root, config)
    excluded = store.excluded_paths()
    book_of = {source: book for book in store.books() for source in book.sources}
    last_record = {}
    for record in store.latest_records():
        for source in record.sources:
            last_record[source] = record

    files: list[FileProgress] = []
    for unit in scan.units:
        placement = "/".join(unit.placement.parts())
        for book_file in unit.files:
            path = str(book_file.path.relative_to(input_root))
            files.append(_state_of(path, placement, excluded, book_of.get(path), last_record.get(path)))
    for skipped in scan.skipped:
        path = str(skipped.path.relative_to(input_root))
        files.append(FileProgress(path=path, placement=str(Path(path).parent), state=SKIPPED, detail=skipped.reason))
    return sorted(files, key=lambda f: f.path)


def _state_of(path, placement, excluded, book, record) -> FileProgress:
    if path in excluded:
        return FileProgress(path, placement, EXCLUDED, "another file was kept", book.id if book else None)
    if book is None:
        return FileProgress(path, placement, NOT_IDENTIFIED)
    common = dict(path=path, placement=placement, book_id=book.id, mislabeled=book.mislabeled)
    if book.status == "review":
        return FileProgress(state=REVIEW, detail="; ".join(book.review_reasons), **common)
    if book.built_folder and path in (book.built_sources or book.sources):
        return FileProgress(state=BUILT, detail=book.built_folder, **common)
    if record is not None and record.status == "failed":
        return FileProgress(state=BUILD_FAILED, detail=record.error or "", **common)
    return FileProgress(state=ACCEPTED, detail=book.metadata.title, **common)


def summarize(files: list[FileProgress]) -> Summary:
    summary = Summary()
    for f in files:
        summary.total += 1
        summary.counts[f.state] += 1
        totals = summary.by_placement.setdefault(f.placement, Totals())
        totals.total += 1
        totals.counts[f.state] += 1
    summary.by_placement = dict(sorted(summary.by_placement.items()))
    return summary
