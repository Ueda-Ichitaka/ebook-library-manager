"""About this file: predicts the series name Kavita derives from a PDF or EPUB file.

It mirrors the logic of Kavita v0.9.1.4 (the server version in use), taken from its
source: Scanner/PdfParser.cs, Helpers/PdfMetadataExtractor.cs,
Helpers/PdfComicInfoExtractor.cs and BookService.ParseInfo. The pipeline uses it to
verify every output file: a book is listed on its own only when the series comes from
the embedded metadata and equals the book's title.

PDF: the XMP field calibre:series/rdf:value, else the title (the XMP dc:title overrides
the Info dictionary title), else the name of the folder holding the file. A title of only
whitespace gives the file name instead. If the metadata can't be read, it's the folder name.
Kavita's PDF reader doesn't reconstruct damaged files, so a PDF that pikepdf only opens
with recovery warnings counts as unreadable.
EPUB: calibre:series when calibre:series_index is also set (or the EPUB 3
belongs-to-collection with group-position), else the dc:title. If the EPUB can't be
read, or there is no title, Kavita doesn't import the file at all.

Simplifications: Kavita additionally cleans file and folder names and parses volume
markers out of names and titles; this module returns the raw names.
"""

import enum
import xml.etree.ElementTree as ET
import zipfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

import pikepdf

_RDF = "{http://www.w3.org/1999/02/22-rdf-syntax-ns#}"
_DC = "{http://purl.org/dc/elements/1.1/}"
_CALIBRE = "{http://calibre-ebook.com/xmp-namespace}"
_OPF = "{http://www.idpf.org/2007/opf}"
_CONTAINER = "{urn:oasis:names:tc:opendocument:xmlns:container}"


class SeriesSource(enum.Enum):
    SERIES = "series"
    TITLE = "title"
    FILENAME = "filename"
    FOLDER = "folder"
    UNREADABLE = "unreadable"


@dataclass(frozen=True)
class KavitaView:
    series: str
    source: SeriesSource
    title: str = ""


def kavita_view(path: Path) -> KavitaView:
    suffix = path.suffix.lower()
    if suffix == ".pdf":
        return _pdf_view(path)
    if suffix == ".epub":
        return _epub_view(path)
    raise ValueError(f"Kavita doesn't read {suffix.lstrip('.')} files: {path}")


# --- PDF


def _pdf_view(path: Path) -> KavitaView:
    folder = path.parent.name
    try:
        metadata = _pdf_metadata(path)
    except (pikepdf.PdfError, ET.ParseError):
        return KavitaView(folder, SeriesSource.UNREADABLE)

    title = metadata.get("Title") or ""
    series = (metadata["Series"] if metadata.get("Series") is not None else title).strip()

    if series:
        source = SeriesSource.SERIES if metadata.get("Series", "").strip() else SeriesSource.TITLE
        name = series
    elif title:
        source, name = SeriesSource.FILENAME, path.stem
    else:
        source, name = SeriesSource.FOLDER, folder

    if name.lower().endswith(".pdf"):
        name = name[: -len(".pdf")]
    return KavitaView(name, source, title)


def _pdf_metadata(path: Path) -> dict[str, str]:
    metadata: dict[str, str] = {}
    with pikepdf.open(path) as pdf:
        if pdf.get_warnings():
            # pikepdf only opened the file by reconstructing it; Kavita's reader doesn't.
            raise pikepdf.PdfError(f"damaged PDF structure: {path}")
        info = pdf.trailer.get("/Info")
        if info is not None and "/Title" in info:
            metadata["Title"] = str(info["/Title"])
        if "/Metadata" in pdf.Root:
            _read_xmp(pdf.Root.Metadata.read_bytes(), metadata)
    return metadata


def _read_xmp(packet: bytes, metadata: dict[str, str]) -> None:
    """Reads the XMP fields Kavita uses; present fields override the Info dictionary."""
    start = packet.find(b"<x:xmpmeta")
    root = ET.fromstring(packet[start:] if start >= 0 else packet)

    title = _first_text(root, f"{_DC}title")
    if title is not None:
        metadata["Title"] = title

    for series in root.iter(f"{_CALIBRE}series"):
        value = series.find(f"{_RDF}value")
        if value is not None:
            metadata["Series"] = "".join(value.itertext())
            break


def _first_text(root: ET.Element, tag: str) -> str | None:
    elements = list(root.iter(tag))
    for element in elements:
        for item in element.iter(f"{_RDF}li"):
            return "".join(item.itertext())
    if elements:
        return "".join(elements[0].itertext())
    return None


# --- EPUB


def _epub_view(path: Path) -> KavitaView:
    try:
        package = _epub_package(path)
    except (zipfile.BadZipFile, KeyError, ET.ParseError, OSError):
        return KavitaView("", SeriesSource.UNREADABLE)

    metadata = package.find(f"{_OPF}metadata")
    if metadata is None:
        return KavitaView("", SeriesSource.UNREADABLE)

    title_element = metadata.find(f"{_DC}title")
    title = "".join(title_element.itertext()).strip() if title_element is not None else ""

    series, index = "", ""
    for meta in metadata.iter(f"{_OPF}meta"):
        name, prop = meta.get("name"), meta.get("property")
        if name == "calibre:series":
            series = meta.get("content", "")
        elif name == "calibre:series_index":
            index = meta.get("content", "")
        elif prop == "belongs-to-collection":
            series = "".join(meta.itertext())
        elif prop == "group-position":
            index = "".join(meta.itertext())

    if series and index:
        return KavitaView(series.strip(), SeriesSource.SERIES, title)
    if not title:
        return KavitaView("", SeriesSource.UNREADABLE)
    return KavitaView(title, SeriesSource.TITLE, title)


def _epub_package(path: Path) -> ET.Element:
    with zipfile.ZipFile(path) as epub:
        container = ET.fromstring(epub.read("META-INF/container.xml"))
        rootfile = container.find(f".//{_CONTAINER}rootfile")
        if rootfile is None:
            raise KeyError("rootfile")
        opf_path = str(PurePosixPath(rootfile.get("full-path", "")))
        return ET.fromstring(epub.read(opf_path))
