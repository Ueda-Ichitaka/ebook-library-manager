"""About this file: book metadata, reading calibre .opf files, and resolving metadata from
local sources.

BookMetadata is the metadata written into each output book; its title also becomes the
series, so Kavita lists the book on its own. Local sources are a sidecar .opf next to the
book (written by calibre's "save to disk", or by an older script) and the filename.
Junk values found in the library (titles like "Untitled-1.pdf" or hash names, authors
like "Test" or "Unknown") are recognized and replaced or dropped, and the book is marked
for review. Values from .opf files get the same cleaning as filenames. An .opf file that
isn't valid XML (the older script wrote raw "&") is ignored in favour of the filename. Online lookup and the review UI build on top of this.
"""

import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass, replace
from pathlib import Path

from ebookprep.filenames import clean_title, parse_authors, parse_filename
from ebookprep.scan import BookFile

_DC = "{http://purl.org/dc/elements/1.1/}"
_OPF = "{http://www.idpf.org/2007/opf}"

_JUNK_TITLE_EXTENSION = re.compile(r"\.(pdf|docx?|indd|qxd|qxp|rtf|txt|epub|tex|odt|ps)$", re.IGNORECASE)
_JUNK_TITLE_PREFIX = re.compile(r"^(microsoft word - |untitled)", re.IGNORECASE)
_JUNK_AUTHORS = frozenset({"", "test", "unknown", "unbekannt", "n/a", "na", "admin", "administrator", "user", "author"})


@dataclass(frozen=True)
class BookMetadata:
    title: str
    authors: tuple[str, ...] = ()
    isbn: str | None = None
    publisher: str | None = None
    language: str | None = None
    pubdate: str | None = None
    comments: str | None = None
    tags: tuple[str, ...] = ()

    @property
    def year(self) -> int | None:
        match = re.match(r"(\d{4})", self.pubdate or "")
        return int(match.group(1)) if match else None


@dataclass(frozen=True)
class OpfData:
    metadata: BookMetadata
    written_by_calibre: bool


@dataclass(frozen=True)
class ResolvedMetadata:
    metadata: BookMetadata
    source: str
    needs_review: bool
    notes: tuple[str, ...] = ()


def read_opf(path: Path) -> OpfData:
    return parse_opf(path.read_bytes())


def parse_opf(data: bytes | str) -> OpfData:
    root = ET.fromstring(data)
    meta = root.find(f"{_OPF}metadata")
    if meta is None:
        meta = ET.Element("metadata")

    def texts(tag: str) -> list[str]:
        return [t for e in meta.findall(f"{_DC}{tag}") if (t := "".join(e.itertext()).strip())]

    def first(tag: str) -> str | None:
        values = texts(tag)
        return values[0] if values else None

    authors = tuple(
        "".join(e.itertext()).strip()
        for e in meta.findall(f"{_DC}creator")
        if e.get(f"{_OPF}role") in (None, "aut") and "".join(e.itertext()).strip()
    )
    written_by_calibre = any(
        e.get(f"{_OPF}role") == "bkp" and "".join(e.itertext()).strip().startswith("calibre")
        for e in meta.findall(f"{_DC}contributor")
    )

    metadata = BookMetadata(
        title=first("title") or "",
        authors=authors,
        isbn=_isbn(meta),
        publisher=first("publisher"),
        language=first("language"),
        pubdate=_real_date(first("date")),
        comments=first("description"),
        tags=tuple(texts("subject")),
    )
    return OpfData(metadata=metadata, written_by_calibre=written_by_calibre)


def _isbn(meta: ET.Element) -> str | None:
    for element in meta.findall(f"{_DC}identifier"):
        value = "".join(element.itertext()).strip()
        scheme = (element.get(f"{_OPF}scheme") or "").lower()
        if scheme == "isbn" or value.lower().startswith("urn:isbn:"):
            return value.split(":")[-1].replace("-", "")
    return None


def _real_date(date: str | None) -> str | None:
    """Drops calibre's placeholder dates (year 101 or year 1)."""
    match = re.match(r"(\d{4})", date or "")
    if not match or int(match.group(1)) < 1000:
        return None
    return date


def _cleaned(metadata: BookMetadata) -> BookMetadata:
    """Applies the filename cleaning to .opf values, which contain the same leftovers
    (download watermarks like "(for Name)", author roles like "(Author)" or ", author")."""
    authors = tuple(dict.fromkeys(a for creator in metadata.authors for a in parse_authors(creator)))
    return replace(metadata, title=clean_title(metadata.title), authors=authors)


def is_junk_title(title: str) -> bool:
    text = title.strip()
    if len(text) < 3 or _JUNK_TITLE_EXTENSION.search(text) or _JUNK_TITLE_PREFIX.match(text):
        return True
    looks_like_hash = " " not in text and len(text) >= 16 and len(re.findall(r"\d", text)) >= 3
    return looks_like_hash


def is_junk_author(author: str) -> bool:
    return author.strip().casefold() in _JUNK_AUTHORS


def resolve_local(book: BookFile) -> ResolvedMetadata:
    from_filename = parse_filename(book.path.stem)
    if book.opf is None:
        return _from_filename(book, ("metadata from filename only",))
    try:
        opf = read_opf(book.opf)
    except (ET.ParseError, OSError) as error:
        return _from_filename(book, (f"sidecar opf unreadable ({error}), metadata from filename only",))

    metadata = _cleaned(opf.metadata)
    notes: list[str] = []
    if not opf.written_by_calibre:
        notes.append("sidecar opf not written by calibre")

    authors = tuple(a for a in metadata.authors if not is_junk_author(a))
    if authors != metadata.authors or not authors:
        notes.append("junk or missing author in sidecar opf")
    metadata = replace(metadata, authors=authors)

    if is_junk_title(metadata.title):
        notes.append(f"junk title {metadata.title!r} in sidecar opf, title taken from filename")
        metadata = replace(metadata, title=from_filename.title, authors=from_filename.authors or metadata.authors)

    return ResolvedMetadata(metadata, "sidecar opf", needs_review=bool(notes), notes=tuple(notes))


def _from_filename(book: BookFile, notes: tuple[str, ...]) -> ResolvedMetadata:
    info = parse_filename(book.path.stem)
    metadata = BookMetadata(
        title=info.title, authors=info.authors, isbn=info.isbn, pubdate=str(info.year) if info.year else None
    )
    return ResolvedMetadata(metadata, "filename", needs_review=True, notes=notes)
