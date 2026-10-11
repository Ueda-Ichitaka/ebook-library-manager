"""About this file: scans the input folder tree and finds the books to process.

The input layout is Library/Category/..., where the first two levels are kept in the
output. Below a category, a subfolder is an existing book folder, unless the category is
configured as a topic category (see config.py); then subfolders are topic folders and
their subfolders are book folders.

The scan produces source units: either a single loose book file, or an existing book
folder with all book files inside it. Calibre sidecar files (.opf, .jpg) next to a book
file with the same name are attached to that file. Everything that isn't processed
(archives, junk, unknown formats, misplaced files) is reported as skipped with a reason.
"""

import os
from dataclasses import dataclass
from pathlib import Path

from ebookprep.config import Config

BOOK_EXTENSIONS = frozenset({".pdf", ".epub", ".mobi", ".azw", ".azw3"})
ARCHIVE_EXTENSIONS = frozenset({".zip", ".rar", ".7z", ".tar", ".gz", ".bz2", ".xz"})
OPF_EXTENSIONS = frozenset({".opf"})
COVER_EXTENSIONS = frozenset({".jpg", ".jpeg", ".png"})
JUNK_NAMES = frozenset({"thumbs.db", "desktop.ini"})


@dataclass(frozen=True)
class Placement:
    """Where a book belongs in the output, above its own book folder."""

    library: str
    category: str
    topic: str | None = None

    def parts(self) -> tuple[str, ...]:
        if self.topic is None:
            return (self.library, self.category)
        return (self.library, self.category, self.topic)


@dataclass(frozen=True)
class BookFile:
    path: Path
    opf: Path | None = None
    cover: Path | None = None


@dataclass(frozen=True)
class SourceUnit:
    placement: Placement
    files: tuple[BookFile, ...]
    # Name of the existing book folder in the input, None for a loose file.
    book_folder: str | None = None


@dataclass(frozen=True)
class SkippedFile:
    path: Path
    reason: str


@dataclass
class ScanResult:
    units: list[SourceUnit]
    skipped: list[SkippedFile]


def scan_input(root: Path, config: Config) -> ScanResult:
    result = ScanResult(units=[], skipped=[])

    for library in _subdirs(root):
        for category in _subdirs(library):
            placement = Placement(library.name, category.name)
            _scan_category(category, placement, config, result)
        _skip_files(library, "outside a category", result)
    _skip_files(root, "outside a category", result)

    result.units.sort(key=lambda u: (u.placement.parts(), u.book_folder or "", u.files[0].path))
    return result


def _scan_category(category: Path, placement: Placement, config: Config, result: ScanResult) -> None:
    _add_loose_files(category, placement, result)

    is_topic_category = config.is_topic_category(placement.library, placement.category)
    for subdir in _subdirs(category):
        if is_topic_category:
            topic_placement = Placement(placement.library, placement.category, subdir.name)
            _add_loose_files(subdir, topic_placement, result)
            for book_folder in _subdirs(subdir):
                _add_book_folder(book_folder, topic_placement, result)
        else:
            _add_book_folder(subdir, placement, result)


def _add_loose_files(directory: Path, placement: Placement, result: ScanResult) -> None:
    for book_file in _book_files_in(directory, result):
        result.units.append(SourceUnit(placement=placement, files=(book_file,)))


def _add_book_folder(folder: Path, placement: Placement, result: ScanResult) -> None:
    files: list[BookFile] = []
    for dirpath, dirnames, _ in os.walk(folder):
        dirnames[:] = sorted(d for d in dirnames if not d.startswith("."))
        files.extend(_book_files_in(Path(dirpath), result))
    if files:
        files.sort(key=lambda f: f.path)
        result.units.append(SourceUnit(placement=placement, files=tuple(files), book_folder=folder.name))


def _book_files_in(directory: Path, result: ScanResult) -> list[BookFile]:
    """Book files directly in directory, with sidecars attached; reports the rest as skipped."""
    books: list[Path] = []
    opfs: dict[str, Path] = {}
    covers: dict[str, Path] = {}

    for path in _files(directory):
        suffix = path.suffix.lower()
        if _is_junk(path):
            result.skipped.append(SkippedFile(path, "junk"))
        elif suffix in BOOK_EXTENSIONS:
            books.append(path)
        elif suffix in ARCHIVE_EXTENSIONS:
            result.skipped.append(SkippedFile(path, "archive"))
        elif suffix in OPF_EXTENSIONS:
            opfs[path.stem] = path
        elif suffix in COVER_EXTENSIONS:
            covers[path.stem] = path
        else:
            result.skipped.append(SkippedFile(path, "unsupported format"))

    book_stems = {book.stem for book in books}
    for sidecar in [*opfs.values(), *covers.values()]:
        if sidecar.stem not in book_stems:
            result.skipped.append(SkippedFile(sidecar, "sidecar without book"))

    return [BookFile(book, opf=opfs.get(book.stem), cover=covers.get(book.stem)) for book in books]


def _skip_files(directory: Path, reason: str, result: ScanResult) -> None:
    for path in _files(directory):
        result.skipped.append(SkippedFile(path, "junk" if _is_junk(path) else reason))


def _is_junk(path: Path) -> bool:
    return path.name.startswith(".") or path.name.lower() in JUNK_NAMES


def _subdirs(directory: Path) -> list[Path]:
    return sorted(p for p in directory.iterdir() if p.is_dir() and not p.name.startswith("."))


def _files(directory: Path) -> list[Path]:
    return sorted(p for p in directory.iterdir() if p.is_file())
