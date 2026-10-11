"""About this file: reads evidence from a book file to check online candidates against.

Evidence is the text of the book's first pages (title page, copyright page) and the
ISBNs printed there. A candidate whose ISBN or whose title and author appear there is
very likely the right book, which mirrors opening the file and checking by hand. PDF
text comes from calibre's bundled pdftotext (poppler); EPUB text from the first
documents in reading order. Files without text (scans), unreadable files and other
formats give empty evidence. ISBNs are compared in both their 10- and 13-digit forms.
"""

import os
import re
import subprocess
import xml.etree.ElementTree as ET
import zipfile
from dataclasses import dataclass
from html.parser import HTMLParser
from pathlib import Path, PurePosixPath

from ebookprep.filenames import is_valid_isbn

PDFTOTEXT = Path(os.environ.get("EBOOKPREP_PDFTOTEXT", "/opt/calibre/bin/pdftotext"))
CALIBRE_LIB = os.environ.get("EBOOKPREP_CALIBRE_LIB", "/opt/calibre/lib")
DEFAULT_PAGES = 10
# EPUBs have no pages; this many characters from the start stand in for the first pages.
EPUB_CHARS = 20000

_ISBN_LIKE = re.compile(r"(?<![\dX])(\d[\d\- ]{8,16}[\dX])(?![\dX])")
_CONTAINER = "{urn:oasis:names:tc:opendocument:xmlns:container}"
_OPF = "{http://www.idpf.org/2007/opf}"


@dataclass(frozen=True)
class Evidence:
    # Lower-case text of the first pages, whitespace collapsed.
    text: str
    # Valid ISBNs found in the text, each in its 10- and 13-digit forms.
    isbns: frozenset[str]


EMPTY = Evidence(text="", isbns=frozenset())


def extract_evidence(path: Path, pages: int = DEFAULT_PAGES) -> Evidence:
    suffix = path.suffix.lower()
    if suffix == ".pdf":
        raw = _pdf_text(path, pages)
    elif suffix == ".epub":
        raw = _epub_text(path)
    else:
        raw = ""
    return Evidence(text=re.sub(r"\s+", " ", raw.casefold()).strip(), isbns=frozenset(_find_isbns(raw)))


def isbn_forms(isbn: str) -> set[str]:
    """The ISBN in all forms: an ISBN-10 and its 978 ISBN-13 are the same book."""
    forms = {isbn}
    if len(isbn) == 10:
        forms.add(_isbn13("978" + isbn[:9]))
    elif isbn.startswith("978"):
        forms.add(_isbn10(isbn[3:12]))
    return forms


def _isbn13(first12: str) -> str:
    total = sum(int(c) * (1 if i % 2 == 0 else 3) for i, c in enumerate(first12))
    return first12 + str((10 - total % 10) % 10)


def _isbn10(first9: str) -> str:
    check = (11 - sum((10 - i) * int(c) for i, c in enumerate(first9)) % 11) % 11
    return first9 + ("X" if check == 10 else str(check))


def _find_isbns(text: str) -> set[str]:
    found: set[str] = set()
    for match in _ISBN_LIKE.findall(text):
        digits = re.sub(r"[\- ]", "", match)
        if len(digits) in (10, 13) and is_valid_isbn(digits):
            found |= isbn_forms(digits)
    return found


def _pdf_text(path: Path, pages: int) -> str:
    if not PDFTOTEXT.exists():
        return ""
    command = [str(PDFTOTEXT), "-f", "1", "-l", str(pages), "-enc", "UTF-8", str(path), "-"]
    env = {**os.environ, "LD_LIBRARY_PATH": CALIBRE_LIB}
    try:
        result = subprocess.run(command, capture_output=True, timeout=120, env=env)
    except subprocess.TimeoutExpired:
        return ""
    return result.stdout.decode("utf-8", errors="replace") if result.returncode == 0 else ""


class _TextCollector(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []

    def handle_data(self, data: str) -> None:
        self.parts.append(data)


def _epub_text(path: Path) -> str:
    try:
        with zipfile.ZipFile(path) as epub:
            text = ""
            for name in _spine_documents(epub):
                collector = _TextCollector()
                collector.feed(epub.read(name).decode("utf-8", errors="replace"))
                text += " " + " ".join(collector.parts)
                if len(text) >= EPUB_CHARS:
                    break
            return text[:EPUB_CHARS]
    except (zipfile.BadZipFile, KeyError, ET.ParseError, OSError):
        return ""


def _spine_documents(epub: zipfile.ZipFile) -> list[str]:
    container = ET.fromstring(epub.read("META-INF/container.xml"))
    rootfile = container.find(f".//{_CONTAINER}rootfile")
    if rootfile is None:
        return []
    opf_path = PurePosixPath(rootfile.get("full-path", ""))
    package = ET.fromstring(epub.read(str(opf_path)))
    manifest = {item.get("id"): item.get("href") for item in package.iter(f"{_OPF}item")}
    base = opf_path.parent
    return [
        str(base / manifest[ref.get("idref")])
        for ref in package.iter(f"{_OPF}itemref")
        if ref.get("idref") in manifest
    ]
