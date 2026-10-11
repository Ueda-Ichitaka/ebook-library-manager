"""About this file: end-to-end test against a real Kavita 0.9.1.4 (same version as the
server). A synthetic input library goes through the real pipeline (identify, decisions,
build) into the folder Kavita reads; Kavita scans it, and its API must show every built
book as its own series named after its title. As a negative control, untreated PDFs
lying in a category folder (the original problem) must be detected as merged.

Runs only in the compose profile "e2e" (fresh Kavita per run, see compose.yaml):
    docker compose --profile e2e run --rm e2e
    docker compose --profile e2e rm -sf kavita-e2e
"""

import os
import shutil
import time
import urllib.request
from pathlib import Path

import pytest
from conftest import damage_trailer, make_epub, make_pdf

from ebookprep.config import Config
from ebookprep.identify import identify
from ebookprep.kavita import KavitaClient
from ebookprep.kavita_verify import expected_files, verify
from ebookprep.pipeline import build_accepted
from ebookprep.state import StateStore

KAVITA_URL = os.environ.get("KAVITA_E2E_URL")
LIBRARY = Path(os.environ.get("E2E_LIBRARY", "/nonexistent"))

pytestmark = [
    pytest.mark.kavita,
    pytest.mark.skipif(not KAVITA_URL, reason="needs the Kavita of the compose profile e2e"),
]

CONFIG = Config(topic_categories=frozenset({"IT/Security"}))
CATEGORY_SERIES = '<meta name="calibre:series" content="Pentesting"/><meta name="calibre:series_index" content="14"/>'


def wait_for_kavita(url: str, timeout: float = 180) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(f"{url}/api/health", timeout=5) as response:
                if response.status == 200:
                    return
        except OSError:
            pass
        time.sleep(2)
    raise TimeoutError(f"Kavita at {url} didn't start")


def synthetic_input(root: Path) -> None:
    android = root / "IT" / "Android"
    android.mkdir(parents=True)
    damage_trailer(make_pdf(android / "Beginning Android Games - Robert Green.pdf"))
    damage_trailer(make_pdf(android / "Android Sensor Programming by Example - Varun Nagpal.pdf"))
    make_pdf(android / "Apache Spark Graph Processing - Rindra Ramamonjison.pdf")
    make_epub(android / "Apache Spark Graph Processing - Rindra Ramamonjison.epub", title="Untitled")

    pentesting = root / "IT" / "Security" / "Pentesting"
    pentesting.mkdir(parents=True)
    make_epub(pentesting / "Black Hat Bash - Dolev Farhi.epub", title="Black Hat Bash", extra_metadata=CATEGORY_SERIES)
    make_epub(pentesting / "Ethical Hacking - Daniel G. Graham.epub", title="Ethical Hacking", extra_metadata=CATEGORY_SERIES)

    wicca = root / "Spiritualität" / "Witchcraft" / "Wicca - Vivianne Crowley"
    wicca.mkdir(parents=True)
    make_pdf(wicca / "Wicca - Crowley, Vivianne.pdf")


def raw_category_folder(library: Path) -> None:
    """The original problem: untreated PDFs without title lying in a category folder."""
    raw = library / "Raw" / "Android"
    raw.mkdir(parents=True)
    damage_trailer(make_pdf(raw / "Beginning Android Games - Robert Green.pdf"))
    make_pdf(raw / "Android Sensor Programming by Example - Varun Nagpal.pdf")


def test_kavita_lists_every_built_book_on_its_own(tmp_path):
    for entry in LIBRARY.iterdir() if LIBRARY.exists() else []:
        shutil.rmtree(entry) if entry.is_dir() else entry.unlink()
    source, work = tmp_path / "input", tmp_path / "work"
    synthetic_input(source)
    identify(source, work, CONFIG, sources=[], log=lambda _: None)
    store = StateStore(work / "state.db")
    for book in store.books(status="review"):
        store.decide(book.id, book.metadata, decided_by="user")
    build_accepted(source, LIBRARY, work, CONFIG, log=lambda _: None)
    raw_category_folder(LIBRARY)

    wait_for_kavita(KAVITA_URL)
    kavita = KavitaClient(KAVITA_URL)
    kavita.register_admin("e2e-admin", "E2e-Test-Passw0rd!")
    for library in sorted(p.name for p in LIBRARY.iterdir() if p.is_dir()):
        kavita.create_book_library(library, [f"/library/{library}"], timeout=300)

    expected = expected_files(store, LIBRARY, mislabeled_folder=CONFIG.mislabeled_folder)
    result = verify(kavita, expected, library_root="/library")

    assert len(expected) == 7
    assert result.missing == []
    assert result.wrong_name == []
    built_merged = [(name, files) for name, files in result.merged if not all(f.startswith("Raw/") for f in files)]
    assert built_merged == []
    assert {f for f in result.unexpected} == {
        "Raw/Android/Beginning Android Games - Robert Green.pdf",
        "Raw/Android/Android Sensor Programming by Example - Varun Nagpal.pdf",
    }
    raw_merged = [name for name, files in result.merged if all(f.startswith("Raw/") for f in files)]
    assert raw_merged == ["Android"], "negative control: untreated files must end up in one folder-named series"
