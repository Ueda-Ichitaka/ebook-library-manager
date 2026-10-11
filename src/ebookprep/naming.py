"""About this file: builds output names for book folders and files.

A book is named "Main title - First author": the subtitle after the first ":" or " - "
is cut and only the first author is used, keeping names short. "+" is written as "plus"
(C++ becomes Cplusplus), since calibre can't export it. The full title stays in the
embedded metadata. Names are made safe for Linux and Windows/SMB filesystems, and kept
unique within a folder (compared case-insensitively) by adding a qualifier such as the
edition or year, or a counter. Names avoid everything calibre's export would rewrite
(see calibre.sanitize_file_name), because the exported files must carry the exact name.
"""

import re
import unicodedata

# Leaves room for the file extension within the 255-byte name limit of common filesystems.
MAX_NAME_BYTES = 200

_REMOVED_CHARS = re.compile(r'[?*"<>|:]')
_SEPARATOR_CHARS = re.compile(r"[/\\]")


def main_title(title: str) -> str:
    main = re.split(r":| - ", title, maxsplit=1)[0].strip()
    return main or title.strip()


def book_name(title: str, authors: tuple[str, ...]) -> str:
    main = main_title(title)
    if authors:
        return safe_name(f"{main} - {authors[0]}")
    return safe_name(main)


def safe_name(raw: str) -> str:
    name = unicodedata.normalize("NFC", raw)
    name = _SEPARATOR_CHARS.sub("-", name)
    name = name.replace("+", "plus")
    name = _REMOVED_CHARS.sub("", name)
    name = re.sub(r"\.{2,}", ".", name)
    name = re.sub(r"\s+", " ", name).strip().lstrip(". ")
    name = _cap_length(name)
    return name.rstrip(". ")


def _cap_length(name: str) -> str:
    if len(name.encode("utf-8")) <= MAX_NAME_BYTES:
        return name
    cut = name.encode("utf-8")[:MAX_NAME_BYTES].decode("utf-8", errors="ignore")
    if " " in cut:
        cut = cut.rsplit(" ", 1)[0]
    return cut.rstrip()


def unique_book_name(title: str, authors: tuple[str, ...], taken: set[str], qualifier: str | None = None) -> str:
    """Returns a name for the book that isn't in taken (a set of casefolded names)."""
    main = main_title(title)
    candidates = [book_name(title, authors)]
    if qualifier:
        candidates.append(book_name(f"{main} ({qualifier})", authors))

    for candidate in candidates:
        if candidate.casefold() not in taken:
            return candidate

    counter = 2
    while (candidate := book_name(f"{main} ({counter})", authors)).casefold() in taken:
        counter += 1
    return candidate
