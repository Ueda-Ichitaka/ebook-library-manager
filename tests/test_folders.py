"""About this file: tests for ebookprep.folders, the folder overview of the start page:
the input tree (files processed per folder, complete when ready for the live library),
the output tree (books and files per folder, ready mark, live mark) and the overview of
moved files and books outside the library structure."""

import os
import time
from pathlib import Path

from ebookprep.config import Config
from ebookprep.folders import folder_overview
from ebookprep.metadata import BookMetadata
from ebookprep.state import KavitaIssue, NewBook, StateStore


def touch(root: Path, *paths: str) -> None:
    for rel in paths:
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        (root / rel).write_bytes(b"x")


def add_book(store, sources, status="accepted", title="T", placement=None, **extra):
    metadata = BookMetadata(title=title)
    return store.add_book(
        NewBook(key=str(sources), placement=placement or sources[0].rsplit("/", 1)[0], group_reason="single",
                book_folder=None, sources=sources, local_metadata=metadata, local_source="filename", local_notes=(),
                evidence_excerpt="", evidence_isbns=(), lookup_errors=(), status=status, decided_by="user",
                review_reasons=(), metadata=metadata, **extra)
    )


def build(store, output, book_id, folder, title="T"):
    touch(output, f"{folder}/{title}.pdf", f"{folder}/{title}.opf")
    store.mark_built(book_id, BookMetadata(title=title), folder)


def setup(tmp_path):
    return tmp_path / "input", tmp_path / "output", StateStore(tmp_path / "state.db")


def overview(tmp_path, store):
    return folder_overview(tmp_path / "input", tmp_path / "output", store, Config())


def node(nodes, path):
    for n in nodes:
        if n.path == path:
            return n
        found = node(n.children, path)
        if found:
            return found
    return None


def test_input_tree_counts_processed_files_per_folder_and_its_parents(tmp_path):
    root, output, store = setup(tmp_path)
    touch(root, "IT/Android/a.pdf", "IT/Android/b.pdf", "IT/Python/c.pdf", "IT/Python/code.zip")
    a = add_book(store, ("IT/Android/a.pdf",))
    b = add_book(store, ("IT/Android/b.pdf",))
    add_book(store, ("IT/Python/c.pdf",), status="review")
    for book_id, name in ((a, "A"), (b, "B")):
        build(store, output, book_id, f"IT/Android/{name}")
    store.record_kavita_check({a, b}, [])

    inputs = overview(tmp_path, store).inputs

    android, python, it = node(inputs, "IT/Android"), node(inputs, "IT/Python"), node(inputs, "IT")
    assert (android.done, android.total, android.complete) == (2, 2, True)
    assert (python.done, python.total, python.complete) == (1, 2, False)
    assert python.reasons == ["1 waiting for review"]
    assert (it.done, it.total, it.complete) == (3, 4, False)
    assert [c.name for c in it.children] == ["Android", "Python"]


def test_folder_with_an_undecided_mislabeled_book_or_a_kavita_problem_is_not_complete(tmp_path):
    root, output, store = setup(tmp_path)
    touch(root, "IT/Android/a.pdf", "IT/Python/b.pdf", "IT/Web/c.pdf")
    mislabeled = add_book(store, ("IT/Android/a.pdf",), mislabeled=True)
    build(store, output, mislabeled, "_mislabeled/IT/Android/A")
    python = add_book(store, ("IT/Python/b.pdf",))
    build(store, output, python, "IT/Python/B")
    web = add_book(store, ("IT/Web/c.pdf",))
    build(store, output, web, "IT/Web/C")
    store.record_kavita_check({python}, [KavitaIssue(kind="missing", path="IT/Python/B/T.pdf", book_id=python)])

    inputs = overview(tmp_path, store).inputs

    assert node(inputs, "IT/Android").reasons == ["1 mislabeled book not decided"]
    assert node(inputs, "IT/Python").reasons == ["1 book not passed in Kavita"]
    assert node(inputs, "IT/Web").reasons == ["1 book not checked in Kavita since its build"]

    store.move_book(mislabeled, "IT/Web")
    assert node(overview(tmp_path, store).inputs, "IT/Android").reasons == ["1 book to be moved by the next build"]


def test_output_tree_stops_at_book_folders_and_has_a_ready_mark(tmp_path):
    root, output, store = setup(tmp_path)
    touch(root, "IT/Android/a.pdf", "IT/Android/b.pdf", "IT/Python/c.pdf")
    ids = [add_book(store, (f"IT/Android/{n}.pdf",)) for n in "ab"]
    for book_id, name in zip(ids, "AB"):
        build(store, output, book_id, f"IT/Android/{name}")
    c = add_book(store, ("IT/Python/c.pdf",))
    build(store, output, c, "IT/Python/C")
    store.record_kavita_check({*ids, c}, [KavitaIssue(kind="unexpected", path="IT/Python/leftover.pdf")])
    touch(output, "_ignored/DIY/Plans/plan.jpg.pdf")

    outputs = overview(tmp_path, store).outputs

    assert [n.path for n in outputs] == ["IT", "_ignored"]
    android = node(outputs, "IT/Android")
    assert (android.books, android.files, android.children, android.ready) == (2, 4, [], True)
    python = node(outputs, "IT/Python")
    assert not python.ready and python.reasons == ["1 file in Kavita that wasn't built"]
    assert node(outputs, "IT").ready is False
    ignored = node(outputs, "_ignored")
    assert (ignored.books, ignored.files, ignored.outside) == (0, 1, True)
    assert node(outputs, "_ignored/DIY/Plans").files == 1


def test_output_folder_is_not_ready_while_its_input_folder_is_not_complete(tmp_path):
    root, output, store = setup(tmp_path)
    touch(root, "IT/Android/a.pdf", "IT/Android/b.pdf")
    a = add_book(store, ("IT/Android/a.pdf",))
    build(store, output, a, "IT/Android/A")
    add_book(store, ("IT/Android/b.pdf",), status="review")
    store.record_kavita_check({a}, [])

    android = node(overview(tmp_path, store).outputs, "IT/Android")

    assert not android.ready and android.reasons == ["input folder not complete"]


def test_live_mark_shows_when_the_folder_changed_afterwards(tmp_path):
    root, output, store = setup(tmp_path)
    touch(root, "IT/Android/a.pdf")
    a = add_book(store, ("IT/Android/a.pdf",))
    build(store, output, a, "IT/Android/A")
    store.mark_live("IT/Android")

    android = node(overview(tmp_path, store).outputs, "IT/Android")
    assert android.live and not android.changed_since_live

    later = time.time() + 5
    touch(output, "IT/Android/A/A.jpg")
    os.utime(output / "IT/Android/A/A.jpg", (later, later))
    android = node(overview(tmp_path, store).outputs, "IT/Android")
    assert android.live and android.changed_since_live


def test_moved_overview_groups_resorted_files_moved_books_and_outside_folders(tmp_path):
    root, output, store = setup(tmp_path)
    touch(root, "DIY/Nähen/a.pdf", "DIY/Nähen/b.pdf", "IT/Web/w.pdf", "IT/Android/m.pdf", "DIY/Plans/p.jpg.pdf",
          "Magazine/elektor/DE2010.pdf", "Magazine/elektor/DE2011.pdf")
    for name in "ab":
        store.record_move(f"DIY/Mittelalter/{name}.pdf", f"DIY/Nähen/{name}.pdf", "same filename", None)
    moved = add_book(store, ("IT/Web/w.pdf",), title="Moral Design", mislabeled=True)
    store.move_book(moved, "Scientific Writing/PhD")
    add_book(store, ("IT/Android/m.pdf",), title="Wrong Book", mislabeled=True)
    store.ignore_sources(["DIY/Plans/p.jpg.pdf"])
    store.flag_not_books("Magazine/elektor")

    moved_view = overview(tmp_path, store).moved

    (resorted,) = moved_view.resorted
    assert (resorted.old_folder, resorted.new_folder, len(resorted.files)) == ("DIY/Mittelalter", "DIY/Nähen", 2)
    assert [(b.placement, b.target_placement) for b in moved_view.moved_books] == [("IT/Web", "Scientific Writing/PhD")]
    assert {o.folder: [(g.origin, len(g.items)) for g in o.groups] for o in moved_view.outside} == {
        "_mislabeled": [("IT/Android", 1)],
        "_ignored": [("DIY/Plans", 1)],
        "_misplaced": [("Magazine/elektor", 2)],
    }
