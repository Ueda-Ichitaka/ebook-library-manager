"""About this file: tests for ebookprep.metadata: the BookMetadata type, reading calibre
.opf files, recognizing junk titles and authors, and resolving metadata from local
sources (sidecar .opf, else filename)."""

from pathlib import Path

import pytest

from ebookprep.metadata import BookMetadata, is_junk_author, is_junk_title, read_opf, resolve_local
from ebookprep.scan import BookFile

CALIBRE_OPF = """<?xml version='1.0' encoding='utf-8'?>
<package xmlns="http://www.idpf.org/2007/opf" unique-identifier="uuid_id" version="2.0">
    <metadata xmlns:dc="http://purl.org/dc/elements/1.1/" xmlns:opf="http://www.idpf.org/2007/opf">
        <dc:identifier opf:scheme="calibre" id="calibre_id">301</dc:identifier>
        <dc:title>Android Sensor Programming by Example</dc:title>
        <dc:creator opf:file-as="Nagpal, Varun" opf:role="aut">Varun Nagpal</dc:creator>
        <dc:contributor opf:file-as="calibre" opf:role="bkp">calibre (8.7.0) [https://calibre-ebook.com]</dc:contributor>
        <dc:date>2016-04-29T15:17:55.468028+00:00</dc:date>
        <dc:description>&lt;p&gt;Take your Android applications further.&lt;/p&gt;</dc:description>
        <dc:publisher>Packt Publishing Ltd</dc:publisher>
        <dc:identifier opf:scheme="ISBN">9781785284663</dc:identifier>
        <dc:identifier opf:scheme="GOOGLE">Ji7gDAAAQBAJ</dc:identifier>
        <dc:language>eng</dc:language>
        <dc:subject>Java</dc:subject>
        <dc:subject>Programming</dc:subject>
        <meta name="calibre:series" content="Android"/>
    </metadata>
</package>"""

SCRIPT_OPF = """<?xml version='1.0' encoding='utf-8'?>
<package xmlns="http://www.idpf.org/2007/opf" version="2.0" unique-identifier="uid">
  <metadata xmlns:dc="http://purl.org/dc/elements/1.1/" xmlns:calibre="http://calibre.mobwiki.com/">
    <dc:title>Earth, Air, Fire &amp; Water More</dc:title>
    <dc:creator>Test</dc:creator>
    <dc:date>0001</dc:date>
    <dc:language>en</dc:language>
    <calibre:series>Earth, Air, Fire &amp; Water More</calibre:series>
  </metadata>
  <manifest></manifest><spine></spine>
</package>"""


def write(path: Path, text: str) -> Path:
    path.write_text(text, encoding="utf-8")
    return path


def test_read_calibre_opf(tmp_path):
    opf = read_opf(write(tmp_path / "b.opf", CALIBRE_OPF))

    assert opf.metadata == BookMetadata(
        title="Android Sensor Programming by Example",
        authors=("Varun Nagpal",),
        isbn="9781785284663",
        publisher="Packt Publishing Ltd",
        language="eng",
        pubdate="2016-04-29T15:17:55.468028+00:00",
        comments="<p>Take your Android applications further.</p>",
        tags=("Java", "Programming"),
    )
    assert opf.written_by_calibre


def test_read_opf_ignores_placeholder_dates_and_series(tmp_path):
    opf = read_opf(write(tmp_path / "b.opf", SCRIPT_OPF))

    assert opf.metadata.title == "Earth, Air, Fire & Water More"
    assert opf.metadata.authors == ("Test",)
    assert opf.metadata.pubdate is None
    assert not opf.written_by_calibre


@pytest.mark.parametrize("date", ["0101-01-01T00:00:00+00:00", "0001", ""])
def test_placeholder_dates_are_dropped(tmp_path, date):
    text = CALIBRE_OPF.replace("2016-04-29T15:17:55.468028+00:00", date)

    assert read_opf(write(tmp_path / "b.opf", text)).metadata.pubdate is None


@pytest.mark.parametrize(
    "title",
    [
        "Untitled-1.pdf",
        "Untitled()",
        "Microsoft Word - Necronomicon - Die Satanische Bibel.doc",
        "p1g4vjd8ei12lia5k1c6l34k1t084",
        "ab",
        "",
        "   ",
        "Book1.indd",
        "DE201309",
    ],
)
def test_junk_titles(title):
    assert is_junk_title(title)


@pytest.mark.parametrize(
    "title",
    ["Wicca", "Android 2: Grundlagen und Programmierung", "PTFM: Purple Team Field Manual", "C++ Quick Syntax Reference"],
)
def test_real_titles_are_not_junk(title):
    assert not is_junk_title(title)


@pytest.mark.parametrize("author", ["Test", "Unknown", "Unbekannt", "N/A", "", "admin", "user", ".", "-", "1, 2"])
def test_junk_authors(author):
    assert is_junk_author(author)


def test_resolve_from_calibre_sidecar_needs_no_review(tmp_path):
    opf = write(tmp_path / "Android Sensor Programming by Example - Varun Nagpal.opf", CALIBRE_OPF)
    book = BookFile(tmp_path / "Android Sensor Programming by Example - Varun Nagpal.pdf", opf=opf)

    resolved = resolve_local(book)

    assert resolved.source == "sidecar opf"
    assert resolved.metadata.title == "Android Sensor Programming by Example"
    assert not resolved.needs_review


def test_resolve_from_non_calibre_sidecar_needs_review(tmp_path):
    opf = write(tmp_path / "b.opf", SCRIPT_OPF.replace("<dc:creator>Test</dc:creator>", "<dc:creator>Scott Cunningham</dc:creator>"))

    resolved = resolve_local(BookFile(tmp_path / "b.pdf", opf=opf))

    assert resolved.source == "sidecar opf"
    assert resolved.needs_review
    assert any("not written by calibre" in note for note in resolved.notes)


def test_resolve_junk_author_in_sidecar_is_dropped_and_needs_review(tmp_path):
    opf = write(tmp_path / "b.opf", CALIBRE_OPF.replace("Varun Nagpal</dc:creator>", "Test</dc:creator>"))

    resolved = resolve_local(BookFile(tmp_path / "b.pdf", opf=opf))

    assert resolved.metadata.authors == ()
    assert resolved.needs_review


def test_resolve_junk_title_in_sidecar_falls_back_to_filename(tmp_path):
    opf = write(tmp_path / "x.opf", CALIBRE_OPF.replace("Android Sensor Programming by Example</dc:title>", "Untitled-1.pdf</dc:title>"))
    book = BookFile(tmp_path / "Effective Python Penetration Testing - Rejah Rehim.pdf", opf=opf)

    resolved = resolve_local(book)

    assert resolved.metadata.title == "Effective Python Penetration Testing"
    assert resolved.metadata.authors == ("Rejah Rehim",)
    assert resolved.metadata.isbn == "9781785284663"
    assert resolved.needs_review


def test_resolve_from_filename_needs_review(tmp_path):
    book = BookFile(
        tmp_path / "Linux firewalls _ attack detection -- Michael Rash -- 2007 -- No Starch -- 9781593271411 -- "
        "848cf4bd357b684fd1871da94b3ff998 -- Anna’s Archive.pdf"
    )

    resolved = resolve_local(book)

    assert resolved.source == "filename"
    assert resolved.metadata == BookMetadata(
        title="Linux firewalls: attack detection",
        authors=("Michael Rash",),
        isbn="9781593271411",
        pubdate="2007",
    )
    assert resolved.needs_review


def test_metadata_year():
    assert BookMetadata(title="t", pubdate="2016-04-29T15:17:55+00:00").year == 2016
    assert BookMetadata(title="t", pubdate="2007").year == 2007
    assert BookMetadata(title="t").year is None


def test_sidecar_values_get_the_same_cleaning_as_filenames(tmp_path):
    text = (
        CALIBRE_OPF.replace("Android Sensor Programming by Example</dc:title>", "Metasploit (for Raymond Rhine)</dc:title>")
        .replace(
            '<dc:creator opf:file-as="Nagpal, Varun" opf:role="aut">Varun Nagpal</dc:creator>',
            '<dc:creator opf:role="aut">Nikolay Elenkov(Author)</dc:creator>'
            '<dc:creator opf:role="aut">Bellotti, Marianne, author</dc:creator>'
            '<dc:creator opf:role="aut">David Kennedy, et al.</dc:creator>',
        )
    )
    opf = write(tmp_path / "b.opf", text)

    resolved = resolve_local(BookFile(tmp_path / "b.pdf", opf=opf))

    assert resolved.metadata.title == "Metasploit"
    assert resolved.metadata.authors == ("Nikolay Elenkov", "Marianne Bellotti", "David Kennedy")


def test_unreadable_sidecar_falls_back_to_filename(tmp_path):
    """The old script wrote raw '&' into .opf files, which is not valid XML."""
    opf = write(tmp_path / "b.opf", SCRIPT_OPF.replace("&amp;", "&"))
    book = BookFile(tmp_path / "Earth, Air, Fire & Water - Scott Cunningham.pdf", opf=opf)

    resolved = resolve_local(book)

    assert resolved.source == "filename"
    assert resolved.metadata.title == "Earth, Air, Fire & Water"
    assert resolved.needs_review
    assert any("unreadable" in note for note in resolved.notes)


def test_junk_author_from_filename_is_dropped(tmp_path):
    book = BookFile(tmp_path / "Sex at Dawn -- . -- 2007 -- 0c7cb65158ace1774df5700304e54df0 -- Anna’s Archive.pdf")

    assert resolve_local(book).metadata.authors == ()
