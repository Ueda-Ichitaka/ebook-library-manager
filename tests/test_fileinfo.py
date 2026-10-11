"""About this file: tests for ebookprep.fileinfo, the facts shown when comparing possible
duplicate files: size, format, and pages the way Kavita counts them next to an estimate
of printed pages."""

from conftest import damage_trailer, make_epub, make_pdf

from ebookprep.fileinfo import WORDS_PER_PAGE, file_info


def test_pdf_pages_are_kavita_pages(tmp_path):
    path = make_pdf(tmp_path / "b.pdf", pages=12)

    info = file_info(path)

    assert info.format == "PDF"
    assert info.size_bytes == path.stat().st_size
    assert info.kavita_pages == 12
    assert info.printed_pages == 12
    assert info.words is None


def test_damaged_pdf_still_gets_its_page_count(tmp_path):
    path = damage_trailer(make_pdf(tmp_path / "b.pdf", pages=3))

    assert file_info(path).kavita_pages == 3


def test_epub_pages_are_its_html_documents_and_words_give_printed_pages(tmp_path):
    words = " ".join(["word"] * (WORDS_PER_PAGE * 3))
    path = make_epub(tmp_path / "b.epub", title="x", body=f"<p>{words}</p>")

    info = file_info(path)

    assert info.format == "EPUB"
    assert info.kavita_pages == 1
    assert info.words == WORDS_PER_PAGE * 3
    assert info.printed_pages == 3


def test_unreadable_or_other_formats_have_no_pages(tmp_path):
    broken = tmp_path / "b.epub"
    broken.write_bytes(b"not a zip")
    mobi = tmp_path / "c.mobi"
    mobi.write_bytes(b"x" * 10)

    assert file_info(broken).kavita_pages is None
    assert (file_info(mobi).format, file_info(mobi).kavita_pages, file_info(mobi).size_bytes) == ("MOBI", None, 10)
