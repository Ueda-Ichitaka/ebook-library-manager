"""About this file: the Open Library metadata source (https://openlibrary.org).

It uses Open Library's public search API, which needs no key: by ISBN when one is known,
otherwise by title and first author. Each result becomes a candidate with title (plus
subtitle), authors, ISBN, publisher, language, first publication year and a cover URL.

Open Library allows 1 request per second for anonymous clients and 3 per second when the
User-Agent names a contact; requests are spaced accordingly (see
https://openlibrary.org/developers/api). The contact is only sent when configured.
"""

import json
import time
import urllib.parse
import urllib.request
from collections.abc import Callable

from ebookprep.filenames import is_valid_isbn
from ebookprep.metadata import BookMetadata
from ebookprep.naming import main_title
from ebookprep.sources import Candidate, LookupQuery, SourceError

SEARCH_URL = "https://openlibrary.org/search.json"
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
        self.min_interval = min_interval
        self.clock, self.sleep = clock, sleep
        self._last_request: float | None = None

    def _http_get_json(self, url: str) -> dict:
        request = urllib.request.Request(url, headers={"User-Agent": self.user_agent})
        with urllib.request.urlopen(request, timeout=TIMEOUT_SECONDS) as response:
            return json.load(response)

    def _wait_for_turn(self) -> None:
        if self._last_request is not None:
            remaining = self.min_interval - (self.clock() - self._last_request)
            if remaining > 0:
                self.sleep(remaining)
        self._last_request = self.clock()

    def search(self, query: LookupQuery) -> list[Candidate]:
        if query.isbn:
            params = {"isbn": query.isbn}
        else:
            params = {"title": main_title(query.title)}
            if query.authors:
                params["author"] = query.authors[0]
        params |= {"fields": FIELDS, "limit": str(self.limit)}
        url = f"{SEARCH_URL}?{urllib.parse.urlencode(params)}"

        self._wait_for_turn()
        try:
            answer = self.fetch_json(url)
        except (OSError, ValueError) as error:
            raise SourceError(f"Open Library request failed: {error}") from error
        return [self._candidate(doc, query) for doc in answer.get("docs", []) if doc.get("title")]

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
