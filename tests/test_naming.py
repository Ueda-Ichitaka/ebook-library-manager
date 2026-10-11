"""About this file: tests for ebookprep.naming, which builds the output folder and file
names ("Main title - First author") and keeps them unique and filesystem-safe."""

import pytest

from ebookprep.naming import MAX_NAME_BYTES, book_name, main_title, safe_name, unique_book_name


@pytest.mark.parametrize(
    ("title", "expected"),
    [
        ("Black Hat Go: Go Programming for Hackers and Pentesters", "Black Hat Go"),
        ("Black Hat Python, 2nd Edition: Python Programming", "Black Hat Python, 2nd Edition"),
        ("Das Necronomicon - Die Goetia", "Das Necronomicon"),
        ("Evasive Malware - A Field Guide to Detecting: Threats", "Evasive Malware"),
        ("Network Know-How: An Essential Guide", "Network Know-How"),
        ("Wicca", "Wicca"),
        (": odd", ": odd"),
    ],
)
def test_main_title_cuts_subtitle_after_colon_or_dash(title, expected):
    assert main_title(title) == expected


def test_book_name_uses_main_title_and_first_author():
    name = book_name("Black Hat Go: Go Programming", ("Tom Steele", "Chris Patten", "Dan Kottmann"))

    assert name == "Black Hat Go - Tom Steele"


def test_book_name_without_author_is_just_the_title():
    assert book_name("Beginning Python", ()) == "Beginning Python"


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("AC/DC Guide", "AC-DC Guide"),
        ('What? Why* "Now" <x> |y|', "What Why Now x y"),
        ("Trailing dots...", "Trailing dots"),
        ("  many   spaces  ", "many spaces"),
        ("tab\tand\nnewline", "tab and newline"),
        ("Back\\slash", "Back-slash"),
        ("Beginning Android C++ Game Development", "Beginning Android Cplusplus Game Development"),
    ],
)
def test_safe_name(raw, expected):
    assert safe_name(raw) == expected


def test_safe_name_normalizes_unicode_to_nfc():
    decomposed = "Spiritualität"

    assert safe_name(decomposed) == "Spiritualität"


def test_safe_name_caps_length_at_word_boundary():
    long = "Word " * 100

    name = safe_name(long)

    assert len(name.encode("utf-8")) <= MAX_NAME_BYTES
    assert not name.endswith(" ")
    assert name.endswith("Word")


def test_unique_name_without_collision():
    assert unique_book_name("Hacking", ("Kevin Beaver",), taken=set()) == "Hacking - Kevin Beaver"


def test_unique_name_adds_qualifier_on_collision():
    taken = {"hacking - kevin beaver"}

    name = unique_book_name("Hacking", ("Kevin Beaver",), taken=taken, qualifier="2nd Edition")

    assert name == "Hacking (2nd Edition) - Kevin Beaver"


def test_unique_name_adds_counter_when_qualifier_missing_or_taken():
    taken = {"hacking - kevin beaver", "hacking (2018) - kevin beaver"}

    assert unique_book_name("Hacking", ("Kevin Beaver",), taken=taken) == "Hacking (2) - Kevin Beaver"
    assert unique_book_name("Hacking", ("Kevin Beaver",), taken=taken, qualifier="2018") == (
        "Hacking (2) - Kevin Beaver"
    )


def test_unique_name_collision_is_case_insensitive():
    taken = {"wicca - vivianne crowley"}

    assert unique_book_name("WICCA", ("Vivianne Crowley",), taken=taken) == "WICCA (2) - Vivianne Crowley"


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("Wait.. What", "Wait. What"),
        ("...And Justice", "And Justice"),
        (".hidden", "hidden"),
    ],
)
def test_safe_name_avoids_names_calibre_would_rewrite(raw, expected):
    """calibre's export replaces '..' and a leading '.' with '_' (calibre.sanitize_file_name)."""
    assert safe_name(raw) == expected
