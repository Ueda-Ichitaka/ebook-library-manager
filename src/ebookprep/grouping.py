"""About this file: decides which source units belong into the same book folder.

The same book can be present several times, e.g. as PDF and EPUB, or as two downloads
with different filenames. Grouping is deliberately loose: units in the same placement
are grouped when their main titles match word by word (very similar, or one truncated
from the other) and they share an author (same surname and first initial, or one side
has no author). Titles with different numbers (version, volume, edition) or different
language markers are never grouped. Because look-alikes can still be different editions
or different books, every group formed by similarity is marked for review. Units whose names differ
only by extension are a certain match. Two existing book folders are never merged,
since the user created them separately.
"""

import enum
import re
import unicodedata
from dataclasses import dataclass
from difflib import SequenceMatcher

from ebookprep.filenames import FilenameInfo, parse_filename
from ebookprep.naming import main_title
from ebookprep.scan import Placement, SourceUnit

# Word-level similarity of two main titles needed to consider them the same book.
TITLE_SIMILARITY = 0.9
# A title truncated to at least this many words still matches the full title.
MIN_PREFIX_WORDS = 3
# Language markers in titles; files in different languages are different books.
LANGUAGE_MARKERS = {
    "de": "de", "deu": "de", "ger": "de", "german": "de", "deutsch": "de",
    "en": "en", "eng": "en", "english": "en",
}


class GroupReason(enum.Enum):
    SINGLE = "single"
    BOOK_FOLDER = "book folder"
    SAME_NAME = "same name"
    SIMILAR = "similar"


@dataclass(frozen=True)
class BookGroup:
    placement: Placement
    units: tuple[SourceUnit, ...]
    reason: GroupReason

    @property
    def needs_review(self) -> bool:
        return self.reason is GroupReason.SIMILAR

    @property
    def book_folder(self) -> str | None:
        return next((u.book_folder for u in self.units if u.book_folder), None)


def group_units(units: list[SourceUnit]) -> list[BookGroup]:
    by_placement: dict[Placement, list[SourceUnit]] = {}
    for unit in units:
        by_placement.setdefault(unit.placement, []).append(unit)

    groups = [group for members in by_placement.values() for group in _group_placement(members)]
    groups.sort(key=lambda g: (g.placement.parts(), _sort_key(g.units[0])))
    return groups


def _group_placement(units: list[SourceUnit]) -> list[BookGroup]:
    units = sorted(units, key=_sort_key)
    infos = [_info(u) for u in units]
    parent = list(range(len(units)))

    def root(i: int) -> int:
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    def has_book_folder(r: int) -> bool:
        return any(units[i].book_folder for i in range(len(units)) if root(i) == r)

    for i in range(len(units)):
        for j in range(i + 1, len(units)):
            ri, rj = root(i), root(j)
            if ri == rj or not _same_book(infos[i], infos[j]):
                continue
            if has_book_folder(ri) and has_book_folder(rj):
                continue
            parent[rj] = ri

    members: dict[int, list[SourceUnit]] = {}
    for i, unit in enumerate(units):
        members.setdefault(root(i), []).append(unit)
    return [BookGroup(units[0].placement, tuple(m), _reason(m)) for m in members.values()]


def _reason(members: list[SourceUnit]) -> GroupReason:
    if len(members) == 1:
        return GroupReason.BOOK_FOLDER if members[0].book_folder else GroupReason.SINGLE
    stems = {f.path.stem.casefold() for u in members for f in u.files}
    if len(stems) == 1 and not any(u.book_folder for u in members):
        return GroupReason.SAME_NAME
    return GroupReason.SIMILAR


def _info(unit: SourceUnit) -> FilenameInfo:
    return parse_filename(unit.book_folder or unit.files[0].path.stem)


def _same_book(a: FilenameInfo, b: FilenameInfo) -> bool:
    words_a, words_b = _words(main_title(a.title)), _words(main_title(b.title))
    if _numbers(words_a) != _numbers(words_b) or _languages(words_a) != _languages(words_b):
        return False
    return _titles_match(words_a, words_b) and _authors_match(a.authors, b.authors)


def _titles_match(a: list[str], b: list[str]) -> bool:
    shorter, longer = sorted((a, b), key=len)
    is_truncated = len(shorter) >= MIN_PREFIX_WORDS and longer[: len(shorter)] == shorter
    return a == b or is_truncated or SequenceMatcher(None, a, b).ratio() >= TITLE_SIMILARITY


def _authors_match(a: tuple[str, ...], b: tuple[str, ...]) -> bool:
    if not a or not b:
        return True
    return any(_same_person(x, y) for x in a for y in b)


def _same_person(a: str, b: str) -> bool:
    words_a, words_b = _words(a), _words(b)
    if not words_a or not words_b or words_a[-1] != words_b[-1]:
        return False
    if len(words_a) == 1 or len(words_b) == 1:
        return True
    return words_a[0][0] == words_b[0][0]


def _numbers(words: list[str]) -> set[str]:
    return {n.lstrip("0") for w in words for n in re.findall(r"\d+", w)}


def _languages(words: list[str]) -> set[str]:
    return {LANGUAGE_MARKERS[w] for w in words if w in LANGUAGE_MARKERS}


def _words(text: str) -> list[str]:
    without_accents = "".join(
        c for c in unicodedata.normalize("NFKD", text.casefold()) if not unicodedata.combining(c)
    )
    return re.findall(r"[^\W_]+", without_accents)


def _sort_key(unit: SourceUnit) -> tuple[str, str]:
    return (unit.book_folder or "", str(unit.files[0].path))
