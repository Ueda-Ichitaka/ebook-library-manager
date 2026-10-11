"""About this file: the online metadata sources the identify stage can use.

calibre's built-in sources (one per download plugin) and Open Library's own API. All of
them are used unless config.toml lists the wanted ones by name under "sources"; they are
then used in that order. Adding a source means adding it to available_sources.
"""

from ebookprep.config import Config, ConfigError
from ebookprep.sources import MetadataSource
from ebookprep.sources.calibre_source import PLUGINS, CalibreSource
from ebookprep.sources.openlibrary import OpenLibrarySource


def available_sources(config: Config) -> list[MetadataSource]:
    return [*(CalibreSource(plugin) for plugin in PLUGINS), OpenLibrarySource(contact=config.openlibrary_contact)]


def default_sources(config: Config) -> list[MetadataSource]:
    available = available_sources(config)
    if config.sources is None:
        return available
    by_name = {source.name: source for source in available}
    unknown = [name for name in config.sources if name not in by_name]
    if unknown:
        raise ConfigError(f"unknown sources {unknown}; available: {', '.join(by_name)}")
    return [by_name[name] for name in config.sources]
