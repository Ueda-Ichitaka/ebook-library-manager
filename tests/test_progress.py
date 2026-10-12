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
