"""About this file: the build stage, which writes accepted books into the output library.

Only accepted books are built (calibre sidecar, accepted automatically on strict
evidence, or approved in the review UI), and only when their metadata or series name
differs from what was last built. The series name is the title, or "Title (Author)" when
books of the same title would be merged by Kavita (see series.py). Source files excluded
as duplicates or ignored in the review are left out: a book that loses files is rebuilt
with the rest, and the output folder of a book whose files are all left out is removed. The ignored files are
copied unchanged into the ignored folder (e.g. _ignored/DIY/Plans/Plan.jpg.pdf) with their
input paths; copies of files that are no longer ignored are removed from there. All files
of categories flagged as not books (magazines, blueprints), including subfolders and
files that are no ebooks, are copied the same way into the misplaced folder (e.g.
_misplaced/Magazine/elektor/DE201011.pdf) and their books are not built. Each book gets a unique folder name in its placement; books whose file
contains a different book than its name says go below the mislabeled folder instead
(e.g. _mislabeled/IT/Security/<Book>), outside the library folders, unless the reviewer
moved them to another placement; changing that placement moves the book's folder. Rebuilding a book
replaces its previous folder: the old folder is moved aside first and restored if the new
build fails, so a working book is never lost. A book is either built completely or not
at all.

Input files are checked for damaged downloads first (see damaged.py): a repairable one is
repaired in the work copy while building (and noted in its record); a book with a file
that can't be restored is skipped without a new attempt or record until the file changes,
so it doesn't fail every build again (its last failure stays in the report).

Every build result is stored in the SQLite state (one record per calibre record), the
skipped input files too, and a Markdown report is written per run. An unexpected error
in one book is recorded as a failed, flagged record and the run continues. The input
folder is only read.
"""

import shutil
from collections.abc import Callable
from dataclasses import replace
from pathlib import Path

from ebookprep.build import RecordOutcome, RecordStatus, build_record, plan_records
from ebookprep.config import Config
from ebookprep.damaged import check_downloads
from ebookprep.identify import placement_selected, relative
from ebookprep.naming import unique_book_name
from ebookprep.scan import BookFile, scan_input
from ebookprep.series import series_names
from ebookprep.report import write_report
from ebookprep.state import BookRow, RecordEntry, StateStore


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
    damage = check_downloads(input_root, store, files_by_path)

    excluded = store.unused_paths()
    all_books = []
    for book in store.books():
        remaining = tuple(s for s in book.sources if s not in excluded)
        if remaining:
            all_books.append(replace(book, sources=remaining))
        elif book.built_folder and placement_selected(book.placement, only):
            _remove_built(book, store, output_root, log)

    misplaced = _category_files(input_root, {c.placement for c in store.not_book_categories()})
    _sync_copies(input_root, output_root / config.misplaced_folder, misplaced, "not a book", log)
    _sync_copies(input_root, output_root / config.ignored_folder, store.ignored_paths() - misplaced, "ignored in review", log)

    taken: dict[str, set[str]] = {}
    for book in all_books:
        if book.built_folder:
            _reserve(taken, book.built_folder)

    series = series_names(all_books)
    pending = [
        b for b in all_books
        if b.status == "accepted"
        and (
            b.needs_build
            or b.built_series != series[b.id]
            or b.built_sources != b.sources
            or (b.built_folder and b.built_folder.rpartition("/")[0] != output_parent(b, config))
        )
        and placement_selected(b.placement, only)
    ]
    unrestorable = [b for b in pending if any(s in damage and not damage[s].repairable for s in b.sources)]
    for book in unrestorable:
        for s in (s for s in book.sources if s in damage):
            log(f"{book.placement}/{book.metadata.title}: skipped, damaged download ({damage[s].note}): {s}")
    pending = [b for b in pending if b not in unrestorable]
    for number, book in enumerate(pending, start=1):
        label = f"[{number}/{len(pending)}] {book.placement}"
        try:
            parent = output_parent(book, config)
            _build_book(
                book, parent, series[book.id], files_by_path, taken, run_id, store, input_root, output_root, work_root, label, log
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
    series: str,
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

    outcomes = [
        build_record(plan, metadata, dest, work_root / "tmp", series=series) for plan in plan_records(files, name)
    ]
    for outcome in outcomes:
        store.add_record(run_id, _entry(book, name, outcome, input_root, output_root))
        log(f"{label}/{outcome.plan.name}: {outcome.status.value}" + (f" ({outcome.error})" if outcome.error else ""))

    if all(o.status is RecordStatus.DONE for o in outcomes):
        shutil.rmtree(stash, ignore_errors=True)
        store.mark_built(book.id, metadata, folder, series, sources=book.sources)
        return

    shutil.rmtree(dest, ignore_errors=True)
    if previous is not None and stash.exists():
        shutil.move(stash, previous)
    _release(taken, folder)
    if book.built_folder:
        _reserve(taken, book.built_folder)


def output_parent(book: BookRow, config: Config) -> str:
    """The folder a book's own folder goes into: where it was moved to in the review, the
    mislabeled folder for a mislabeled book, else its placement."""
    if book.target_placement:
        return book.target_placement
    if book.mislabeled:
        return f"{config.mislabeled_folder}/{book.placement}"
    return book.placement


def _category_files(input_root: Path, categories: set[str]) -> set[str]:
    """All files in the input folders of the categories, as paths relative to the input."""
    return {
        relative(path, input_root)
        for category in categories
        for path in input_root.joinpath(*category.split("/")).rglob("*")
        if path.is_file()
    }


def _sync_copies(input_root: Path, folder: Path, paths: set[str], reason: str, log: Callable[[str], None]) -> None:
    """Keeps unchanged copies of exactly these input files in the folder, with their input paths."""
    for path in sorted(paths):
        source, copy = input_root / path, folder / path
        if source.is_file() and not (copy.is_file() and copy.stat().st_size == source.stat().st_size):
            copy.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, copy)
            log(f"{folder.name}/{path}: copied ({reason})")
    if not folder.is_dir():
        return
    for copy in sorted(folder.rglob("*"), reverse=True):
        relative_path = str(copy.relative_to(folder))
        if copy.is_file() and relative_path not in paths:
            copy.unlink()
            log(f"{folder.name}/{relative_path}: removed (no longer {reason})")
        elif copy.is_dir() and not any(copy.iterdir()):
            copy.rmdir()


def _remove_built(book: BookRow, store: StateStore, output_root: Path, log: Callable[[str], None]) -> None:
    """Removes the output folder of a book whose files are all left out, and the folders
    above it that become empty."""
    folder = output_root.joinpath(*book.built_folder.split("/"))
    shutil.rmtree(folder, ignore_errors=True)
    for parent in folder.parents:
        if parent == output_root or not parent.is_dir() or any(parent.iterdir()):
            break
        parent.rmdir()
    store.clear_built(book.id)
    log(f"{book.built_folder}: removed (all its files are excluded, ignored or not books)")


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
        notes=book.review_reasons
        + tuple(f"damaged download repaired: {relative(s, input_root)}: {note}" for s, note in outcome.download_repairs),
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
