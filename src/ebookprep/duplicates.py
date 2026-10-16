"""About this file: finds groups of files that may be the same book, for the dedup view.

Connections between books come in three tiers, shown as separate sections:
- certain: the files of one book (e.g. a PDF and an EPUB the pipeline grouped into one
  book); the same ISBN (10- or 13-digit form); the same title (compared like Kavita
  compares series names) and a shared author (any author of either book; same surname
  and first initial),
- likely: the same main title (subtitle, edition and remarks in parentheses removed, so
  "Serious Cryptography, 2nd Edition: ..." and "Serious Cryptography" match) with a
  shared author, or the same main title where one book has no known author,
- possible: a shared author and a main title that starts like the other one
  ("Windows Security Internals" / "Windows Security Internals with PowerShell"); or the
  same main title by different authors.
Connected books form one group. Each tier also uses the stricter tiers' connections; a
group is listed in the strictest tier that finds it, and a looser tier lists it again
only when it adds books. Files excluded earlier (the reviewer kept another file) are left
out, groups with fewer than two files remaining disappear, and dismissed groups stay
hidden. A group's key is derived from its files, so a dismissal holds until the group's
files change. Only books sharing an ISBN, a title, a main title or an author surname are
compared (every rule needs one of these), which keeps the check fast for large libraries.

reconcile_reviews keeps the review queue in step with these decisions: a book that waits
for review only because its files were grouped by similar names no longer needs that
check once at most one of its files is left; without other reasons it is accepted.
"""

import hashlib
import re
import unicodedata
from dataclasses import dataclass
from difflib import SequenceMatcher

from ebookprep.evidence import isbn_forms
from ebookprep.identify import REASON_SIMILAR_GROUP
from ebookprep.series import kavita_normalize
from ebookprep.state import BookRow, StateStore

TIERS = ("certain", "likely", "possible")

REASON_ONE_BOOK = "files of one book"
REASON_ISBN = "same ISBN"
REASON_TITLE_AUTHOR = "same title and author"
REASON_MAIN_TITLE_AUTHOR = "same main title and author"
REASON_TITLE = "same title"
REASON_SIMILAR_TITLE = "similar title, same author"
REASON_MAIN_TITLE_OTHER_AUTHORS = "same main title, different authors"
_REASON_ORDER = (
    REASON_ONE_BOOK, REASON_ISBN, REASON_TITLE_AUTHOR, REASON_MAIN_TITLE_AUTHOR, REASON_TITLE,
    REASON_SIMILAR_TITLE, REASON_MAIN_TITLE_OTHER_AUTHORS,
)

# Word-level similarity of two main titles of the same author for a possible duplicate.
SIMILAR_TITLE = 0.75
_EDITION = re.compile(
    r"[,.]?\s*\b(\d+(st|nd|rd|th)|first|second|third|fourth|fifth|sixth|revised|updated|new)\s+(edition|ed)\b\.?"
    r"|\b\d+\.\s*(auflage|aufl)\b\.?",
    re.IGNORECASE,
)


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
    tier: str = "certain"


def group_key(paths) -> str:
    return hashlib.sha1("\n".join(sorted(paths)).encode("utf-8")).hexdigest()[:16]


@dataclass(frozen=True)
class _Features:
    """What the rules compare, computed once per book."""

    title: str
    main_title: tuple[str, ...]
    authors: tuple[tuple[str, ...], ...]
    isbns: frozenset[str]

    @property
    def known_author(self) -> bool:
        return bool(self.authors) and bool(self.authors[0])


def _features(book: BookRow) -> _Features:
    return _Features(
        title=kavita_normalize(book.metadata.title),
        main_title=tuple(_main_title_words(book.metadata.title)),
        authors=tuple(tuple(_name_words(a)) for a in book.metadata.authors),
        isbns=frozenset(isbn_forms(book.metadata.isbn)) if book.metadata.isbn else frozenset(),
    )


def _candidate_pairs(books: list[BookRow], features: dict[int, _Features]) -> set[tuple[int, int]]:
    """Pairs of books sharing an ISBN, a title, a main title or an author surname."""
    buckets: dict[tuple, list[int]] = {}
    for b in books:
        f = features[b.id]
        keys = [("isbn", i) for i in f.isbns] + [("title", f.title), ("main", f.main_title)]
        keys += [("surname", name[-1]) for name in f.authors if name]
        for key in keys:
            if key[1]:
                buckets.setdefault(key, []).append(b.id)
    pairs = set()
    for ids in buckets.values():
        for i, a in enumerate(ids):
            for b in ids[i + 1 :]:
                pairs.add((min(a, b), max(a, b)))
    return pairs


def find_duplicates(
    books: list[BookRow], excluded: set[str] = frozenset(), dismissed: set[str] = frozenset()
) -> list[DuplicateGroup]:
    books = sorted(books, key=lambda b: b.id)
    by_id = {b.id: b for b in books}
    features = {b.id: _features(b) for b in books}
    connections = [
        (TIERS.index(tier), by_id[a], by_id[b], reason)
        for a, b in sorted(_candidate_pairs(books, features))
        for tier, reason in _connections(features[a], features[b])
    ]
    several_files = {b.id for b in books if len([s for s in b.sources if s not in excluded]) > 1}

    groups: list[DuplicateGroup] = []
    seen: set[str] = set()
    for level, tier in enumerate(TIERS):
        parent = {b.id: b.id for b in books}

        def root(i: int) -> int:
            while parent[i] != i:
                parent[i] = parent[parent[i]]
                i = parent[i]
            return i

        for conn_level, a, b, _ in connections:
            if conn_level <= level:
                parent[root(b.id)] = root(a.id)

        members: dict[int, list[BookRow]] = {}
        for b in books:
            members.setdefault(root(b.id), []).append(b)

        for group_books in members.values():
            ids = {b.id for b in group_books}
            files = tuple(
                DuplicateFile(path=s, book_id=b.id, book_title=b.metadata.title, book_status=b.status, placement=b.placement)
                for b in group_books
                for s in b.sources
                if s not in excluded
            )
            key = group_key(f.path for f in files)
            if len(files) < 2 or key in dismissed or key in seen:
                continue
            seen.add(key)
            reasons = {r for lvl, a, b, r in connections if lvl <= level and a.id in ids and b.id in ids}
            if ids & several_files:
                reasons.add(REASON_ONE_BOOK)
            ordered = tuple(r for r in _REASON_ORDER if r in reasons)
            groups.append(DuplicateGroup(key=key, title=group_books[0].metadata.title, reasons=ordered, files=files, tier=tier))
    return groups


def _connections(a: _Features, b: _Features) -> list[tuple[str, str]]:
    found = []
    if a.isbns & b.isbns:
        found.append(("certain", REASON_ISBN))
    shared_author = any(_same_person(x, y) for x in a.authors for y in b.authors)
    unknown_author = not a.known_author or not b.known_author
    same_title = a.title == b.title
    if same_title and shared_author:
        found.append(("certain", REASON_TITLE_AUTHOR))
        return found
    if same_title and unknown_author:
        found.append(("likely", REASON_TITLE))
        return found
    if a.main_title and a.main_title == b.main_title:
        if shared_author:
            found.append(("likely", REASON_MAIN_TITLE_AUTHOR))
        elif unknown_author:
            found.append(("likely", REASON_TITLE))
        else:
            found.append(("possible", REASON_MAIN_TITLE_OTHER_AUTHORS))
    elif shared_author and _similar_titles(list(a.main_title), list(b.main_title)):
        found.append(("possible", REASON_SIMILAR_TITLE))
    return found


def _main_title_words(title: str) -> list[str]:
    """The title without subtitle, edition and remarks in parentheses, as plain words."""
    text = re.sub(r"\([^)]*\)|\[[^\]]*\]", " ", title)
    text = re.split(":| - | \u2013 ", text, maxsplit=1)[0]
    text = _EDITION.sub(" ", text)
    return _name_words(text)


def _similar_titles(a: list[str], b: list[str]) -> bool:
    if not a or not b:
        return False
    shorter, longer = sorted((a, b), key=len)
    if len(shorter) >= 2 and longer[: len(shorter)] == shorter:
        return True
    return SequenceMatcher(None, a, b).ratio() >= SIMILAR_TITLE


def _same_person(a: tuple[str, ...], b: tuple[str, ...]) -> bool:
    if not a or not b or a[-1] != b[-1]:
        return False
    # same surname: also the same first initial, unless one side is only a surname
    return len(a) == 1 or len(b) == 1 or a[0][0] == b[0][0]


def _name_words(name: str) -> list[str]:
    plain = "".join(c for c in unicodedata.normalize("NFKD", name.casefold()) if not unicodedata.combining(c))
    return re.findall(r"[^\W_]+", plain)


def reconcile_reviews(store: StateStore) -> int:
    """Removes the similar-names review reason where the duplicates are resolved; returns
    how many books changed."""
    unused = store.unused_paths()
    changed = 0
    for book in store.books(status="review"):
        if REASON_SIMILAR_GROUP not in book.review_reasons:
            continue
        if len([s for s in book.sources if s not in unused]) > 1:
            continue
        reasons = tuple(r for r in book.review_reasons if r != REASON_SIMILAR_GROUP)
        store.update_review(book.id, "review" if reasons else "accepted", reasons)
        changed += 1
    return changed
