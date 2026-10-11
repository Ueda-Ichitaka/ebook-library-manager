"""About this file: tests for ebookprep.grouping, which decides which source files belong
into the same book folder (same book in several formats or copies)."""

from pathlib import Path

from ebookprep.grouping import GroupReason, group_units
from ebookprep.scan import BookFile, Placement, SourceUnit

ANDROID = Placement("IT", "Android")
PENTESTING = Placement("IT", "Security", "Pentesting")
WITCHCRAFT = Placement("Spiritualität", "Witchcraft")


def loose(placement: Placement, filename: str) -> SourceUnit:
    return SourceUnit(placement=placement, files=(BookFile(Path("/in", *placement.parts(), filename)),))


def folder(placement: Placement, name: str, *filenames: str) -> SourceUnit:
    files = tuple(BookFile(Path("/in", *placement.parts(), name, f)) for f in filenames)
    return SourceUnit(placement=placement, files=files, book_folder=name)


def names(group) -> list[str]:
    return [f.path.name for unit in group.units for f in unit.files]


def test_unrelated_books_stay_single():
    units = [loose(ANDROID, "Beginning Android Games - Robert Green.pdf"), loose(ANDROID, "Android 2 - Arno Becker.pdf")]

    groups = group_units(units)

    assert [g.reason for g in groups] == [GroupReason.SINGLE, GroupReason.SINGLE]
    assert not any(g.needs_review for g in groups)


def test_same_name_in_two_formats_is_grouped_without_review():
    units = [
        loose(ANDROID, "Apache Spark Graph Processing - Rindra Ramamonjison.epub"),
        loose(ANDROID, "Apache Spark Graph Processing - Rindra Ramamonjison.pdf"),
    ]

    (group,) = group_units(units)

    assert group.reason is GroupReason.SAME_NAME
    assert not group.needs_review
    assert names(group) == [
        "Apache Spark Graph Processing - Rindra Ramamonjison.epub",
        "Apache Spark Graph Processing - Rindra Ramamonjison.pdf",
    ]


def test_similar_title_same_author_is_grouped_for_review():
    units = [
        loose(PENTESTING, "Ethical Hacking - Daniel G. Graham.pdf"),
        loose(PENTESTING, "Ethical Hacking_ A Hands-On Introduction to Breaking In - Daniel G. Graham.epub"),
    ]

    (group,) = group_units(units)

    assert group.reason is GroupReason.SIMILAR
    assert group.needs_review


def test_case_and_annas_archive_format_differences_still_group():
    units = [
        loose(
            Placement("Scientific Writing", "PhD"),
            "Writing Your Dissertation in Fifteen Minutes a Day_ A Guide -- Joan Bolker -- 1998 -- "
            "An Owl Book -- a2bd1645c2896c8a547460c7ba8c514d -- Anna’s Archive.mobi",
        ),
        loose(
            Placement("Scientific Writing", "PhD"),
            "Writing your dissertation in fifteen minutes a day _ a guide -- Joan Bolker -- 1st ed_, 1998 -- "
            "Henry Holt -- 9780805048919 -- af010c9b8647906f8981b037097829e1 -- Anna’s Arc.epub",
        ),
    ]

    (group,) = group_units(units)

    assert group.reason is GroupReason.SIMILAR


def test_same_main_title_different_authors_are_different_books():
    units = [
        loose(WITCHCRAFT, "Wicca - Vivianne Crowley.pdf"),
        loose(WITCHCRAFT, "Wicca- 2 books in 1 -Wicca for beginners - Joy Cunningham.pdf"),
    ]

    assert [g.reason for g in group_units(units)] == [GroupReason.SINGLE, GroupReason.SINGLE]


def test_different_editions_are_not_grouped():
    units = [
        loose(Placement("IT", "Security", "Cryptography"), "Serious Cryptography (for Paul scholle) - Jean-Philippe Aumasson.epub"),
        loose(
            Placement("IT", "Security", "Cryptography"),
            "Serious Cryptography, 2nd Edition_ A Practical Introduction - Jean-Philippe Aumasson.pdf",
        ),
    ]

    assert len(group_units(units)) == 2


def test_same_surname_different_first_name_are_different_authors():
    units = [
        loose(WITCHCRAFT, "Wicca - Scott Cunningham.pdf"),
        loose(WITCHCRAFT, "Wicca- 2 books in 1 -Wicca for beginners - Joy Cunningham.pdf"),
    ]

    assert len(group_units(units)) == 2


def test_accents_in_author_names_are_ignored():
    units = [
        loose(Placement("S", "W"), "The only academic phrasebook - Barros, Luiz Otavio.epub"),
        loose(Placement("S", "W"), "The only academic phrasebook - Barros, Luiz Otávio.pdf"),
    ]

    assert len(group_units(units)) == 1


def test_different_languages_are_not_grouped():
    pytha = Placement("Woodworking", "Pytha")
    units = [loose(pytha, "PYTHA_Parametrik_DEU.pdf"), loose(pytha, "PYTHA_Parametrik_eng.pdf")]

    assert len(group_units(units)) == 2


def test_different_version_or_volume_numbers_are_not_grouped():
    pytha = Placement("Woodworking", "Pytha")
    shoji = Placement("Woodworking", "shoji")
    units = [
        loose(pytha, "PYTHA_v23_Neuerungen_deu.pdf"),
        loose(pytha, "PYTHA_v24_Neuerungen_DEU.pdf"),
        loose(shoji, "The Complete Guide to Shoji and Kumiko Patterns Volume 2 - Desmond King.pdf"),
        loose(shoji, "The Complete Guide to Shoji and Kumiko Patterns Volume 3 - Desmond King.pdf"),
    ]

    assert len(group_units(units)) == 4


def test_one_different_word_makes_a_different_book():
    drechseln = Placement("Woodworking", "drechseln")
    units = [
        loose(drechseln, "Turning Toys with Richard Raffan - Richard Raffan.pdf"),
        loose(drechseln, "Turning wood with Richard Raffan - Richard Raffan.pdf"),
    ]

    assert len(group_units(units)) == 2


def test_truncated_title_groups_with_full_title():
    units = [
        loose(
            Placement("IT", "Security"),
            "Cybersecurity Tabletop Exercises - From Planning to -- Robert Lelewski & John Hollenberger -- "
            "No Starch Press -- a9b22ebdef4502ec9d8e98e9a462e7c1 -- Anna’s Archive.epub",
        ),
        loose(
            Placement("IT", "Security"),
            "Cybersecurity Tabletop Exercises - From Planning to Execution (for Duc Pham) - "
            "Robert Lelewski & John Hollenberger.pdf",
        ),
    ]

    (group,) = group_units(units)

    assert group.needs_review


def test_short_title_prefix_does_not_group():
    units = [loose(ANDROID, "Android - Author A.pdf"), loose(ANDROID, "Android Games - Author A.pdf")]

    assert len(group_units(units)) == 2


def test_missing_author_on_one_side_still_groups_for_review():
    units = [loose(ANDROID, "Beginning Python.pdf"), loose(ANDROID, "Beginning Python - Magnus Lie Hetland.epub")]

    (group,) = group_units(units)

    assert group.needs_review


def test_books_in_different_placements_are_never_grouped():
    units = [loose(ANDROID, "Same Book - Author.pdf"), loose(PENTESTING, "Same Book - Author.pdf")]

    assert len(group_units(units)) == 2


def test_loose_file_joins_matching_book_folder_for_review():
    units = [
        folder(WITCHCRAFT, "Wicca - Vivianne Crowley", "Wicca - Crowley, Vivianne.pdf"),
        loose(WITCHCRAFT, "Wicca - Vivianne Crowley.epub"),
    ]

    (group,) = group_units(units)

    assert group.book_folder == "Wicca - Vivianne Crowley"
    assert group.needs_review


def test_book_folder_alone_keeps_its_folder_and_needs_no_review():
    (group,) = group_units([folder(WITCHCRAFT, "Wicca - Vivianne Crowley", "a.pdf", "b.epub")])

    assert group.reason is GroupReason.BOOK_FOLDER
    assert group.book_folder == "Wicca - Vivianne Crowley"
    assert not group.needs_review


def test_two_existing_book_folders_are_never_merged():
    units = [
        folder(WITCHCRAFT, "Wicca - Vivianne Crowley", "a.pdf"),
        folder(WITCHCRAFT, "Wicca - Vivianne Crowley 2", "b.pdf"),
    ]

    assert len(group_units(units)) == 2


def test_groups_are_in_stable_order():
    units = [loose(ANDROID, "B Book - X.pdf"), loose(ANDROID, "A Book - Y.pdf"), loose(ANDROID, "A Book - Y.epub")]

    groups = group_units(units)

    assert [names(g) for g in groups] == [["A Book - Y.epub", "A Book - Y.pdf"], ["B Book - X.pdf"]]
