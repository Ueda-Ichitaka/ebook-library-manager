"""About this file: shared test helpers that build small synthetic PDF and EPUB files.

The tests never use books from the real library. The helpers can reproduce the damage
found there: a PDF without a trailer, which calibre's PDF backend can't write metadata
into, while pikepdf can still read and repair it; and a damaged download, whose start the
download site's rate-limit message overwrote.
"""

import zipfile
from pathlib import Path

import pikepdf


def make_pdf(path: Path, title: str | None = None, pages: int = 1) -> Path:
    pdf = pikepdf.new()
    for _ in range(pages):
        pdf.add_blank_page()
    if title is not None:
        pdf.docinfo["/Title"] = title
    pdf.save(path)
    return path


def damage_trailer(path: Path) -> Path:
    """Removes the trailer, the damage that makes calibre's PDF writer fail."""
    data = path.read_bytes()
    path.write_bytes(data[: data.rfind(b"trailer")] + b"%%EOF\n")
    return path


def make_text_pdf(path: Path, pages: list[list[str]]) -> Path:
    """A PDF whose pages contain the given text lines (Helvetica), readable by pdftotext."""
    pdf = pikepdf.new()
    font = pdf.make_indirect(
        pikepdf.Dictionary(Type=pikepdf.Name.Font, Subtype=pikepdf.Name.Type1, BaseFont=pikepdf.Name.Helvetica)
    )
    for lines in pages:
        commands = ["BT", "/F1 14 Tf", "72 720 Td", "16 TL"]
        for line in lines:
            escaped = line.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
            commands.append(f"({escaped}) Tj T*")
        commands.append("ET")
        page = pdf.add_blank_page()
        page.Resources = pikepdf.Dictionary(Font=pikepdf.Dictionary(F1=font))
        page.Contents = pdf.make_stream("\n".join(commands).encode("latin-1"))
    pdf.save(path)
    return path


def make_epub(path: Path, title: str, extra_metadata: str = "", body: str = "<p>x</p>") -> Path:
    opf = f"""<?xml version="1.0" encoding="utf-8"?>
<package xmlns="http://www.idpf.org/2007/opf" version="2.0" unique-identifier="uid">
  <metadata xmlns:dc="http://purl.org/dc/elements/1.1/" xmlns:opf="http://www.idpf.org/2007/opf">
    <dc:title>{title}</dc:title>
    <dc:identifier id="uid">urn:test:{title}</dc:identifier>
    <dc:language>en</dc:language>
    {extra_metadata}
  </metadata>
  <manifest><item id="c1" href="c1.xhtml" media-type="application/xhtml+xml"/></manifest>
  <spine><itemref idref="c1"/></spine>
</package>"""
    container = """<?xml version="1.0"?>
<container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container">
  <rootfiles><rootfile full-path="OEBPS/content.opf" media-type="application/oebps-package+xml"/></rootfiles>
</container>"""
    chapter = f'<html xmlns="http://www.w3.org/1999/xhtml"><head><title>c</title></head><body>{body}</body></html>'
    with zipfile.ZipFile(path, "w") as epub:
        epub.writestr("mimetype", "application/epub+zip", compress_type=zipfile.ZIP_STORED)
        epub.writestr("META-INF/container.xml", container)
        epub.writestr("OEBPS/content.opf", opf)
        epub.writestr("OEBPS/c1.xhtml", chapter)
    return path


UNMERGEABLE_XMP = b"""<?xpacket begin="\xef\xbb\xbf" id="W5M0MpCehiHzreSzNTczkc9d"?>
<x:xmpmeta xmlns:x="adobe:ns:meta/"><rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#">
<rdf:Description rdf:about="" xmlns:dc="http://purl.org/dc/elements/1.1/">
<dc:title><rdf:Alt><rdf:li xml:lang="x-default">Publisher Title</rdf:li></rdf:Alt></dc:title>
<PublisherSpec>1.28</PublisherSpec>
</rdf:Description></rdf:RDF></x:xmpmeta><?xpacket end="w"?>"""


def make_pdf_with_unmergeable_xmp(path: Path) -> Path:
    """A PDF whose XMP has an element without namespace. calibre's merge_xmp_packet raises
    KeyError on it and calibre silently skips writing the series (seen in the library)."""
    pdf = pikepdf.new()
    pdf.add_blank_page()
    stream = pikepdf.Stream(pdf, UNMERGEABLE_XMP)
    stream.Type = pikepdf.Name.Metadata
    stream.Subtype = pikepdf.Name.XML
    pdf.Root.Metadata = pdf.make_indirect(stream)
    pdf.save(path)
    return path


# The message as found at the start of two damaged files in the library (154 bytes).
MESSAGE = (
    b"Too many downloads at the same time from the same IP address... Visit annas-archive.org, .li, or .se to "
    b"become a member and get more concurrent downloads."
)


def overwrite_start(path: Path) -> Path:
    data = path.read_bytes()
    path.write_bytes(MESSAGE + data[len(MESSAGE):])
    return path


def pdf_with_long_catalog(path: Path) -> Path:
    """A PDF whose catalog (object 1, right after the header) is longer than the message,
    like the damaged Handbook of Attachment."""
    make_text_pdf(path, [["Handbook of Attachment"], ["Chapter 1"]])
    with pikepdf.open(path, allow_overwriting_input=True) as pdf:
        pdf.Root.OutputIntents = pikepdf.Array([pikepdf.Dictionary(
            Type=pikepdf.Name.OutputIntent, S=pikepdf.Name.GTS_PDFA1,
            OutputConditionIdentifier=pikepdf.String("sRGB IEC61966-2.1 " * 6),
        )])
        pdf.Root.ViewerPreferences = pikepdf.Dictionary(Direction=pikepdf.Name.L2R)
        pdf.save(path, object_stream_mode=pikepdf.ObjectStreamMode.disable, normalize_content=False, linearize=False)
    data = path.read_bytes()
    assert data.index(b"1 0 obj") < len(MESSAGE) < data.index(b"endobj"), "catalog must span the damaged range"
    return path
