"""About this file: tests for ebookprep.pdf_repair, which rewrites PDFs with a clean
structure so calibre can write metadata into them and Kavita can read it."""

import subprocess

import pikepdf
import pytest
from conftest import damage_trailer, make_pdf

from ebookprep.pdf_repair import PdfRepairError, repair_pdf, strip_xmp


def test_intact_pdf_is_copied_with_content_and_metadata(tmp_path):
    src = make_pdf(tmp_path / "in.pdf", title="Kept Title", pages=3)

    report = repair_pdf(src, tmp_path / "out.pdf")

    assert not report.was_damaged
    with pikepdf.open(tmp_path / "out.pdf") as pdf:
        assert len(pdf.pages) == 3
        assert str(pdf.docinfo["/Title"]) == "Kept Title"


def test_damaged_pdf_is_reported_and_rewritten_clean(tmp_path):
    src = damage_trailer(make_pdf(tmp_path / "in.pdf", pages=2))

    report = repair_pdf(src, tmp_path / "out.pdf")

    assert report.was_damaged
    assert any("damaged" in w for w in report.warnings)
    with pikepdf.open(tmp_path / "out.pdf") as pdf:
        assert len(pdf.pages) == 2
        assert pdf.get_warnings() == []


def test_source_file_is_never_modified(tmp_path):
    src = damage_trailer(make_pdf(tmp_path / "in.pdf"))
    before = src.read_bytes()

    repair_pdf(src, tmp_path / "out.pdf")

    assert src.read_bytes() == before


def test_owner_password_encryption_is_removed(tmp_path):
    src = tmp_path / "in.pdf"
    pdf = pikepdf.new()
    pdf.add_blank_page()
    pdf.save(src, encryption=pikepdf.Encryption(owner="secret", user=""))

    report = repair_pdf(src, tmp_path / "out.pdf")

    assert report.was_encrypted
    with pikepdf.open(tmp_path / "out.pdf") as out:
        assert not out.is_encrypted


def test_user_password_protected_pdf_raises(tmp_path):
    src = tmp_path / "in.pdf"
    pdf = pikepdf.new()
    pdf.add_blank_page()
    pdf.save(src, encryption=pikepdf.Encryption(owner="secret", user="userpw"))

    with pytest.raises(PdfRepairError, match="password"):
        repair_pdf(src, tmp_path / "out.pdf")
    assert not (tmp_path / "out.pdf").exists()


def test_not_a_pdf_raises(tmp_path):
    src = tmp_path / "in.pdf"
    src.write_bytes(b"this is not a pdf")

    with pytest.raises(PdfRepairError):
        repair_pdf(src, tmp_path / "out.pdf")
    assert not (tmp_path / "out.pdf").exists()


@pytest.mark.calibre
def test_calibre_can_write_series_after_repair(tmp_path):
    src = damage_trailer(make_pdf(tmp_path / "in.pdf"))
    out = tmp_path / "out.pdf"

    repair_pdf(src, out)
    write = subprocess.run(
        ["ebook-meta", str(out), "--series", "Probe Series", "--index", "1"], capture_output=True, text=True
    )

    assert "PdfErrorCode" not in write.stdout + write.stderr
    read = subprocess.run(["ebook-meta", str(out)], capture_output=True, text=True, check=True)
    assert "Probe Series #1" in read.stdout


@pytest.mark.calibre
def test_calibre_fails_silently_on_the_damaged_original(tmp_path):
    """Documents the bug the repair works around: exit code 0, but nothing is written."""
    src = damage_trailer(make_pdf(tmp_path / "in.pdf"))

    write = subprocess.run(["ebook-meta", str(src), "--series", "Probe Series"], capture_output=True, text=True)

    assert write.returncode == 0
    assert "PdfErrorCode" in write.stdout + write.stderr


# --- strip_xmp: used when calibre silently fails to merge into an existing XMP packet


def test_strip_xmp_removes_and_returns_the_packet(tmp_path):
    path = make_pdf(tmp_path / "b.pdf", title="Info Title")
    with pikepdf.open(path, allow_overwriting_input=True) as pdf:
        with pdf.open_metadata(update_docinfo=False) as xmp:
            xmp["dc:title"] = "Publisher Title"
        pdf.save(path)

    removed = strip_xmp(path)

    assert removed is not None and "Publisher Title" in removed
    with pikepdf.open(path) as pdf:
        assert "/Metadata" not in pdf.Root
        assert str(pdf.docinfo["/Title"]) == "Info Title"


def test_strip_xmp_without_packet_returns_none(tmp_path):
    path = make_pdf(tmp_path / "b.pdf")

    assert strip_xmp(path) is None
