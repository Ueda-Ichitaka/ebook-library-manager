"""About this file: checks what a real Kavita shows against what was built.

The expected state comes from the SQLite state: every PDF/EPUB of a built book (outside
the mislabeled folder, which is no Kavita library) should appear in Kavita as a series of
its own, named like the series the build wrote (the title, or "Title (Author)" when titles
clash, see series.py). The check reports
- missing: built files Kavita doesn't show,
- merged: series that contain files of more than one book (the original problem: books
  grouped into a series named after their folder); a built file belongs to its book
  folder, any other file counts as a book of its own,
- wrong_name: files whose series isn't named after the book's title,
- outdated_name (only noted, not a problem): the series name differs only in case or
  punctuation; Kavita matches series by normalized name and keeps the display name it
  had from an earlier scan,
- unexpected: files Kavita shows that weren't built (e.g. leftovers in the output).
"""

from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Protocol

from ebookprep.series import kavita_normalize
from ebookprep.state import StateStore

KAVITA_FORMATS = (".pdf", ".epub")


class SeriesSource(Protocol):
    def all_series(self) -> list[dict]: ...

    def series_files(self, series_id: int) -> list[str]: ...


@dataclass
class VerifyResult:
    checked_files: int = 0
    series: int = 0
    missing: list[str] = field(default_factory=list)
    # (series name, files) for series with files of more than one book folder
    merged: list[tuple[str, list[str]]] = field(default_factory=list)
    # (file, expected series name, actual series name)
    wrong_name: list[tuple[str, str, str]] = field(default_factory=list)
    # (file, expected series name, older spelling Kavita still shows)
    outdated_name: list[tuple[str, str, str]] = field(default_factory=list)
    unexpected: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not (self.missing or self.merged or self.wrong_name or self.unexpected)


def expected_files(store: StateStore, output_root: Path, mislabeled_folder: str) -> dict[str, str]:
    """Output-relative path of every built PDF/EPUB shown by Kavita, mapped to its title."""
    expected = {}
    for book in store.books():
        if not book.built_folder or book.built_folder.split("/")[0] == mislabeled_folder:
            continue
        folder = output_root.joinpath(*book.built_folder.split("/"))
        for path in sorted(folder.iterdir()) if folder.is_dir() else []:
            if path.suffix.lower() in KAVITA_FORMATS:
                expected[f"{book.built_folder}/{path.name}"] = book.built_series or book.metadata.title
    return expected


def verify(kavita: SeriesSource, expected: dict[str, str], library_root: str) -> VerifyResult:
    result = VerifyResult(checked_files=len(expected))
    seen: set[str] = set()
    root = PurePosixPath(library_root)

    for series in kavita.all_series():
        result.series += 1
        name = series["name"].strip()
        files = [str(PurePosixPath(p).relative_to(root)) for p in kavita.series_files(series["id"])]
        if len({_book_of(f, expected) for f in files}) > 1:
            result.merged.append((name, files))
        for file in files:
            seen.add(file)
            if file not in expected:
                result.unexpected.append(file)
            elif expected[file].strip() != name:
                same_series = kavita_normalize(expected[file]) == kavita_normalize(name)
                (result.outdated_name if same_series else result.wrong_name).append((file, expected[file], name))

    result.missing = sorted(set(expected) - seen)
    return result


def _book_of(file: str, expected: dict[str, str]) -> str:
    """The book a file belongs to: its book folder if it was built, else the file itself
    (loose files in a category folder are separate books, as in the original problem)."""
    return str(PurePosixPath(file).parent) if file in expected else file
