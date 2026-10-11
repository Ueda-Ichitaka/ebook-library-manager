"""About this file: tests for ebookprep.filenames, which extracts title, authors, year and
ISBN from book filenames. The cases are real filenames from the input library."""

import pytest

from ebookprep.filenames import FilenameInfo, clean_title, is_valid_isbn, parse_authors, parse_filename


# --- Anna's Archive: "Title -- Authors -- edition/place/date -- publisher -- isbn -- md5 -- Anna's Archive"


def test_annas_archive_full():
    stem = (
        "Linux firewalls _ attack detection and response with -- Michael Rash -- September 15, 2007 -- "
        "No Starch Press, Incorporated -- 9781593271411 -- 848cf4bd357b684fd1871da94b3ff998 -- Anna’s Archive"
    )

    assert parse_filename(stem) == FilenameInfo(
        title="Linux firewalls: attack detection and response with",
        authors=("Michael Rash",),
        year=2007,
        isbn="9781593271411",
    )


def test_annas_archive_isbn13_prefix_and_truncated_suffix():
    stem = (
        "Black Hat Bash (EARLY ACCESS) -- Dolev Farhi , Nick Aleks -- 2023 -- No Starch Press, Incorporated -- "
        "isbn13 9781718503748 -- 42df58055564c5c25cb4c8592295313c -- Anna’s Archi"
    )

    info = parse_filename(stem)

    assert info.title == "Black Hat Bash (EARLY ACCESS)"
    assert info.authors == ("Dolev Farhi", "Nick Aleks")
    assert info.year == 2023
    assert info.isbn == "9781718503748"


def test_annas_archive_without_year_or_isbn():
    stem = "Starting Ansible_ Easy guide for beginners -- Nathan Hull -- 65819be781ee9b47fe626067accb618b -- Anna’s Archive"

    assert parse_filename(stem) == FilenameInfo(
        title="Starting Ansible: Easy guide for beginners", authors=("Nathan Hull",)
    )


def test_annas_archive_leading_isbn_brackets():
    stem = (
        "[0834815168] [9780834815162] The Art Of Japanese -- Kiyosi Seike; translated and adapted with an "
        "introduction by -- 1st English ed_, New York, NY, USA -- 9780834815162 -- c1ef814fa8f608813dbf2a5387c42d9a -- Anna’"
    )

    info = parse_filename(stem)

    assert info.title == "The Art Of Japanese"
    assert info.authors == ("Kiyosi Seike",)
    assert info.isbn == "9780834815162"


def test_annas_archive_last_first_author_with_role():
    stem = (
        "All New Turning Projects with Richard Raffan -- Raffan, Richard, author -- Newtown, CT, 2015 -- "
        "Taunton Press, Incorporated -- 9781627107921 -- bca689e923ae4af33d004610ac26f3df -- Anna’s Archive"
    )

    info = parse_filename(stem)

    assert info.authors == ("Richard Raffan",)
    assert info.year == 2015


def test_annas_archive_by_prefix_and_initials():
    stem = (
        "Physical Security and the Inspection Process -- By Carl A_ Roper -- 1, 1997 -- Butterworth-Heinemann Ltd -- "
        "isbn13 9780750697125 -- 4f102068a0cfeb379c7734b6653516a8 -- Anna’s Archive"
    )

    assert parse_filename(stem).authors == ("Carl A. Roper",)


def test_annas_archive_ignores_invalid_isbn_looking_numbers():
    stem = "Some Book -- Some Author -- 2010 -- 9781234567890 -- 3539a7e50963bfc613ce7ccdcec63f6a -- Anna’s Archive"

    assert parse_filename(stem).isbn is None


# --- libgen style: "Authors - Title (Year, Publisher)"


def test_libgen_author_first_with_year_publisher():
    stem = "Matt Kenney - The Art of Kumiko_ Learn to Make Beautiful Panels by Hand (2020, Blue Hills Press)"

    assert parse_filename(stem) == FilenameInfo(
        title="The Art of Kumiko: Learn to Make Beautiful Panels by Hand",
        authors=("Matt Kenney",),
        year=2020,
    )


def test_libgen_publisher_with_nested_parentheses():
    stem = (
        "Daniel Moore - Offensive Cyber Operations_ Understanding Intangible Warfare "
        "(2022, Lightning Source Inc. (Tier 1)_ Oxford University Press, USA)"
    )

    assert parse_filename(stem) == FilenameInfo(
        title="Offensive Cyber Operations: Understanding Intangible Warfare", authors=("Daniel Moore",), year=2022
    )


def test_libgen_with_trailing_source_tag():
    stem = "Geerling, Jeff - Ansible for DevOps (2015, leanpub.com) - libgen.li"

    assert parse_filename(stem) == FilenameInfo(title="Ansible for DevOps", authors=("Jeff Geerling",), year=2015)


# --- calibre export style: "Title - Authors"


def test_calibre_title_author():
    assert parse_filename("Android Sensor Programming by Example - Varun Nagpal") == FilenameInfo(
        title="Android Sensor Programming by Example", authors=("Varun Nagpal",)
    )


def test_calibre_multiple_authors_with_ampersand():
    stem = "Black Hat Python, 2nd Edition_ Python Programming for Hackers and Pentesters - Justin Seitz & Tim Arnold"

    assert parse_filename(stem) == FilenameInfo(
        title="Black Hat Python, 2nd Edition: Python Programming for Hackers and Pentesters",
        authors=("Justin Seitz", "Tim Arnold"),
    )


def test_calibre_title_with_dashes_splits_at_last_dash():
    stem = "Das Necronomicon - Die Goetia - Abdul Alhazred"

    assert parse_filename(stem) == FilenameInfo(title="Das Necronomicon - Die Goetia", authors=("Abdul Alhazred",))


def test_calibre_author_sort_brackets_are_removed():
    stem = "Pflanzen der Kelten - Storl, Wolf-Dieter [Storl, Wolf-Dieter]"

    assert parse_filename(stem).authors == ("Wolf-Dieter Storl",)


def test_calibre_watermark_for_name_is_removed():
    stem = "Metasploit (for Raymond Rhine) - David Kennedy, et al_"

    assert parse_filename(stem) == FilenameInfo(title="Metasploit", authors=("David Kennedy",))


# --- plain names


def test_plain_title_only():
    assert parse_filename("Beginning Reactive Programming with Swift") == FilenameInfo(
        title="Beginning Reactive Programming with Swift"
    )


def test_snake_case_title():
    assert parse_filename("getting_started_with_arduino_2nd_edition") == FilenameInfo(
        title="getting started with arduino 2nd edition"
    )


def test_snake_case_by_author():
    assert parse_filename("Star_Wars_Der_Kristallstern_by_Vonda_McIntyre") == FilenameInfo(
        title="Star Wars Der Kristallstern", authors=("Vonda McIntyre",)
    )


def test_ebook_tag_is_removed():
    assert parse_filename("Building a Home Security System with Raspberry Pi [eBook]").title == (
        "Building a Home Security System with Raspberry Pi"
    )


# --- building blocks


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("Kill it with fire _ manage aging computer systems", "Kill it with fire: manage aging computer systems"),
        ("The Spiral Dance- A Rebirth of the Ancient Religion", "The Spiral Dance: A Rebirth of the Ancient Religion"),
        ("Turning wood with Richard Raffan_", "Turning wood with Richard Raffan"),
        ("Network   Know-How", "Network Know-How"),
        ("Go H__ck Yourself_ A Simple Introduction", "Go H__ck Yourself: A Simple Introduction"),
    ],
)
def test_clean_title(raw, expected):
    assert clean_title(raw) == expected


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("Kirkland, Eric", ("Eric Kirkland",)),
        ("Phil Davy, Ben Plewes", ("Phil Davy", "Ben Plewes")),
        ("Baker, Paul R_, Benny, Daniel J_", ("Paul R. Baker", "Daniel J. Benny")),
        ("Crowley, Aleister, 1875-1947", ("Aleister Crowley",)),
        ("Herbert Lin & Amy Zegart, editors", ("Herbert Lin", "Amy Zegart")),
        ("Nikolay Elenkov(Author)", ("Nikolay Elenkov",)),
        ("Deviant Ollam; Shane Lawson, technical editor", ("Deviant Ollam", "Shane Lawson")),
        ("John               Ross", ("John Ross",)),
        ("Janet Farrar [Janet; Farrar, Stewart]", ("Janet Farrar",)),
        ("Easttom, William (Chuck), II", ("William Easttom II",)),
        ("Pile, John, Jr.", ("John Pile Jr.",)),
        ("John Pile Jr, Jane Doe", ("John Pile Jr", "Jane Doe")),
        ("", ()),
    ],
)
def test_parse_authors(raw, expected):
    assert parse_authors(raw) == expected


@pytest.mark.parametrize(
    ("isbn", "valid"),
    [
        ("9781593271411", True),
        ("9780123979308", True),
        ("0834815168", True),
        ("080504891X", True),
        ("0834815169", False),
        ("9781234567890", False),
        ("12345", False),
    ],
)
def test_is_valid_isbn(isbn, valid):
    assert is_valid_isbn(isbn) is valid
