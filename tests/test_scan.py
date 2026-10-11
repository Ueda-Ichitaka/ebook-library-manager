"""About this file: tests for ebookprep.scan, which turns the input folder tree into
source units (loose book files or existing book folders) with their placement."""

from pathlib import Path

from ebookprep.config import Config
from ebookprep.scan import BookFile, Placement, SkippedFile, SourceUnit, scan_input

SECURITY_TOPICS = Config(topic_categories=frozenset({"IT/Security"}))


def touch(root: Path, *relative: str) -> None:
    for rel in relative:
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"x")


def test_loose_file_in_category_is_its_own_unit(tmp_path):
    touch(tmp_path, "IT/Android/Book A - Author.pdf")

    result = scan_input(tmp_path, Config())

    assert result.units == [
        SourceUnit(
            placement=Placement("IT", "Android"),
            files=(BookFile(tmp_path / "IT/Android/Book A - Author.pdf"),),
        )
    ]
    assert result.skipped == []


def test_sidecar_opf_and_cover_are_attached_by_stem(tmp_path):
    touch(tmp_path, "IT/Android/B.pdf", "IT/Android/B.opf", "IT/Android/B.jpg")

    (unit,) = scan_input(tmp_path, Config()).units

    assert unit.files == (
        BookFile(
            tmp_path / "IT/Android/B.pdf",
            opf=tmp_path / "IT/Android/B.opf",
            cover=tmp_path / "IT/Android/B.jpg",
        ),
    )


def test_same_stem_in_two_formats_shares_the_sidecars(tmp_path):
    touch(tmp_path, "IT/Android/S.pdf", "IT/Android/S.epub", "IT/Android/S.opf")

    units = scan_input(tmp_path, Config()).units

    assert [u.files[0].path.name for u in units] == ["S.epub", "S.pdf"]
    assert all(u.files[0].opf == tmp_path / "IT/Android/S.opf" for u in units)


def test_subfolder_of_normal_category_is_a_book_folder(tmp_path):
    touch(
        tmp_path,
        "Spi/Witchcraft/Wicca - Vivianne Crowley/Wicca - Crowley, Vivianne.pdf",
        "Spi/Witchcraft/Wicca - Vivianne Crowley/Wicca - Crowley, Vivianne.opf",
        "Spi/Witchcraft/Wicca - Vivianne Crowley/Wicca - Vivianne Crowley.epub",
    )

    (unit,) = scan_input(tmp_path, Config()).units

    folder = tmp_path / "Spi/Witchcraft/Wicca - Vivianne Crowley"
    assert unit.placement == Placement("Spi", "Witchcraft")
    assert unit.book_folder == "Wicca - Vivianne Crowley"
    assert unit.files == (
        BookFile(folder / "Wicca - Crowley, Vivianne.pdf", opf=folder / "Wicca - Crowley, Vivianne.opf"),
        BookFile(folder / "Wicca - Vivianne Crowley.epub"),
    )


def test_book_folder_collects_files_from_nested_folders(tmp_path):
    touch(tmp_path, "IT/Micro/Home Security/Home Security.epub", "IT/Micro/Home Security/extra/notes.pdf")

    (unit,) = scan_input(tmp_path, Config()).units

    assert [f.path.name for f in unit.files] == ["Home Security.epub", "notes.pdf"]


def test_topic_category_subfolders_are_topics(tmp_path):
    touch(
        tmp_path,
        "IT/Security/Loose Book.pdf",
        "IT/Security/Pentesting/P1.pdf",
        "IT/Security/Pentesting/P2.epub",
        "IT/Security/Forensic/Only Book.pdf",
    )

    units = scan_input(tmp_path, SECURITY_TOPICS).units

    by_name = {u.files[0].path.name: u for u in units}
    assert by_name["Loose Book.pdf"].placement == Placement("IT", "Security")
    assert by_name["P1.pdf"].placement == Placement("IT", "Security", "Pentesting")
    assert by_name["P2.epub"].placement == Placement("IT", "Security", "Pentesting")
    assert by_name["Only Book.pdf"].placement == Placement("IT", "Security", "Forensic")
    assert all(u.book_folder is None for u in units)


def test_subfolder_of_topic_folder_is_a_book_folder(tmp_path):
    touch(tmp_path, "IT/Security/Pentesting/Black Hat Bash/Black Hat Bash.epub")

    (unit,) = scan_input(tmp_path, SECURITY_TOPICS).units

    assert unit.placement == Placement("IT", "Security", "Pentesting")
    assert unit.book_folder == "Black Hat Bash"


def test_skipped_files_are_reported_with_reason(tmp_path):
    touch(
        tmp_path,
        "top.pdf",
        "IT/library-level.pdf",
        "IT/Python/Distributed Computing with Python.zip",
        "IT/Micro/Build.rar",
        "IT/Security/Pentesting/Thumbs.db",
        "IT/Android/.DS_Store",
        "IT/Android/notes.txt",
        "IT/Android/Lonely.opf",
        "IT/Android/Lonely.jpg",
    )

    result = scan_input(tmp_path, SECURITY_TOPICS)

    assert result.units == []
    assert set(result.skipped) == {
        SkippedFile(tmp_path / "top.pdf", "outside a category"),
        SkippedFile(tmp_path / "IT/library-level.pdf", "outside a category"),
        SkippedFile(tmp_path / "IT/Python/Distributed Computing with Python.zip", "archive"),
        SkippedFile(tmp_path / "IT/Micro/Build.rar", "archive"),
        SkippedFile(tmp_path / "IT/Security/Pentesting/Thumbs.db", "junk"),
        SkippedFile(tmp_path / "IT/Android/.DS_Store", "junk"),
        SkippedFile(tmp_path / "IT/Android/notes.txt", "unsupported format"),
        SkippedFile(tmp_path / "IT/Android/Lonely.opf", "sidecar without book"),
        SkippedFile(tmp_path / "IT/Android/Lonely.jpg", "sidecar without book"),
    }


def test_archive_inside_book_folder_is_skipped_but_book_kept(tmp_path):
    touch(tmp_path, "IT/Micro/Pi/Pi.epub", "IT/Micro/Pi/Pi Code.zip")

    result = scan_input(tmp_path, Config())

    assert [f.path.name for f in result.units[0].files] == ["Pi.epub"]
    assert result.skipped == [SkippedFile(tmp_path / "IT/Micro/Pi/Pi Code.zip", "archive")]


def test_extension_matching_is_case_insensitive(tmp_path):
    touch(tmp_path, "IT/Android/UPPER.PDF", "IT/Android/UPPER.OPF")

    (unit,) = scan_input(tmp_path, Config()).units

    assert unit.files[0].opf == tmp_path / "IT/Android/UPPER.OPF"


def test_mobi_and_azw_are_book_files(tmp_path):
    touch(tmp_path, "S/W/a.mobi", "S/W/b.azw", "S/W/c.azw3")

    units = scan_input(tmp_path, Config()).units

    assert [u.files[0].path.name for u in units] == ["a.mobi", "b.azw", "c.azw3"]


def test_placement_parts():
    assert Placement("IT", "Android").parts() == ("IT", "Android")
    assert Placement("IT", "Security", "Pentesting").parts() == ("IT", "Security", "Pentesting")
