"""About this file: tests for ebookprep.series, which chooses each book's Kavita series
name: its title, or "Title (First author)" when Kavita would otherwise merge it with
another book of the same title."""

from ebookprep.metadata import BookMetadata
from ebookprep.series import kavita_normalize, series_names
from ebookprep.state import BookRow


def book(book_id, placement, title, authors, sources, status="accepted", mislabeled=False, target=None) -> BookRow:
    metadata = BookMetadata(title=title, authors=authors)
    return BookRow(
        id=book_id, key=str(book_id), placement=placement, group_reason="single", book_folder=None,
        sources=sources, local_metadata=metadata, local_source="filename", local_notes=(), evidence_excerpt="",
        evidence_isbns=(), lookup_errors=(), status=status, decided_by="user", review_reasons=(),
        metadata=metadata, mislabeled=mislabeled, built_metadata=None, built_folder=None,
        target_placement=target,
    )


def test_kavita_normalize_matches_kavita():
    assert kavita_normalize("Egils Saga") == kavita_normalize("Egils saga") == "egilssaga"
    assert kavita_normalize("Naturrituale · Mit X") == kavita_normalize("Naturrituale: Mit X")
    assert kavita_normalize("C++ Quick Syntax!") == "c++quicksyntax!"
    assert kavita_normalize("Hexenmedizin – Storl") == "hexenmedizinstorl"


def test_unique_titles_keep_their_title():
    books = [book(1, "IT/Android", "A", ("X",), ("a.pdf",)), book(2, "IT/Python", "B", ("Y",), ("b.pdf",))]

    assert series_names(books) == {1: "A", 2: "B"}


def test_same_title_and_format_in_one_library_gets_the_author():
    books = [
        book(1, "Spiritualität/Witchcraft", "Wicca", ("Scott Cunningham",), ("w1.pdf",)),
        book(2, "Spiritualität/Witchcraft", "Wicca", ("Vivianne Crowley",), ("w2.pdf",)),
        book(3, "Spiritualität/Pagan", "WICCA", ("Joy Cunningham",), ("w3.pdf",)),
        book(4, "Spiritualität/Witchcraft", "Paganism", ("River Higginbotham",), ("p.pdf",)),
    ]

    assert series_names(books) == {
        1: "Wicca (Scott Cunningham)",
        2: "Wicca (Vivianne Crowley)",
        3: "WICCA (Joy Cunningham)",
        4: "Paganism",
    }


def test_different_formats_or_libraries_do_not_clash():
    books = [
        book(1, "Spiritualität/Witchcraft", "Wicca", ("A",), ("w1.pdf",)),
        book(2, "Spiritualität/Witchcraft", "Wicca", ("B",), ("w2.epub",)),
        book(3, "IT/Misc", "Wicca", ("C",), ("w3.pdf",)),
    ]

    assert series_names(books) == {1: "Wicca", 2: "Wicca", 3: "Wicca"}


def test_mobi_counts_as_epub_because_it_is_converted():
    books = [
        book(1, "S/W", "Wicca", ("A",), ("w1.mobi",)),
        book(2, "S/W", "Wicca", ("B",), ("w2.epub",)),
    ]

    assert series_names(books) == {1: "Wicca (A)", 2: "Wicca (B)"}


def test_same_title_and_same_author_gets_a_counter():
    books = [
        book(1, "IT/Python", "Beginning Python", ("Magnus Hetland",), ("a.pdf",)),
        book(2, "IT/Programming", "Beginning Python", ("Magnus Hetland",), ("b.pdf",)),
    ]

    assert series_names(books) == {1: "Beginning Python (Magnus Hetland)", 2: "Beginning Python (Magnus Hetland, 2)"}


def test_books_waiting_for_review_and_mislabeled_books_are_not_counted():
    books = [
        book(1, "S/W", "Wicca", ("A",), ("w1.pdf",)),
        book(2, "S/W", "Wicca", ("B",), ("w2.pdf",), status="review"),
        book(3, "S/W", "Wicca", ("C",), ("w3.pdf",), mislabeled=True),
    ]

    assert series_names(books) == {1: "Wicca", 3: "Wicca"}


def test_moved_mislabeled_book_clashes_in_its_new_library():
    books = [
        book(1, "S/W", "Wicca", ("A",), ("w1.pdf",)),
        book(2, "IT/Physical Security", "Wicca", ("B",), ("w2.pdf",), mislabeled=True, target="S/Pagan"),
    ]

    assert series_names(books) == {1: "Wicca (A)", 2: "Wicca (B)"}
