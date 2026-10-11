"""About this file: builds the output files of one book.

A book's source files are split into calibre records: different formats of the same book
share a record (one .opf and one cover for all of them); a second file of the same
format gets its own record, named "<name> (2)" and so on. MOBI/AZW files are converted
to EPUB, which Kavita can read, and the original is kept in the same record.

Each record is built in a work folder: PDFs are repaired first, then the files go into a
temporary calibre library, get the metadata (series = title) and are exported. Every
exported PDF/EPUB is then checked the way Kavita reads it. calibre sometimes fails to
write metadata without reporting an error, so a failed check is retried once: for PDFs
the existing XMP packet is removed first (and returned for the report), then the
metadata is written again from the exported .opf. If the check fails again, the record
is skipped and flagged. Finished files are only moved into the output folder after all
checks passed, and existing files there are never overwritten.
"""

import enum
import shutil
import tempfile
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from ebookprep.calibre import CalibreError, ExportedRecord, convert, temporary_library, write_metadata_from_opf
from ebookprep.kavita_check import KavitaView, SeriesSource, kavita_view
from ebookprep.metadata import BookMetadata
from ebookprep.pdf_repair import PdfRepairError, RepairReport, repair_pdf, strip_xmp
from ebookprep.scan import BookFile

CONVERTED_FORMATS = frozenset({".mobi", ".azw", ".azw3"})
KAVITA_FORMATS = frozenset({".pdf", ".epub"})


class RecordStatus(enum.Enum):
    DONE = "done"
    FAILED = "failed"


@dataclass(frozen=True)
class RecordPlan:
    name: str
    sources: tuple[BookFile, ...]


@dataclass(frozen=True)
class RecordOutcome:
    plan: RecordPlan
    status: RecordStatus
    outputs: tuple[Path, ...] = ()
    repairs: tuple[tuple[Path, RepairReport], ...] = ()
    converted: tuple[Path, ...] = ()
    retried: bool = False
    # (output file name, removed XMP packet) for PDFs whose XMP was removed in the retry
    removed_xmp: tuple[tuple[str, str], ...] = ()
    error: str | None = None

    @property
    def flagged(self) -> bool:
        return self.status is RecordStatus.FAILED


class _BuildFailed(Exception):
    pass


def plan_records(files: tuple[BookFile, ...], name: str) -> list[RecordPlan]:
    slots: list[set[str]] = []
    members: list[list[BookFile]] = []
    for book_file in files:
        needed = _formats_needed(book_file.path)
        index = next((i for i, used in enumerate(slots) if not used & needed), None)
        if index is None:
            slots.append(set())
            members.append([])
            index = len(slots) - 1
        slots[index] |= needed
        members[index].append(book_file)

    return [
        RecordPlan(name if i == 0 else f"{name} ({i + 1})", tuple(record_files))
        for i, record_files in enumerate(members)
    ]


def _formats_needed(path: Path) -> set[str]:
    suffix = path.suffix.lower()
    return {suffix, ".epub"} if suffix in CONVERTED_FORMATS else {suffix}


def build_record(
    plan: RecordPlan,
    metadata: BookMetadata,
    dest: Path,
    work_dir: Path,
    rewrite: Callable[[Path, Path], None] = write_metadata_from_opf,
) -> RecordOutcome:
    work_dir.mkdir(parents=True, exist_ok=True)
    tmp = Path(tempfile.mkdtemp(prefix="record-", dir=work_dir))
    state = _State()
    try:
        prepared = _prepare(plan, tmp / "prepared", state)
        cover = next((f.cover for f in plan.sources if f.cover), None)
        with temporary_library(tmp) as library:
            book_id = library.add(prepared[0])
            for extra in prepared[1:]:
                library.add_format(book_id, extra)
            library.set_metadata(book_id, metadata, cover=cover)
            exported = library.export(book_id, tmp / "export", plan.name)
        _verify_with_retry(exported, metadata, rewrite, state)
        outputs = _move_into_place(exported, dest)
        return state.outcome(plan, RecordStatus.DONE, outputs=outputs)
    except (_BuildFailed, CalibreError, PdfRepairError) as error:
        return state.outcome(plan, RecordStatus.FAILED, error=str(error))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


@dataclass
class _State:
    """What happened while building a record, collected for the outcome."""

    repairs: list[tuple[Path, RepairReport]] = field(default_factory=list)
    converted: list[Path] = field(default_factory=list)
    removed_xmp: list[tuple[str, str]] = field(default_factory=list)
    retried: bool = False

    def outcome(
        self, plan: RecordPlan, status: RecordStatus, outputs: list[Path] | tuple = (), error: str | None = None
    ) -> RecordOutcome:
        return RecordOutcome(
            plan=plan,
            status=status,
            outputs=tuple(outputs),
            repairs=tuple(self.repairs),
            converted=tuple(self.converted),
            retried=self.retried,
            removed_xmp=tuple(self.removed_xmp),
            error=error,
        )


def _prepare(plan: RecordPlan, folder: Path, state: _State) -> list[Path]:
    """Copies the sources into the work folder: PDFs repaired, MOBI/AZW also converted."""
    folder.mkdir(parents=True)
    prepared: list[Path] = []
    for index, book_file in enumerate(plan.sources):
        suffix = book_file.path.suffix.lower()
        target = folder / f"{index}{suffix}"
        if suffix == ".pdf":
            state.repairs.append((book_file.path, repair_pdf(book_file.path, target)))
            prepared.append(target)
        elif suffix in CONVERTED_FORMATS:
            shutil.copyfile(book_file.path, target)
            converted = folder / f"{index}.epub"
            convert(target, converted)
            state.converted.append(book_file.path)
            prepared.extend([converted, target])
        else:
            shutil.copyfile(book_file.path, target)
            prepared.append(target)
    return prepared


def _verify_with_retry(
    exported: ExportedRecord, metadata: BookMetadata, rewrite: Callable[[Path, Path], None], state: _State
) -> None:
    failing = _failing_files(exported, metadata)
    if not failing:
        return

    state.retried = True
    if exported.opf is None:
        raise _BuildFailed("calibre exported no .opf file to retry from")
    for book in failing:
        if book.suffix.lower() == ".pdf":
            packet = strip_xmp(book)
            if packet is not None:
                state.removed_xmp.append((book.name, packet))
        rewrite(book, exported.opf)

    still_failing = _failing_files(exported, metadata)
    if still_failing:
        details = "; ".join(f"{book.name}: {_describe(kavita_view(book))}" for book in still_failing)
        raise _BuildFailed(f"series not written after retry: {details}")


def _failing_files(exported: ExportedRecord, metadata: BookMetadata) -> list[Path]:
    expected = metadata.title.strip()
    failing = []
    for book in exported.books:
        if book.suffix.lower() not in KAVITA_FORMATS:
            continue
        view = kavita_view(book)
        if view.source is not SeriesSource.SERIES or view.series != expected:
            failing.append(book)
    return failing


def _describe(view: KavitaView) -> str:
    return f"Kavita would show series {view.series!r} (from {view.source.value})"


def _move_into_place(exported: ExportedRecord, dest: Path) -> list[Path]:
    files = [*exported.books, *(p for p in (exported.opf, exported.cover) if p is not None)]
    existing = [dest / f.name for f in files if (dest / f.name).exists()]
    if existing:
        raise _BuildFailed(f"output already exists, not overwritten: {[p.name for p in existing]}")
    dest.mkdir(parents=True, exist_ok=True)
    moved = []
    for file in files:
        target = dest / file.name
        shutil.move(file, target)
        moved.append(target)
    return moved
