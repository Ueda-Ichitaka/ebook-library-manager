"""About this file: the Google Books API metadata source
(https://developers.google.com/books/docs/v1/using).

Google answers requests without an API key with HTTP 429 (the shared anonymous quota is
zero), so the source needs the user's own free API key, read from the environment
variable GOOGLE_BOOKS_API_KEY (see api-keys.env); without one every search fails with a
note saying so. It searches by ISBN when one is known (q=isbn:...), otherwise like
calibre's Google plugin with one intitle: term per word of the main title and one
inauthor: term per word of the first author. Each volume becomes a candidate with title
(plus subtitle), authors, ISBN (the queried one when listed, else ISBN-13), publisher,
publication date, language (two-letter code) and the thumbnail as cover. Requests are
spaced MIN_INTERVAL seconds apart.
"""

import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable

from ebookprep.filenames import is_valid_isbn
from ebookprep.metadata import BookMetadata
from ebookprep.naming import main_title
from ebookprep.sources import Candidate, LookupQuery, SourceError, Throttle

VOLUMES_URL = "https://www.googleapis.com/books/v1/volumes"
API_KEY_VARIABLE = "GOOGLE_BOOKS_API_KEY"
TIMEOUT_SECONDS = 30
MIN_INTERVAL = 1.0


class GoogleBooksSource:
    name = "Google Books API"

    def __init__(
        self,
        api_key: str | None = None,
        fetch_json: Callable[[str], dict] | None = None,
        limit: int = 5,
        min_interval: float = MIN_INTERVAL,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ):
        self.api_key = os.environ.get(API_KEY_VARIABLE, "") if api_key is None else api_key
        self.fetch_json = fetch_json or _http_get_json
        self.limit = limit
        self.throttle = Throttle(min_interval, clock, sleep)

    def search(self, query: LookupQuery) -> list[Candidate]:
        if not self.api_key:
            raise SourceError(f"no API key: set {API_KEY_VARIABLE} in api-keys.env")
        if query.isbn:
            q = f"isbn:{query.isbn}"
        else:
            terms = [f"intitle:{w}" for w in main_title(query.title).split()]
            if query.authors:
                terms += [f"inauthor:{w}" for w in query.authors[0].split()]
            q = " ".join(terms)
        params = {"q": q, "maxResults": str(self.limit), "key": self.api_key}
        self.throttle.wait()
        try:
            answer = self.fetch_json(f"{VOLUMES_URL}?{urllib.parse.urlencode(params)}")
        except urllib.error.HTTPError as error:
            raise SourceError(f"Google Books API request failed: HTTP {error.code}") from error
        except (OSError, ValueError) as error:
            raise SourceError(f"Google Books API request failed: {error}") from error
        volumes = (item.get("volumeInfo", {}) for item in answer.get("items", ()))
        return [_candidate(info, query.isbn) for info in volumes if info.get("title")]


def _http_get_json(url: str) -> dict:
    with urllib.request.urlopen(url, timeout=TIMEOUT_SECONDS) as response:
        return json.load(response)


def _candidate(info: dict, wanted_isbn: str | None) -> Candidate:
    identifiers = {i.get("type"): i.get("identifier") for i in info.get("industryIdentifiers", ())}
    isbns = [i for i in (identifiers.get("ISBN_13"), identifiers.get("ISBN_10")) if i and is_valid_isbn(i)]
    isbn = wanted_isbn if wanted_isbn in isbns else next(iter(isbns), None)
    metadata = BookMetadata(
        title=info["title"] + (f": {info['subtitle']}" if info.get("subtitle") else ""),
        authors=tuple(info.get("authors", ())),
        isbn=isbn,
        publisher=info.get("publisher"),
        language=info.get("language"),
        pubdate=info.get("publishedDate"),
    )
    thumbnail = info.get("imageLinks", {}).get("thumbnail")
    cover = thumbnail.replace("http://", "https://", 1) if thumbnail else None
    return Candidate(source=GoogleBooksSource.name, metadata=metadata, cover_url=cover)
