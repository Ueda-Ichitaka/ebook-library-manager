"""About this file: extracts title, authors, year and ISBN from book filenames.

The result is evidence for identifying a book, not the final metadata: filenames in the
library follow several conventions and some are ambiguous. Supported conventions:

- Anna's Archive: "Title -- Authors -- edition/place/date -- publisher -- isbn -- md5 -- Anna's Archive"
  (fields vary, the end is often truncated)
- libgen style: "Authors - Title (Year, Publisher)", sometimes followed by " - libgen.li"
- calibre export: "Title - Authors", authors possibly with a "[sort name]" suffix
- snake_case names, optionally "title_by_author"
- plain titles

Characters that are invalid in filenames were replaced by the tools that created them,
e.g. ":" became "_" or " _ " and "." in initials became "_"; cleaning reverses that.
"""

import re
from dataclasses import dataclass


@dataclass(frozen=True)
class FilenameInfo:
    title: str
    authors: tuple[str, ...] = ()
    year: int | None = None
    isbn: str | None = None


_MD5 = re.compile(r"^[0-9a-f]{32}$", re.IGNORECASE)
_YEAR = re.compile(r"(?<!\d)(19\d{2}|20\d{2})(?!\d)")
_ISBN_CANDIDATE = re.compile(r"(?<![\dX])(97[89]\d{10}|\d{9}[\dX])(?![\dX])")
_LEADING_BRACKETS = re.compile(r"^(\s*\[[^\]]*\])+\s*")
_LIBGEN = re.compile(r"^(?P<authors>.+?) - (?P<title>.+?) \((?P<year>\d{4})(?:,.*)?\)$")
_SOURCE_TAG = re.compile(r"\s+-\s+(libgen\.\w+|z-lib(\.\w+)?)$", re.IGNORECASE)
_BRACKETS = re.compile(r"\s*\[[^\]]*\]")
_PARENTHESES = re.compile(r"\([^)]*\)")
_WATERMARK = re.compile(r"\s*\(for [^)]*\)")
_NAME_SUFFIX = re.compile(r"^(jr\.?|sr\.?|ii|iii|iv)$", re.IGNORECASE)
_AUTHOR_ROLE = re.compile(
    r"^(authors?|editors?|technical editor|translator|illustrator|et al[._]?|eds?\.?)$", re.IGNORECASE
)


def parse_filename(stem: str) -> FilenameInfo:
    stem = stem.strip()
    if " -- " in stem:
        return _parse_annas_archive(stem)

    stem = _SOURCE_TAG.sub("", stem)
    libgen = _LIBGEN.match(stem)
    if libgen:
        return FilenameInfo(
            title=clean_title(libgen["title"]),
            authors=parse_authors(libgen["authors"]),
            year=int(libgen["year"]),
        )

    if " - " in stem:
        title, authors = stem.rsplit(" - ", 1)
        return FilenameInfo(title=clean_title(title), authors=parse_authors(authors))

    if " " not in stem and "_" in stem:
        if "_by_" in stem:
            title, author = stem.split("_by_", 1)
            return FilenameInfo(title=clean_title(title.replace("_", " ")), authors=parse_authors(author.replace("_", " ")))
        return FilenameInfo(title=clean_title(stem.replace("_", " ")))

    return FilenameInfo(title=clean_title(stem))


def _parse_annas_archive(stem: str) -> FilenameInfo:
    leading = _LEADING_BRACKETS.match(stem)
    leading_text = leading.group(0) if leading else ""
    parts = [part.strip() for part in stem[len(leading_text):].split(" -- ")]

    year: int | None = None
    isbn: str | None = None
    for part in parts[2:]:
        if _MD5.match(part) or part.startswith("Anna"):
            continue
        isbn = isbn or _find_isbn(part)
        if year is None and (match := _YEAR.search(part)):
            year = int(match.group(1))

    return FilenameInfo(
        title=clean_title(parts[0]),
        authors=parse_authors(parts[1]) if len(parts) > 1 else (),
        year=year,
        isbn=isbn or _find_isbn(leading_text),
    )


def _find_isbn(text: str) -> str | None:
    for candidate in _ISBN_CANDIDATE.findall(text.replace("-", "")):
        if is_valid_isbn(candidate):
            return candidate
    return None


def clean_title(raw: str) -> str:
    title = _BRACKETS.sub("", raw)
    title = _WATERMARK.sub("", title)
    title = re.sub(r"\s+_\s+", ": ", title)
    title = re.sub(r"(?<=\S)_\s+", ": ", title)
    title = re.sub(r"(?<=\w)- ", ": ", title)
    title = re.sub(r"_+$", "", title)
    return re.sub(r"\s+", " ", title).strip()


def parse_authors(raw: str) -> tuple[str, ...]:
    text = _PARENTHESES.sub("", _BRACKETS.sub("", raw))
    authors: list[str] = []
    for chunk in re.split(r"[;&]", text):
        parts = [_clean_name(part) for part in chunk.split(",")]
        parts = [p for p in parts if p and not _AUTHOR_ROLE.match(p) and not p[0].isdigit()]
        authors.extend(_combine_name_parts(parts))

    unique = dict.fromkeys(a for a in authors if not a[0].islower())
    return tuple(unique)


def _clean_name(raw: str) -> str:
    name = re.sub(r"^\s*By\s+", "", raw)
    name = re.sub(r"(?<![A-Za-z])([A-Z])_", r"\1.", name)
    name = re.sub(r"(?<=\w)_+$", "", name.strip())
    return re.sub(r"\s+", " ", name).strip()


def _combine_name_parts(parts: list[str]) -> list[str]:
    """Turns ["Last", "First", ...] pairs into "First Last"; other lists are separate names.
    A name suffix part ("Jr.", "II") is attached to the name before it."""
    suffixes = [p for p in parts if _NAME_SUFFIX.match(p)]
    names = _pair_names([p for p in parts if not _NAME_SUFFIX.match(p)])
    if suffixes and names:
        names[-1] = f"{names[-1]} {' '.join(suffixes)}"
    return names


def _pair_names(parts: list[str]) -> list[str]:
    is_last_first_list = (
        len(parts) >= 2 and len(parts) % 2 == 0 and all(len(parts[i].split()) == 1 for i in range(0, len(parts), 2))
    )
    if is_last_first_list:
        return [f"{parts[i + 1]} {parts[i]}" for i in range(0, len(parts), 2)]
    return parts


def is_valid_isbn(isbn: str) -> bool:
    if re.fullmatch(r"97[89]\d{10}", isbn):
        return sum(int(c) * (1 if i % 2 == 0 else 3) for i, c in enumerate(isbn)) % 10 == 0
    if re.fullmatch(r"\d{9}[\dX]", isbn):
        return sum((10 - i) * (10 if c == "X" else int(c)) for i, c in enumerate(isbn)) % 11 == 0
    return False
