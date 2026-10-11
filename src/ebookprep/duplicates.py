"""About this file: finds groups of files that may be the same book, for the dedup view.

Two kinds of connections are found:
- the files of one book (e.g. a PDF and an EPUB the pipeline grouped into one book),
- different books anywhere in the library with the same ISBN (in either 10- or 13-digit
  form), or the same title (compared like Kavita compares series names) and the same
  first author (same surname and first initial).
Connected books form one group. Files excluded earlier (the reviewer kept another file)
are left out, groups with fewer than two files remaining disappear, and groups the
reviewer dismissed stay hidden. A group's key is derived from its files, so a dismissal
holds until the group's files change.
"""

import hashlib
import re
import unicodedata
from dataclasses import dataclass

from ebookprep.evidence import isbn_forms
from ebookprep.series import kavita_normalize
from ebookprep.state import BookRow

REASON_ONE_BOOK = "files of one book"
REASON_ISBN = "same ISBN"
REASON_TITLE_AUTHOR = "same title and author"


@dataclass(frozen=True)
class DuplicateFile:
    path: str
    book_id: int
    book_title: str
    book_status: str
    placement: str


@dataclass(frozen=True)
class DuplicateGroup:
    key: str
    title: str
    reasons: tuple[str, ...]
    files: tuple[DuplicateFile, ...]


def group_key(paths) -> str:
    return hashlib.sha1("\n".join(sorted(paths)).encode("utf-8")).hexdigest()[:16]


def find_duplicates(
    books: list[BookRow], excluded: set[str] = frozenset(), dismissed: set[str] = frozenset()
) -> list[DuplicateGroup]:
    books = sorted(books, key=lambda b: b.id)
    parent = {b.id: b.id for b in books}
    reasons: dict[int, set[str]] = {b.id: set() for b in books}

    def root(i: int) -> int:
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    def connect(a: BookRow, b: BookRow, reason: str) -> None:
        ra, rb = root(a.id), root(b.id)
        merged = reasons[ra] | reasons[rb] | {reason}
        parent[rb] = ra
        reasons[ra] = merged

    for i, a in enumerate(books):
        for b in books[i + 1 :]:
            if _same_isbn(a, b):
                connect(a, b, REASON_ISBN)
            if _same_title_and_author(a, b):
                connect(a, b, REASON_TITLE_AUTHOR)
    for b in books:
        if len([s for s in b.sources if s not in excluded]) > 1:
            reasons[root(b.id)].add(REASON_ONE_BOOK)

    members: dict[int, list[BookRow]] = {}
    for b in books:
        members.setdefault(root(b.id), []).append(b)

    groups = []
    for group_root, group_books in members.items():
        files = tuple(
            DuplicateFile(path=s, book_id=b.id, book_title=b.metadata.title, book_status=b.status, placement=b.placement)
            for b in group_books
            for s in b.sources
            if s not in excluded
        )
        key = group_key(f.path for f in files)
        if len(files) < 2 or key in dismissed:
            continue
        ordered = tuple(r for r in (REASON_ONE_BOOK, REASON_ISBN, REASON_TITLE_AUTHOR) if r in reasons[group_root])
        groups.append(DuplicateGroup(key=key, title=group_books[0].metadata.title, reasons=ordered, files=files))
    return groups


def _same_isbn(a: BookRow, b: BookRow) -> bool:
    if not a.metadata.isbn or not b.metadata.isbn:
        return False
    return bool(isbn_forms(a.metadata.isbn) & isbn_forms(b.metadata.isbn))


def _same_title_and_author(a: BookRow, b: BookRow) -> bool:
    if kavita_normalize(a.metadata.title) != kavita_normalize(b.metadata.title):
        return False
    if not a.metadata.authors or not b.metadata.authors:
        return False
    name_a, name_b = _name_words(a.metadata.authors[0]), _name_words(b.metadata.authors[0])
    if not name_a or not name_b or name_a[-1] != name_b[-1]:
        return False
    # same surname: also the same first initial, unless one side is only a surname
    return len(name_a) == 1 or len(name_b) == 1 or name_a[0][0] == name_b[0][0]


def _name_words(name: str) -> list[str]:
    plain = "".join(c for c in unicodedata.normalize("NFKD", name.casefold()) if not unicodedata.combining(c))
    return re.findall(r"[^\W_]+", plain)
