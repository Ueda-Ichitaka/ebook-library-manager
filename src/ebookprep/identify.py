"""About this file: the identify stage, which decides every book's metadata.

For each book (group of source files, see grouping.py) it resolves local metadata (calibre
.opf next to the book, else the filename). Books with an .opf written by calibre were
checked by hand already and are accepted as they are. For all others it reads evidence
from the book's first pages, asks the online sources (by ISBN when one is known, else or
additionally by title and author) and lets scoring.py decide: a candidate is accepted
automatically only on strict evidence, otherwise the book waits for review with its local
metadata as proposal. A file whose content turns out to be a different book than its
name says is marked as mislabeled (it is built outside the library folders). A group formed by similar names always needs review, because the
files might be different books or editions.

rescore re-decides stored books with the current rules from their stored candidates and
freshly read evidence, without online requests; decisions by the user and calibre
sidecars are kept.

Everything is stored in the SQLite state. Files that already belong to a stored book are
skipped, so decisions made in the review UI are never overwritten, and a group split in
the review stays split. An unexpected error in one book is
logged and the book is left for the next run.
"""

import json
from collections.abc import Callable, Sequence
from dataclasses import dataclass, replace
from pathlib import Path

from ebookprep.config import Config
from ebookprep.evidence import Evidence, extract_evidence
from ebookprep.grouping import BookGroup, group_units
from ebookprep.metadata import ResolvedMetadata, resolve_local
from ebookprep.scan import BookFile, scan_input
from ebookprep.scoring import Decision, Verdict, decide
from ebookprep.sources import Candidate, LookupQuery, MetadataSource, lookup_all, normalize_candidate
from ebookprep.state import NewBook, StateStore, StoredCandidate

EXCERPT_CHARS = 3000
EVIDENCE_FORMATS = (".pdf", ".epub")

REASON_SIMILAR_GROUP = "group formed by similar names: check that the files are the same book"
REASON_NO_EVIDENCE = "no candidate with strict evidence (ISBN in the book, or title and author on the first pages)"


@dataclass
class IdentifySummary:
    new: int = 0
    known: int = 0
    accepted: int = 0
    review: int = 0
    failed: int = 0


def identify(
    input_root: Path,
    work_root: Path,
    config: Config,
    sources: Sequence[MetadataSource],
    only: tuple[str, ...] = (),
    log: Callable[[str], None] = print,
) -> IdentifySummary:
    store = StateStore(work_root / "state.db")
    groups = [g for g in group_units(scan_input(input_root, config).units) if selected(g, only)]
    summary = IdentifySummary()
    known = store.known_sources()

    for number, group in enumerate(groups, start=1):
        files = [f for unit in group.units for f in unit.files]
        source_paths = tuple(relative(f.path, input_root) for f in files)
        key = json.dumps(sorted(source_paths), ensure_ascii=False)
        label = f"[{number}/{len(groups)}] {'/'.join(group.placement.parts())}"
        if set(source_paths) <= known:
            summary.known += 1
            continue
        try:
            book = _identify_group(group, files, source_paths, key, sources)
        except Exception as error:  # one broken book must never stop the run
            summary.failed += 1
            log(f"{label}: identify failed ({type(error).__name__}: {error})")
            continue
        store.add_book(book)
        summary.new += 1
        summary.accepted += book.status == "accepted"
        summary.review += book.status == "review"
        log(f"{label}/{book.metadata.title}: {book.status} ({book.decided_by})")

    log(f"identify: {summary}")
    return summary


def _identify_group(
    group: BookGroup,
    files: list[BookFile],
    source_paths: tuple[str, ...],
    key: str,
    sources: Sequence[MetadataSource],
) -> NewBook:
    resolved = group_metadata(group)
    reasons = [REASON_SIMILAR_GROUP] if group.needs_review else []
    common = dict(
        key=key,
        placement="/".join(group.placement.parts()),
        group_reason=group.reason.value,
        book_folder=group.book_folder,
        sources=source_paths,
        local_metadata=resolved.metadata,
        local_source=resolved.source,
        local_notes=resolved.notes,
    )

    if resolved.source == "sidecar opf" and not resolved.needs_review:
        return NewBook(
            **common,
            evidence_excerpt="",
            evidence_isbns=(),
            lookup_errors=(),
            status="review" if reasons else "accepted",
            decided_by="calibre sidecar",
            review_reasons=tuple(reasons),
            metadata=resolved.metadata,
        )

    evidence = _group_evidence(files)
    candidates, errors = _lookup(resolved, evidence, sources)
    decision = decide(resolved.metadata, candidates, evidence)
    status, decided_by, reasons = _outcome(decision, group.needs_review, resolved.notes)

    return NewBook(
        **common,
        evidence_excerpt=evidence.text[:EXCERPT_CHARS],
        evidence_isbns=tuple(sorted(evidence.isbns)),
        lookup_errors=errors,
        status=status,
        decided_by=decided_by,
        review_reasons=reasons,
        metadata=decision.metadata,
        mislabeled=decision.mislabeled,
        candidates=_stored(decision),
    )


def _outcome(decision: Decision, similar_group: bool, local_notes: tuple[str, ...]) -> tuple[str, str, tuple[str, ...]]:
    """Status, decided_by and review reasons of a book from the scoring decision."""
    reasons = [REASON_SIMILAR_GROUP] if similar_group else []
    if decision.verdict is Verdict.ACCEPT:
        decided_by = f"auto: {decision.chosen_source}"
    else:
        decided_by = "proposal: local"
        reasons += [REASON_NO_EVIDENCE, *local_notes]
    return ("review" if reasons else "accepted"), decided_by, tuple(reasons)


def _stored(decision: Decision) -> tuple[StoredCandidate, ...]:
    return tuple(
        StoredCandidate(
            source=a.candidate.source,
            metadata=a.candidate.metadata,
            cover_url=a.candidate.cover_url,
            score=a.score,
            isbn_match=a.isbn_match,
            title_in_text=a.title_in_text,
            author_in_text=a.author_in_text,
            auto_acceptable=a.auto_acceptable,
        )
        for a in decision.assessments
    )


@dataclass
class RescoreSummary:
    rescored: int = 0
    skipped: int = 0
    changed: int = 0


def rescore(input_root: Path, work_root: Path, log: Callable[[str], None] = print) -> RescoreSummary:
    store = StateStore(work_root / "state.db")
    summary = RescoreSummary()
    for book in store.books():
        if book.decided_by in ("user", "calibre sidecar"):
            summary.skipped += 1
            continue
        paths = [input_root / s for s in book.sources]
        evidence = _evidence_of(paths)
        candidates = [
            normalize_candidate(Candidate(source=c.source, metadata=c.metadata, cover_url=c.cover_url))
            for c in store.candidates(book.id)
        ]
        decision = decide(book.local_metadata, candidates, evidence)
        status, decided_by, reasons = _outcome(decision, book.group_reason == "similar", book.local_notes)
        store.replace_decision(
            book.id, status, decided_by, reasons, decision.metadata, decision.mislabeled, _stored(decision)
        )
        summary.rescored += 1
        if (status, decision.metadata, decision.mislabeled) != (book.status, book.metadata, book.mislabeled):
            summary.changed += 1
            log(f"{book.placement}/{decision.metadata.title}: {status} ({decided_by})")
    log(f"rescore: {summary}")
    return summary


def _lookup(resolved: ResolvedMetadata, evidence: Evidence, sources: Sequence[MetadataSource]):
    local = resolved.metadata
    isbn = local.isbn or _preferred_isbn(evidence.isbns)
    query = LookupQuery(title=local.title, authors=local.authors, isbn=isbn)
    result = lookup_all(sources, query)
    candidates, errors = list(result.candidates), list(result.errors)
    if not candidates and isbn:
        by_title = lookup_all(sources, replace(query, isbn=None))
        candidates += by_title.candidates
        errors += by_title.errors
    return candidates, tuple(dict.fromkeys(errors))


def _preferred_isbn(isbns: frozenset[str]) -> str | None:
    ordered = sorted(isbns, key=lambda i: (len(i) != 13, i))
    return ordered[0] if ordered else None


def _group_evidence(files: list[BookFile]) -> Evidence:
    return _evidence_of([f.path for f in files])


def _evidence_of(paths: list[Path]) -> Evidence:
    parts = [extract_evidence(p) for p in paths if p.suffix.lower() in EVIDENCE_FORMATS]
    return Evidence(
        text=" ".join(p.text for p in parts if p.text),
        isbns=frozenset(i for p in parts for i in p.isbns),
    )


def group_metadata(group: BookGroup) -> ResolvedMetadata:
    """Local metadata of a group: from the first file with a sidecar .opf, else the first file."""
    files = [f for unit in group.units for f in unit.files]
    with_opf = next((f for f in files if f.opf is not None), None)
    return resolve_local(with_opf or files[0])


def selected(group: BookGroup, only: tuple[str, ...]) -> bool:
    return placement_selected("/".join(group.placement.parts()), only)


def placement_selected(placement: str, only: tuple[str, ...]) -> bool:
    """Whether a placement like "IT/Security/Pentesting" is within one of the given paths."""
    if not only:
        return True
    return any(placement == p.rstrip("/") or placement.startswith(p.rstrip("/") + "/") for p in only)


def relative(path: Path, root: Path) -> str:
    return str(path.relative_to(root))
