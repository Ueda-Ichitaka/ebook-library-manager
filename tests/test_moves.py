"""About this file: tests for ebookprep.moves, which recognises known input files that
were moved or renamed (e.g. after re-sorting the input) and carries their book, decisions
and states over to the new path."""

import shutil
from pathlib import Path

from ebookprep.config import Config
from ebookprep.duplicates import find_duplicates
from ebookprep.metadata import BookMetadata
from ebookprep.moves import REASON_SPLIT_PLACEMENT, detect_moves
from ebookprep.state import NewBook, StateStore

QUIET = {"log": lambda _: None}


def write(root: Path, rel: str, content: bytes = b"%PDF-1.4 x") -> str:
    (root / rel).parent.mkdir(parents=True, exist_ok=True)
    (root / rel).write_bytes(content)
    return rel


def move(root: Path, old: str, new: str) -> None:
    (root / new).parent.mkdir(parents=True, exist_ok=True)
    shutil.move(root / old, root / new)


def add_book(store, placement, sources, title="T", **extra):
    meta = BookMetadata(title=title, authors=("A B",))
    values = dict(key=str(sources), placement=placement, group_reason="single", book_folder=None, sources=sources,
                  local_metadata=meta, local_source="filename", local_notes=(), evidence_excerpt="",
                  evidence_isbns=(), lookup_errors=(), status="accepted", decided_by="user", review_reasons=(),
                  metadata=meta)
    values.update(extra)
    return store.add_book(NewBook(**values))


def setup(tmp_path):
    return tmp_path / "input", StateStore(tmp_path / "state.db")


def test_moved_and_renamed_file_is_recognised_by_its_content(tmp_path):
    root, store = setup(tmp_path)
    old = write(root, "DIY/Nähen/schnittmuster-12911.pdf", b"%PDF pattern 12911")
    book_id = add_book(store, "DIY/Nähen", (old,))
    assert detect_moves(root, store, Config(), **QUIET).moved == []

    move(root, old, "DIY/Schnittmuster/Bluse 12911.pdf")
    summary = detect_moves(root, store, Config(), **QUIET)

    book = store.book(book_id)
    assert book.sources == ("DIY/Schnittmuster/Bluse 12911.pdf",)
    assert book.placement == "DIY/Schnittmuster"
    assert (book.status, book.decided_by) == ("accepted", "user")
    assert [(m.old_path, m.new_path, m.method) for m in summary.moved] == [
        (old, "DIY/Schnittmuster/Bluse 12911.pdf", "same content")
    ]
    assert [m.new_path for m in store.moved_files()] == ["DIY/Schnittmuster/Bluse 12911.pdf"]


def test_file_moved_before_its_hash_was_stored_is_recognised_by_a_unique_filename(tmp_path):
    root, store = setup(tmp_path)
    write(root, "DIY/Schnittmuster/schnittmuster-12911.pdf")
    write(root, "DIY/Schnittmuster/other.pdf", b"other")
    book_id = add_book(store, "DIY/Nähen", ("DIY/Nähen/schnittmuster-12911.pdf",))

    summary = detect_moves(root, store, Config(), **QUIET)

    assert [(m.new_path, m.method) for m in summary.moved] == [("DIY/Schnittmuster/schnittmuster-12911.pdf", "same filename")]
    assert store.book(book_id).placement == "DIY/Schnittmuster"


def test_filename_that_is_not_unique_is_not_taken_as_a_move(tmp_path):
    root, store = setup(tmp_path)
    write(root, "DIY/A/plan.pdf", b"1")
    write(root, "DIY/B/plan.pdf", b"2")
    book_id = add_book(store, "DIY/Nähen", ("DIY/Nähen/plan.pdf",))

    summary = detect_moves(root, store, Config(), **QUIET)

    assert summary.moved == [] and summary.missing == ["DIY/Nähen/plan.pdf"]
    assert store.book(book_id).sources == ("DIY/Nähen/plan.pdf",)


def test_copy_of_a_file_that_is_still_there_is_not_a_move(tmp_path):
    root, store = setup(tmp_path)
    old = write(root, "IT/Android/a.pdf", b"same")
    add_book(store, "IT/Android", (old,))
    detect_moves(root, store, Config(), **QUIET)
    write(root, "IT/Python/a.pdf", b"same")

    assert detect_moves(root, store, Config(), **QUIET).moved == []


def test_renamed_file_in_the_same_folder_keeps_its_build(tmp_path):
    root, store = setup(tmp_path)
    old = write(root, "IT/Android/a.pdf", b"content a")
    book_id = add_book(store, "IT/Android", (old,))
    store.mark_built(book_id, BookMetadata(title="T", authors=("A B",)), "IT/Android/T - A B", "T", sources=(old,))
    detect_moves(root, store, Config(), **QUIET)

    move(root, old, "IT/Android/Android Book.pdf")
    detect_moves(root, store, Config(), **QUIET)

    book = store.book(book_id)
    assert book.built_sources == book.sources == ("IT/Android/Android Book.pdf",)
    assert book.placement == "IT/Android" and not book.needs_build


def test_book_whose_files_end_up_in_different_categories_goes_to_review(tmp_path):
    root, store = setup(tmp_path)
    pdf, epub = write(root, "IT/Android/a.pdf", b"pdf"), write(root, "IT/Android/a.epub", b"epub")
    book_id = add_book(store, "IT/Android", (pdf, epub))
    detect_moves(root, store, Config(), **QUIET)

    move(root, epub, "IT/Python/a.epub")
    detect_moves(root, store, Config(), **QUIET)

    book = store.book(book_id)
    assert book.sources == (pdf, "IT/Python/a.epub")
    assert (book.placement, book.status) == ("IT/Android", "review")
    assert REASON_SPLIT_PLACEMENT in book.review_reasons


def test_dismissed_duplicate_group_stays_dismissed_after_a_move(tmp_path):
    root, store = setup(tmp_path)
    a, b = write(root, "IT/Android/a.pdf", b"a"), write(root, "IT/Android/b.pdf", b"b")
    add_book(store, "IT/Android", (a,), title="Same Title")
    add_book(store, "IT/Android", (b,), title="Same Title")
    (group,) = find_duplicates(store.books())
    store.dismiss_duplicates(group.key)
    detect_moves(root, store, Config(), **QUIET)

    move(root, b, "IT/Python/b.pdf")
    detect_moves(root, store, Config(), **QUIET)

    assert find_duplicates(store.books(), dismissed=store.dismissed_duplicates()) == []


def test_missing_file_without_a_match_is_reported(tmp_path):
    root, store = setup(tmp_path)
    write(root, "IT/Android/other.pdf", b"x")
    add_book(store, "IT/Android", ("IT/Android/gone.pdf",))

    summary = detect_moves(root, store, Config(), **QUIET)

    assert summary.missing == ["IT/Android/gone.pdf"]


def test_hashes_are_only_computed_for_new_or_changed_files(tmp_path, monkeypatch):
    from ebookprep import moves

    root, store = setup(tmp_path)
    write(root, "IT/Android/a.pdf", b"a")
    detect_moves(root, store, Config(), **QUIET)
    hashed = []
    original = moves.sha256_of
    monkeypatch.setattr(moves, "sha256_of", lambda path: hashed.append(path.name) or original(path))

    write(root, "IT/Android/b.pdf", b"b")
    detect_moves(root, store, Config(), **QUIET)

    assert hashed == ["b.pdf"]


def test_identify_and_build_detect_moves_first_and_the_command_runs_alone(tmp_path, capsys):
    from ebookprep.__main__ import main

    root, store = setup(tmp_path)
    work = tmp_path / "work"
    store = StateStore(work / "state.db")
    old = write(root, "DIY/Nähen/schnittmuster-1.pdf", b"one")
    book_id = add_book(store, "DIY/Nähen", (old,), status="review", decided_by="proposal: local")
    config = tmp_path / "config.toml"
    config.write_text("sources = []\n")
    paths = ["--input", str(root), "--work", str(work), "--config", str(config)]

    assert main(["detect-moves", *paths]) == 0
    move(root, old, "DIY/Schnittmuster/schnittmuster-1.pdf")
    assert main(["identify", *paths]) == 0

    assert [b.id for b in store.books()] == [book_id]
    assert store.book(book_id).sources == ("DIY/Schnittmuster/schnittmuster-1.pdf",)
    out = capsys.readouterr().out
    assert "moved (same content): DIY/Nähen/schnittmuster-1.pdf -> DIY/Schnittmuster/schnittmuster-1.pdf" in out

    move(root, "DIY/Schnittmuster/schnittmuster-1.pdf", "DIY/Plans/schnittmuster-1.pdf")
    main(["build", "--input", str(root), "--output", str(tmp_path / "output"), "--work", str(work), "--config", str(config)])
    assert store.book(book_id).placement == "DIY/Plans"
