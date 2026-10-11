"""About this file: facts about a book file, shown when comparing possible duplicates.

Besides size and format it gives the page count the way Kavita counts it: for a PDF its
pages, for an EPUB the number of HTML documents in reading order (Kavita's
BookService.GetNumberOfPages). An EPUB made of a few huge HTML documents therefore shows
only a few pages in Kavita and in reader apps using Kavita, which makes reading progress
hard to follow. For comparison, an EPUB's words are counted and turned into printed
pages at WORDS_PER_PAGE words per page.
"""

import zipfile
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path

import pikepdf

from ebookprep.evidence import TextCollector, spine_documents

WORDS_PER_PAGE = 250


@dataclass(frozen=True)
class FileInfo:
    path: Path
    format: str
    size_bytes: int
    # pages as Kavita counts them, None if unknown
    kavita_pages: int | None
    # printed pages: a PDF's pages, an EPUB's words divided by WORDS_PER_PAGE
    printed_pages: int | None
    words: int | None


def file_info(path: Path) -> FileInfo:
    suffix = path.suffix.lower()
    kavita_pages = printed_pages = words = None
    if suffix == ".pdf":
        kavita_pages = printed_pages = _pdf_pages(path)
    elif suffix == ".epub":
        kavita_pages, words = _epub_pages_and_words(path)
        if words is not None:
            printed_pages = round(words / WORDS_PER_PAGE)
    return FileInfo(
        path=path,
        format=suffix.lstrip(".").upper(),
        size_bytes=path.stat().st_size,
        kavita_pages=kavita_pages,
        printed_pages=printed_pages,
        words=words,
    )


def _pdf_pages(path: Path) -> int | None:
    try:
        with pikepdf.open(path) as pdf:
            return len(pdf.pages)
    except pikepdf.PdfError:
        return None


def _epub_pages_and_words(path: Path) -> tuple[int | None, int | None]:
    try:
        with zipfile.ZipFile(path) as epub:
            documents = spine_documents(epub)
            words = 0
            for name in documents:
                collector = TextCollector()
                collector.feed(epub.read(name).decode("utf-8", errors="replace"))
                words += sum(len(part.split()) for part in collector.parts)
            return len(documents), words
    except (zipfile.BadZipFile, KeyError, ET.ParseError, OSError):
        return None, None
