"""About this file: calibre's built-in metadata sources (Google, Amazon.com, Edelweiss).

Each calibre download plugin is used as a separate source, so the review shows one
candidate per plugin instead of calibre's merged guess. fetch-ebook-metadata prints the
result as OPF, which is parsed like any other .opf file. When a plugin finds nothing (or
is blocked, which calibre reports as "no results"), the source returns no candidate.
"""

import subprocess
from collections.abc import Callable

from ebookprep.metadata import parse_opf
from ebookprep.sources import Candidate, LookupQuery, SourceError

PLUGINS = ("Google", "Amazon.com", "Edelweiss")
TIMEOUT_SECONDS = 60


def _run(args: list[str]) -> str | None:
    try:
        result = subprocess.run(args, capture_output=True, text=True, timeout=TIMEOUT_SECONDS + 30)
    except subprocess.TimeoutExpired as error:
        raise SourceError(f"{args[2]} timed out") from error
    output = result.stdout.strip()
    return output if result.returncode == 0 and output.startswith("<?xml") else None


class CalibreSource:
    def __init__(self, plugin: str, runner: Callable[[list[str]], str | None] = _run):
        self.plugin = plugin
        self.name = f"calibre: {plugin}"
        self.runner = runner

    def search(self, query: LookupQuery) -> list[Candidate]:
        args = ["fetch-ebook-metadata", "--allowed-plugin", self.plugin, "--title", query.title]
        if query.authors:
            args += ["--authors", " & ".join(query.authors)]
        if query.isbn:
            args += ["--isbn", query.isbn]
        args += ["--timeout", str(TIMEOUT_SECONDS), "--opf"]

        output = self.runner(args)
        if output is None:
            return []
        return [Candidate(source=self.name, metadata=parse_opf(output).metadata)]
