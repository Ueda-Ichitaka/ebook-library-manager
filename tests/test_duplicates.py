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
             book(2, ("b.pdf",), title="Paganism", authors=("Vivianne Crowley",))]

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


def test_same_surname_with_different_first_name_is_only_a_possible_duplicate():
    books = [book(1, ("a.pdf",), title="Wicca", authors=("Scott Cunningham",)),
             book(2, ("b.pdf",), title="Wicca", authors=("Joy Cunningham",))]

    (group,) = find_duplicates(books)

    assert group.tier == "possible"
    assert group.reasons == ("same main title, different authors",)


# --- review state after duplicate decisions


def test_resolved_similar_group_leaves_the_review(tmp_path):
    from ebookprep.duplicates import reconcile_reviews
    from ebookprep.identify import REASON_SIMILAR_GROUP
    from ebookprep.state import NewBook, StateStore

    store = StateStore(tmp_path / "state.db")
    meta = BookMetadata(title="Better Bondage for Every Body", authors=("Evie Vane",))

    def add(key, sources, reasons):
        return store.add_book(NewBook(
            key=key, placement="BDSM/Shibari", group_reason="similar", book_folder=None, sources=sources,
            local_metadata=meta, local_source="filename", local_notes=(), evidence_excerpt="", evidence_isbns=(),
            lookup_errors=(), status="review", decided_by="auto: Open Library", review_reasons=reasons, metadata=meta))

    only_group = add("a", ("a.pdf", "a.azw"), (REASON_SIMILAR_GROUP,))
    two_reasons = add("b", ("b.pdf", "b.epub"), (REASON_SIMILAR_GROUP, "metadata from filename only"))
    still_two = add("c", ("c.pdf", "c.epub", "c.mobi"), (REASON_SIMILAR_GROUP,))
    store.exclude_sources(["a.azw"], kept="a.pdf", group="g1")
    store.exclude_sources(["b.epub"], kept="b.pdf", group="g2")
    store.exclude_sources(["c.mobi"], kept="c.pdf", group="g3")

    changed = reconcile_reviews(store)

    assert changed == 2
    assert (store.book(only_group).status, store.book(only_group).review_reasons) == ("accepted", ())
    assert (store.book(two_reasons).status, store.book(two_reasons).review_reasons) == ("review", ("metadata from filename only",))
    assert store.book(still_two).status == "review"



def test_same_title_with_unknown_author_is_a_possible_duplicate():
    books = [
        book(1, ("a.epub", "b.epub"), title="Sex at Dawn: The Prehistoric Origins of Modern Sexuality",
             authors=("Christopher Ryan",)),
        book(2, ("c.pdf",), title="Sex at Dawn The Prehistoric Origins of Modern Sexuality", authors=()),
    ]

    group = next(g for g in find_duplicates(books) if "c.pdf" in paths(g))

    assert paths(group) == ["a.epub", "b.epub", "c.pdf"]
    assert "same title" in group.reasons


def test_author_without_letters_counts_as_unknown():
    books = [book(1, ("a.epub",), title="Sex at Dawn", authors=("Christopher Ryan",)),
             book(2, ("c.pdf",), title="Sex at Dawn", authors=(".",))]

    (group,) = find_duplicates(books)

    assert group.reasons == ("same title",)



# --- three sections: certain, likely, possible


def test_shared_author_in_any_position_is_certain():
    books = [book(1, ("IT/Security/a.epub",), title="Black Hat Bash", authors=("Nick Aleks", "Dolev Farhi")),
             book(2, ("IT/Pentesting/b.epub",), title="Black Hat Bash", authors=("Dolev Farhi", "Nick Aleks"))]

    (group,) = find_duplicates(books)

    assert (group.tier, group.reasons) == ("certain", ("same title and author",))


def test_same_main_title_without_edition_and_subtitle_is_likely():
    books = [book(1, ("a.epub",), title="Serious Cryptography", authors=("Jean-Philippe Aumasson",)),
             book(2, ("b.pdf",), title="Serious Cryptography, 2nd Edition: A Practical Introduction to Modern Encryption",
                  authors=("Jean-Philippe Aumasson",)),
             book(3, ("c.epub",), title="Black Hat Bash (EARLY ACCESS)", authors=("Dolev Farhi",)),
             book(4, ("d.epub",), title="Black Hat Bash", authors=("Dolev Farhi",))]

    groups = {tuple(paths(g)): g for g in find_duplicates(books)}

    assert groups[("a.epub", "b.pdf")].tier == "likely"
    assert groups[("a.epub", "b.pdf")].reasons == ("same main title and author",)
    assert groups[("c.epub", "d.epub")].tier == "likely"


def test_title_starting_like_the_other_with_same_author_is_possible():
    books = [book(1, ("a.pdf",), title="Windows Security Internals with PowerShell", authors=("James Forshaw",)),
             book(2, ("b.pdf",), title="Windows Security Internals - A Deep Dive into Windows Authentication",
                  authors=("James Forshaw",))]

    (group,) = find_duplicates(books)

    assert (group.tier, group.reasons) == ("possible", ("similar title, same author",))


def test_a_group_shows_in_the_strictest_section_only_once():
    books = [book(1, ("a.pdf", "a.epub"), title="X Y Z", authors=("A B",))]

    groups = find_duplicates(books)

    assert [g.tier for g in groups] == ["certain"]


def test_looser_section_lists_a_bigger_group_that_adds_books():
    books = [book(1, ("a.pdf", "a.epub"), title="Serious Cryptography", authors=("Jean-Philippe Aumasson",)),
             book(2, ("b.pdf",), title="Serious Cryptography, 2nd Edition", authors=("Jean-Philippe Aumasson",))]

    groups = find_duplicates(books)

    assert [(g.tier, len(g.files)) for g in groups] == [("certain", 2), ("likely", 3)]


def test_different_books_of_one_author_with_different_titles_are_no_group():
    books = [book(1, ("a.pdf",), title="Penetration Testing With Raspberry Pi", authors=("Michael McPhee",)),
             book(2, ("b.pdf",), title="Mastering Kali Linux for Web Penetration Testing", authors=("Michael McPhee",))]

    assert find_duplicates(books) == []
