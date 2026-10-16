"""About this file: the Open Library metadata source (https://openlibrary.org).

It uses Open Library's public search API, which needs no key: by ISBN when one is known,
otherwise by title and first author. Each result becomes a candidate with title (plus
subtitle), authors, ISBN, publisher, language, first publication year and a cover URL.
When the search finds nothing for an ISBN, the ISBN API (/isbn/<isbn>.json, the edition
record) is asked as well; the edition's authors (or else its work's authors) are read
from their own records, at most MAX_AUTHORS of them. An unknown ISBN (HTTP 404) gives no
candidate.

Open Library allows 1 request per second for anonymous clients and 3 per second when the
User-Agent names a contact; requests are spaced accordingly (see
https://openlibrary.org/developers/api). The contact is only sent when configured.
"""

import json
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable

from ebookprep.filenames import is_valid_isbn
from ebookprep.metadata import BookMetadata
from ebookprep.naming import main_title
from ebookprep.sources import Candidate, LookupQuery, SourceError, Throttle

BASE_URL = "https://openlibrary.org"
SEARCH_URL = f"{BASE_URL}/search.json"
MAX_AUTHORS = 4
COVER_URL = "https://covers.openlibrary.org/b/id/{}-L.jpg"
FIELDS = "title,subtitle,author_name,isbn,publisher,first_publish_year,language,cover_i"
APP_NAME = "ebookprep"
TIMEOUT_SECONDS = 30
ANONYMOUS_INTERVAL = 1.0
IDENTIFIED_INTERVAL = 1 / 3


class OpenLibrarySource:
    name = "Open Library"

    def __init__(
        self,
        fetch_json: Callable[[str], dict] | None = None,
        limit: int = 5,
        contact: str = "",
        min_interval: float | None = None,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ):
        self.user_agent = f"{APP_NAME} ({contact})" if contact else f"{APP_NAME} (personal ebook library tool)"
        self.fetch_json = fetch_json or self._http_get_json
        self.limit = limit
        if min_interval is None:
            min_interval = IDENTIFIED_INTERVAL if contact else ANONYMOUS_INTERVAL
        self.throttle = Throttle(min_interval, clock, sleep)

    def _http_get_json(self, url: str) -> dict:
        request = urllib.request.Request(url, headers={"User-Agent": self.user_agent})
        with urllib.request.urlopen(request, timeout=TIMEOUT_SECONDS) as response:
            return json.load(response)

    def search(self, query: LookupQuery) -> list[Candidate]:
        if query.isbn:
            params = {"isbn": query.isbn}
        else:
            params = {"title": main_title(query.title)}
            if query.authors:
                params["author"] = query.authors[0]
        params |= {"fields": FIELDS, "limit": str(self.limit)}
        url = f"{SEARCH_URL}?{urllib.parse.urlencode(params)}"

        answer = self._get(url)
        candidates = [self._candidate(doc, query) for doc in answer.get("docs", []) if doc.get("title")]
        if not candidates and query.isbn:
            return self._edition_candidates(query.isbn)
        return candidates

    def _get(self, url: str, missing_ok: bool = False) -> dict | None:
        """The JSON answer; None for HTTP 404 when missing_ok."""
        self.throttle.wait()
        try:
            return self.fetch_json(url)
        except urllib.error.HTTPError as error:
            if missing_ok and error.code == 404:
                return None
            raise SourceError(f"Open Library request failed: {error}") from error
        except (OSError, ValueError) as error:
            raise SourceError(f"Open Library request failed: {error}") from error

    def _edition_candidates(self, isbn: str) -> list[Candidate]:
        edition = self._get(f"{BASE_URL}/isbn/{isbn}.json", missing_ok=True)
        if not edition or not edition.get("title"):
            return []
        author_keys = [a["key"] for a in edition.get("authors", ()) if a.get("key")]
        if not author_keys and edition.get("works"):
            work = self._get(f"{BASE_URL}{edition['works'][0]['key']}.json", missing_ok=True) or {}
            author_keys = [a["author"]["key"] for a in work.get("authors", ()) if a.get("author", {}).get("key")]
        authors = []
        for key in author_keys[:MAX_AUTHORS]:
            author = self._get(f"{BASE_URL}{key}.json", missing_ok=True)
            if author and author.get("name"):
                authors.append(author["name"])
        year = re.search(r"\b(\d{4})\b", edition.get("publish_date", ""))
        language = next((lang["key"].rsplit("/", 1)[-1] for lang in edition.get("languages", ()) if lang.get("key")), None)
        metadata = BookMetadata(
            title=edition["title"] + (f": {edition['subtitle']}" if edition.get("subtitle") else ""),
            authors=tuple(authors),
            isbn=isbn,
            publisher=next(iter(edition.get("publishers", ())), None),
            language=language,
            pubdate=year.group(1) if year else None,
        )
        cover = COVER_URL.format(edition["covers"][0]) if edition.get("covers") else None
        return [Candidate(source=self.name, metadata=metadata, cover_url=cover)]

    def _candidate(self, doc: dict, query: LookupQuery) -> Candidate:
        title = doc["title"] + (f": {doc['subtitle']}" if doc.get("subtitle") else "")
        metadata = BookMetadata(
            title=title,
            authors=tuple(doc.get("author_name", ())),
            isbn=_pick_isbn(doc.get("isbn", ()), query.isbn),
            publisher=next(iter(doc.get("publisher", ())), None),
            language=next(iter(doc.get("language", ())), None),
            pubdate=str(doc["first_publish_year"]) if doc.get("first_publish_year") else None,
        )
        cover = COVER_URL.format(doc["cover_i"]) if doc.get("cover_i") else None
        return Candidate(source=self.name, metadata=metadata, cover_url=cover)


def _pick_isbn(isbns: list[str], wanted: str | None) -> str | None:
    if wanted and wanted in isbns:
        return wanted
    valid = [i for i in isbns if is_valid_isbn(i)]
    thirteen = [i for i in valid if len(i) == 13]
    return (thirteen or valid or [None])[0]
