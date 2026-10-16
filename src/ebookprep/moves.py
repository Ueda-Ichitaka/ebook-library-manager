"""About this file: recognises known input files that were moved or renamed, e.g. after
re-sorting the input, so their book keeps its decisions instead of being identified again
as a new book while the old one points at a missing file.

Every input file's SHA-256 is stored with its size and modification time; it is only
computed again for new files and files whose size or time changed. A known file (one
belonging to a stored book) that is missing from its path has moved when:
- same content: exactly one new file (not belonging to any book) has its stored hash, or
- same filename: it has no stored hash (it moved before hashes were kept), and exactly
  one new file has its filename, while no other missing file has that name.
A missing file with more than one possible new path is left alone. A file whose old path
still exists is never a move (a copy is just a new file).

A move renames the path everywhere (see StateStore.move_source), so the book keeps its
metadata and review decision, and excluded, ignored and dismissed states follow the file.
The book's placement follows its files: when they all are in one new placement, the book
moves there (the next build moves its output folder); when they end up in different
placements, the book goes back to the review queue (REASON_SPLIT_PLACEMENT, defined in
identify.py so rescore and relookup keep it). Every move
is recorded (Files page, report) and logged, and so are missing files without a match.
"""

import hashlib
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath

from ebookprep.config import Config
from ebookprep.duplicates import find_duplicates, group_key
from ebookprep.identify import REASON_SPLIT_PLACEMENT, relative
from ebookprep.scan import scan_input
from ebookprep.state import FileHash, StateStore

SAME_CONTENT = "same content"
SAME_FILENAME = "same filename"


@dataclass(frozen=True)
class Move:
    old_path: str
    new_path: str
    method: str
    book_id: int | None


@dataclass
class MoveSummary:
    moved: list[Move] = field(default_factory=list)
    # known files missing from their path without a recognisable new path
    missing: list[str] = field(default_factory=list)


def sha256_of(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(1 << 20):
            digest.update(chunk)
    return digest.hexdigest()


def detect_moves(input_root: Path, store: StateStore, config: Config, log: Callable[[str], None] = print) -> MoveSummary:
    scan = scan_input(input_root, config)
    placement_of = {
        relative(f.path, input_root): "/".join(unit.placement.parts()) for unit in scan.units for f in unit.files
    }
    stored = _update_hashes(input_root, store, placement_of)

    known = store.known_sources()
    missing = sorted(p for p in known if p not in placement_of)
    summary = MoveSummary()
    if not missing:
        return summary
    fresh = [p for p in placement_of if p not in known]
    book_of = {s: b.id for b in store.books() for s in b.sources}
    moves = [Move(old, new, method, book_of.get(old)) for old, new, method in _match(missing, fresh, stored)]
    summary.missing = [p for p in missing if p not in {m.old_path for m in moves}]
    _apply(store, moves, placement_of)
    summary.moved = moves

    for m in moves:
        log(f"moved ({m.method}): {m.old_path} -> {m.new_path}")
    for path in summary.missing:
        log(f"missing from the input: {path}")
    log(f"moved files: {len(moves)} recognised, {len(summary.missing)} known files missing without a match")
    return summary


def _update_hashes(input_root: Path, store: StateStore, paths) -> dict[str, FileHash]:
    """Stored hashes, with those of new and changed files computed and saved."""
    stored = store.file_hashes()
    changed = []
    for path in paths:
        stat = (input_root / path).stat()
        known = stored.get(path)
        if known is None or (known.size, known.mtime_ns) != (stat.st_size, stat.st_mtime_ns):
            changed.append(FileHash(path, stat.st_size, stat.st_mtime_ns, sha256_of(input_root / path)))
    store.save_file_hashes(changed)
    stored.update((h.path, h) for h in changed)
    return stored


def _match(missing: list[str], fresh: list[str], hashes: dict[str, FileHash]) -> list[tuple[str, str, str]]:
    by_hash: dict[str, list[str]] = {}
    for path in fresh:
        by_hash.setdefault(hashes[path].sha256, []).append(path)
    taken: set[str] = set()
    moves = []
    without_hash = []
    for old in missing:
        if old not in hashes:
            without_hash.append(old)
            continue
        matches = by_hash.get(hashes[old].sha256, [])
        if len(matches) == 1 and matches[0] not in taken:
            moves.append((old, matches[0], SAME_CONTENT))
            taken.add(matches[0])

    missing_names = Counter(_name(p) for p in without_hash)
    fresh_by_name: dict[str, list[str]] = {}
    for path in fresh:
        if path not in taken:
            fresh_by_name.setdefault(_name(path), []).append(path)
    for old in without_hash:
        matches = fresh_by_name.get(_name(old), [])
        if missing_names[_name(old)] == 1 and len(matches) == 1:
            moves.append((old, matches[0], SAME_FILENAME))
    return moves


def _name(path: str) -> str:
    return PurePosixPath(path).name


def _apply(store: StateStore, moves: list[Move], placement_of: dict[str, str]) -> None:
    renamed = {m.old_path: m.new_path for m in moves}
    dismissed = store.dismissed_duplicates()
    groups = find_duplicates(store.books(), store.unused_paths()) if dismissed else []

    for m in moves:
        store.move_source(m.old_path, m.new_path)
        store.record_move(m.old_path, m.new_path, m.method, m.book_id)

    for group in groups:
        if group.key in dismissed:
            store.dismiss_duplicates(group_key(renamed.get(f.path, f.path) for f in group.files))

    for book_id in {m.book_id for m in moves if m.book_id is not None}:
        book = store.book(book_id)
        placements = {placement_of[s] for s in book.sources if s in placement_of}
        if len(placements) > 1:
            store.require_review(book_id, REASON_SPLIT_PLACEMENT)
        elif placements and placements != {book.placement}:
            store.set_placement(book_id, placements.pop())
