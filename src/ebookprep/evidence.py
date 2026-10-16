"""About this file: reads evidence from a book file to check online candidates against.

Evidence is the text of the book's first pages (title page, copyright page) and the
ISBNs printed there. A candidate whose ISBN or whose title and author appear there is
very likely the right book, which mirrors opening the file and checking by hand. PDF
text comes from calibre's bundled pdftotext (poppler); EPUB text from the first
documents in reading order. Files without text (scans), unreadable files and other
formats give empty evidence. ISBNs are compared in both their 10- and 13-digit forms.

Many books (often German ones and EPUBs) print the copyright page at the back instead.
The last pages are therefore searched too, but only for numbers right after the word
"ISBN", and they are kept apart as back ISBNs: the back pages also advertise other books
with their ISBNs, so a back ISBN is weaker evidence (see scoring.py). The placeholder
ISBN 0-12-345678-9 used in templates is never taken.
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
PDFINFO = PDFTOTEXT.with_name("pdfinfo")
DEFAULT_PAGES = 10
BACK_PAGES = 5
# EPUBs have no pages; this many characters from the start (and from the end) stand in
# for the first (and last) pages.
EPUB_CHARS = 20000

_ISBN_LIKE = re.compile(r"(?<![\dX])(\d[\d\- ]{8,16}[\dX])(?![\dX])")
_ISBN_AFTER_WORD = re.compile(r"isbn(?:[- ]?1[03])?[^\d]{0,25}?(\d[\d\- ]{8,16}[\dX])(?![\dX])", re.IGNORECASE)
_CONTAINER = "{urn:oasis:names:tc:opendocument:xmlns:container}"
_OPF = "{http://www.idpf.org/2007/opf}"


@dataclass(frozen=True)
class Evidence:
    # Lower-case text of the first pages, whitespace collapsed.
    text: str
    # Valid ISBNs found in the text, each in its 10- and 13-digit forms.
    isbns: frozenset[str]
    # Valid ISBNs after the word "ISBN" on the last pages, not among isbns.
    back_isbns: frozenset[str] = frozenset()


EMPTY = Evidence(text="", isbns=frozenset())


def extract_evidence(path: Path, pages: int = DEFAULT_PAGES) -> Evidence:
    suffix = path.suffix.lower()
    if suffix == ".pdf":
        raw, back = _pdf_text(path, 1, pages), _pdf_back_text(path, pages)
    elif suffix == ".epub":
        raw, back = _epub_texts(path)
    else:
        raw, back = "", ""
    isbns = frozenset(_find_isbns(raw))
    return Evidence(
        text=re.sub(r"\s+", " ", raw.casefold()).strip(),
        isbns=isbns,
        back_isbns=frozenset(_find_isbns(back, _ISBN_AFTER_WORD)) - isbns,
    )


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


_PLACEHOLDER = isbn_forms("0123456789")


def _find_isbns(text: str, pattern: re.Pattern = _ISBN_LIKE) -> set[str]:
    found: set[str] = set()
    for match in pattern.findall(text):
        digits = re.sub(r"[\- ]", "", match)
        if len(digits) in (10, 13) and is_valid_isbn(digits) and digits not in _PLACEHOLDER:
            found |= isbn_forms(digits)
    return found


def _poppler(tool: Path, args: list[str]) -> str:
    """Output of one of calibre's bundled poppler tools, empty if it fails."""
    if not tool.exists():
        return ""
    env = {**os.environ, "LD_LIBRARY_PATH": CALIBRE_LIB}
    try:
        result = subprocess.run([str(tool), *args], capture_output=True, timeout=120, env=env)
    except subprocess.TimeoutExpired:
        return ""
    return result.stdout.decode("utf-8", errors="replace") if result.returncode == 0 else ""


def _pdf_text(path: Path, first: int, last: int) -> str:
    return _poppler(PDFTOTEXT, ["-f", str(first), "-l", str(last), "-enc", "UTF-8", str(path), "-"])


def _pdf_back_text(path: Path, front_pages: int) -> str:
    """Text of the last pages that are not among the first pages."""
    match = re.search(r"^Pages:\s+(\d+)", _poppler(PDFINFO, [str(path)]), re.MULTILINE)
    count = int(match.group(1)) if match else 0
    if count <= front_pages:
        return ""
    return _pdf_text(path, max(front_pages + 1, count - BACK_PAGES + 1), count)


class TextCollector(HTMLParser):
    """Collects the readable text of an HTML document (not head, style or script)."""

    SKIPPED = frozenset({"head", "style", "script"})

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self._skipping = 0

    def handle_starttag(self, tag: str, attrs) -> None:
        if tag in self.SKIPPED:
            self._skipping += 1

    def handle_endtag(self, tag: str) -> None:
        if tag in self.SKIPPED and self._skipping:
            self._skipping -= 1

    def handle_data(self, data: str) -> None:
        if not self._skipping:
            self.parts.append(data)


def _epub_texts(path: Path) -> tuple[str, str]:
    """The text of the EPUB's start and of its end (not overlapping the start)."""
    try:
        with zipfile.ZipFile(path) as epub:
            text = ""
            for name in spine_documents(epub):
                collector = TextCollector()
                collector.feed(epub.read(name).decode("utf-8", errors="replace"))
                text += " " + " ".join(collector.parts)
    except (zipfile.BadZipFile, KeyError, ET.ParseError, OSError):
        return "", ""
    return text[:EPUB_CHARS], text[max(EPUB_CHARS, len(text) - EPUB_CHARS) :]


def spine_documents(epub: zipfile.ZipFile) -> list[str]:
    """The EPUB's HTML documents in reading order (Kavita counts each as one page)."""
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
