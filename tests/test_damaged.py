"""About this file: tests for ebookprep.damaged, which recognises damaged downloads (the
download site's rate-limit message written over the start of the file, or an MD5 that
differs from the one in the filename on a file that can't be read) and repairs what can
be fully restored."""

import io
import zipfile
from pathlib import Path

import pikepdf
import pytest
from conftest import MESSAGE, make_text_pdf, overwrite_start, pdf_with_long_catalog

from ebookprep.damaged import DownloadRepairError, RATE_LIMIT_MESSAGE, check_downloads, repair_download
from ebookprep.state import StateStore

def epub(path: Path, entries: list[tuple[str, bytes]]) -> Path:
    with zipfile.ZipFile(path, "w") as z:
        for name, content in entries:
            z.writestr(name, content, compress_type=zipfile.ZIP_STORED if name == "mimetype" else zipfile.ZIP_DEFLATED)
    return path


CONTAINER = (b'<?xml version="1.0"?><container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container">'
             b'<rootfiles><rootfile full-path="OEBPS/content.opf" media-type="application/oebps-package+xml"/>'
             b'</rootfiles></container>')
OPF = b'<?xml version="1.0"?><package xmlns="http://www.idpf.org/2007/opf" version="2.0"><metadata/></package>'
CHAPTER = b"<html><body><p>" + b"Chapter one text. " * 400 + b"</p></body></html>"


def test_message_constant_matches_the_damaged_files():
    assert RATE_LIMIT_MESSAGE == MESSAGE[:61] and len(MESSAGE) == 154


def test_pdf_with_overwritten_header_and_catalog_start_is_repaired(tmp_path):
    damaged = overwrite_start(pdf_with_long_catalog(tmp_path / "damaged.pdf"))
    with pytest.raises(pikepdf.PdfError):
        pikepdf.open(damaged)

    note = repair_download(damaged, tmp_path / "repaired.pdf")

    with pikepdf.open(tmp_path / "repaired.pdf") as pdf:
        assert len(pdf.pages) == 2
        assert pdf.Root.Type == pikepdf.Name.Catalog
        assert pdf.Root.ViewerPreferences.Direction == pikepdf.Name.L2R
        assert "/EbookprepLost" not in pdf.Root
    assert "header" in note


def test_epub_with_lost_mimetype_and_container_is_repaired(tmp_path):
    damaged = overwrite_start(epub(tmp_path / "damaged.epub", [
        ("mimetype", b"application/epub+zip"), ("META-INF/container.xml", CONTAINER),
        ("OEBPS/content.opf", OPF), ("OEBPS/ch1.xhtml", CHAPTER),
    ]))

    repair_download(damaged, tmp_path / "repaired.epub")

    with zipfile.ZipFile(tmp_path / "repaired.epub") as z:
        assert z.namelist()[0] == "mimetype" and z.getinfo("mimetype").compress_type == zipfile.ZIP_STORED
        assert z.read("mimetype") == b"application/epub+zip"
        assert b'full-path="OEBPS/content.opf"' in z.read("META-INF/container.xml")
        assert z.read("OEBPS/ch1.xhtml") == CHAPTER
        assert z.testzip() is None


def test_epub_with_lost_content_is_not_repaired(tmp_path):
    damaged = overwrite_start(epub(tmp_path / "damaged.epub", [
        ("mimetype", b"application/epub+zip"), ("OEBPS/xhtml/Ch01.xhtml", CHAPTER),
        ("META-INF/container.xml", CONTAINER), ("OEBPS/content.opf", OPF),
    ]))

    with pytest.raises(DownloadRepairError, match="OEBPS/xhtml/Ch01.xhtml"):
        repair_download(damaged, tmp_path / "repaired.epub")
    assert not (tmp_path / "repaired.epub").exists()


def test_check_downloads_classifies_and_remembers_files(tmp_path, monkeypatch):
    from ebookprep import damaged as module

    root = tmp_path / "input"
    (root / "A/b").mkdir(parents=True)
    overwrite_start(pdf_with_long_catalog(root / "A/b/repairable.pdf"))
    overwrite_start(epub(root / "A/b/lost.epub", [
        ("mimetype", b"application/epub+zip"), ("OEBPS/xhtml/Ch01.xhtml", CHAPTER), ("OEBPS/content.opf", OPF)]))
    md5_name = "Book -- 0123456789abcdef0123456789abcdef -- Anna’s Archive.pdf"
    (root / "A/b" / md5_name).write_bytes(b"not a pdf at all")
    edited = root / "A/b/Edited -- 0123456789abcdef0123456789abcdef -- Anna’s Archive.pdf"
    make_text_pdf(edited, [["fine"]])
    make_text_pdf(root / "A/b/plain.pdf", [["fine"]])
    store = StateStore(tmp_path / "state.db")
    paths = ["A/b/repairable.pdf", "A/b/lost.epub", f"A/b/{md5_name}", f"A/b/{edited.name}", "A/b/plain.pdf"]

    damage = check_downloads(root, store, paths)

    assert set(damage) == {"A/b/repairable.pdf", "A/b/lost.epub", f"A/b/{md5_name}"}
    assert damage["A/b/repairable.pdf"].repairable
    lost = damage["A/b/lost.epub"]
    assert not lost.repairable and "OEBPS/xhtml/Ch01.xhtml" in lost.note
    broken = damage[f"A/b/{md5_name}"]
    assert not broken.repairable and "0123456789abcdef0123456789abcdef" in broken.note
    assert store.download_damage() == damage

    calls = []
    monkeypatch.setattr(module, "_inspect", lambda path: calls.append(path) or None)
    check_downloads(root, store, paths)
    assert calls == []


def test_identify_and_detect_moves_check_for_damaged_downloads(tmp_path, capsys):
    from ebookprep.__main__ import main

    root = tmp_path / "input"
    (root / "Beziehungskonzepte/unsortiert").mkdir(parents=True)
    overwrite_start(pdf_with_long_catalog(root / "Beziehungskonzepte/unsortiert/Handbook - Cassidy.pdf"))
    config = tmp_path / "config.toml"
    config.write_text("sources = []\n")
    paths = ["--input", str(root), "--work", str(tmp_path / "work"), "--config", str(config)]

    assert main(["detect-moves", *paths]) == 0

    store = StateStore(tmp_path / "work" / "state.db")
    assert list(store.download_damage()) == ["Beziehungskonzepte/unsortiert/Handbook - Cassidy.pdf"]
    assert "damaged download (repairable): Beziehungskonzepte/unsortiert/Handbook - Cassidy.pdf" in capsys.readouterr().out
