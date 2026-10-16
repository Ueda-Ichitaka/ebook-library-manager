"""About this file: tests for ebookprep.identify, the stage that resolves every book's
metadata (local sources, online candidates checked against the book's own text) and
stores it as accepted or waiting for review. Online sources are replaced by fakes."""

from pathlib import Path

import pytest
from conftest import make_epub, make_pdf, make_text_pdf

from ebookprep.config import Config
from ebookprep.identify import identify, relookup
from ebookprep.metadata import BookMetadata
from ebookprep.sources import Candidate, SourceError
from ebookprep.state import StateStore

CONFIG = Config(topic_categories=frozenset({"IT/Security"}))

CALIBRE_OPF = """<?xml version='1.0' encoding='utf-8'?>
<package xmlns="http://www.idpf.org/2007/opf" version="2.0">
  <metadata xmlns:dc="http://purl.org/dc/elements/1.1/" xmlns:opf="http://www.idpf.org/2007/opf">
    <dc:title>Android Sensor Programming by Example</dc:title>
    <dc:creator opf:role="aut">Varun Nagpal</dc:creator>
    <dc:contributor opf:role="bkp">calibre (8.7.0) [https://calibre-ebook.com]</dc:contributor>
  </metadata>
</package>"""


class FakeSource:
    def __init__(self, name="Fake", answers=None, error=None):
        self.name = name
        self.answers = answers or {}
        self.error = error
        self.queries = []

    def search(self, query):
        self.queries.append(query)
        if self.error:
            raise SourceError(self.error)
        return list(self.answers.get(query.isbn or query.title, []))


def found(title, authors, isbn=None, source="Fake"):
    return Candidate(source=source, metadata=BookMetadata(title=title, authors=authors, isbn=isbn))


def run(tmp_path, sources, only=()):
    work = tmp_path / "work"
    summary = identify(tmp_path / "input", work, CONFIG, sources, only=only, log=lambda _: None)
    return summary, StateStore(work / "state.db")


def book_by_source(store, fragment):
    return next(b for b in store.books() if any(fragment in s for s in b.sources))


def test_calibre_sidecar_is_accepted_without_lookup(tmp_path):
    android = tmp_path / "input" / "IT" / "Android"
    android.mkdir(parents=True)
    make_pdf(android / "Android Sensor Programming by Example - Varun Nagpal.pdf")
    (android / "Android Sensor Programming by Example - Varun Nagpal.opf").write_text(CALIBRE_OPF, encoding="utf-8")
    source = FakeSource()

    summary, store = run(tmp_path, [source])

    (book,) = store.books()
    assert book.status == "accepted"
    assert book.decided_by == "calibre sidecar"
    assert book.metadata.title == "Android Sensor Programming by Example"
    assert source.queries == []
    assert (summary.new, summary.accepted, summary.review) == (1, 1, 0)


@pytest.mark.calibre
def test_isbn_printed_in_book_accepts_matching_candidate(tmp_path):
    folder = tmp_path / "input" / "IT" / "Security"
    folder.mkdir(parents=True)
    make_text_pdf(folder / "Black Hat Go - Tom Steele.pdf", [["BLACK HAT GO"], ["ISBN 978-1-59327-865-6"]])
    right = found("Black Hat Go: Go Programming for Hackers and Pentesters", ("Tom Steele",), "9781593278656")
    source = FakeSource(answers={"9781593278656": [right]})

    _, store = run(tmp_path, [source])

    (book,) = store.books()
    assert source.queries[0].isbn == "9781593278656"
    assert book.status == "accepted"
    assert book.decided_by == "auto: Fake"
    assert book.metadata.title == "Black Hat Go: Go Programming for Hackers and Pentesters"
    assert "9781593278656" in book.evidence_isbns
    (stored,) = store.candidates(book.id)
    assert stored.isbn_match and stored.auto_acceptable


def test_isbns_on_the_last_pages_are_looked_up_with_the_title(tmp_path):
    folder = tmp_path / "input" / "Beziehungskonzepte" / "unsortiert"
    folder.mkdir(parents=True)
    pages = [["MANY LOVE", "A Memoir of Polyamory"]] + [[f"page {n}"] for n in range(2, 20)]
    pages += [["Also available: ISBN 978-0-06-170780-3"], ["Copyright", "ISBN 978-1-5011-8978-4"]]
    make_text_pdf(folder / "Many love - Sophie Lucido Johnson.pdf", pages)
    advertised = found("Sex at Dawn", ("Christopher Ryan",), "9780061707803")
    right = found("Many Love", ("Sophie Lucido Johnson",), "9781501189784")
    source = FakeSource(answers={"9780061707803": [advertised], "9781501189784": [right]})

    _, store = run(tmp_path, [source])

    assert [(q.isbn, q.title) for q in source.queries] == [
        ("9780061707803", "Many love"), ("9781501189784", "Many love"), (None, "Many love")
    ]
    (book,) = store.books()
    assert (book.status, book.metadata.title, book.metadata.isbn) == ("accepted", "Many Love", "9781501189784")
    assert book.evidence_isbns == () and "9781501189784" in book.evidence_back_isbns
    assert [c.back_isbn_match for c in store.candidates(book.id)] == [True, True]


def test_unverified_candidate_goes_to_review_with_local_proposal(tmp_path):
    folder = tmp_path / "input" / "IT" / "Android"
    folder.mkdir(parents=True)
    make_pdf(folder / "Beginning Android Games - Robert Green.pdf")
    wrong = found("Beginning Android Games Development", ("Someone Else",))
    source = FakeSource(answers={"Beginning Android Games": [wrong]})

    _, store = run(tmp_path, [source])

    (book,) = store.books()
    assert book.status == "review"
    assert book.metadata == BookMetadata(title="Beginning Android Games", authors=("Robert Green",))
    assert any("no candidate" in reason for reason in book.review_reasons)
    (stored,) = store.candidates(book.id)
    assert stored.metadata.title == "Beginning Android Games Development"
    assert not stored.auto_acceptable


def test_isbn_query_without_result_falls_back_to_title(tmp_path):
    folder = tmp_path / "input" / "IT" / "Linux"
    folder.mkdir(parents=True)
    make_pdf(folder / "Linux firewalls -- Michael Rash -- 2007 -- NSP -- 9781593271411 -- 848cf4bd357b684fd1871da94b3ff998 -- Anna’s Archive.pdf")
    source = FakeSource()

    run(tmp_path, [source])

    assert [(q.isbn, q.title) for q in source.queries] == [("9781593271411", "Linux firewalls"), (None, "Linux firewalls")]


def test_similar_group_always_needs_review(tmp_path):
    folder = tmp_path / "input" / "IT" / "Pentesting"
    folder.mkdir(parents=True)
    make_epub(folder / "Ethical Hacking - Daniel G. Graham.epub", title="Ethical Hacking", body="<p>Ethical Hacking by Daniel G. Graham</p>")
    make_epub(
        folder / "Ethical Hacking_ A Hands-On Introduction - Daniel G. Graham.epub",
        title="Ethical Hacking",
        body="<p>Ethical Hacking by Daniel G. Graham</p>",
    )
    right = found("Ethical Hacking: A Hands-On Introduction to Breaking In", ("Daniel G. Graham",))
    source = FakeSource(answers={"Ethical Hacking": [right], "Ethical Hacking: A Hands-On Introduction": [right]})

    _, store = run(tmp_path, [source])

    (book,) = store.books()
    assert len(book.sources) == 2
    assert book.status == "review"
    assert book.decided_by == "auto: Fake"
    assert any("similar" in reason for reason in book.review_reasons)


def test_source_errors_are_recorded(tmp_path):
    folder = tmp_path / "input" / "IT" / "Android"
    folder.mkdir(parents=True)
    make_pdf(folder / "Some Book - Some Author.pdf")

    _, store = run(tmp_path, [FakeSource(name="Google", error="HTTP 429")])

    (book,) = store.books()
    assert ("Google", "HTTP 429") in book.lookup_errors
    assert book.status == "review"


def test_known_books_are_not_identified_again(tmp_path):
    folder = tmp_path / "input" / "IT" / "Android"
    folder.mkdir(parents=True)
    make_pdf(folder / "Some Book - Some Author.pdf")
    run(tmp_path, [FakeSource()])
    source = FakeSource()

    summary, store = run(tmp_path, [source])

    assert source.queries == []
    assert summary.new == 0 and summary.known == 1
    assert len(store.books()) == 1


def test_only_filter(tmp_path):
    for placement in ("IT/Android", "Woodworking/joinery"):
        folder = tmp_path / "input" / placement
        folder.mkdir(parents=True)
        make_pdf(folder / "Book - Author.pdf")

    _, store = run(tmp_path, [FakeSource()], only=("Woodworking",))

    assert [b.placement for b in store.books()] == ["Woodworking/joinery"]


def test_categories_flagged_as_not_books_are_not_identified(tmp_path):
    for placement in ("Magazine/elektor", "Magazine/elektor/2011", "Woodworking/joinery"):
        folder = tmp_path / "input" / placement
        folder.mkdir(parents=True)
        make_pdf(folder / "Book - Author.pdf")
    StateStore(tmp_path / "work" / "state.db").flag_not_books("Magazine/elektor")
    source = FakeSource()

    summary, store = run(tmp_path, [source])

    assert [b.placement for b in store.books()] == ["Woodworking/joinery"]
    assert len(source.queries) == 1
    assert summary.not_books == 2


def test_decision_by_user_and_build_bookkeeping(tmp_path):
    folder = tmp_path / "input" / "IT" / "Android"
    folder.mkdir(parents=True)
    make_pdf(folder / "Some Book - Some Author.pdf")
    _, store = run(tmp_path, [FakeSource()])
    (book,) = store.books()
    approved = BookMetadata(title="Some Book: Real Subtitle", authors=("Some Author",))

    store.decide(book.id, approved, decided_by="user")
    decided = store.book(book.id)
    assert (decided.status, decided.metadata, decided.needs_build) == ("accepted", approved, True)

    store.mark_built(book.id, approved, "IT/Android/Some Book - Some Author")
    built = store.book(book.id)
    assert built.built_metadata == approved and built.built_folder == "IT/Android/Some Book - Some Author"
    assert not built.needs_build
    assert store.books(status="review") == []


def test_split_group_stays_split_in_later_runs(tmp_path):
    folder = tmp_path / "input" / "IT" / "Pentesting"
    folder.mkdir(parents=True)
    make_pdf(folder / "Ethical Hacking - Daniel G. Graham.pdf")
    make_pdf(folder / "Ethical Hacking_ A Hands-On Introduction - Daniel G. Graham.pdf")
    _, store = run(tmp_path, [FakeSource()])
    (group,) = store.books()

    new_ids = store.split_book(group.id)
    summary, store = run(tmp_path, [FakeSource()])

    books = store.books()
    assert len(new_ids) == 2 and len(books) == 2
    assert sorted(len(b.sources) for b in books) == [1, 1]
    assert all(b.status == "review" for b in books)
    assert {b.metadata.title for b in books} == {"Ethical Hacking", "Ethical Hacking: A Hands-On Introduction"}
    assert summary.new == 0
    assert store.candidates(new_ids[0]) == []


@pytest.mark.calibre
def test_mislabeled_file_is_accepted_as_its_real_content(tmp_path):
    folder = tmp_path / "input" / "IT" / "Physical Security"
    folder.mkdir(parents=True)
    make_text_pdf(
        folder / "The Complete Guide to Physical Security - Paul Baker.pdf",
        [["MORAL DESIGN AND TECHNOLOGY", "edited by Bart Wernaart"], ["ISBN: 978-90-8686-370-9"]],
    )
    real = found("Moral Design and Technology", ("Bart Wernaart",), "9789086863709")

    _, store = run(tmp_path, [FakeSource(answers={"9789086863709": [real]})])

    (book,) = store.books()
    assert book.status == "accepted"
    assert book.mislabeled
    assert book.metadata.title == "Moral Design and Technology"
    assert book.local_metadata.title == "The Complete Guide to Physical Security"


# --- rescore: re-decide stored books with the current rules, without online requests


def stored_book(store, source, local, candidates, **extra):
    from ebookprep.state import NewBook, StoredCandidate

    values = dict(
        key=f'["{source}"]', placement="Spiritualität/Witchcraft", group_reason="single", book_folder=None,
        sources=(source,), local_metadata=local, local_source="sidecar opf", local_notes=(),
        evidence_excerpt="", evidence_isbns=(), lookup_errors=(), status="accepted",
        decided_by="auto: calibre: Google", review_reasons=(), metadata=candidates[0].metadata,
        candidates=tuple(
            StoredCandidate(source=c.source, metadata=c.metadata, cover_url=None, score=0, isbn_match=False,
                            title_in_text=False, author_in_text=False, auto_acceptable=True)
            for c in candidates
        ),
    )
    values.update(extra)
    return store.add_book(NewBook(**values))


@pytest.mark.calibre
def test_rescore_applies_current_rules_from_stored_candidates(tmp_path):
    from ebookprep.identify import rescore

    folder = tmp_path / "input" / "Spiritualität" / "Witchcraft"
    folder.mkdir(parents=True)
    make_text_pdf(folder / "Naturrituale.pdf", [["NATURRITUALE", "Wolf-Dieter Storl"], ["ISBN 978-3-03800-131-7"]])
    make_text_pdf(folder / "Physical.pdf", [["MORAL DESIGN AND TECHNOLOGY", "edited by Bart Wernaart"]])
    store = StateStore(tmp_path / "work" / "state.db")
    junk_author = stored_book(
        store, "Spiritualität/Witchcraft/Naturrituale.pdf", BookMetadata(title="Naturrituale", authors=("Wolf-Dieter Storl",)),
        [found("Naturrituale: Mit schamanischen Ritualen", ("Storl, Naturrituale",), "9783038001317", source="calibre: Google")],
    )
    other_book = stored_book(
        store, "Spiritualität/Witchcraft/Physical.pdf",
        BookMetadata(title="The Complete Guide to Physical Security", authors=("Paul Baker",)),
        [found("Moral Design and Technology", ("Bart Wernaart",), source="Open Library")],
    )
    by_user = stored_book(
        store, "Spiritualität/Witchcraft/User.pdf", BookMetadata(title="Mine"), [found("Theirs", ("X",))],
        decided_by="user", metadata=BookMetadata(title="Mine"),
    )

    summary = rescore(tmp_path / "input", tmp_path / "work", log=lambda _: None)

    assert store.book(junk_author).metadata.authors == ("Wolf-Dieter Storl",)
    assert store.book(junk_author).status == "accepted"
    assert store.book(other_book).mislabeled
    assert store.book(by_user).metadata == BookMetadata(title="Mine")
    assert (summary.rescored, summary.skipped) == (2, 1)
    (candidate,) = store.candidates(junk_author)
    assert candidate.isbn_match and candidate.metadata.authors == ("Naturrituale Storl",)


def test_rescore_command(tmp_path, capsys):
    from ebookprep.__main__ import main

    exit_code = main(["rescore", "--input", str(tmp_path / "input"), "--work", str(tmp_path / "work")])

    assert exit_code == 0
    assert "rescore:" in capsys.readouterr().out


def test_log_names_books_and_explains_the_summary(tmp_path):
    folder = tmp_path / "input" / "IT" / "Android"
    folder.mkdir(parents=True)
    make_pdf(folder / "Some Book - Some Author.pdf")
    run(tmp_path, [FakeSource()])
    make_pdf(folder / "Other Book - Other Author.pdf")
    lines = []

    identify(tmp_path / "input", tmp_path / "work", CONFIG, [FakeSource()], log=lines.append)

    assert lines[0].startswith("[book 1 of 2] IT/Android/Other Book")
    assert lines[-1] == (
        "identify: 2 books in the input; 1 new (0 accepted, 1 waiting for review), 1 already known, 0 failed"
    )


def test_relookup_asks_the_sources_again_for_books_waiting_for_review(tmp_path):
    for placement, name in (("IT/Android", "Beginning Android Games - Robert Green.pdf"),
                            ("IT/Python", "Learning Python - Mark Lutz.pdf"),
                            ("Magazine/elektor", "Elektor - Redaktion.pdf")):
        (tmp_path / "input" / placement).mkdir(parents=True)
        make_text_pdf(tmp_path / "input" / placement / name, [[name.removesuffix(".pdf").replace(" - ", " by ")]])
    old = found("Beginning Android", ("Someone",))
    _, store = run(tmp_path, [FakeSource(answers={"Beginning Android Games": [old]})])
    python = book_by_source(store, "Learning Python")
    store.decide(python.id, python.metadata, decided_by="user")
    store.flag_not_books("Magazine/elektor")
    right = found("Beginning Android Games", ("Robert Green",), "9781430230427", source="New")
    source = FakeSource(name="New", answers={"Beginning Android Games": [right]})

    summary = relookup(tmp_path / "input", tmp_path / "work", [source], log=lambda _: None)

    assert [q.title for q in source.queries] == ["Beginning Android Games"]
    android = book_by_source(store, "Beginning Android")
    assert (android.status, android.decided_by, android.metadata.isbn) == ("accepted", "auto: New", "9781430230427")
    assert {c.metadata.title for c in store.candidates(android.id)} == {"Beginning Android Games", "Beginning Android"}
    assert (summary.looked_up, summary.accepted) == (1, 1)


def test_relookup_command(tmp_path, capsys):
    from ebookprep.__main__ import main

    (tmp_path / "config.toml").write_text('sources = []\n')
    exit_code = main(["relookup", "--input", str(tmp_path / "input"), "--work", str(tmp_path / "work"),
                      "--config", str(tmp_path / "config.toml")])

    assert exit_code == 0
    assert "relookup:" in capsys.readouterr().out


def test_rescore_keeps_a_book_whose_files_are_in_different_categories_in_review(tmp_path):
    from ebookprep.identify import rescore
    from ebookprep.moves import REASON_SPLIT_PLACEMENT

    folder = tmp_path / "input" / "IT" / "Security"
    folder.mkdir(parents=True)
    make_text_pdf(folder / "Black Hat Go - Tom Steele.pdf", [["BLACK HAT GO"], ["ISBN 978-1-59327-865-6"]])
    right = found("Black Hat Go", ("Tom Steele",), "9781593278656")
    _, store = run(tmp_path, [FakeSource(answers={"9781593278656": [right]})])
    (book,) = store.books()
    store.require_review(book.id, REASON_SPLIT_PLACEMENT)

    rescore(tmp_path / "input", tmp_path / "work", log=lambda _: None)

    book = store.book(book.id)
    assert book.status == "review" and REASON_SPLIT_PLACEMENT in book.review_reasons
