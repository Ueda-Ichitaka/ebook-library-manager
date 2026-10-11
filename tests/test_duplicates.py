"""About this file: tests for ebookprep.duplicates, which finds groups of files that may
be the same book: the files of one book (e.g. PDF and EPUB), and books anywhere in the
library with the same ISBN or the same title and author."""

from ebookprep.duplicates import find_duplicates, group_key
from ebookprep.metadata import BookMetadata
from ebookprep.state import BookRow


def book(book_id, sources, title="T", authors=("A Author",), isbn=None, placement="IT/Android", status="accepted"):
    metadata = BookMetadata(title=title, authors=authors, isbn=isbn)
    return BookRow(
        id=book_id, key=str(book_id), placement=placement, group_reason="single", book_folder=None,
        sources=sources, local_metadata=metadata, local_source="filename", local_notes=(), evidence_excerpt="",
        evidence_isbns=(), lookup_errors=(), status=status, decided_by="user", review_reasons=(),
        metadata=metadata, mislabeled=False, built_metadata=None, built_folder=None,
    )


def paths(group):
    return sorted(f.path for f in group.files)


def test_files_of_one_book_are_a_group():
    (group,) = find_duplicates([book(1, ("IT/Android/Spark.pdf", "IT/Android/Spark.epub"), title="Spark")])

    assert paths(group) == ["IT/Android/Spark.epub", "IT/Android/Spark.pdf"]
    assert group.reasons == ("files of one book",)
    assert group.title == "Spark"


def test_single_file_books_with_different_identity_are_no_group():
    books = [book(1, ("a.pdf",), title="Wicca", authors=("Scott Cunningham",)),
             book(2, ("b.pdf",), title="Wicca", authors=("Vivianne Crowley",))]

    assert find_duplicates(books) == []


def test_same_isbn_across_the_library():
    books = [book(1, ("IT/Python/a.pdf",), title="Beginning Python", isbn="1590595190"),
             book(2, ("IT/Programming/b.pdf",), title="Beginning Python: From Novice", authors=("Other",),
                  isbn="9781590595190", placement="IT/Programming")]

    (group,) = find_duplicates(books)

    assert paths(group) == ["IT/Programming/b.pdf", "IT/Python/a.pdf"]
    assert group.reasons == ("same ISBN",)


def test_same_title_and_author_across_the_library():
    books = [book(1, ("IT/Python/a.pdf",), title="Beginning Python", authors=("Magnus Lie Hetland",)),
             book(2, ("IT/Programming/b.epub",), title="beginning python", authors=("M. Hetland",),
                  placement="IT/Programming", status="review")]

    (group,) = find_duplicates(books)

    assert group.reasons == ("same title and author",)
    assert {f.book_id for f in group.files} == {1, 2}
    assert {f.book_status for f in group.files} == {"accepted", "review"}


def test_connections_are_combined_into_one_group():
    books = [book(1, ("a.pdf", "a.epub"), title="X", isbn="9781593278656"),
             book(2, ("b.pdf",), title="Other title", isbn="9781593278656"),
             book(3, ("c.pdf",), title="Other title", authors=("A Author",))]

    (group,) = find_duplicates(books)

    assert paths(group) == ["a.epub", "a.pdf", "b.pdf", "c.pdf"]
    assert set(group.reasons) == {"files of one book", "same ISBN", "same title and author"}


def test_excluded_files_are_left_out_and_resolved_groups_disappear():
    books = [book(1, ("a.pdf", "a.epub"), title="X")]

    assert find_duplicates(books, excluded={"a.epub"}) == []


def test_dismissed_groups_are_hidden():
    books = [book(1, ("a.pdf", "a.epub"))]
    (group,) = find_duplicates(books)

    assert find_duplicates(books, dismissed={group.key}) == []


def test_group_key_is_stable_and_url_safe():
    assert group_key(["b.pdf", "a.epub"]) == group_key(["a.epub", "b.pdf"])
    assert group_key(["a.epub", "b.pdf"]).isalnum()
    assert group_key(["a.epub", "b.pdf"]) != group_key(["a.epub", "c.pdf"])


def test_same_surname_with_different_first_name_is_another_author():
    books = [book(1, ("a.pdf",), title="Wicca", authors=("Scott Cunningham",)),
             book(2, ("b.pdf",), title="Wicca", authors=("Joy Cunningham",))]

    assert find_duplicates(books) == []
