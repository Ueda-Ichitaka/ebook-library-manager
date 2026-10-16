"""About this file: tests for ebookprep.build, which turns the source files of one book
into exported calibre records in the book's output folder, verified the way Kavita reads
them, with one automatic retry and skip-and-flag on a second failure."""

import subprocess
from pathlib import Path

import pikepdf
import pytest
from conftest import (
    damage_trailer, make_epub, make_pdf, make_pdf_with_unmergeable_xmp, overwrite_start, pdf_with_long_catalog,
)

from ebookprep.build import RecordPlan, RecordStatus, build_record, plan_records
from ebookprep.calibre import convert
from ebookprep.kavita_check import SeriesSource, kavita_view
from ebookprep.metadata import BookMetadata
from ebookprep.scan import BookFile

META = BookMetadata(title="Black Hat Go: Go Programming for Hackers", authors=("Tom Steele",))
NAME = "Black Hat Go - Tom Steele"


def files(*names: str) -> tuple[BookFile, ...]:
    return tuple(BookFile(Path("/in") / n) for n in names)


# --- plan_records (no calibre needed)


def test_different_formats_share_one_record():
    plans = plan_records(files("a.pdf", "a.epub"), NAME)

    assert [p.name for p in plans] == [NAME]
    assert [f.path.name for f in plans[0].sources] == ["a.pdf", "a.epub"]


def test_same_format_twice_gives_numbered_records():
    plans = plan_records(files("a.epub", "b.epub", "c.epub"), NAME)

    assert [p.name for p in plans] == [NAME, f"{NAME} (2)", f"{NAME} (3)"]


def test_mobi_needs_the_epub_slot_for_its_conversion():
    assert len(plan_records(files("a.epub", "b.mobi"), NAME)) == 2
    assert len(plan_records(files("a.pdf", "b.mobi"), NAME)) == 1
    assert len(plan_records(files("a.mobi", "b.azw"), NAME)) == 2


# --- build_record (calibre integration)

calibre = pytest.mark.calibre


@calibre
def test_epub_with_category_series_is_fixed(tmp_path):
    extra = '<meta name="calibre:series" content="Pentesting"/><meta name="calibre:series_index" content="14"/>'
    src = make_epub(tmp_path / "in.epub", title="Old", extra_metadata=extra)
    dest = tmp_path / "out" / NAME

    outcome = build_record(RecordPlan(NAME, (BookFile(src),)), META, dest, tmp_path / "work")

    assert outcome.status is RecordStatus.DONE
    assert sorted(p.name for p in dest.iterdir()) == [f"{NAME}.epub", f"{NAME}.jpg", f"{NAME}.opf"]
    assert outcome.outputs == (dest / f"{NAME}.epub", dest / f"{NAME}.opf", dest / f"{NAME}.jpg")
    view = kavita_view(dest / f"{NAME}.epub")
    assert (view.series, view.source) == (META.title, SeriesSource.SERIES)
    assert not outcome.retried
    assert list((tmp_path / "work").iterdir()) == []


@calibre
def test_damaged_pdf_is_repaired_and_reported(tmp_path):
    src = damage_trailer(make_pdf(tmp_path / "in.pdf"))
    dest = tmp_path / "out" / NAME

    outcome = build_record(RecordPlan(NAME, (BookFile(src),)), META, dest, tmp_path / "work")

    assert outcome.status is RecordStatus.DONE
    ((repaired_source, report),) = outcome.repairs
    assert repaired_source == src and report.was_damaged
    assert kavita_view(dest / f"{NAME}.pdf").series == META.title


@calibre
def test_damaged_download_is_repaired_in_the_work_copy(tmp_path):
    src = overwrite_start(pdf_with_long_catalog(tmp_path / "in.pdf"))
    original = src.read_bytes()
    dest = tmp_path / "out" / NAME

    outcome = build_record(RecordPlan(NAME, (BookFile(src),)), META, dest, tmp_path / "work")

    assert outcome.status is RecordStatus.DONE
    ((repaired_source, note),) = outcome.download_repairs
    assert repaired_source == src and "header" in note
    assert kavita_view(dest / f"{NAME}.pdf").series == META.title
    assert src.read_bytes() == original


def test_damaged_download_with_lost_content_fails(tmp_path):
    import zipfile

    src = tmp_path / "in.epub"
    with zipfile.ZipFile(src, "w") as z:
        z.writestr("mimetype", b"application/epub+zip", zipfile.ZIP_STORED)
        z.writestr("OEBPS/Ch01.xhtml", b"<p>" + b"text " * 500 + b"</p>")
    overwrite_start(src)

    outcome = build_record(RecordPlan(NAME, (BookFile(src),)), META, tmp_path / "out" / NAME, tmp_path / "work")

    assert outcome.status is RecordStatus.FAILED
    assert "damaged download" in outcome.error and "OEBPS/Ch01.xhtml" in outcome.error


@calibre
def test_pdf_and_epub_in_one_record(tmp_path):
    pdf = make_pdf(tmp_path / "a.pdf")
    epub = make_epub(tmp_path / "a.epub", title="x")
    dest = tmp_path / "out" / NAME

    build_record(RecordPlan(NAME, (BookFile(pdf), BookFile(epub))), META, dest, tmp_path / "work")

    assert {p.suffix for p in dest.iterdir()} == {".pdf", ".epub", ".opf", ".jpg"}
    assert kavita_view(dest / f"{NAME}.pdf").series == META.title
    assert kavita_view(dest / f"{NAME}.epub").series == META.title


@calibre
def test_mobi_is_converted_and_kept(tmp_path):
    mobi = tmp_path / "in.mobi"
    convert(make_epub(tmp_path / "x.epub", title="x"), mobi)
    dest = tmp_path / "out" / NAME

    outcome = build_record(RecordPlan(NAME, (BookFile(mobi),)), META, dest, tmp_path / "work")

    assert outcome.converted == (mobi,)
    assert {p.suffix for p in dest.iterdir()} == {".epub", ".mobi", ".opf", ".jpg"}
    assert kavita_view(dest / f"{NAME}.epub").series == META.title


@calibre
def test_sidecar_cover_is_used(tmp_path):
    cover = tmp_path / "cover.jpg"
    subprocess.run(
        ["ebook-meta", str(make_epub(tmp_path / "c.epub", title="Cover")), "--get-cover", str(cover)],
        check=True,
        capture_output=True,
    )
    src = make_pdf(tmp_path / "in.pdf")
    dest = tmp_path / "out" / NAME

    build_record(RecordPlan(NAME, (BookFile(src, cover=cover),)), META, dest, tmp_path / "work")

    assert (dest / f"{NAME}.jpg").read_bytes() == cover.read_bytes()


@calibre
def test_silent_calibre_failure_is_retried_without_xmp(tmp_path):
    src = make_pdf_with_unmergeable_xmp(tmp_path / "in.pdf")
    dest = tmp_path / "out" / NAME

    outcome = build_record(RecordPlan(NAME, (BookFile(src),)), META, dest, tmp_path / "work")

    assert outcome.status is RecordStatus.DONE
    assert outcome.retried
    ((file_name, packet),) = outcome.removed_xmp
    assert file_name == f"{NAME}.pdf" and "PublisherSpec" in packet
    assert kavita_view(dest / f"{NAME}.pdf").series == META.title


@calibre
def test_second_failure_skips_and_flags(tmp_path):
    src = make_pdf_with_unmergeable_xmp(tmp_path / "in.pdf")
    dest = tmp_path / "out" / NAME

    outcome = build_record(
        RecordPlan(NAME, (BookFile(src),)), META, dest, tmp_path / "work", rewrite=lambda book, opf: None
    )

    assert outcome.status is RecordStatus.FAILED
    assert outcome.flagged
    assert outcome.retried
    assert "series" in outcome.error
    assert not dest.exists()
    assert list((tmp_path / "work").iterdir()) == []


@calibre
def test_unreadable_pdf_fails_and_flags(tmp_path):
    src = tmp_path / "in.pdf"
    pdf = pikepdf.new()
    pdf.add_blank_page()
    pdf.save(src, encryption=pikepdf.Encryption(owner="o", user="u"))
    dest = tmp_path / "out" / NAME

    outcome = build_record(RecordPlan(NAME, (BookFile(src),)), META, dest, tmp_path / "work")

    assert outcome.status is RecordStatus.FAILED
    assert "password" in outcome.error
    assert not dest.exists()


@calibre
def test_existing_output_is_never_overwritten(tmp_path):
    src = make_pdf(tmp_path / "in.pdf")
    dest = tmp_path / "out" / NAME
    dest.mkdir(parents=True)
    (dest / f"{NAME}.pdf").write_bytes(b"keep me")

    outcome = build_record(RecordPlan(NAME, (BookFile(src),)), META, dest, tmp_path / "work")

    assert outcome.status is RecordStatus.FAILED
    assert "exists" in outcome.error
    assert (dest / f"{NAME}.pdf").read_bytes() == b"keep me"
    assert sorted(p.name for p in dest.iterdir()) == [f"{NAME}.pdf"]


@calibre
def test_series_name_is_written_and_verified(tmp_path):
    src = make_pdf(tmp_path / "in.pdf")
    dest = tmp_path / "out" / NAME

    outcome = build_record(RecordPlan(NAME, (BookFile(src),)), META, dest, tmp_path / "work", series="Black Hat Go (Tom Steele)")

    assert outcome.status is RecordStatus.DONE
    assert kavita_view(dest / f"{NAME}.pdf").series == "Black Hat Go (Tom Steele)"
