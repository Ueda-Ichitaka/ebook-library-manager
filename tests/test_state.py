"""About this file: tests for ebookprep.state, the SQLite store of pipeline results, and
the Markdown reports generated from it (ebookprep.report)."""

import sqlite3

from ebookprep.report import write_report, write_summary_report
from ebookprep.state import RecordEntry, StateStore


def entry(**overrides) -> RecordEntry:
    values = dict(
        placement="IT/Android",
        book_name="Black Hat Go - Tom Steele",
        record_name="Black Hat Go - Tom Steele",
        sources=("IT/Android/Black Hat Go.pdf",),
        outputs=("IT/Android/Black Hat Go - Tom Steele/Black Hat Go - Tom Steele.pdf",),
        title="Black Hat Go: Go Programming",
        authors=("Tom Steele",),
        metadata_source="sidecar opf",
        notes=(),
        needs_review=False,
        group_reason="single",
        status="done",
        retried=False,
        removed_xmp=(),
        repaired=("IT/Android/Black Hat Go.pdf",),
        converted=(),
        error=None,
    )
    values.update(overrides)
    return RecordEntry(**values)


def test_store_creates_schema_and_round_trips(tmp_path):
    store = StateStore(tmp_path / "state.db")
    run = store.start_run("/data/input", "/data/output")

    store.add_record(run, entry())
    store.add_skipped(run, "IT/Python/x.zip", "archive")
    store.finish_run(run)

    (row,) = store.records(run)
    assert row.book_name == "Black Hat Go - Tom Steele"
    assert row.authors == ("Tom Steele",)
    assert row.sources == ("IT/Android/Black Hat Go.pdf",)
    assert row.status == "done"
    assert store.skipped(run) == [("IT/Python/x.zip", "archive")]


def test_store_persists_across_instances(tmp_path):
    path = tmp_path / "state.db"
    run = StateStore(path).start_run("/in", "/out")
    StateStore(path).add_record(run, entry())

    assert len(StateStore(path).records(run)) == 1


def test_database_is_plain_sqlite(tmp_path):
    path = tmp_path / "state.db"
    store = StateStore(path)
    store.add_record(store.start_run("/in", "/out"), entry())

    with sqlite3.connect(path) as connection:
        assert connection.execute("select count(*) from records").fetchone() == (1,)


def test_report_lists_flagged_retried_review_and_skipped(tmp_path):
    store = StateStore(tmp_path / "state.db")
    run = store.start_run("/in", "/out")
    store.add_record(run, entry())
    store.add_record(
        run,
        entry(
            book_name="Keys to the Kingdom - Deviant Ollam",
            record_name="Keys to the Kingdom - Deviant Ollam",
            retried=True,
            removed_xmp=(("Keys to the Kingdom - Deviant Ollam.pdf", "<x:xmpmeta>PublisherSpec</x:xmpmeta>"),),
        ),
    )
    store.add_record(
        run,
        entry(book_name="Broken - X", record_name="Broken - X", status="failed", error="series not written after retry"),
    )
    store.add_record(
        run,
        entry(book_name="Wicca - Vivianne Crowley", record_name="Wicca - Vivianne Crowley", needs_review=True, group_reason="similar", notes=("metadata from filename only",)),
    )
    store.add_skipped(run, "IT/Python/x.zip", "archive")
    store.finish_run(run)

    report = write_report(store, run, tmp_path / "report.md").read_text()

    assert "| Done | 3 |" in report
    assert "| Failed (flagged) | 1 |" in report
    assert "Broken - X" in report and "series not written after retry" in report
    assert "Keys to the Kingdom - Deviant Ollam.pdf" in report and "PublisherSpec" in report
    assert "Wicca - Vivianne Crowley" in report and "metadata from filename only" in report
    assert "IT/Python/x.zip" in report and "archive" in report
    assert "IT/Android/Black Hat Go.pdf" in report


# --- report across all runs


def test_latest_records_keep_the_newest_result_per_source(tmp_path):
    store = StateStore(tmp_path / "state.db")
    first = store.start_run("/in", "/out")
    store.add_record(first, entry(sources=("a.pdf",), status="failed", error="boom"))
    store.add_record(first, entry(sources=("b.pdf",), retried=True))
    second = store.start_run("/in", "/out")
    store.add_record(second, entry(sources=("a.pdf",)))

    latest = store.latest_records()

    assert sorted((r.sources, r.status) for r in latest) == [(("a.pdf",), "done"), (("b.pdf",), "done")]
    assert any(r.retried for r in latest)


def test_summary_report_covers_all_runs(tmp_path):
    store = StateStore(tmp_path / "state.db")
    first = store.start_run("/in", "/out")
    store.add_record(first, entry(sources=("a.pdf",), repaired=("a.pdf",)))
    store.add_skipped(first, "x.zip", "archive")
    second = store.start_run("/in", "/out")
    store.add_record(second, entry(sources=("b.pdf",), status="failed", error="truncated file", repaired=()))
    store.add_skipped(second, "x.zip", "archive")

    report = write_summary_report(store, tmp_path / "report.md").read_text()

    assert "all runs" in report
    assert "| Done | 1 |" in report
    assert "| Failed (flagged) | 1 |" in report
    assert "| Repaired PDFs | 1 |" in report
    assert report.count("`x.zip`: archive") == 1


def test_existing_database_gets_the_mislabeled_column(tmp_path):
    path = tmp_path / "state.db"
    with sqlite3.connect(path) as db:
        db.execute("CREATE TABLE books (id INTEGER PRIMARY KEY, key TEXT)")

    StateStore(path)

    with sqlite3.connect(path) as db:
        columns = {row[1] for row in db.execute("PRAGMA table_info(books)")}
    assert "mislabeled" in columns


def test_summary_report_lists_mislabeled_files(tmp_path):
    from ebookprep.metadata import BookMetadata
    from ebookprep.state import NewBook

    store = StateStore(tmp_path / "state.db")
    book_id = store.add_book(
        NewBook(
            key="k", placement="IT/Physical Security", group_reason="single", book_folder=None,
            sources=("IT/Physical Security/The Complete Guide to Physical Security.pdf",),
            local_metadata=BookMetadata(title="The Complete Guide to Physical Security"), local_source="filename",
            local_notes=(), evidence_excerpt="", evidence_isbns=(), lookup_errors=(), status="accepted",
            decided_by="auto: Open Library", review_reasons=(),
            metadata=BookMetadata(title="Moral Design and Technology", authors=("Bart Wernaart",)), mislabeled=True,
        )
    )
    store.mark_built(book_id, store.book(book_id).metadata, "_mislabeled/IT/Physical Security/Moral Design and Technology - Bart Wernaart")

    report = write_summary_report(store, tmp_path / "report.md").read_text()

    assert "## Content differs from the filename" in report
    assert "The Complete Guide to Physical Security" in report and "Moral Design and Technology" in report
    assert "`IT/Physical Security/The Complete Guide to Physical Security.pdf`" in report
    assert "`_mislabeled/IT/Physical Security/Moral Design and Technology - Bart Wernaart`" in report


# --- duplicates: excluded files and dismissed groups


def test_exclude_and_restore_sources(tmp_path):
    store = StateStore(tmp_path / "state.db")

    store.exclude_sources(["IT/a.epub", "IT/b.pdf"], kept="IT/a.pdf", group="g1")

    assert store.excluded_paths() == {"IT/a.epub", "IT/b.pdf"}
    (first, second) = store.excluded_sources()
    assert (first.path, first.kept, first.group) == ("IT/a.epub", "IT/a.pdf", "g1")

    store.restore_source("IT/a.epub")

    assert store.excluded_paths() == {"IT/b.pdf"}


def test_dismissed_duplicate_groups(tmp_path):
    store = StateStore(tmp_path / "state.db")

    store.dismiss_duplicates("abc123")
    store.dismiss_duplicates("abc123")

    assert store.dismissed_duplicates() == {"abc123"}


def test_built_sources_are_recorded_and_can_be_cleared(tmp_path):
    from ebookprep.metadata import BookMetadata
    from ebookprep.state import NewBook

    store = StateStore(tmp_path / "state.db")
    metadata = BookMetadata(title="T")
    book_id = store.add_book(
        NewBook(key="k", placement="IT/A", group_reason="single", book_folder=None, sources=("a.pdf", "a.epub"),
                local_metadata=metadata, local_source="filename", local_notes=(), evidence_excerpt="",
                evidence_isbns=(), lookup_errors=(), status="accepted", decided_by="user", review_reasons=(),
                metadata=metadata)
    )

    store.mark_built(book_id, metadata, "IT/A/T", sources=("a.pdf",))
    assert store.book(book_id).built_sources == ("a.pdf",)

    store.clear_built(book_id)
    book = store.book(book_id)
    assert (book.built_folder, book.built_metadata, book.built_series, book.built_sources) == (None, None, None, None)


def test_ignore_and_restore_sources(tmp_path):
    store = StateStore(tmp_path / "state.db")
    store.exclude_sources(["dup.epub"], kept="dup.pdf", group="g")

    store.ignore_sources(["DIY/plan.jpg.pdf", "DIY/other.jpg.pdf"])

    assert store.ignored_paths() == {"DIY/plan.jpg.pdf", "DIY/other.jpg.pdf"}
    assert store.unused_paths() == {"DIY/plan.jpg.pdf", "DIY/other.jpg.pdf", "dup.epub"}
    store.restore_ignored("DIY/other.jpg.pdf")
    assert [i.path for i in store.ignored_sources()] == ["DIY/plan.jpg.pdf"]


def test_move_book_sets_and_clears_the_target_placement(tmp_path):
    from ebookprep.metadata import BookMetadata
    from ebookprep.state import NewBook

    store = StateStore(tmp_path / "state.db")
    meta = BookMetadata(title="Moral Design and Technology")
    book_id = store.add_book(
        NewBook(key="k", placement="IT/Physical Security", group_reason="single", book_folder=None, sources=("a.pdf",),
                local_metadata=meta, local_source="filename", local_notes=(), evidence_excerpt="", evidence_isbns=(),
                lookup_errors=(), status="accepted", decided_by="auto: Open Library", review_reasons=(), metadata=meta,
                mislabeled=True)
    )
    assert store.book(book_id).target_placement is None

    store.move_book(book_id, "Scientific Writing/PhD")
    assert store.book(book_id).target_placement == "Scientific Writing/PhD"

    store.move_book(book_id, None)
    assert store.book(book_id).target_placement is None
