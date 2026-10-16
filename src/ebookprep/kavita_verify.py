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
- spacing: the series name has the right letters, but its words are run together or split
  differently ("FormDerWissenschaftlichenAusarbeitung"); Kavita matches series by
  normalized name (without spaces, case and punctuation) and keeps the display name it
  had from an earlier scan, so only a rename in Kavita fixes it,
- outdated_name (only noted, not a problem): the series name differs only in case or
  punctuation, for the same reason,
- unexpected: files Kavita shows that weren't built (e.g. leftovers in the output).
record_check stores these as issues (one per file, with Kavita's series id for a rename)
in the state, where the reviewer can accept them or mark them fixed (see the Kavita page
of the web UI), and returns the number of open problems.
"""

import re

from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Protocol

from ebookprep.series import kavita_normalize
from ebookprep.state import KavitaIssue, StateStore

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
    # (file, expected series name, older name with other spaces Kavita still shows)
    spacing: list[tuple[str, str, str]] = field(default_factory=list)
    # (file, expected series name, older spelling Kavita still shows)
    outdated_name: list[tuple[str, str, str]] = field(default_factory=list)
    unexpected: list[str] = field(default_factory=list)
    # Kavita's series id of every file it shows
    series_ids: dict[str, int] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return not (self.missing or self.merged or self.wrong_name or self.spacing or self.unexpected)


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
            result.series_ids[file] = series["id"]
            if file not in expected:
                result.unexpected.append(file)
            elif expected[file].strip() != name:
                entry = (file, expected[file], name)
                if kavita_normalize(expected[file]) != kavita_normalize(name):
                    result.wrong_name.append(entry)
                elif _words(expected[file]) != _words(name):
                    result.spacing.append(entry)
                else:
                    result.outdated_name.append(entry)

    result.missing = sorted(set(expected) - seen)
    return result


def record_check(store: StateStore, expected: dict[str, str], result: VerifyResult) -> int:
    """Stores the check's issues for the checked books (those with files in expected) and
    returns the number of open problems (not accepted)."""
    book_of = {b.built_folder: b.id for b in store.books() if b.built_folder}

    def issue(kind: str, file: str, wanted: str = "", actual: str = "") -> KavitaIssue:
        return KavitaIssue(kind, file, book_of.get(str(PurePosixPath(file).parent)), wanted, actual,
                           result.series_ids.get(file))

    issues = [issue("missing", f, expected[f]) for f in result.missing]
    issues += [issue("wrong_name", *entry) for entry in result.wrong_name]
    issues += [issue("spacing", *entry) for entry in result.spacing]
    issues += [issue("case", *entry) for entry in result.outdated_name]
    issues += [issue("merged", f, expected[f], name) for name, files in result.merged for f in files if f in expected]
    issues += [issue("unexpected", f) for f in sorted(set(result.unexpected))]
    checked = {book_of[str(PurePosixPath(f).parent)] for f in expected if str(PurePosixPath(f).parent) in book_of}
    store.record_kavita_check(checked, issues)
    return sum(i.open for i in store.kavita_issues())


def _words(name: str) -> list[str]:
    return re.findall(r"[^\W_]+", name.casefold())


def _book_of(file: str, expected: dict[str, str]) -> str:
    """The book a file belongs to: its book folder if it was built, else the file itself
    (loose files in a category folder are separate books, as in the original problem)."""
    return str(PurePosixPath(file).parent) if file in expected else file
