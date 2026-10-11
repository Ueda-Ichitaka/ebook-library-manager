"""About this file: checks online candidates against the book's own evidence and decides
whether a book's metadata can be accepted automatically.

Online sources sometimes return the wrong book or authors, so a candidate is only
accepted without review on strict evidence (the user's rule):
- its ISBN matches an ISBN found in the book (first pages' text, filename or .opf), or
- its main title appears as a phrase in the first pages and its first author's surname
  appears there too.
The best acceptable candidate wins; fields it lacks are filled from the local metadata.
Without an acceptable candidate the book goes to review with its local metadata as the
proposal, and all candidates ranked for the reviewer.

Sources sometimes return junk author strings, so for an author whose surname matches a
local author, the local spelling is used unless the candidate's full name is printed on
the first pages; a bare surname always yields to a fuller local name. When the accepted
title has little in common with the local title (filename or .opf), the file contains a
different book than its name says; the decision marks it as mislabeled.
"""

import enum
import re
import unicodedata
from dataclasses import dataclass, fields, replace
from difflib import SequenceMatcher

from ebookprep.evidence import Evidence, isbn_forms
from ebookprep.metadata import BookMetadata, is_junk_title
from ebookprep.naming import main_title
from ebookprep.sources import Candidate


class Verdict(enum.Enum):
    ACCEPT = "accept"
    REVIEW = "review"


@dataclass(frozen=True)
class Assessment:
    candidate: Candidate
    isbn_match: bool
    title_in_text: bool
    author_in_text: bool
    # Word-level similarity of the candidate's and the local main title (0..1).
    title_similarity: float

    @property
    def auto_acceptable(self) -> bool:
        return self.isbn_match or (self.title_in_text and self.author_in_text)

    @property
    def score(self) -> float:
        return 4 * self.isbn_match + 2 * self.title_in_text + self.author_in_text + self.title_similarity


@dataclass(frozen=True)
class Decision:
    verdict: Verdict
    metadata: BookMetadata
    # "local" or the name of the source whose candidate was chosen
    chosen_source: str
    # all candidates, best first
    assessments: tuple[Assessment, ...]
    # the file contains a different book than its filename or .opf says
    mislabeled: bool = False


def assess(candidate: Candidate, local: BookMetadata, evidence: Evidence) -> Assessment:
    text_words = _words(evidence.text)
    title_words = _words(main_title(candidate.metadata.title))
    surname = _words(candidate.metadata.authors[0])[-1:] if candidate.metadata.authors else []

    known_isbns = set(evidence.isbns) | (isbn_forms(local.isbn) if local.isbn else set())
    candidate_isbns = isbn_forms(candidate.metadata.isbn) if candidate.metadata.isbn else set()

    return Assessment(
        candidate=candidate,
        isbn_match=bool(candidate_isbns & known_isbns),
        title_in_text=bool(title_words) and _contains_phrase(text_words, title_words),
        author_in_text=bool(surname) and len(surname[0]) > 1 and surname[0] in text_words,
        title_similarity=SequenceMatcher(None, title_words, _words(main_title(local.title))).ratio(),
    )


def decide(local: BookMetadata, candidates: list[Candidate], evidence: Evidence) -> Decision:
    ranked = tuple(sorted((assess(c, local, evidence) for c in candidates), key=lambda a: -a.score))
    acceptable = [a for a in ranked if a.auto_acceptable]
    if not acceptable:
        return Decision(Verdict.REVIEW, local, "local", ranked)
    best = acceptable[0].candidate
    metadata = fill_gaps(best.metadata, local)
    metadata = replace(metadata, authors=_prefer_local_spelling(metadata.authors, local.authors, _words(evidence.text)))
    return Decision(
        Verdict.ACCEPT, metadata, best.source, ranked, mislabeled=content_differs(local.title, metadata.title)
    )


def content_differs(local_title: str, accepted_title: str) -> bool:
    """Whether two titles name different books (a junk local title says nothing)."""
    if is_junk_title(local_title):
        return False
    local, accepted = _significant(local_title), _significant(accepted_title)
    if not local or not accepted:
        return False
    overlap = len(local & accepted) / min(len(local), len(accepted))
    if overlap >= 0.5:
        return False
    # Titles written without spaces ("FormDerWissenschaftlichenAusarbeitung") share no words.
    compact = SequenceMatcher(None, "".join(_words(local_title)), "".join(_words(accepted_title))).ratio()
    return compact < 0.6


_STOPWORDS = frozenset(
    "a an and the of for to in on with by from der die das und mit von zu den des ein eine im fur".split()
)


def _significant(title: str) -> set[str]:
    return {w for w in _words(title) if w not in _STOPWORDS}


def _prefer_local_spelling(authors: tuple[str, ...], local: tuple[str, ...], text_words: list[str]) -> tuple[str, ...]:
    result = []
    for author in authors:
        words = _words(author)
        same_person = next((l for l in local if words and _words(l)[-1:] == words[-1:]), None)
        only_surname = len(words) == 1 and same_person is not None and len(_words(same_person)) > 1
        if same_person and same_person != author and (only_surname or not _contains_phrase(text_words, words)):
            result.append(same_person)
        else:
            result.append(author)
    return tuple(dict.fromkeys(result))


def fill_gaps(chosen: BookMetadata, local: BookMetadata) -> BookMetadata:
    gaps = {f.name: getattr(local, f.name) for f in fields(BookMetadata) if not getattr(chosen, f.name)}
    return replace(chosen, **gaps)


def _words(text: str) -> list[str]:
    without_accents = "".join(c for c in unicodedata.normalize("NFKD", text.casefold()) if not unicodedata.combining(c))
    return re.findall(r"[^\W_]+", without_accents)


def _contains_phrase(haystack: list[str], needle: list[str]) -> bool:
    size = len(needle)
    return any(haystack[i : i + size] == needle for i in range(len(haystack) - size + 1))
