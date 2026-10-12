"""About this file: tests for ebookprep.scoring, which checks online candidates against the
book's own evidence and decides between automatic acceptance and review."""

import pytest

from ebookprep.evidence import Evidence
from ebookprep.metadata import BookMetadata
from ebookprep.scoring import Verdict, assess, content_differs, decide
from ebookprep.sources import Candidate

TEXT = (
    "black hat go go programming for hackers and pentesters by tom steele, chris patten, dan kottmann "
    "copyright 2020 no starch press"
)
EVIDENCE = Evidence(text=TEXT, isbns=frozenset({"9781593278656", "1593278659"}))
LOCAL = BookMetadata(title="Black Hat Go: Go Programming for Hackers", authors=("Tom Steele",))


def candidate(title, authors=("Tom Steele",), isbn=None, source="Open Library", **extra) -> Candidate:
    return Candidate(source=source, metadata=BookMetadata(title=title, authors=authors, isbn=isbn, **extra))


def test_isbn_match_with_text_isbn():
    result = assess(candidate("Something Else", authors=("Nobody",), isbn="9781593278656"), LOCAL, EVIDENCE)

    assert result.isbn_match
    assert result.auto_acceptable


def test_isbn_match_with_local_isbn_in_other_form():
    local = BookMetadata(title="x", isbn="1593278659")

    result = assess(candidate("Black Hat Go", isbn="9781593278656"), local, Evidence(text="", isbns=frozenset()))

    assert result.isbn_match


def test_title_and_author_in_text():
    result = assess(candidate("Black Hat Go: Go Programming for Hackers and Pentesters"), LOCAL, EVIDENCE)

    assert result.title_in_text and result.author_in_text
    assert result.auto_acceptable


def test_title_without_author_in_text_is_not_enough():
    result = assess(candidate("Black Hat Go", authors=("Justin Seitz",)), LOCAL, EVIDENCE)

    assert result.title_in_text and not result.author_in_text
    assert not result.auto_acceptable


def test_title_must_appear_as_phrase_not_scattered_words():
    result = assess(candidate("Go Hackers Black"), LOCAL, EVIDENCE)

    assert not result.title_in_text


def test_wrong_isbn_and_absent_title_needs_review():
    result = assess(candidate("Black Hat Python", authors=("Justin Seitz",), isbn="9781718501126"), LOCAL, EVIDENCE)

    assert not result.auto_acceptable


def test_accents_and_punctuation_are_ignored_in_text_matching():
    evidence = Evidence(text="the only academic phrasebook you'll ever need luiz otávio barros", isbns=frozenset())
    result = assess(
        candidate("The Only Academic Phrasebook You'll Ever Need", authors=("Luiz Otavio Barros",)),
        BookMetadata(title="x"),
        evidence,
    )

    assert result.auto_acceptable


def test_decide_accepts_best_acceptable_candidate_and_fills_gaps_from_local():
    local = BookMetadata(title="Black Hat Go", authors=("Tom Steele",), comments="<p>local</p>", language="eng")
    weak = candidate("Black Hat Python", authors=("Justin Seitz",), source="calibre: Google")
    strong = candidate("Black Hat Go: Go Programming for Hackers and Pentesters", isbn="9781593278656", publisher="NSP")

    decision = decide(local, [weak, strong], EVIDENCE)

    assert decision.verdict is Verdict.ACCEPT
    assert decision.chosen_source == "Open Library"
    assert decision.metadata.title == "Black Hat Go: Go Programming for Hackers and Pentesters"
    assert decision.metadata.publisher == "NSP"
    assert decision.metadata.comments == "<p>local</p>"
    assert decision.metadata.language == "eng"
    assert [a.candidate for a in decision.assessments] == [strong, weak]


def test_decide_without_acceptable_candidate_proposes_best_for_review():
    poor = candidate("Black Hat Python", authors=("Justin Seitz",))

    decision = decide(LOCAL, [poor], Evidence(text="", isbns=frozenset()))

    assert decision.verdict is Verdict.REVIEW
    assert decision.metadata == LOCAL
    assert decision.chosen_source == "local"


def test_decide_without_candidates_needs_review():
    decision = decide(LOCAL, [], EVIDENCE)

    assert decision.verdict is Verdict.REVIEW
    assert decision.metadata == LOCAL


# --- author spelling: prefer the local name for the same person


def test_local_author_spelling_wins_over_junk_candidate_author():
    local = BookMetadata(title="Naturrituale", authors=("Wolf-Dieter Storl",))
    evidence = Evidence(text="naturrituale wolf-dieter storl at verlag isbn 978-3-03800-131-7", isbns=frozenset({"9783038001317"}))
    junk = candidate("Naturrituale: Mit schamanischen Ritualen", authors=("Naturrituale Storl",), isbn="9783038001317")

    decision = decide(local, [junk], evidence)

    assert decision.verdict is Verdict.ACCEPT
    assert decision.metadata.authors == ("Wolf-Dieter Storl",)


def test_candidate_spelling_kept_when_printed_in_the_book():
    local = BookMetadata(title="Beginning PHP and MySQL", authors=("Jason Gilmore",))
    evidence = Evidence(text="beginning php and mysql w. jason gilmore apress", isbns=frozenset())
    better = candidate("Beginning PHP and MySQL", authors=("W. Jason Gilmore",))

    assert decide(local, [better], evidence).metadata.authors == ("W. Jason Gilmore",)


def test_additional_candidate_authors_are_kept():
    local = BookMetadata(title="Black Hat Go", authors=("Tom Steele",))
    full = candidate("Black Hat Go", authors=("Tom Steele", "Chris Patten", "Dan Kottmann"), isbn="9781593278656")

    assert decide(local, [full], EVIDENCE).metadata.authors == ("Tom Steele", "Chris Patten", "Dan Kottmann")


# --- content differs from the filename (mislabeled file)


def test_mislabeled_file_is_detected():
    local = BookMetadata(title="The Complete Guide to Physical Security", authors=("Paul R. Baker",))
    evidence = Evidence(text="moral design and technology edited by bart wernaart", isbns=frozenset({"9789086863709"}))
    real = candidate("Moral Design and Technology", authors=("Bart Wernaart",), isbn="9789086863709")

    decision = decide(local, [real], evidence)

    assert decision.verdict is Verdict.ACCEPT
    assert decision.mislabeled
    assert decision.metadata.authors == ("Bart Wernaart",)


@pytest.mark.parametrize(
    ("local_title", "accepted_title"),
    [
        ("Bash Pocket Reference, 2nd Edition", "bash: Pocket Reference"),
        ("FormDerWissenschaftlichenAusarbeitung", "Form der wissenschaftlichen Ausarbeitung"),
        ("david clark practical sdr getting started with software defined radio", "Practical SDR: Getting Started With Software-Defined Radio"),
        ("Naturrituale · Mit schamanischen Ritualen zu den eigenen Wurzeln finden", "Naturrituale: Mit schamanischen Ritualen"),
        ("p1g4vjd8ei12lia5k1c6l34k1t084", "The Witches' Goddess"),
        ("Raspberry Pi, 2nd Edition", "Raspberry Pi"),
    ],
)
def test_similar_or_unknown_titles_are_not_mislabeled(local_title, accepted_title):
    assert not content_differs(local_title, accepted_title)


def test_raspbian_file_with_unity_book_is_mislabeled():
    assert content_differs("Raspbian OS Programming with the Raspberry Pi", "Developing 2D Games with Unity: Independent Game Programming with C#")


def test_local_full_name_wins_over_bare_surname():
    local = BookMetadata(title="Wicca", authors=("Scott Cunningham",))
    evidence = Evidence(text="wicca a guide for the solitary practitioner scott cunningham llewellyn", isbns=frozenset())
    bare = candidate("Wicca: A Guide for the Solitary Practitioner", authors=("Cunningham",))

    assert decide(local, [bare], evidence).metadata.authors == ("Scott Cunningham",)


@pytest.mark.parametrize(
    ("local_title", "accepted_title", "authors"),
    [
        ("Müller", "Anorganische Strukturchemie", ("Ulrich Müller",)),
        ("Holleman Wiberg", "Lehrbuch der anorganischen Chemie", ("A. F. Holleman", "Egon Wiberg", "Nils Wiberg")),
        ("Janiak Meyer Gudat Kurz", "Riedel Moderne Anorganische Chemie", ("Christoph Janiak", "Dietrich Gudat", "Philipp Kurz", "H.-J. Meyer")),
    ],
)
def test_filename_with_only_the_authors_is_not_mislabeled(local_title, accepted_title, authors):
    assert not content_differs(local_title, accepted_title, authors)


def test_filename_naming_other_authors_is_still_mislabeled():
    assert content_differs("Huheey Keiter Keiter", "Trennungsmethoden der analytischen Chemie", ("Rudolf Bock",))


@pytest.mark.parametrize(
    ("local_title", "accepted_title", "authors"),
    [
        ("Janiak Meyer Gudat Kurz", "Riedel Moderne Anorganische Chemie",
         ("Christoph Janiak", "Dietrich Gudat", "Hans-Jürgen Meyer")),
        ("George R. R. Martin - Das Lied von Eis und Feuer 03", "Das Lied von Eis und Feuer 3: Der Thron der Sieben Königreiche",
         ("George R. R. Martin",)),
        ("DE201309", "Raspberry Pi: 45 Experimente mit Hard- und Software", ("Bert van Dam",)),
    ],
)
def test_more_filenames_that_are_not_another_book(local_title, accepted_title, authors):
    assert not content_differs(local_title, accepted_title, authors)
