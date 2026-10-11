"""About this file: integration tests for ebookprep.calibre, the wrapper around calibre's
command-line tools (calibredb, ebook-meta, ebook-convert). They need calibre, which is
installed in the Docker image."""

import pytest
from conftest import make_epub, make_pdf

from ebookprep.calibre import CalibreError, convert, temporary_library, write_metadata_from_opf
from ebookprep.kavita_check import SeriesSource, kavita_view
from ebookprep.metadata import BookMetadata, read_opf

pytestmark = pytest.mark.calibre

FULL = BookMetadata(
    title="Black Hat Go: Go Programming for Hackers and Pentesters",
    authors=("Tom Steele", "Chris Patten"),
    isbn="9781593278656",
    publisher="No Starch Press",
    language="eng",
    pubdate="2020-01-24",
    comments="<p>Learn Go.</p>",
    tags=("Security", "Go"),
)


def test_temporary_library_is_removed_afterwards(tmp_path):
    with temporary_library(tmp_path) as library:
        path = library.path
        assert path.is_dir()
    assert not path.exists()


def test_add_returns_increasing_ids(tmp_path):
    a = make_pdf(tmp_path / "a.pdf")
    b = make_epub(tmp_path / "b.epub", title="B")

    with temporary_library(tmp_path) as library:
        assert library.add(a) == 1
        assert library.add(b) == 2


def test_set_metadata_and_export(tmp_path):
    src = make_pdf(tmp_path / "a.pdf", title="Untitled-1.pdf")
    out = tmp_path / "out"

    with temporary_library(tmp_path) as library:
        book_id = library.add(src)
        library.set_metadata(book_id, FULL)
        exported = library.export(book_id, out, "Black Hat Go - Tom Steele")

    assert exported.books == (out / "Black Hat Go - Tom Steele.pdf",)
    assert exported.opf == out / "Black Hat Go - Tom Steele.opf"
    assert exported.cover == out / "Black Hat Go - Tom Steele.jpg"
    assert exported.cover.exists()

    opf = read_opf(exported.opf).metadata
    assert opf.title == FULL.title
    assert opf.authors == FULL.authors
    assert opf.isbn == FULL.isbn
    assert opf.publisher == FULL.publisher
    assert opf.language == "eng"
    assert opf.year == 2020
    assert opf.comments == FULL.comments
    assert set(opf.tags) == set(FULL.tags)
    assert "calibre:title_sort" in exported.opf.read_text() and "Untitled-1.pdf" not in exported.opf.read_text()

    view = kavita_view(exported.books[0])
    assert (view.series, view.source) == (FULL.title, SeriesSource.SERIES)


def test_export_keeps_non_ascii_names(tmp_path):
    with temporary_library(tmp_path) as library:
        book_id = library.add(make_pdf(tmp_path / "a.pdf"))
        exported = library.export(book_id, tmp_path / "out", "Die Königsdrohne - Ōdate Toshio")

    assert exported.books == (tmp_path / "out" / "Die Königsdrohne - Ōdate Toshio.pdf",)


def test_epub_category_series_is_overwritten(tmp_path):
    extra = '<meta name="calibre:series" content="Pentesting"/><meta name="calibre:series_index" content="14"/>'
    src = make_epub(tmp_path / "b.epub", title="Black Hat Bash", extra_metadata=extra)

    with temporary_library(tmp_path) as library:
        book_id = library.add(src)
        library.set_metadata(book_id, BookMetadata(title="Black Hat Bash", authors=("Dolev Farhi",)))
        exported = library.export(book_id, tmp_path / "out", "Black Hat Bash - Dolev Farhi")

    assert kavita_view(exported.books[0]).series == "Black Hat Bash"


def test_cover_file_is_used(tmp_path):
    cover_source = make_epub(tmp_path / "c.epub", title="Cover Source")
    out = tmp_path / "out"
    with temporary_library(tmp_path) as library:
        cover_id = library.add(cover_source)
        cover = library.export(cover_id, tmp_path / "cover", "cover").cover

        book_id = library.add(make_pdf(tmp_path / "a.pdf"))
        library.set_metadata(book_id, BookMetadata(title="With Cover"), cover=cover)
        exported = library.export(book_id, out, "With Cover")

    assert exported.cover.read_bytes() == cover.read_bytes()


def test_add_format_exports_both_formats(tmp_path):
    epub = make_epub(tmp_path / "b.epub", title="Spiral Dance")
    mobi = tmp_path / "b.mobi"
    convert(epub, mobi)

    with temporary_library(tmp_path) as library:
        book_id = library.add(epub)
        library.add_format(book_id, mobi)
        library.set_metadata(book_id, BookMetadata(title="The Spiral Dance", authors=("Starhawk",)))
        exported = library.export(book_id, tmp_path / "out", "The Spiral Dance - Starhawk")

    assert sorted(p.name for p in exported.books) == ["The Spiral Dance - Starhawk.epub", "The Spiral Dance - Starhawk.mobi"]


def test_convert_mobi_to_epub(tmp_path):
    mobi = tmp_path / "b.mobi"
    convert(make_epub(tmp_path / "b.epub", title="Converted"), mobi)

    epub = tmp_path / "c.epub"
    convert(mobi, epub)

    assert kavita_view(epub).series == "Converted"


def test_write_metadata_from_opf(tmp_path):
    with temporary_library(tmp_path) as library:
        book_id = library.add(make_pdf(tmp_path / "a.pdf"))
        library.set_metadata(book_id, BookMetadata(title="From OPF", authors=("A Author",)))
        exported = library.export(book_id, tmp_path / "out", "From OPF")
    plain = make_pdf(tmp_path / "plain.pdf")

    write_metadata_from_opf(plain, exported.opf)

    assert kavita_view(plain).series == "From OPF"


def test_failing_command_raises(tmp_path):
    with temporary_library(tmp_path) as library, pytest.raises(CalibreError, match="no such file|exist"):
        library.add(tmp_path / "missing.pdf")
