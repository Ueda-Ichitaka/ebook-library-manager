"""About this file: tests for ebookprep.evidence, which reads the text of a book's first
pages and the ISBNs printed there, to check online candidates against the book itself."""

import pytest
from conftest import make_epub, make_pdf, make_text_pdf

from ebookprep.evidence import extract_evidence, isbn_forms

calibre = pytest.mark.calibre


@calibre
def test_pdf_first_pages_text_and_isbn(tmp_path):
    path = make_text_pdf(
        tmp_path / "b.pdf",
        [
            ["BLACK HAT GO", "Go Programming for Hackers and Pentesters", "by Tom Steele, Chris Patten"],
            ["Copyright 2020 No Starch Press", "ISBN-13: 978-1-59327-865-6"],
        ],
    )

    evidence = extract_evidence(path)

    assert "black hat go" in evidence.text
    assert "tom steele" in evidence.text
    assert "9781593278656" in evidence.isbns


@calibre
def test_only_the_first_pages_are_read(tmp_path):
    pages = [[f"page {n}"] for n in range(1, 16)] + [["LATE MARKER"]]

    evidence = extract_evidence(make_text_pdf(tmp_path / "b.pdf", pages), pages=10)

    assert "page 10" in evidence.text
    assert "late marker" not in evidence.text


@calibre
def test_pdf_without_text_gives_empty_evidence(tmp_path):
    evidence = extract_evidence(make_pdf(tmp_path / "scan.pdf"))

    assert evidence.text == ""
    assert evidence.isbns == frozenset()


def test_epub_text_and_isbn(tmp_path):
    body = "<h1>The Spiral Dance</h1><p>by Starhawk</p><p>ISBN 0-06-250814-8</p>"

    evidence = extract_evidence(make_epub(tmp_path / "b.epub", title="x", body=body))

    assert "the spiral dance" in evidence.text
    assert "starhawk" in evidence.text
    assert "0062508148" in evidence.isbns
    assert "9780062508140" in evidence.isbns


def test_invalid_numbers_are_not_isbns(tmp_path):
    body = "<p>Call 978-1-23456-789-0 or 1234567890 today.</p>"

    assert extract_evidence(make_epub(tmp_path / "b.epub", title="x", body=body)).isbns == frozenset()


def test_unreadable_file_gives_empty_evidence(tmp_path):
    path = tmp_path / "b.epub"
    path.write_bytes(b"not a zip")

    assert extract_evidence(path).text == ""


def test_unsupported_format_gives_empty_evidence(tmp_path):
    path = tmp_path / "b.mobi"
    path.write_bytes(b"x")

    assert extract_evidence(path).text == ""


def test_isbn_forms_convert_between_10_and_13():
    assert isbn_forms("0062508148") == {"0062508148", "9780062508140"}
    assert isbn_forms("9780062508140") == {"0062508148", "9780062508140"}
    assert isbn_forms("9798868810107") == {"9798868810107"}


# --- ISBNs on the last pages


@calibre
def test_isbn_after_the_word_isbn_on_the_last_pages_is_a_back_isbn(tmp_path):
    pages = [["Black Hat Go"]] + [[f"page {n}"] for n in range(2, 20)]
    pages += [["Also: 978-1-59327-865-6"], ["Copyright 2020", "ISBN 978-1-71850-374-8"]]

    evidence = extract_evidence(make_text_pdf(tmp_path / "b.pdf", pages), pages=10)

    assert evidence.isbns == frozenset()
    assert evidence.back_isbns == frozenset({"9781718503748", "1718503741"})


@calibre
def test_front_isbns_are_not_repeated_as_back_isbns(tmp_path):
    pages = [["ISBN 978-1-71850-374-8"]] + [[f"page {n}"] for n in range(2, 20)] + [["ISBN 978-1-71850-374-8"]]

    evidence = extract_evidence(make_text_pdf(tmp_path / "b.pdf", pages), pages=10)

    assert "9781718503748" in evidence.isbns
    assert evidence.back_isbns == frozenset()


@calibre
def test_short_pdf_has_no_back_pages(tmp_path):
    evidence = extract_evidence(make_text_pdf(tmp_path / "b.pdf", [["a"], ["ISBN 978-1-71850-374-8"]]), pages=10)

    assert "9781718503748" in evidence.isbns and evidence.back_isbns == frozenset()


def test_epub_end_is_searched_for_back_isbns(tmp_path):
    body = "<h1>Many Love</h1>" + "<p>" + "words " * 6000 + "</p><p>ISBN 978-1-5011-8978-4 (ebook)</p>"

    evidence = extract_evidence(make_epub(tmp_path / "b.epub", title="x", body=body))

    assert evidence.isbns == frozenset()
    assert "9781501189784" in evidence.back_isbns


def test_placeholder_isbn_is_never_an_isbn(tmp_path):
    body = "<p>ISBN 0-12-345678-9</p><p>ISBN 978-0-12-345678-6</p>"

    evidence = extract_evidence(make_epub(tmp_path / "b.epub", title="x", body=body))

    assert evidence.isbns == frozenset()
