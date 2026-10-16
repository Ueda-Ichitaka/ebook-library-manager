"""About this file: tests for ebookprep.commands, the pipeline commands the web UI can
start, with the command lines they run."""

import sys
from pathlib import Path

import pytest

from ebookprep.commands import COMMANDS, CommandSettings, command_argv

SETTINGS = CommandSettings(
    input=Path("/data/input"), output=Path("/data/output"), work=Path("/data/work"),
    config=Path("/app/config.toml"), kavita_url="http://kavita:5000",
)
BASE = [sys.executable, "-m", "ebookprep"]


def test_every_command_has_a_label_and_description():
    assert set(COMMANDS) == {
        "identify", "detect-moves", "relookup", "rescore", "build", "report", "kavita-scan"
    }
    assert all(c.label and c.description for c in COMMANDS.values())


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("identify", ["identify", "--input", "/data/input", "--work", "/data/work", "--config", "/app/config.toml"]),
        ("rescore", ["rescore", "--input", "/data/input", "--work", "/data/work"]),
        ("detect-moves", ["detect-moves", "--input", "/data/input", "--work", "/data/work", "--config", "/app/config.toml"]),
        ("relookup", ["relookup", "--input", "/data/input", "--work", "/data/work", "--config", "/app/config.toml"]),
        ("build", ["build", "--input", "/data/input", "--output", "/data/output", "--work", "/data/work",
                   "--config", "/app/config.toml"]),
        ("report", ["report", "--work", "/data/work"]),
        ("kavita-scan", ["kavita-check", "--output", "/data/output", "--work", "/data/work", "--config",
                         "/app/config.toml", "--url", "http://kavita:5000", "--scan"]),
    ],
)
def test_command_lines(name, expected):
    assert command_argv(name, SETTINGS) == BASE + expected


def test_only_is_passed_to_commands_that_support_it():
    assert command_argv("build", SETTINGS, only=("IT/Android", "Woodworking"))[-3:] == ["--only", "IT/Android", "Woodworking"]
    assert "--only" not in command_argv("report", SETTINGS, only=("IT",))


def test_unknown_command():
    with pytest.raises(KeyError):
        command_argv("format-disk", SETTINGS)
