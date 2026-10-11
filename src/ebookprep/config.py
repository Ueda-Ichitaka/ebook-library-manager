"""About this file: loads and validates config.toml.

The config tells the tool which categories hold topic folders. Those categories get the
layout Library/Category/Topic/Book/; every other category gets Library/Category/Book/.
Whether a folder is a book folder or a topic folder can't be reliably detected from the
files, so it is configured explicitly.

openlibrary_contact is optional: a contact (e.g. an email address) sent to Open Library
in the User-Agent, which raises its rate limit. Nothing is sent when it's empty.

sources lists the online metadata sources to use, by name (see sources/registry.py); all
known sources are used when it is missing.

mislabeled_folder is the top-level output folder for files that contain a different book
than their name says; it is outside the library folders, so Kavita doesn't show them.
"""

import tomllib
from dataclasses import dataclass
from pathlib import Path


class ConfigError(ValueError):
    """Raised when config.toml is malformed."""


@dataclass(frozen=True)
class Config:
    # "Library/Category" paths, relative to the input folder.
    topic_categories: frozenset[str] = frozenset()
    openlibrary_contact: str = ""
    mislabeled_folder: str = "_mislabeled"
    # names of the online metadata sources to use, None for all
    sources: tuple[str, ...] | None = None

    def is_topic_category(self, library: str, category: str) -> bool:
        return f"{library}/{category}" in self.topic_categories


def load_config(path: Path) -> Config:
    with path.open("rb") as handle:
        data = tomllib.load(handle)

    entries = data.get("topic_categories", [])
    if not isinstance(entries, list) or not all(isinstance(e, str) for e in entries):
        raise ConfigError("topic_categories must be a list of strings")

    contact = data.get("openlibrary_contact", "")
    if not isinstance(contact, str):
        raise ConfigError("openlibrary_contact must be a string")

    mislabeled = data.get("mislabeled_folder", "_mislabeled")
    if not isinstance(mislabeled, str) or not mislabeled.strip() or "/" in mislabeled or "\\" in mislabeled:
        raise ConfigError("mislabeled_folder must be a single folder name")

    sources = data.get("sources")
    if sources is not None and (not isinstance(sources, list) or not all(isinstance(n, str) for n in sources)):
        raise ConfigError("sources must be a list of source names")

    return Config(
        topic_categories=frozenset(_validate_entry(e) for e in entries),
        openlibrary_contact=contact,
        mislabeled_folder=mislabeled.strip(),
        sources=tuple(sources) if sources is not None else None,
    )


def _validate_entry(entry: str) -> str:
    parts = entry.rstrip("/").split("/")
    if len(parts) != 2 or not all(parts):
        raise ConfigError(f"topic_categories entry {entry!r} must have the form Library/Category")
    return "/".join(parts)
