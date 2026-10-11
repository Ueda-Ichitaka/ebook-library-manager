"""About this file: rewrites a PDF with a clean structure.

About 40% of the library's PDFs have a damaged structure (e.g. a missing trailer).
calibre's PDF backend can't write metadata into those, but still exits with status 0,
and Kavita then falls back to the folder name as series. pikepdf (based on qpdf) can
read and rebuild these files without touching the page content. Encryption that only
needs an owner password is removed, because Kavita can't read encrypted PDFs. The
source file is never modified.

It also removes the XMP metadata packet from a work copy when calibre can't merge into
it (see strip_xmp).
"""

from dataclasses import dataclass
from pathlib import Path

import pikepdf


class PdfRepairError(Exception):
    """Raised when a PDF can't be opened or rewritten."""


@dataclass(frozen=True)
class RepairReport:
    was_damaged: bool
    was_encrypted: bool
    warnings: tuple[str, ...]


def repair_pdf(src: Path, dst: Path) -> RepairReport:
    partial = dst.with_name(dst.name + ".partial")
    try:
        with pikepdf.open(src) as pdf:
            warnings = tuple(pdf.get_warnings())
            was_encrypted = pdf.is_encrypted
            # A trailer rebuilt from a damaged file can lack /Size; qpdf only writes the
            # computed value when the key exists, so a placeholder is added.
            if "/Size" not in pdf.trailer:
                pdf.trailer.Size = 0
            pdf.save(partial, encryption=False)
    except pikepdf.PasswordError as error:
        partial.unlink(missing_ok=True)
        raise PdfRepairError(f"{src} is protected by a password") from error
    except pikepdf.PdfError as error:
        partial.unlink(missing_ok=True)
        raise PdfRepairError(f"{src} can't be read as PDF: {error}") from error

    partial.replace(dst)
    return RepairReport(was_damaged=bool(warnings), was_encrypted=was_encrypted, warnings=warnings)


def strip_xmp(path: Path) -> str | None:
    """Removes the XMP metadata packet from the PDF in place and returns it.

    calibre's PDF writer silently skips the series when it can't merge its values into
    an existing XMP packet; without a packet it writes a fresh one. The removed packet is
    returned so it can be logged in the report. Returns None if there was no packet.
    """
    with pikepdf.open(path, allow_overwriting_input=True) as pdf:
        if "/Metadata" not in pdf.Root:
            return None
        packet = pdf.Root.Metadata.read_bytes().decode("utf-8", errors="replace")
        del pdf.Root["/Metadata"]
        pdf.save(path)
    return packet
