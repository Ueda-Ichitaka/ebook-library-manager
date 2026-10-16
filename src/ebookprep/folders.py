"""About this file: the folder overview of the start page, to see which folders are fully
processed and can go back into the live library.

Input tree: every input folder with its subfolders and the number of its files (all
levels below) that are done (see progress.py: built, excluded, ignored, not books,
skipped). A folder is complete when
- every file is done,
- no book from it is mislabeled without a decision (moved elsewhere, or "not mislabeled"),
- no book from it still has to be moved by the next build, and
- every book built from it passed the last Kavita check after its build.
Otherwise its reasons say what is open.

Output tree: every output folder down to the folders that hold book folders (book folders
themselves are counted, not listed), with its books and files. A library folder is ready
when the input folder of the same path (if there is one) is complete, every book built
into it passed Kavita, no book still has to be built into it, and Kavita showed no file
in it that wasn't built. The folders outside the libraries (mislabeled, ignored and
misplaced folder) have no ready mark. A library folder can be marked as copied to the
live library; the mark shows when anything in the folder changed afterwards.

Moved: files recognised as moved in the input, grouped by old and new folder; mislabeled
books the reviewer moved to another placement; and what is outside the libraries, grouped
by the input folder it came from.
"""

import os
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path, PurePosixPath

from ebookprep.config import Config
from ebookprep.pipeline import output_parent
from ebookprep.progress import DONE_STATES, NOT_A_BOOK, STATES, FileProgress, file_progress
from ebookprep.state import BookRow, MovedFile, StateStore, is_under


@dataclass
class InputFolder:
    path: str
    total: int = 0
    done: int = 0
    reasons: list[str] = field(default_factory=list)
    children: list["InputFolder"] = field(default_factory=list)

    @property
    def name(self) -> str:
        return PurePosixPath(self.path).name

    @property
    def complete(self) -> bool:
        return self.total > 0 and not self.reasons


@dataclass
class OutputFolder:
    path: str
    books: int = 0
    files: int = 0
    # outside the libraries (mislabeled, ignored, misplaced folder): no ready or live mark
    outside: bool = False
    reasons: list[str] = field(default_factory=list)
    live: bool = False
    changed_since_live: bool = False
    children: list["OutputFolder"] = field(default_factory=list)

    @property
    def name(self) -> str:
        return PurePosixPath(self.path).name

    @property
    def ready(self) -> bool:
        return not self.outside and not self.reasons


@dataclass
class ResortedFolder:
    old_folder: str
    new_folder: str
    files: list[MovedFile] = field(default_factory=list)


@dataclass
class OriginGroup:
    origin: str
    items: list[str] = field(default_factory=list)


@dataclass
class OutsideFolder:
    folder: str
    groups: list[OriginGroup] = field(default_factory=list)


@dataclass
class Moved:
    resorted: list[ResortedFolder]
    moved_books: list[BookRow]
    outside: list[OutsideFolder]


@dataclass
class Overview:
    inputs: list[InputFolder]
    outputs: list[OutputFolder]
    moved: Moved


def folder_overview(input_root: Path, output_root: Path, store: StateStore, config: Config) -> Overview:
    files = file_progress(input_root, store, config) if input_root.is_dir() else []
    unused = store.unused_paths()
    books = [b for b in store.books() if any(s not in unused for s in b.sources)]
    inputs = _input_tree(files, books, config)
    outputs = _output_tree(output_root, books, config, _flatten(inputs), store.kavita_unexpected(), store.live_folders())
    return Overview(inputs, outputs, _moved(store, books, files, config))


def _plural(count: int, singular: str, plural: str) -> str:
    return f"{count} {singular if count == 1 else plural}"


def _ancestors(path: str) -> list[str]:
    """"IT/Android/a.pdf" -> ["IT", "IT/Android"] (the folders containing the path)."""
    parts = PurePosixPath(path).parts[:-1]
    return ["/".join(parts[: i + 1]) for i in range(len(parts))]


def _book_reasons(books: list[BookRow], config: Config) -> dict[str, Counter]:
    """Per input folder, the open points of the books with files in it."""
    reasons: dict[str, Counter] = {}
    for book in books:
        if book.status != "accepted":
            continue
        if book.mislabeled and not book.target_placement:
            reason = "mislabeled"
        elif book.built_folder and book.built_folder.rpartition("/")[0] != output_parent(book, config):
            reason = "move"
        elif book.built_folder and book.needs_build:
            continue  # its files count as "accepted, not built yet"
        elif book.built_folder and not book.kavita_passed:
            checked_after_build = book.kavita_checked_at and book.kavita_checked_at >= (book.built_at or "")
            reason = "kavita problem" if checked_after_build else "kavita unchecked"
        else:
            continue
        for folder in {a for s in book.sources for a in _ancestors(s)}:
            reasons.setdefault(folder, Counter())[reason] += 1
    return reasons


_BOOK_REASON_TEXT = {
    "mislabeled": ("mislabeled book not decided", "mislabeled books not decided"),
    "move": ("book to be moved by the next build", "books to be moved by the next build"),
    "kavita problem": ("book not passed in Kavita", "books not passed in Kavita"),
    "kavita unchecked": ("book not checked in Kavita since its build", "books not checked in Kavita since their build"),
}


def _input_tree(files: list[FileProgress], books: list[BookRow], config: Config) -> list[InputFolder]:
    nodes: dict[str, InputFolder] = {}
    states: dict[str, Counter] = {}
    for f in files:
        for folder in _ancestors(f.path):
            node = nodes.setdefault(folder, InputFolder(folder))
            node.total += 1
            node.done += f.state in DONE_STATES
            states.setdefault(folder, Counter())[f.state] += 1
    book_reasons = _book_reasons(books, config)
    for path, node in nodes.items():
        node.reasons = [f"{states[path][s]} {s}" for s in STATES if s not in DONE_STATES and states[path][s]]
        for key, (singular, plural) in _BOOK_REASON_TEXT.items():
            if book_reasons.get(path, {}).get(key):
                node.reasons.append(_plural(book_reasons[path][key], singular, plural))
    return _link(nodes)


def _link(nodes: dict) -> list:
    """Puts every node below its parent; returns the top-level nodes, all sorted by name,
    with folders starting with "_" (outside the libraries) last."""
    roots = []
    for path in sorted(nodes, key=lambda p: [(part.startswith("_"), part.casefold()) for part in p.split("/")]):
        parent = path.rpartition("/")[0]
        (nodes[parent].children if parent in nodes else roots).append(nodes[path])
    return roots


def _flatten(nodes: list) -> dict:
    flat = {}
    for n in nodes:
        flat[n.path] = n
        flat.update(_flatten(n.children))
    return flat


def _output_tree(
    output_root: Path,
    books: list[BookRow],
    config: Config,
    inputs: dict[str, InputFolder],
    unexpected: list[str],
    live: dict[str, str],
) -> list[OutputFolder]:
    if not output_root.is_dir():
        return []
    book_folders = {b.built_folder: b for b in books if b.built_folder}
    outside_names = {config.mislabeled_folder, config.ignored_folder, config.misplaced_folder}
    nodes: dict[str, OutputFolder] = {}
    newest: Counter = Counter()

    for dirpath, dirnames, filenames in os.walk(output_root):
        rel = Path(dirpath).relative_to(output_root).as_posix()
        if rel == ".":
            continue
        parts = rel.split("/")
        chain = ["/".join(parts[: i + 1]) for i in range(len(parts))]
        book_at = next((i for i, folder in enumerate(chain) if folder in book_folders), None)
        folders = chain if book_at is None else chain[:book_at]
        for folder in folders:
            nodes.setdefault(folder, OutputFolder(folder, outside=parts[0] in outside_names))
        if book_at is not None and len(chain) == book_at + 1:
            for folder in folders:
                nodes[folder].books += 1
        mtimes = [os.stat(dirpath).st_mtime] + [os.stat(os.path.join(dirpath, f)).st_mtime for f in filenames]
        for folder in folders:
            nodes[folder].files += len(filenames)
            newest[folder] = max(newest[folder], *mtimes)

    for path, node in nodes.items():
        if node.outside:
            continue
        if path in inputs and not inputs[path].complete:
            node.reasons.append("input folder not complete")
        inside = [b for f, b in book_folders.items() if is_under(f.rpartition("/")[0], {path})]
        not_passed = sum(not b.kavita_passed for b in inside)
        if not_passed:
            node.reasons.append(_plural(not_passed, "book not passed in Kavita", "books not passed in Kavita"))
        coming = sum(
            1 for b in books
            if b.status == "accepted"
            and is_under(output_parent(b, config), {path})
            and (not b.built_folder or b.needs_build or not is_under(b.built_folder.rpartition("/")[0], {path}))
        )
        if coming:
            node.reasons.append(_plural(coming, "book still to be built here", "books still to be built here"))
        leftovers = sum(is_under(PurePosixPath(u).parent.as_posix(), {path}) for u in unexpected)
        if leftovers:
            node.reasons.append(_plural(leftovers, "file in Kavita that wasn't built", "files in Kavita that weren't built"))
        if path in live:
            node.live = True
            node.changed_since_live = newest[path] > datetime.fromisoformat(live[path]).timestamp()
    return _link(nodes)


def _moved(store: StateStore, books: list[BookRow], files: list[FileProgress], config: Config) -> Moved:
    resorted: dict[tuple[str, str], ResortedFolder] = {}
    for m in store.moved_files():
        key = (str(PurePosixPath(m.old_path).parent), str(PurePosixPath(m.new_path).parent))
        resorted.setdefault(key, ResortedFolder(*key)).files.append(m)

    moved_books = sorted((b for b in books if b.target_placement), key=lambda b: (b.placement, b.metadata.title))

    def grouped(folder: str, pairs) -> OutsideFolder:
        groups: dict[str, OriginGroup] = {}
        for origin, item in sorted(pairs):
            groups.setdefault(origin, OriginGroup(origin)).items.append(item)
        return OutsideFolder(folder, list(groups.values()))

    categories = {c.placement for c in store.not_book_categories()}
    misplaced = []
    for f in files:
        if f.detail == NOT_A_BOOK:
            category = next(c for c in sorted(categories) if is_under(str(PurePosixPath(f.path).parent), {c}))
            misplaced.append((category, f.path))
    outside = [
        grouped(
            config.mislabeled_folder,
            ((b.placement, b.metadata.title) for b in books if b.mislabeled and not b.target_placement and b.status == "accepted"),
        ),
        grouped(config.ignored_folder, ((str(PurePosixPath(i.path).parent), i.path) for i in store.ignored_sources())),
        grouped(config.misplaced_folder, misplaced),
    ]
    return Moved(list(resorted.values()), moved_books, outside)
