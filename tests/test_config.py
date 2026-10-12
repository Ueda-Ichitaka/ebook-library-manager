"""About this file: tests for ebookprep.config, the loader of config.toml."""

from pathlib import Path

import pytest

from ebookprep.config import Config, ConfigError, load_config


def write(tmp_path: Path, text: str) -> Path:
    path = tmp_path / "config.toml"
    path.write_text(text, encoding="utf-8")
    return path


def test_loads_topic_categories(tmp_path):
    path = write(tmp_path, 'topic_categories = ["IT/Security", "Woodworking/joinery"]\n')

    config = load_config(path)

    assert config.topic_categories == frozenset({"IT/Security", "Woodworking/joinery"})


def test_topic_categories_default_to_empty(tmp_path):
    path = write(tmp_path, "")

    assert load_config(path).topic_categories == frozenset()


def test_is_topic_category(tmp_path):
    config = Config(topic_categories=frozenset({"IT/Security"}))

    assert config.is_topic_category("IT", "Security")
    assert not config.is_topic_category("IT", "Android")
    assert not config.is_topic_category("Woodworking", "Security")


def test_trailing_slashes_are_ignored(tmp_path):
    path = write(tmp_path, 'topic_categories = ["IT/Security/"]\n')

    assert load_config(path).topic_categories == frozenset({"IT/Security"})


@pytest.mark.parametrize("entry", ["IT", "IT/Security/Pentesting", "/Security", ""])
def test_entry_must_be_library_slash_category(tmp_path, entry):
    path = write(tmp_path, f'topic_categories = ["{entry}"]\n')

    with pytest.raises(ConfigError, match="Library/Category"):
        load_config(path)


def test_topic_categories_must_be_a_list_of_strings(tmp_path):
    path = write(tmp_path, 'topic_categories = "IT/Security"\n')

    with pytest.raises(ConfigError, match="list"):
        load_config(path)


def test_project_config_file_is_valid():
    project_config = Path(__file__).resolve().parent.parent / "config.toml"

    assert load_config(project_config).is_topic_category("IT", "Security")


def test_openlibrary_contact_is_optional(tmp_path):
    assert load_config(write(tmp_path, "")).openlibrary_contact == ""
    assert load_config(write(tmp_path, 'openlibrary_contact = "me@example.org"\n')).openlibrary_contact == "me@example.org"


def test_openlibrary_contact_must_be_a_string(tmp_path):
    with pytest.raises(ConfigError, match="openlibrary_contact"):
        load_config(write(tmp_path, "openlibrary_contact = 5\n"))


def test_mislabeled_folder_defaults_and_validation(tmp_path):
    assert load_config(write(tmp_path, "")).mislabeled_folder == "_mislabeled"
    assert load_config(write(tmp_path, 'mislabeled_folder = "wrong files"\n')).mislabeled_folder == "wrong files"
    for bad in ('"a/b"', '""', "3"):
        with pytest.raises(ConfigError, match="mislabeled_folder"):
            load_config(write(tmp_path, f"mislabeled_folder = {bad}\n"))


def test_sources_are_optional_and_must_be_a_list_of_names(tmp_path):
    assert load_config(write(tmp_path, "")).sources is None
    assert load_config(write(tmp_path, 'sources = ["Open Library", "calibre: Google"]\n')).sources == (
        "Open Library",
        "calibre: Google",
    )
    with pytest.raises(ConfigError, match="sources"):
        load_config(write(tmp_path, 'sources = "Open Library"\n'))


def test_ignored_folder_defaults_and_validation(tmp_path):
    assert load_config(write(tmp_path, "")).ignored_folder == "_ignored"
    assert load_config(write(tmp_path, 'ignored_folder = "not books"\n')).ignored_folder == "not books"
    for bad in ('"a/b"', '""', "3"):
        with pytest.raises(ConfigError, match="ignored_folder"):
            load_config(write(tmp_path, f"ignored_folder = {bad}\n"))
