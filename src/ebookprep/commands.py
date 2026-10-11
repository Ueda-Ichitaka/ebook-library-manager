"""About this file: the pipeline commands the web UI can start as jobs.

Each command has a label and description for its button and builds the command line it
runs (the same command line interface as on the console, see __main__.py). Commands that
support it can be limited to placements such as IT/Android.
"""

import sys
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class CommandSettings:
    input: Path
    output: Path
    work: Path
    config: Path
    kavita_url: str


@dataclass(frozen=True)
class Command:
    label: str
    description: str
    supports_only: bool


COMMANDS = {
    "identify": Command(
        "Identify new books",
        "Find new files in the input and decide their metadata: online lookup checked against the book's "
        "first pages; accepted, or waiting for review.",
        True,
    ),
    "rescore": Command(
        "Re-decide stored books",
        "Apply the current rules to all stored books again, from stored candidates (no online requests). "
        "Your decisions are kept.",
        False,
    ),
    "build": Command(
        "Build",
        "Write accepted books into the output: new books, changed decisions, excluded duplicates.",
        True,
    ),
    "report": Command("Write report", "Write work/report.md with the newest result of every book.", False),
    "kavita-check": Command(
        "Check in Kavita",
        "Ask the Kavita preview whether every built book is its own series named after its title.",
        False,
    ),
    "kavita-scan": Command(
        "Rescan Kavita and check",
        "Rescan all Kavita libraries one after another, then check (use after a build).",
        False,
    ),
}


def command_argv(name: str, settings: CommandSettings, only: tuple[str, ...] = ()) -> list[str]:
    command = COMMANDS[name]
    s = settings
    paths = {
        "identify": ["identify", "--input", str(s.input), "--work", str(s.work), "--config", str(s.config)],
        "rescore": ["rescore", "--input", str(s.input), "--work", str(s.work)],
        "build": ["build", "--input", str(s.input), "--output", str(s.output), "--work", str(s.work),
                  "--config", str(s.config)],
        "report": ["report", "--work", str(s.work)],
        "kavita-check": ["kavita-check", "--output", str(s.output), "--work", str(s.work), "--config",
                         str(s.config), "--url", s.kavita_url],
    }
    args = paths["kavita-check"] + ["--scan"] if name == "kavita-scan" else paths[name]
    if command.supports_only and only:
        args += ["--only", *only]
    return [sys.executable, "-m", "ebookprep", *args]
