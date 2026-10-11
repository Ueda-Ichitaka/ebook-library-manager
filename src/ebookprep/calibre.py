"""About this file: a thin wrapper around calibre's command-line tools.

Each book record is processed in its own temporary calibre library, which is deleted
afterwards, so only one book is ever loaded at a time. calibredb adds the files, sets
the metadata (the series is always the book's title) and exports the record into a
folder: the book file(s), a .opf file and a .jpg cover, all with the same name.
ebook-convert turns MOBI/AZW into EPUB, and ebook-meta re-writes metadata from an .opf
file. A command that exits with an error raises CalibreError; note that calibre's PDF
writer can fail silently, so callers verify the written files.
"""

import re
import shutil
import subprocess
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

from ebookprep.metadata import BookMetadata

COMMAND_TIMEOUT_SECONDS = 600


class CalibreError(Exception):
    """Raised when a calibre command fails or produces unexpected output."""


@dataclass(frozen=True)
class ExportedRecord:
    books: tuple[Path, ...]
    opf: Path | None
    cover: Path | None


class CalibreLibrary:
    def __init__(self, path: Path):
        self.path = path

    def add(self, file: Path) -> int:
        if not file.is_file():
            raise CalibreError(f"{file} does not exist")
        output = self._calibredb("add", "--duplicates", str(file))
        match = re.search(r"Added book ids: (\d+)", output)
        if not match:
            raise CalibreError(f"calibredb add didn't add {file}: {output.strip()}")
        return int(match.group(1))

    def add_format(self, book_id: int, file: Path) -> None:
        self._calibredb("add_format", str(book_id), str(file))

    def set_metadata(self, book_id: int, metadata: BookMetadata, cover: Path | None = None) -> None:
        fields = [
            ("title", metadata.title),
            ("sort", metadata.title),
            ("authors", " & ".join(metadata.authors)),
            ("series", metadata.title),
            ("series_index", "1"),
            ("identifiers", f"isbn:{metadata.isbn}" if metadata.isbn else ""),
            ("publisher", metadata.publisher or ""),
            ("languages", metadata.language or ""),
            ("pubdate", metadata.pubdate or ""),
            ("comments", metadata.comments or ""),
            ("tags", ",".join(metadata.tags)),
            ("cover", str(cover) if cover else ""),
        ]
        args = [arg for name, value in fields if value for arg in ("--field", f"{name}:{value}")]
        self._calibredb("set_metadata", str(book_id), *args)

    def export(self, book_id: int, dest: Path, name: str) -> ExportedRecord:
        """Exports the record as dest/name.<ext>, dest/name.opf and dest/name.jpg."""
        dest.mkdir(parents=True, exist_ok=True)
        before = set(dest.iterdir())
        self._calibredb(
            "export", "--to-dir", str(dest), "--single-dir", "--dont-asciiize", "--template", name, str(book_id)
        )
        created = sorted(set(dest.iterdir()) - before)

        unexpected = [p for p in created if p.stem != name]
        if unexpected or not created:
            raise CalibreError(f"calibredb export of {name!r} produced unexpected files: {[p.name for p in created]}")

        books = tuple(p for p in created if p.suffix.lower() not in (".opf", ".jpg"))
        opf = dest / f"{name}.opf"
        cover = dest / f"{name}.jpg"
        return ExportedRecord(books=books, opf=opf if opf in created else None, cover=cover if cover in created else None)

    def _calibredb(self, command: str, *args: str) -> str:
        return run(["calibredb", command, "--library-path", str(self.path), *args])


@contextmanager
def temporary_library(work_dir: Path) -> Iterator[CalibreLibrary]:
    work_dir.mkdir(parents=True, exist_ok=True)
    path = Path(tempfile.mkdtemp(prefix="calibre-library-", dir=work_dir))
    try:
        yield CalibreLibrary(path)
    finally:
        shutil.rmtree(path, ignore_errors=True)


def convert(src: Path, dst: Path) -> None:
    run(["ebook-convert", str(src), str(dst)])
    if not dst.is_file():
        raise CalibreError(f"ebook-convert didn't create {dst}")


def write_metadata_from_opf(book: Path, opf: Path) -> None:
    run(["ebook-meta", str(book), "--from-opf", str(opf)])


def run(command: list[str]) -> str:
    try:
        result = subprocess.run(command, capture_output=True, text=True, timeout=COMMAND_TIMEOUT_SECONDS)
    except subprocess.TimeoutExpired as error:
        raise CalibreError(f"{command[0]} {command[1]} timed out after {COMMAND_TIMEOUT_SECONDS}s") from error
    if result.returncode != 0:
        details = (result.stderr or result.stdout).strip().splitlines()[-5:]
        raise CalibreError(f"{command[0]} {command[1]} failed: {' | '.join(details)}")
    return result.stdout
