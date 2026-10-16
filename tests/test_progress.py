"""About this file: tests for ebookprep.progress, which tells for every input file how far
it is: skipped, not identified yet, waiting for review, accepted, built, build failed,
excluded duplicate; with totals per library and category."""

from pathlib import Path

from ebookprep.config import Config
from ebookprep.metadata import BookMetadata
from ebookprep.progress import (
    ACCEPTED, BUILD_FAILED, BUILT, EXCLUDED, NOT_IDENTIFIED, REVIEW, SKIPPED, file_progress, summarize,
)
from ebookprep.state import NewBook, RecordEntry, StateStore


def touch(root: Path, *paths: str) -> None:
    for rel in paths:
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        (root / rel).write_bytes(b"x")


def add_book(store, sources, status="accepted", title="T"):
    metadata = BookMetadata(title=title)
    return store.add_book(
        NewBook(key=str(sources), placement=sources[0].rsplit("/", 1)[0], group_reason="single", book_folder=None,
                sources=sources, local_metadata=metadata, local_source="filename", local_notes=(),
                evidence_excerpt="", evidence_isbns=(), lookup_errors=(), status=status, decided_by="user",
                review_reasons=(), metadata=metadata)
    )


def failed_record(sources):
    return RecordEntry(placement="IT/Android", book_name="F", record_name="F", sources=sources, outputs=(),
                       title="F", authors=(), metadata_source="user", notes=(), needs_review=False,
                       group_reason="single", status="failed", retried=False, removed_xmp=(), repaired=(),
                       converted=(), error="truncated file")


def test_every_input_file_gets_its_state(tmp_path):
    root = tmp_path / "input"
    touch(root, "IT/Android/built.pdf", "IT/Android/built.epub", "IT/Android/review.pdf", "IT/Android/accepted.pdf",
          "IT/Android/new.pdf", "IT/Android/broken.pdf", "IT/Python/code.zip", "Woodworking/joinery/j.pdf")
    store = StateStore(tmp_path / "state.db")
    built = add_book(store, ("IT/Android/built.pdf", "IT/Android/built.epub"))
    store.mark_built(built, BookMetadata(title="T"), "IT/Android/T", sources=("IT/Android/built.pdf",))
    store.exclude_sources(["IT/Android/built.epub"], kept="IT/Android/built.pdf", group="g")
    add_book(store, ("IT/Android/review.pdf",), status="review", title="R")
    add_book(store, ("IT/Android/accepted.pdf",))
    add_book(store, ("IT/Android/broken.pdf",))
    add_book(store, ("Woodworking/joinery/j.pdf",), status="review")
    run = store.start_run("i", "o")
    store.add_record(run, failed_record(("IT/Android/broken.pdf",)))

    files = {f.path: f for f in file_progress(root, store, Config())}

    assert {p: f.state for p, f in files.items()} == {
        "IT/Android/built.pdf": BUILT,
        "IT/Android/built.epub": EXCLUDED,
        "IT/Android/review.pdf": REVIEW,
        "IT/Android/accepted.pdf": ACCEPTED,
        "IT/Android/new.pdf": NOT_IDENTIFIED,
        "IT/Android/broken.pdf": BUILD_FAILED,
        "IT/Python/code.zip": SKIPPED,
        "Woodworking/joinery/j.pdf": REVIEW,
    }
    assert files["IT/Android/broken.pdf"].detail == "truncated file"
    assert files["IT/Python/code.zip"].detail == "archive"
    assert files["IT/Android/review.pdf"].book_id is not None
    assert files["IT/Android/review.pdf"].placement == "IT/Android"


def test_summary_counts_per_state_and_placement(tmp_path):
    root = tmp_path / "input"
    touch(root, "IT/Android/a.pdf", "IT/Android/b.pdf", "Woodworking/joinery/c.pdf")
    store = StateStore(tmp_path / "state.db")
    a = add_book(store, ("IT/Android/a.pdf",))
    store.mark_built(a, BookMetadata(title="T"), "IT/Android/T")
    add_book(store, ("IT/Android/b.pdf",), status="review")

    summary = summarize(file_progress(root, store, Config()))

    assert summary.total == 3
    assert summary.counts[BUILT] == 1 and summary.counts[REVIEW] == 1 and summary.counts[NOT_IDENTIFIED] == 1
    android = summary.by_placement["IT/Android"]
    assert (android.total, android.counts[BUILT], android.done_percent) == (2, 1, 50)
    assert summary.by_placement["Woodworking/joinery"].done_percent == 0


def test_ignored_files_count_as_skipped(tmp_path):
    root = tmp_path / "input"
    touch(root, "DIY/Plans/plan.jpg.pdf")
    store = StateStore(tmp_path / "state.db")
    add_book(store, ("DIY/Plans/plan.jpg.pdf",), status="review")
    store.ignore_sources(["DIY/Plans/plan.jpg.pdf"])

    (f,) = file_progress(root, store, Config())

    assert (f.state, f.detail) == (SKIPPED, "ignored in review")


def test_files_of_categories_that_are_not_books_count_as_skipped(tmp_path):
    root = tmp_path / "input"
    touch(root, "Magazine/elektor/DE2010.pdf", "Magazine/elektor/new.pdf", "Magazine/elektor/plans.zip")
    store = StateStore(tmp_path / "state.db")
    add_book(store, ("Magazine/elektor/DE2010.pdf",), status="review")

    store.flag_not_books("Magazine/elektor")
    files = file_progress(root, store, Config())

    assert {(f.path, f.state, f.detail) for f in files} == {
        ("Magazine/elektor/DE2010.pdf", SKIPPED, "not a book"),
        ("Magazine/elektor/new.pdf", SKIPPED, "not a book"),
        ("Magazine/elektor/plans.zip", SKIPPED, "not a book"),
    }
    assert {f.placement for f in files} == {"Magazine/elektor"}


def test_known_files_missing_from_the_input_are_listed(tmp_path):
    from ebookprep.progress import MISSING

    root = tmp_path / "input"
    touch(root, "IT/Android/here.pdf")
    store = StateStore(tmp_path / "state.db")
    book_id = add_book(store, ("IT/Android/gone.pdf",))

    files = {f.path: f for f in file_progress(root, store, Config())}

    assert (files["IT/Android/gone.pdf"].state, files["IT/Android/gone.pdf"].book_id) == (MISSING, book_id)
    assert files["IT/Android/gone.pdf"].placement == "IT/Android"


def test_built_book_whose_metadata_changed_since_is_not_done(tmp_path):
    root = tmp_path / "input"
    touch(root, "IT/Android/a.pdf")
    store = StateStore(tmp_path / "state.db")
    book_id = add_book(store, ("IT/Android/a.pdf",))
    store.mark_built(book_id, BookMetadata(title="Old title"), "IT/Android/Old title")

    (f,) = file_progress(root, store, Config())

    assert (f.state, f.detail) == (ACCEPTED, "changed since the last build")


def test_damaged_downloads_that_cant_be_repaired_are_not_done(tmp_path):
    from ebookprep.progress import DAMAGED
    from ebookprep.state import DownloadCheck

    root = tmp_path / "input"
    touch(root, "A/b/lost.epub", "A/b/fixable.pdf")
    store = StateStore(tmp_path / "state.db")
    add_book(store, ("A/b/lost.epub",))
    add_book(store, ("A/b/fixable.pdf",))
    store.save_download_checks([DownloadCheck("A/b/lost.epub", 1, 1, "overwritten", False, "lost content: Ch01"),
                                DownloadCheck("A/b/fixable.pdf", 1, 1, "overwritten", True, "header rebuilt")])

    files = {f.path: f for f in file_progress(root, store, Config())}

    assert (files["A/b/lost.epub"].state, files["A/b/lost.epub"].detail) == (DAMAGED, "overwritten; lost content: Ch01")
    assert files["A/b/fixable.pdf"].state == ACCEPTED
