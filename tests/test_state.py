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


def test_not_book_categories_leave_all_their_files_unused(tmp_path):
    from ebookprep.metadata import BookMetadata
    from ebookprep.state import NewBook

    store = StateStore(tmp_path / "state.db")
    meta = BookMetadata(title="Elektor")
    for key, placement, source in (("a", "Magazine/elektor", "Magazine/elektor/DE2010.pdf"),
                                   ("b", "Magazine/elektor/2011", "Magazine/elektor/2011/DE2011.pdf"),
                                   ("c", "Magazine/elektorx", "Magazine/elektorx/x.pdf")):
        store.add_book(
            NewBook(key=key, placement=placement, group_reason="single", book_folder=None, sources=(source,),
                    local_metadata=meta, local_source="filename", local_notes=(), evidence_excerpt="",
                    evidence_isbns=(), lookup_errors=(), status="review", decided_by="", review_reasons=(), metadata=meta)
        )

    store.flag_not_books("Magazine/elektor")

    assert [c.placement for c in store.not_book_categories()] == ["Magazine/elektor"]
    assert store.not_book_paths() == {"Magazine/elektor/DE2010.pdf", "Magazine/elektor/2011/DE2011.pdf"}
    assert store.unused_paths() == store.not_book_paths()
    store.restore_books("Magazine/elektor")
    assert store.not_book_categories() == [] and store.unused_paths() == set()


def test_is_not_book_placement():
    from ebookprep.state import is_under

    assert is_under("Magazine/elektor", {"Magazine/elektor"})
    assert is_under("Magazine/elektor/2011", {"Magazine/elektor"})
    assert not is_under("Magazine/elektorx", {"Magazine/elektor"})
    assert not is_under("Magazine", {"Magazine/elektor"})


def test_summary_report_lists_categories_that_are_not_books(tmp_path):
    from ebookprep.report import summary_report

    store = StateStore(tmp_path / "state.db")
    store.flag_not_books("Magazine/elektor")

    report = write_summary_report(store, tmp_path / "report.md").read_text()

    assert "## Not books" in report and "- `Magazine/elektor`" in report
    assert [c.placement for c in summary_report(store).not_books] == ["Magazine/elektor"]


# --- moved files


def _book(store, key, placement, sources, **extra):
    from ebookprep.metadata import BookMetadata
    from ebookprep.state import NewBook

    meta = BookMetadata(title=key)
    values = dict(key=key, placement=placement, group_reason="single", book_folder=None, sources=sources,
                  local_metadata=meta, local_source="filename", local_notes=(), evidence_excerpt="",
                  evidence_isbns=(), lookup_errors=(), status="accepted", decided_by="user", review_reasons=(),
                  metadata=meta)
    values.update(extra)
    return store.add_book(NewBook(**values))


def test_file_hashes_are_stored_and_replaced(tmp_path):
    from ebookprep.state import FileHash

    store = StateStore(tmp_path / "state.db")
    store.save_file_hashes([FileHash("a.pdf", 10, 1, "aa"), FileHash("b.pdf", 20, 2, "bb")])
    store.save_file_hashes([FileHash("a.pdf", 11, 3, "cc")])

    assert store.file_hashes() == {"a.pdf": FileHash("a.pdf", 11, 3, "cc"), "b.pdf": FileHash("b.pdf", 20, 2, "bb")}


def test_move_source_renames_the_path_everywhere(tmp_path):
    from ebookprep.metadata import BookMetadata
    from ebookprep.state import FileHash

    store = StateStore(tmp_path / "state.db")
    book_id = _book(store, "k", "DIY/Nähen", ("DIY/Nähen/a.pdf", "DIY/Nähen/a.epub"))
    store.mark_built(book_id, BookMetadata(title="k"), "DIY/Nähen/k", sources=("DIY/Nähen/a.pdf",))
    store.exclude_sources(["DIY/Nähen/a.epub"], kept="DIY/Nähen/a.pdf", group="g")
    store.ignore_sources(["DIY/Nähen/x.pdf"])
    store.save_file_hashes([FileHash("DIY/Nähen/a.pdf", 1, 1, "h")])

    store.move_source("DIY/Nähen/a.pdf", "DIY/Schnittmuster/a.pdf")
    store.move_source("DIY/Nähen/x.pdf", "DIY/Schnittmuster/x.pdf")

    book = store.book(book_id)
    assert book.sources == ("DIY/Schnittmuster/a.pdf", "DIY/Nähen/a.epub")
    assert book.built_sources == ("DIY/Schnittmuster/a.pdf",)
    assert store.known_sources() == {"DIY/Schnittmuster/a.pdf", "DIY/Nähen/a.epub"}
    assert [(e.path, e.kept) for e in store.excluded_sources()] == [("DIY/Nähen/a.epub", "DIY/Schnittmuster/a.pdf")]
    assert store.ignored_paths() == {"DIY/Schnittmuster/x.pdf"}
    assert set(store.file_hashes()) == {"DIY/Schnittmuster/a.pdf"}


def test_moved_files_are_recorded_and_placement_and_review_can_be_set(tmp_path):
    store = StateStore(tmp_path / "state.db")
    book_id = _book(store, "k", "DIY/Nähen", ("DIY/Nähen/a.pdf",))

    store.record_move("DIY/Nähen/a.pdf", "DIY/Schnittmuster/a.pdf", "same content", book_id)
    store.set_placement(book_id, "DIY/Schnittmuster")
    store.require_review(book_id, "files in different categories")
    store.require_review(book_id, "files in different categories")

    (move,) = store.moved_files()
    assert (move.old_path, move.new_path, move.method, move.book_id) == (
        "DIY/Nähen/a.pdf", "DIY/Schnittmuster/a.pdf", "same content", book_id
    )
    book = store.book(book_id)
    assert (book.placement, book.status, book.review_reasons) == ("DIY/Schnittmuster", "review", ("files in different categories",))


def test_summary_report_lists_moved_files(tmp_path):
    store = StateStore(tmp_path / "state.db")
    store.record_move("A/x/a.pdf", "A/y/a.pdf", "same content", None)

    report = write_summary_report(store, tmp_path / "report.md").read_text()

    assert "## Moved files" in report and "| `A/x/a.pdf` | `A/y/a.pdf` | same content |" in report


# --- Kavita check results and live marks


def test_kavita_check_passes_only_without_problem_and_after_the_last_build(tmp_path):
    from ebookprep.metadata import BookMetadata
    from ebookprep.state import KavitaIssue

    store = StateStore(tmp_path / "state.db")
    ok, broken = _book(store, "ok", "IT/Android", ("a.pdf",)), _book(store, "broken", "IT/Android", ("b.pdf",))
    for book_id in (ok, broken):
        store.mark_built(book_id, BookMetadata(title="T"), f"IT/Android/{book_id}")
    assert store.book(ok).built_at is not None and not store.book(ok).kavita_passed

    store.record_kavita_check({ok, broken}, [KavitaIssue(kind="missing", path="IT/Android/2/T.pdf", book_id=broken)])

    assert store.book(ok).kavita_passed and not store.book(broken).kavita_passed
    assert store.book(broken).kavita_problem == "missing in Kavita: T.pdf"
    store.mark_built(ok, BookMetadata(title="T2"), "IT/Android/1")
    assert not store.book(ok).kavita_passed


def test_live_marks_are_stored_and_removed(tmp_path):
    store = StateStore(tmp_path / "state.db")

    store.mark_live("IT/Android")
    store.mark_live("Romane/Tolkien")
    store.unmark_live("Romane/Tolkien")

    assert list(store.live_folders()) == ["IT/Android"]


def test_existing_jobs_table_learns_the_flagged_status(tmp_path):
    path = tmp_path / "state.db"
    with sqlite3.connect(path) as db:
        db.execute(
            "CREATE TABLE jobs (id INTEGER PRIMARY KEY, name TEXT NOT NULL, status TEXT NOT NULL CHECK (status IN "
            "('running', 'succeeded', 'failed', 'stopped', 'interrupted')), log_path TEXT NOT NULL, exit_code INTEGER, "
            "started_at TEXT NOT NULL, finished_at TEXT)"
        )
        db.execute("INSERT INTO jobs VALUES (7, 'build', 'failed', 'job-7.log', 1, '2026-10-13', '2026-10-13')")

    store = StateStore(path)
    job_id = store.add_job("build", "job.log")
    store.finish_job(job_id, "flagged", 3)

    assert [(j.id, j.status) for j in store.jobs()] == [(job_id, "flagged"), (7, "failed")]
