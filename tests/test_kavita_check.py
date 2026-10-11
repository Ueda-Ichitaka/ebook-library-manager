"""About this file: tests for ebookprep.kavita_check, which predicts the series name
Kavita 0.9.1.4 derives from a PDF or EPUB file."""

import subprocess
import zipfile

import pikepdf
import pytest
from conftest import damage_trailer, make_epub, make_pdf

from ebookprep.kavita_check import SeriesSource, kavita_view

XMP_TEMPLATE = """<?xpacket begin="﻿" id="W5M0MpCehiHzreSzNTczkc9d"?>
<x:xmpmeta xmlns:x="adobe:ns:meta/">
 <rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#">
  <rdf:Description rdf:about=""
    xmlns:dc="http://purl.org/dc/elements/1.1/"
    xmlns:calibre="http://calibre-ebook.com/xmp-namespace"
    xmlns:calibreSI="http://calibre-ebook.com/xmp-namespace-series-index">
   {body}
  </rdf:Description>
 </rdf:RDF>
</x:xmpmeta>
<?xpacket end="w"?>"""

XMP_TITLE = '<dc:title><rdf:Alt><rdf:li xml:lang="x-default">{}</rdf:li></rdf:Alt></dc:title>'
XMP_SERIES = (
    '<calibre:series rdf:parseType="Resource"><rdf:value>{}</rdf:value>'
    "<calibreSI:series_index>1.00</calibreSI:series_index></calibre:series>"
)


def make_pdf_with_xmp(path, body, info_title=None):
    make_pdf(path, title=info_title)
    with pikepdf.open(path, allow_overwriting_input=True) as pdf:
        stream = pikepdf.Stream(pdf, XMP_TEMPLATE.format(body=body).encode("utf-8"))
        stream.Type = pikepdf.Name.Metadata
        stream.Subtype = pikepdf.Name.XML
        pdf.Root.Metadata = pdf.make_indirect(stream)
        pdf.save(path)
    return path


# --- PDF


def test_pdf_info_title_becomes_series(tmp_path):
    view = kavita_view(make_pdf(tmp_path / "b.pdf", title="Android Sensor Programming"))

    assert (view.series, view.source) == ("Android Sensor Programming", SeriesSource.TITLE)


def test_pdf_without_title_falls_back_to_folder_name(tmp_path):
    folder = tmp_path / "Android"
    folder.mkdir()

    view = kavita_view(make_pdf(folder / "b.pdf"))

    assert (view.series, view.source) == ("Android", SeriesSource.FOLDER)


def test_pdf_whitespace_title_falls_back_to_file_name(tmp_path):
    folder = tmp_path / "Pentesting"
    folder.mkdir()

    view = kavita_view(make_pdf(folder / "Some Book.pdf", title="   "))

    assert (view.series, view.source) == ("Some Book", SeriesSource.FILENAME)


def test_pdf_xmp_title_overrides_info_title(tmp_path):
    path = make_pdf_with_xmp(tmp_path / "b.pdf", XMP_TITLE.format("Untitled-1.pdf"), info_title="Real Title")

    view = kavita_view(path)

    assert (view.series, view.source) == ("Untitled-1", SeriesSource.TITLE)


def test_pdf_calibre_series_wins(tmp_path):
    body = XMP_TITLE.format("Black Hat Go: Go Programming") + XMP_SERIES.format("Black Hat Go")

    view = kavita_view(make_pdf_with_xmp(tmp_path / "b.pdf", body))

    assert (view.series, view.source) == ("Black Hat Go", SeriesSource.SERIES)
    assert view.title == "Black Hat Go: Go Programming"


def test_unreadable_pdf_falls_back_to_folder_name(tmp_path):
    folder = tmp_path / "Android"
    folder.mkdir()
    path = folder / "broken.pdf"
    path.write_bytes(b"not a pdf at all")

    view = kavita_view(path)

    assert (view.series, view.source) == ("Android", SeriesSource.UNREADABLE)


def test_damaged_pdf_counts_as_unreadable_even_with_title(tmp_path):
    """Kavita's own PDF reader doesn't reconstruct damaged files; it falls back to the folder."""
    folder = tmp_path / "Android"
    folder.mkdir()
    path = damage_trailer(make_pdf(folder / "b.pdf", title="Beginning Android Games"))

    view = kavita_view(path)

    assert (view.series, view.source) == ("Android", SeriesSource.UNREADABLE)


@pytest.mark.calibre
def test_pdf_series_written_by_calibre_is_seen(tmp_path):
    path = make_pdf(tmp_path / "b.pdf", title="Old")
    subprocess.run(
        ["ebook-meta", str(path), "--title", "New Title", "--series", "New Title", "--index", "1"],
        check=True,
        capture_output=True,
    )

    view = kavita_view(path)

    assert (view.series, view.source, view.title) == ("New Title", SeriesSource.SERIES, "New Title")


# --- EPUB


def test_epub_title_becomes_series(tmp_path):
    view = kavita_view(make_epub(tmp_path / "b.epub", title="The Prose Edda"))

    assert (view.series, view.source) == ("The Prose Edda", SeriesSource.TITLE)


def test_epub_calibre_series_with_index_wins(tmp_path):
    extra = '<meta name="calibre:series" content="Pentesting"/><meta name="calibre:series_index" content="14"/>'

    view = kavita_view(make_epub(tmp_path / "b.epub", title="Black Hat Bash", extra_metadata=extra))

    assert (view.series, view.source, view.title) == ("Pentesting", SeriesSource.SERIES, "Black Hat Bash")


def test_epub_calibre_series_without_index_is_ignored(tmp_path):
    extra = '<meta name="calibre:series" content="Pentesting"/>'

    view = kavita_view(make_epub(tmp_path / "b.epub", title="Black Hat Bash", extra_metadata=extra))

    assert (view.series, view.source) == ("Black Hat Bash", SeriesSource.TITLE)


def test_epub3_collection_series(tmp_path):
    extra = (
        '<meta property="belongs-to-collection" id="c01">The Lord of the Rings</meta>'
        '<meta refines="#c01" property="group-position">2</meta>'
    )

    view = kavita_view(make_epub(tmp_path / "b.epub", title="The Two Towers", extra_metadata=extra))

    assert (view.series, view.source) == ("The Lord of the Rings", SeriesSource.SERIES)


def test_unreadable_epub_is_not_imported(tmp_path):
    path = tmp_path / "b.epub"
    with zipfile.ZipFile(path, "w") as epub:
        epub.writestr("mimetype", "application/epub+zip")

    view = kavita_view(path)

    assert (view.series, view.source) == ("", SeriesSource.UNREADABLE)


@pytest.mark.calibre
def test_epub_series_written_by_calibre_is_seen(tmp_path):
    path = make_epub(tmp_path / "b.epub", title="Old")
    subprocess.run(
        ["ebook-meta", str(path), "--title", "New Title", "--series", "New Title", "--index", "1"],
        check=True,
        capture_output=True,
    )

    assert kavita_view(path).series == "New Title"


def test_unsupported_format_raises(tmp_path):
    path = tmp_path / "b.mobi"
    path.write_bytes(b"x")

    with pytest.raises(ValueError, match="mobi"):
        kavita_view(path)
