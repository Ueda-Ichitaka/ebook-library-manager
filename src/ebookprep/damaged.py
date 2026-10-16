"""About this file: recognises damaged downloads in the input and repairs what can be fully
restored.

Two library files turned out to start with the download site's rate-limit message ("Too
many downloads at the same time from the same IP address... concurrent downloads."),
written over the first 154 bytes of the real file. The rest of such a file is intact. A
file counts as a damaged download when
- it starts with that message, or
- its name carries an MD5 (Anna's Archive names: "-- <32 hex digits> --"), its content
  has a different MD5, and it can't be read as PDF or EPUB. A different MD5 alone is no
  damage: files whose metadata was edited later differ as well.

Repair (into a new file; the input is never modified) is only done when nothing of the
book's content is lost:
- PDF: the header and the start of the object the message ran into are rebuilt. The
  object's remaining text tells which containers (dictionaries, arrays) were opened in
  the lost part; they are opened again, and the lost key-value pairs are dropped (a key
  /EbookprepLost holds what is left of them and is removed). Objects that lay entirely in
  the overwritten range would be lost, so the repair stops then. Only classic
  cross-reference tables are supported. The result must open with pikepdf with a catalog
  and pages.
- EPUB: the zip is written again from its central directory. A lost "mimetype" entry is
  always "application/epub+zip" and a lost META-INF/container.xml only points to the
  package file, so both are rebuilt; any other lost entry (e.g. a chapter) makes the
  repair fail, naming the lost entries.

check_downloads inspects files (only again when their size or modification time
changed), stores the result in the state and tells for every damaged file whether it can
be repaired; the build repairs those (see build.py) and skips the others.
"""

import hashlib
import io
import re
import tempfile
import zipfile
import zlib
from dataclasses import dataclass
from pathlib import Path

import pikepdf

from ebookprep.state import DownloadCheck, DownloadDamage, StateStore

RATE_LIMIT_MESSAGE = b"Too many downloads at the same time from the same IP address."
_MESSAGE_END = b"concurrent downloads."
_MD5_IN_NAME = re.compile(r"-- ([0-9a-f]{32}) --")
_LOST_KEY = b"/EbookprepLost"
_CONTAINER_XML = (
    '<?xml version="1.0" encoding="UTF-8"?>\n'
    '<container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container">\n'
    '  <rootfiles>\n    <rootfile full-path="{}" media-type="application/oebps-package+xml"/>\n  </rootfiles>\n'
    "</container>\n"
)


class DownloadRepairError(Exception):
    """Raised when a damaged download can't be restored completely."""


@dataclass(frozen=True)
class _Finding:
    damage: str
    repairable: bool
    note: str


def overwritten_length(data: bytes) -> int | None:
    """How many bytes at the start the rate-limit message replaced; None if it isn't there,
    0 if it is there but its end can't be found."""
    if not data.startswith(RATE_LIMIT_MESSAGE):
        return None
    end = data.find(_MESSAGE_END, 0, 1000)
    return end + len(_MESSAGE_END) if end >= 0 else 0


def repair_download(src: Path, dst: Path) -> str:
    """Writes the repaired file to dst and returns what was restored."""
    data = src.read_bytes()
    length = overwritten_length(data)
    if not length:
        raise DownloadRepairError("no rate-limit message of known length at the start of the file")
    suffix = src.suffix.lower()
    if suffix == ".pdf":
        repaired, note = _repair_pdf(data, length)
    elif suffix == ".epub":
        repaired, note = _repair_epub(data)
    else:
        raise DownloadRepairError(f"{suffix} files can't be repaired")
    dst.write_bytes(repaired)
    return note


def check_downloads(input_root: Path, store: StateStore, paths) -> dict[str, DownloadDamage]:
    """The damaged downloads among the paths (relative to the input root); files are only
    inspected again when their size or modification time changed."""
    known = store.download_checks()
    checked = []
    for path in paths:
        stat = (input_root / path).stat()
        previous = known.get(path)
        if previous is not None and (previous.size, previous.mtime_ns) == (stat.st_size, stat.st_mtime_ns):
            continue
        finding = _inspect(input_root / path)
        checked.append(DownloadCheck(
            path, stat.st_size, stat.st_mtime_ns,
            finding.damage if finding else None, bool(finding and finding.repairable), finding.note if finding else "",
        ))
    store.save_download_checks(checked)
    wanted = set(paths)
    return {path: d for path, d in store.download_damage().items() if path in wanted}


def _inspect(path: Path) -> _Finding | None:
    with path.open("rb") as handle:
        start = handle.read(1000)
    md5 = _MD5_IN_NAME.search(path.name)
    redownload = f"re-download the file with MD5 {md5.group(1)}" if md5 else "re-download the file"
    if overwritten_length(start) is not None:
        damage = "the download site's rate-limit message overwrote the start of the file"
        with tempfile.TemporaryDirectory() as tmp:
            try:
                note = repair_download(path, Path(tmp) / f"repaired{path.suffix.lower()}")
            except DownloadRepairError as error:
                return _Finding(damage, False, f"{error}; {redownload}")
        return _Finding(damage, True, note)
    if md5 and _md5_of(path) != md5.group(1) and not _readable(path):
        return _Finding("MD5 differs from the one in the filename and the file can't be read", False, redownload)
    return None


def _md5_of(path: Path) -> str:
    digest = hashlib.md5()
    with path.open("rb") as handle:
        while chunk := handle.read(1 << 20):
            digest.update(chunk)
    return digest.hexdigest()


def _readable(path: Path) -> bool:
    suffix = path.suffix.lower()
    try:
        if suffix == ".pdf":
            with pikepdf.open(path) as pdf:
                return len(pdf.pages) > 0
        if suffix == ".epub":
            with zipfile.ZipFile(path) as z:
                return z.testzip() is None
    except (pikepdf.PdfError, zipfile.BadZipFile, OSError, ValueError):
        return False
    return True


def _xref_offsets(data: bytes) -> dict[int, int]:
    """Object number -> offset, from the classic cross-reference table at startxref."""
    starts = re.findall(rb"startxref\s+(\d+)", data[-2048:])
    if not starts:
        raise DownloadRepairError("no startxref at the end of the PDF")
    pos = int(starts[-1])
    if not data[pos:].startswith(b"xref"):
        raise DownloadRepairError("cross-reference stream PDFs can't be repaired")
    offsets = {}
    pos += 4
    while True:
        section = re.match(rb"\s*(\d+) (\d+)[ \t]*\r?\n", data[pos:])
        if not section:
            break
        first, count = int(section.group(1)), int(section.group(2))
        pos += section.end()
        for i in range(count):
            entry = data[pos + 20 * i : pos + 20 * i + 20]
            if entry[17:18] == b"n":
                offsets[first + i] = int(entry[:10])
        pos += 20 * count
    return offsets


def _unmatched_closers(text: bytes) -> list[bytes]:
    """The containers closed in text that were opened before it, innermost first."""
    stack: list[bytes] = []
    closers: list[bytes] = []
    i = 0
    while i < len(text):
        if text[i : i + 1] == b"(":
            depth, i = 1, i + 1
            while i < len(text) and depth:
                if text[i : i + 1] == b"\\":
                    i += 2
                    continue
                depth += {b"(": 1, b")": -1}.get(text[i : i + 1], 0)
                i += 1
            continue
        pair = text[i : i + 2]
        if pair in (b"<<", b">>"):
            if pair == b"<<":
                stack.append(b"<<")
            elif stack:
                stack.pop()
            else:
                closers.append(b"<<")
            i += 2
            continue
        char = text[i : i + 1]
        if char == b"<":
            i = text.find(b">", i) + 1 or len(text)
            continue
        if char == b"[":
            stack.append(b"[")
        elif char == b"]":
            if stack:
                stack.pop()
            else:
                closers.append(b"[")
        i += 1
    return closers


def _repair_pdf(data: bytes, length: int) -> tuple[bytes, str]:
    offsets = _xref_offsets(data)
    damaged = sorted((offset, number) for number, offset in offsets.items() if offset < length)
    if len(damaged) != 1:
        lost = [number for _, number in damaged[:-1]]
        raise DownloadRepairError(f"objects {lost} lay entirely in the overwritten part" if lost else "no object found")
    offset, number = damaged[0]
    end = data.find(b"endobj", length)
    rest = data[length:end]
    containers = list(reversed(_unmatched_closers(rest)))
    opener = b""
    for index, container in enumerate(containers):
        opener += container
        if container == b"<<" and index + 1 < len(containers):
            opener += _LOST_KEY
    header = b"%PDF-1.7\n%\xe2\xe3\xcf\xd3\n"
    body = f"{number} 0 obj".encode()
    leads = [b"/", _LOST_KEY + b" /"] if rest[:1].isalpha() else [_LOST_KEY + b" "]
    for lead in leads:
        fill = length - offset - len(body) - len(opener) - len(lead)
        if offset < len(header) or fill < 1:
            raise DownloadRepairError("the overwritten part is too short to rebuild the header")
        candidate = header.ljust(offset, b" ") + body + b" " * fill + opener + lead + data[length:]
        try:
            with pikepdf.open(io.BytesIO(candidate)) as pdf:
                if pdf.Root.get("/Type") != pikepdf.Name.Catalog or len(pdf.pages) == 0:
                    continue
                repaired_object = pdf.get_object((number, 0))
                if isinstance(repaired_object, pikepdf.Dictionary) and _LOST_KEY.decode() in repaired_object:
                    del repaired_object[_LOST_KEY.decode()]
                out = io.BytesIO()
                pdf.save(out)
                return out.getvalue(), f"header and the start of object {number} rebuilt, {len(pdf.pages)} pages"
        except pikepdf.PdfError:
            continue
    raise DownloadRepairError(f"object {number} couldn't be rebuilt")


def _repair_epub(data: bytes) -> tuple[bytes, str]:
    try:
        source = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile as error:
        raise DownloadRepairError(f"zip directory unreadable: {error}") from error
    entries: list[tuple[str, bytes]] = []
    unreadable = []
    for info in source.infolist():
        try:
            entries.append((info.filename, source.read(info)))
        except (zipfile.BadZipFile, zlib.error, EOFError):
            unreadable.append(info.filename)
    rebuilt = []
    lost = []
    for name in unreadable:
        if name == "mimetype":
            entries.append((name, b"application/epub+zip"))
        elif name == "META-INF/container.xml":
            opf = next((n for n, _ in entries if n.endswith(".opf")), None)
            if opf is None:
                lost.append(name)
                continue
            entries.append((name, _CONTAINER_XML.format(opf).encode()))
        else:
            lost.append(name)
            continue
        rebuilt.append(name)
    if lost:
        raise DownloadRepairError(f"lost content: {', '.join(lost)}")
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w") as target:
        target.writestr("mimetype", dict(entries).get("mimetype", b"application/epub+zip"), zipfile.ZIP_STORED)
        for name, content in entries:
            if name != "mimetype":
                target.writestr(name, content, zipfile.ZIP_DEFLATED)
    return out.getvalue(), f"zip rebuilt; restored {', '.join(rebuilt) or 'nothing'}"
