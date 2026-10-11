"""About this file: end-to-end tests for ebookprep.pipeline (the build stage) and the
command line: a small synthetic input library is identified (with fake online sources),
decisions are made, and accepted books are built into an output library with calibre."""

import hashlib
from pathlib import Path

import pytest
from conftest import damage_trailer, make_epub, make_pdf

import ebookprep.pipeline as pipeline
from ebookprep.__main__ import main
from ebookprep.build import RecordOutcome, RecordStatus
from ebookprep.config import Config
from ebookprep.identify import identify
from ebookprep.kavita_check import SeriesSource, kavita_view
from ebookprep.metadata import BookMetadata
from ebookprep.pipeline import build_accepted
from ebookprep.state import StateStore

pytestmark = pytest.mark.calibre

CONFIG = Config(topic_categories=frozenset({"IT/Security"}))
CATEGORY_SERIES = '<meta name="calibre:series" content="Pentesting"/><meta name="calibre:series_index" content="14"/>'
QUIET = {"log": lambda _: None}


def build_input(root: Path) -> Path:
    android = root / "IT" / "Android"
    android.mkdir(parents=True)
    damage_trailer(make_pdf(android / "A Book_ The Subtitle - Author One.pdf"))
    make_epub(android / "A Book_ The Subtitle - Author One.epub", title="Untitled")

    pentesting = root / "IT" / "Security" / "Pentesting"
    pentesting.mkdir(parents=True)
    make_epub(pentesting / "Black Hat Bash - Dolev Farhi.epub", title="Black Hat Bash", extra_metadata=CATEGORY_SERIES)
    make_pdf(root / "IT" / "Security" / "Knoppix Hacks - Kyle Rankin.pdf")

    wicca = root / "Spiritualität" / "Witchcraft" / "Wicca - Vivianne Crowley"
    wicca.mkdir(parents=True)
    make_pdf(wicca / "Wicca - Crowley, Vivianne.pdf")

    python = root / "IT" / "Python"
    python.mkdir(parents=True)
    (python / "code.zip").write_bytes(b"zip")
    return root


def checksums(root: Path) -> dict[str, str]:
    return {str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest() for p in root.rglob("*") if p.is_file()}


def tree(root: Path) -> list[str]:
    return sorted(str(p.relative_to(root)) for p in root.rglob("*") if p.is_file())


def identified(tmp_path: Path) -> tuple[Path, Path, Path, StateStore]:
    source, output, work = build_input(tmp_path / "input"), tmp_path / "output", tmp_path / "work"
    identify(source, work, CONFIG, sources=[], **QUIET)
    return source, output, work, StateStore(work / "state.db")


def accept_all(store: StateStore) -> None:
    for book in store.books(status="review"):
        store.decide(book.id, book.metadata, decided_by="user")


def test_only_accepted_books_are_built(tmp_path):
    source, output, work, store = identified(tmp_path)
    knoppix = next(b for b in store.books() if "Knoppix" in b.sources[0])
    store.decide(knoppix.id, knoppix.metadata, decided_by="user")

    build_accepted(source, output, work, CONFIG, **QUIET)

    assert tree(output) == [
        "IT/Security/Knoppix Hacks - Kyle Rankin/Knoppix Hacks - Kyle Rankin.jpg",
        "IT/Security/Knoppix Hacks - Kyle Rankin/Knoppix Hacks - Kyle Rankin.opf",
        "IT/Security/Knoppix Hacks - Kyle Rankin/Knoppix Hacks - Kyle Rankin.pdf",
    ]
    assert store.book(knoppix.id).built_folder == "IT/Security/Knoppix Hacks - Kyle Rankin"


def test_accepted_library_is_built_and_verified(tmp_path):
    source, output, work, store = identified(tmp_path)
    accept_all(store)
    before = checksums(source)

    run_id = build_accepted(source, output, work, CONFIG, **QUIET)

    assert tree(output) == [
        "IT/Android/A Book - Author One/A Book - Author One.epub",
        "IT/Android/A Book - Author One/A Book - Author One.jpg",
        "IT/Android/A Book - Author One/A Book - Author One.opf",
        "IT/Android/A Book - Author One/A Book - Author One.pdf",
        "IT/Security/Knoppix Hacks - Kyle Rankin/Knoppix Hacks - Kyle Rankin.jpg",
        "IT/Security/Knoppix Hacks - Kyle Rankin/Knoppix Hacks - Kyle Rankin.opf",
        "IT/Security/Knoppix Hacks - Kyle Rankin/Knoppix Hacks - Kyle Rankin.pdf",
        "IT/Security/Pentesting/Black Hat Bash - Dolev Farhi/Black Hat Bash - Dolev Farhi.epub",
        "IT/Security/Pentesting/Black Hat Bash - Dolev Farhi/Black Hat Bash - Dolev Farhi.jpg",
        "IT/Security/Pentesting/Black Hat Bash - Dolev Farhi/Black Hat Bash - Dolev Farhi.opf",
        "Spiritualität/Witchcraft/Wicca - Vivianne Crowley/Wicca - Vivianne Crowley.jpg",
        "Spiritualität/Witchcraft/Wicca - Vivianne Crowley/Wicca - Vivianne Crowley.opf",
        "Spiritualität/Witchcraft/Wicca - Vivianne Crowley/Wicca - Vivianne Crowley.pdf",
    ]
    for book in [p for p in output.rglob("*") if p.suffix in (".pdf", ".epub")]:
        view = kavita_view(book)
        assert view.source is SeriesSource.SERIES and view.series == view.title, book
    assert checksums(source) == before
    assert all(not b.needs_build for b in store.books())
    records = store.records(run_id)
    assert len(records) == 4 and all(r.status == "done" for r in records)
    assert store.skipped(run_id) == [("IT/Python/code.zip", "archive")]
    assert (work / f"report-run-{run_id}.md").exists()


def test_second_build_does_nothing(tmp_path):
    source, output, work, store = identified(tmp_path)
    accept_all(store)
    build_accepted(source, output, work, CONFIG, **QUIET)
    first = checksums(output)

    second = build_accepted(source, output, work, CONFIG, **QUIET)

    assert store.records(second) == []
    assert checksums(output) == first


def test_changed_decision_replaces_the_old_book_folder(tmp_path):
    source, output, work, store = identified(tmp_path)
    accept_all(store)
    build_accepted(source, output, work, CONFIG, **QUIET)
    knoppix = next(b for b in store.books() if "Knoppix" in b.sources[0])

    store.decide(knoppix.id, BookMetadata(title="Knoppix Hacks: Tips and Tools", authors=("Kyle Rankin",)), "user")
    build_accepted(source, output, work, CONFIG, **QUIET)

    security = sorted(p.name for p in (output / "IT" / "Security").iterdir())
    assert security == ["Knoppix Hacks - Kyle Rankin", "Pentesting"]
    pdf = output / "IT/Security/Knoppix Hacks - Kyle Rankin/Knoppix Hacks - Kyle Rankin.pdf"
    assert kavita_view(pdf).series == "Knoppix Hacks: Tips and Tools"


def test_renamed_book_moves_to_its_new_folder(tmp_path):
    source, output, work, store = identified(tmp_path)
    accept_all(store)
    build_accepted(source, output, work, CONFIG, **QUIET)
    knoppix = next(b for b in store.books() if "Knoppix" in b.sources[0])

    store.decide(knoppix.id, BookMetadata(title="Knoppix Pocket Guide", authors=("Kyle Rankin",)), "user")
    build_accepted(source, output, work, CONFIG, **QUIET)

    assert sorted(p.name for p in (output / "IT" / "Security").iterdir()) == [
        "Knoppix Pocket Guide - Kyle Rankin",
        "Pentesting",
    ]
    assert store.book(knoppix.id).built_folder == "IT/Security/Knoppix Pocket Guide - Kyle Rankin"


def test_failed_rebuild_keeps_the_previous_build(tmp_path, monkeypatch):
    source, output, work, store = identified(tmp_path)
    accept_all(store)
    build_accepted(source, output, work, CONFIG, **QUIET)
    knoppix = next(b for b in store.books() if "Knoppix" in b.sources[0])
    before = checksums(output)
    store.decide(knoppix.id, BookMetadata(title="Knoppix Renamed", authors=("Kyle Rankin",)), "user")
    monkeypatch.setattr(
        pipeline, "build_record", lambda plan, *a, **k: RecordOutcome(plan, RecordStatus.FAILED, error="boom")
    )

    run_id = build_accepted(source, output, work, CONFIG, **QUIET)

    assert checksums(output) == before
    assert store.book(knoppix.id).needs_build
    assert [r.error for r in store.records(run_id)] == ["boom"]


def test_unexpected_error_in_one_book_is_flagged_and_the_run_continues(tmp_path, monkeypatch):
    source, output, work, store = identified(tmp_path)
    accept_all(store)
    real_plan = pipeline.plan_records

    def plan_or_fail(files, name):
        if "Knoppix" in name:
            raise RuntimeError("unexpected problem")
        return real_plan(files, name)

    monkeypatch.setattr(pipeline, "plan_records", plan_or_fail)

    run_id = build_accepted(source, output, work, CONFIG, **QUIET)

    records = store.records(run_id)
    failed = [r for r in records if r.flagged]
    assert len(records) == 4 and len(failed) == 1
    assert "unexpected problem" in failed[0].error


def test_only_filter(tmp_path):
    source, output, work, store = identified(tmp_path)
    accept_all(store)

    build_accepted(source, output, work, CONFIG, only=("IT/Security",), **QUIET)

    assert {p.relative_to(output).parts[:2] for p in output.rglob("*") if p.is_file()} == {("IT", "Security")}


def test_command_line(tmp_path, monkeypatch):
    source = build_input(tmp_path / "input")
    config = tmp_path / "config.toml"
    config.write_text('topic_categories = ["IT/Security"]\n', encoding="utf-8")
    monkeypatch.setattr("ebookprep.__main__.default_sources", lambda config: [])
    paths = ["--input", str(source), "--work", str(tmp_path / "work"), "--config", str(config), "--only", "IT/Android"]

    assert main(["identify", *paths]) == 0
    accept_all(StateStore(tmp_path / "work" / "state.db"))
    assert main(["build", *paths, "--output", str(tmp_path / "output")]) == 0
    assert main(["report", "--work", str(tmp_path / "work")]) == 0

    assert (tmp_path / "output" / "IT" / "Android" / "A Book - Author One").is_dir()
    assert not (tmp_path / "output" / "Spiritualität").exists()
    assert "| Done | 1 |" in (tmp_path / "work" / "report.md").read_text()


def test_mislabeled_book_is_built_outside_the_library_structure(tmp_path):
    source, output, work, store = identified(tmp_path)
    knoppix = next(b for b in store.books() if "Knoppix" in b.sources[0])
    store.decide(knoppix.id, BookMetadata(title="Moral Design and Technology", authors=("Bart Wernaart",)), "user")
    store.set_mislabeled(knoppix.id, True)

    build_accepted(source, output, work, CONFIG, **QUIET)

    folder = output / "_mislabeled" / "IT" / "Security" / "Moral Design and Technology - Bart Wernaart"
    assert (folder / "Moral Design and Technology - Bart Wernaart.pdf").is_file()
    assert store.book(knoppix.id).built_folder == "_mislabeled/IT/Security/Moral Design and Technology - Bart Wernaart"


def test_same_title_in_one_library_gets_author_in_series_and_rebuilds(tmp_path):
    source, output, work, store = identified(tmp_path)
    accept_all(store)
    build_accepted(source, output, work, CONFIG, **QUIET)
    knoppix = next(b for b in store.books() if "Knoppix" in b.sources[0])
    android = next(b for b in store.books() if "A Book" in b.sources[0])

    store.decide(knoppix.id, BookMetadata(title="A Book: The Subtitle", authors=("Kyle Rankin",)), "user")
    run_id = build_accepted(source, output, work, CONFIG, **QUIET)

    android_pdf = output / "IT/Android/A Book - Author One/A Book - Author One.pdf"
    knoppix_pdf = output / "IT/Security/A Book - Kyle Rankin/A Book - Kyle Rankin.pdf"
    assert kavita_view(android_pdf).series == "A Book: The Subtitle (Author One)"
    assert kavita_view(knoppix_pdf).series == "A Book: The Subtitle (Kyle Rankin)"
    assert store.book(android.id).built_series == "A Book: The Subtitle (Author One)"
    assert {r.book_name for r in store.records(run_id)} == {"A Book - Author One", "A Book - Kyle Rankin"}


# --- excluded duplicate files


def test_excluded_file_is_left_out_and_restore_brings_it_back(tmp_path):
    source, output, work, store = identified(tmp_path)
    accept_all(store)
    build_accepted(source, output, work, CONFIG, **QUIET)
    folder = output / "IT/Android/A Book - Author One"
    epub = "IT/Android/A Book_ The Subtitle - Author One.epub"
    pdf = "IT/Android/A Book_ The Subtitle - Author One.pdf"

    store.exclude_sources([epub], kept=pdf, group="g")
    build_accepted(source, output, work, CONFIG, **QUIET)

    assert sorted(p.suffix for p in folder.iterdir()) == [".jpg", ".opf", ".pdf"]
    android = next(b for b in store.books() if "A Book" in b.sources[0])
    assert store.book(android.id).built_sources == (pdf,)

    store.restore_source(epub)
    build_accepted(source, output, work, CONFIG, **QUIET)

    assert sorted(p.suffix for p in folder.iterdir()) == [".epub", ".jpg", ".opf", ".pdf"]


def test_book_with_all_files_excluded_is_removed_from_output(tmp_path):
    source, output, work, store = identified(tmp_path)
    accept_all(store)
    build_accepted(source, output, work, CONFIG, **QUIET)
    knoppix = next(b for b in store.books() if "Knoppix" in b.sources[0])

    store.exclude_sources(list(knoppix.sources), kept="IT/Security/elsewhere.pdf", group="g")
    build_accepted(source, output, work, CONFIG, **QUIET)

    assert not (output / "IT/Security/Knoppix Hacks - Kyle Rankin").exists()
    assert store.book(knoppix.id).built_folder is None
    assert (output / "IT/Security/Pentesting").is_dir()


def test_summary_report_lists_excluded_files(tmp_path):
    from ebookprep.report import write_summary_report

    source, output, work, store = identified(tmp_path)
    store.exclude_sources(["IT/Android/x.epub"], kept="IT/Android/x.pdf", group="g")

    report = write_summary_report(store, work / "report.md").read_text()

    assert "## Excluded duplicate files" in report
    assert "`IT/Android/x.epub`" in report and "`IT/Android/x.pdf`" in report
