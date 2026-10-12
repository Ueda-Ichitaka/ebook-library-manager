"""About this file: online metadata sources behind one interface.

A source takes a LookupQuery (title, authors, ISBN) and returns candidates: complete
metadata proposals from one place. New sources only need a name and a search method,
and are added to the list of sources the pipeline uses. lookup_all asks every
source and collects their candidates; a failing source (network error, rate limit) is
recorded as an error and doesn't stop the others. Author names are normalized, since
some sources return them as "Last, First", and publication dates are cut to the plain
date (sources like Google return full timestamps; calibre's placeholder year 101 is
dropped).
"""

import re
from collections.abc import Iterable
from dataclasses import dataclass, replace
from typing import Protocol

from ebookprep.filenames import parse_authors
from ebookprep.metadata import BookMetadata


class SourceError(Exception):
    """Raised by a source that couldn't be queried (network error, rate limit, bad answer)."""


@dataclass(frozen=True)
class LookupQuery:
    title: str
    authors: tuple[str, ...] = ()
    isbn: str | None = None


@dataclass(frozen=True)
class Candidate:
    source: str
    metadata: BookMetadata
    cover_url: str | None = None


class MetadataSource(Protocol):
    name: str

    def search(self, query: LookupQuery) -> list[Candidate]: ...


@dataclass(frozen=True)
class LookupResult:
    candidates: tuple[Candidate, ...]
    # (source name, error message) for every source that failed
    errors: tuple[tuple[str, str], ...]


def lookup_all(sources: Iterable[MetadataSource], query: LookupQuery) -> LookupResult:
    candidates: list[Candidate] = []
    errors: list[tuple[str, str]] = []
    for source in sources:
        try:
            candidates.extend(normalize_candidate(c) for c in source.search(query))
        except SourceError as error:
            errors.append((source.name, str(error)))
    return LookupResult(candidates=tuple(candidates), errors=tuple(errors))


def normalize_candidate(candidate: Candidate) -> Candidate:
    authors = tuple(dict.fromkeys(a for name in candidate.metadata.authors for a in parse_authors(name)))
    pubdate = plain_date(candidate.metadata.pubdate)
    return replace(candidate, metadata=replace(candidate.metadata, authors=authors, pubdate=pubdate))


def plain_date(pubdate: str | None) -> str | None:
    """"2010-09-21T07:00:00+00:00" -> "2010-09-21"; placeholders and non-dates -> None."""
    match = re.match(r"(\d{4})(-\d{2}(-\d{2})?)?", pubdate or "")
    if not match or int(match.group(1)) < 1000:
        return None
    return match.group(0)
