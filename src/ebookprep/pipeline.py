"""About this file: the build stage, which writes accepted books into the output library.

Only accepted books are built (calibre sidecar, accepted automatically on strict
evidence, or approved in the review UI), and only when their metadata differs from what
was last built. Each book gets a unique folder name in its placement; books whose file
contains a different book than its name says go below the mislabeled folder instead
(e.g. _mislabeled/IT/Security/<Book>), outside the library folders. Rebuilding a book
replaces its previous folder: the old folder is moved aside first and restored if the new
build fails, so a working book is never lost. A book is either built completely or not
at all.

Every build result is stored in the SQLite state (one record per calibre record), the
skipped input files too, and a Markdown report is written per run. An unexpected error
in one book is recorded as a failed, flagged record and the run continues. The input
folder is only read.
"""

import shutil
from collections.abc import Callable
from pathlib import Path

from ebookprep.build import RecordOutcome, RecordStatus, build_record, plan_records
from ebookprep.config import Config
from ebookprep.identify import placement_selected, relative
from ebookprep.naming import unique_book_name
from ebookprep.scan import BookFile, scan_input
from ebookprep.state import BookRow, RecordEntry, StateStore, write_report


def build_accepted(
    input_root: Path,
    output_root: Path,
    work_root: Path,
    config: Config,
    only: tuple[str, ...] = (),
    log: Callable[[str], None] = print,
) -> int:
    store = StateStore(work_root / "state.db")
    run_id = store.start_run(str(input_root), str(output_root))

    scan = scan_input(input_root, config)
    for skipped in scan.skipped:
        store.add_skipped(run_id, relative(skipped.path, input_root), skipped.reason)
    files_by_path = {relative(f.path, input_root): f for unit in scan.units for f in unit.files}

    all_books = store.books()
    taken: dict[str, set[str]] = {}
    for book in all_books:
        if book.built_folder:
            _reserve(taken, book.built_folder)

    pending = [b for b in all_books if b.needs_build and placement_selected(b.placement, only)]
    for number, book in enumerate(pending, start=1):
        label = f"[{number}/{len(pending)}] {book.placement}"
        try:
            parent = f"{config.mislabeled_folder}/{book.placement}" if book.mislabeled else book.placement
            _build_book(
                book, parent, files_by_path, taken, run_id, store, input_root, output_root, work_root, label, log
            )
        except Exception as error:  # one broken book must never stop the run
            message = f"unexpected error: {type(error).__name__}: {error}"
            store.add_record(run_id, _failed_entry(book, message))
            log(f"{label}: failed ({type(error).__name__}: {error})")

    store.finish_run(run_id)
    report = write_report(store, run_id, work_root / f"report-run-{run_id}.md")
    log(f"report: {report}")
    return run_id


def _build_book(
    book: BookRow,
    parent: str,
    files_by_path: dict[str, BookFile],
    taken: dict[str, set[str]],
    run_id: int,
    store: StateStore,
    input_root: Path,
    output_root: Path,
    work_root: Path,
    label: str,
    log: Callable[[str], None],
) -> None:
    missing = [s for s in book.sources if s not in files_by_path]
    if missing:
        raise FileNotFoundError(f"source files no longer in the input: {missing}")
    files = tuple(files_by_path[s] for s in book.sources)

    if book.built_folder:
        _release(taken, book.built_folder)
    metadata = book.metadata
    qualifier = str(metadata.year) if metadata.year else None
    name = unique_book_name(metadata.title, metadata.authors, taken.setdefault(parent, set()), qualifier=qualifier)
    folder = f"{parent}/{name}"
    _reserve(taken, folder)

    dest = output_root.joinpath(*folder.split("/"))
    previous = output_root.joinpath(*book.built_folder.split("/")) if book.built_folder else None
    stash = work_root / "previous" / str(book.id)
    if previous is not None and previous.exists():
        shutil.rmtree(stash, ignore_errors=True)
        stash.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(previous, stash)

    outcomes = [build_record(plan, metadata, dest, work_root / "tmp") for plan in plan_records(files, name)]
    for outcome in outcomes:
        store.add_record(run_id, _entry(book, name, outcome, input_root, output_root))
        log(f"{label}/{outcome.plan.name}: {outcome.status.value}" + (f" ({outcome.error})" if outcome.error else ""))

    if all(o.status is RecordStatus.DONE for o in outcomes):
        shutil.rmtree(stash, ignore_errors=True)
        store.mark_built(book.id, metadata, folder)
        return

    shutil.rmtree(dest, ignore_errors=True)
    if previous is not None and stash.exists():
        shutil.move(stash, previous)
    _release(taken, folder)
    if book.built_folder:
        _reserve(taken, book.built_folder)


def _reserve(taken: dict[str, set[str]], folder: str) -> None:
    """Marks a book folder ("parent/name") as used, so no other book gets its name."""
    parent, _, name = folder.rpartition("/")
    taken.setdefault(parent, set()).add(name.casefold())


def _release(taken: dict[str, set[str]], folder: str) -> None:
    parent, _, name = folder.rpartition("/")
    taken.get(parent, set()).discard(name.casefold())


def _entry(book: BookRow, name: str, outcome: RecordOutcome, input_root: Path, output_root: Path) -> RecordEntry:
    return RecordEntry(
        placement=book.placement,
        book_name=name,
        record_name=outcome.plan.name,
        sources=tuple(relative(f.path, input_root) for f in outcome.plan.sources),
        outputs=tuple(relative(p, output_root) for p in outcome.outputs),
        title=book.metadata.title,
        authors=book.metadata.authors,
        metadata_source=book.decided_by,
        notes=book.review_reasons,
        needs_review=False,
        group_reason=book.group_reason,
        status=outcome.status.value,
        retried=outcome.retried,
        removed_xmp=outcome.removed_xmp,
        repaired=tuple(relative(s, input_root) for s, report in outcome.repairs if report.was_damaged),
        converted=tuple(relative(p, input_root) for p in outcome.converted),
        error=outcome.error,
    )


def _failed_entry(book: BookRow, error: str) -> RecordEntry:
    return RecordEntry(
        placement=book.placement,
        book_name=book.metadata.title,
        record_name=book.metadata.title,
        sources=book.sources,
        outputs=(),
        title=book.metadata.title,
        authors=book.metadata.authors,
        metadata_source=book.decided_by,
        notes=book.review_reasons,
        needs_review=False,
        group_reason=book.group_reason,
        status="failed",
        retried=False,
        removed_xmp=(),
        repaired=(),
        converted=(),
        error=error,
    )
