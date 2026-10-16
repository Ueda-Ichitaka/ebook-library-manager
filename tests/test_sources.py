"""About this file: tests for ebookprep.sources, the online metadata sources (Open Library
API, calibre's built-in sources) behind one interface. No network is used: Open Library
answers come from recorded API responses in tests/fixtures, and calibre's output is
simulated."""

import json
import urllib.error
from pathlib import Path

import pytest

from ebookprep.metadata import BookMetadata
from ebookprep.sources import Candidate, LookupQuery, SourceError, lookup_all
from ebookprep.sources.calibre_source import CalibreSource
from ebookprep.sources.dnb import DnbSource
from ebookprep.sources.googlebooks import GoogleBooksSource
from ebookprep.sources.openlibrary import OpenLibrarySource

FIXTURES = Path(__file__).parent / "fixtures"


def fixture(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


class RecordingFetch:
    def __init__(self, answer: dict):
        self.answer = answer
        self.urls: list[str] = []

    def __call__(self, url: str) -> dict:
        self.urls.append(url)
        return self.answer


# --- Open Library


def test_openlibrary_isbn_search():
    fetch = RecordingFetch(fixture("openlibrary_isbn_linux_firewalls.json"))

    (candidate,) = OpenLibrarySource(fetch_json=fetch).search(LookupQuery(title="Linux firewalls", isbn="9781593271411"))

    assert "isbn=9781593271411" in fetch.urls[0]
    assert candidate == Candidate(
        source="Open Library",
        metadata=BookMetadata(
            title="Linux Firewalls",
            authors=("Michael Rash",),
            isbn="9781593271411",
            publisher="No Starch Press",
            language="eng",
            pubdate="2007",
        ),
        cover_url="https://covers.openlibrary.org/b/id/1984283-L.jpg",
    )


class UrlFetch:
    """Answers by URL ending; unknown URLs give HTTP 404 like Open Library."""

    def __init__(self, answers: dict[str, dict]):
        self.answers = answers
        self.urls: list[str] = []

    def __call__(self, url: str) -> dict:
        self.urls.append(url)
        for ending, answer in self.answers.items():
            if url.endswith(ending):
                return answer
        raise urllib.error.HTTPError(url, 404, "Not Found", None, None)


def test_openlibrary_isbn_without_search_result_falls_back_to_the_isbn_api():
    fetch = UrlFetch({
        "fields=title%2Csubtitle%2Cauthor_name%2Cisbn%2Cpublisher%2Cfirst_publish_year%2Clanguage%2Ccover_i&limit=5":
            fixture("openlibrary_empty.json"),
        "/isbn/9783855027057.json": fixture("openlibrary_edition_pflanzen_der_kelten.json"),
        "/authors/OL1389139A.json": fixture("openlibrary_author_storl.json"),
    })

    (candidate,) = OpenLibrarySource(fetch_json=fetch, min_interval=0).search(
        LookupQuery(title="Pflanzen der Kelten", isbn="9783855027057")
    )

    assert fetch.urls[1] == "https://openlibrary.org/isbn/9783855027057.json"
    assert candidate == Candidate(
        source="Open Library",
        metadata=BookMetadata(
            title="Pflanzen der Kelten: Heilkunde, Pflanzenzauber, Baumkalender",
            authors=("Wolf-Dieter Storl",),
            isbn="9783855027057",
            publisher="AT Verlag",
            language="ger",
            pubdate="2000",
        ),
        cover_url=None,
    )


def test_openlibrary_isbn_api_takes_authors_from_the_work_when_the_edition_has_none():
    fetch = UrlFetch({
        "limit=5": fixture("openlibrary_empty.json"),
        "/isbn/9781990869617.json": fixture("openlibrary_edition_more_than_two.json"),
        "/works/OL19983427W.json": fixture("openlibrary_work_more_than_two.json"),
        "/authors/OL7596381A.json": fixture("openlibrary_author_OL7596381A.json"),
        "/authors/OL7786338A.json": fixture("openlibrary_author_OL7786338A.json"),
    })

    (candidate,) = OpenLibrarySource(fetch_json=fetch, min_interval=0).search(
        LookupQuery(title="More than two", isbn="9781990869617")
    )

    assert candidate.metadata.authors == ("Franklin Veaux", "Eve Rickert")
    assert candidate.metadata.pubdate == "2024"
    assert candidate.cover_url == "https://covers.openlibrary.org/b/id/15120744-L.jpg"


def test_openlibrary_unknown_isbn_gives_no_candidate():
    fetch = UrlFetch({"limit=5": fixture("openlibrary_empty.json")})

    assert OpenLibrarySource(fetch_json=fetch, min_interval=0).search(LookupQuery(title="x", isbn="9780991399765")) == []
    assert len(fetch.urls) == 2


def test_openlibrary_title_search_uses_title_and_first_author():
    fetch = RecordingFetch(fixture("openlibrary_title_black_hat_go.json"))

    (candidate,) = OpenLibrarySource(fetch_json=fetch).search(
        LookupQuery(title="Black Hat Go: Go Programming", authors=("Tom Steele", "Chris Patten"))
    )

    assert "title=Black+Hat+Go" in fetch.urls[0] and "author=Tom+Steele" in fetch.urls[0]
    assert "isbn=" not in fetch.urls[0]
    assert candidate.metadata.authors == ("Tom Steele", "Chris Patten", "Dan Kottmann")
    assert candidate.metadata.isbn == "9781593278656"


def test_openlibrary_no_results():
    assert OpenLibrarySource(fetch_json=RecordingFetch(fixture("openlibrary_empty.json"))).search(
        LookupQuery(title="zzzz")
    ) == []


def test_openlibrary_network_error_raises_source_error():
    def failing(url):
        raise OSError("connection refused")

    with pytest.raises(SourceError, match="connection refused"):
        OpenLibrarySource(fetch_json=failing).search(LookupQuery(title="x"))


def test_openlibrary_subtitle_is_appended():
    answer = {"docs": [{"title": "Linux Firewalls", "subtitle": "Attack Detection and Response", "author_name": ["M"]}]}

    (candidate,) = OpenLibrarySource(fetch_json=RecordingFetch(answer)).search(LookupQuery(title="x"))

    assert candidate.metadata.title == "Linux Firewalls: Attack Detection and Response"


# --- calibre


CALIBRE_OUTPUT = """<?xml version='1.0' encoding='utf-8'?>
<package xmlns="http://www.idpf.org/2007/opf" unique-identifier="uuid_id" version="2.0">
    <metadata xmlns:dc="http://purl.org/dc/elements/1.1/" xmlns:opf="http://www.idpf.org/2007/opf">
        <dc:title>Black Hat Go</dc:title>
        <dc:creator opf:role="aut">Tom Steele</dc:creator>
        <dc:creator opf:role="aut">Chris Patten</dc:creator>
        <dc:identifier opf:scheme="ISBN">9781593278656</dc:identifier>
        <dc:publisher>No Starch Press</dc:publisher>
        <dc:date>2020-01-24T00:00:00+00:00</dc:date>
    </metadata>
</package>"""


def test_calibre_source_turns_opf_output_into_candidate():
    calls = []

    def runner(args):
        calls.append(args)
        return CALIBRE_OUTPUT

    source = CalibreSource("Google", runner=runner)
    (candidate,) = source.search(LookupQuery(title="Black Hat Go", authors=("Tom Steele",), isbn="9781593278656"))

    assert source.name == "calibre: Google"
    assert candidate.source == "calibre: Google"
    assert candidate.metadata.title == "Black Hat Go"
    assert candidate.metadata.authors == ("Tom Steele", "Chris Patten")
    assert candidate.metadata.year == 2020
    assert calls[0][:3] == ["fetch-ebook-metadata", "--allowed-plugin", "Google"]
    assert "--isbn" in calls[0] and "--opf" in calls[0]


def test_calibre_source_without_result_returns_nothing():
    assert CalibreSource("Google", runner=lambda args: None).search(LookupQuery(title="x")) == []


# --- running several sources


class FixedSource:
    def __init__(self, name, result):
        self.name, self.result = name, result

    def search(self, query):
        if isinstance(self.result, Exception):
            raise self.result
        return self.result


def test_lookup_all_collects_candidates_and_errors():
    good = Candidate(source="A", metadata=BookMetadata(title="T"))
    sources = [FixedSource("A", [good]), FixedSource("B", SourceError("HTTP 429")), FixedSource("C", [])]

    result = lookup_all(sources, LookupQuery(title="T"))

    assert result.candidates == (good,)
    assert result.errors == (("B", "HTTP 429"),)


def test_openlibrary_waits_between_requests():
    now = [100.0]
    slept = []

    def clock():
        return now[0]

    def sleep(seconds):
        slept.append(seconds)
        now[0] += seconds

    source = OpenLibrarySource(
        fetch_json=RecordingFetch(fixture("openlibrary_empty.json")), min_interval=1.0, clock=clock, sleep=sleep
    )
    source.search(LookupQuery(title="a"))
    now[0] += 0.25
    source.search(LookupQuery(title="b"))

    assert slept == [0.75]


def test_openlibrary_user_agent_includes_contact_when_configured():
    assert "(me@example.org)" in OpenLibrarySource(contact="me@example.org").user_agent
    assert "@" not in OpenLibrarySource().user_agent


def test_candidate_authors_are_normalized():
    sorted_name = Candidate(source="A", metadata=BookMetadata(title="Network know-how", authors=("Ross, John", "Jane Doe")))

    result = lookup_all([FixedSource("A", [sorted_name])], LookupQuery(title="x"))

    assert result.candidates[0].metadata.authors == ("John Ross", "Jane Doe")


# --- registry


def test_all_sources_by_default():
    from ebookprep.config import Config
    from ebookprep.sources.registry import default_sources

    names = [s.name for s in default_sources(Config())]

    assert names == [
        "calibre: Google", "calibre: Amazon.com", "calibre: Edelweiss", "Open Library", "DNB", "Google Books API"
    ]


def test_configured_sources_in_configured_order():
    from ebookprep.config import Config
    from ebookprep.sources.registry import default_sources

    sources = default_sources(Config(sources=("Open Library", "calibre: Google")))

    assert [s.name for s in sources] == ["Open Library", "calibre: Google"]


def test_unknown_source_name_is_a_config_error():
    from ebookprep.config import Config, ConfigError
    from ebookprep.sources.registry import default_sources

    with pytest.raises(ConfigError, match="calibre: Edelweiss"):
        default_sources(Config(sources=("Amazon",)))


@pytest.mark.parametrize(
    ("pubdate", "expected"),
    [("2010-09-21T07:00:00+00:00", "2010-09-21"), ("2012", "2012"), ("2016-04", "2016-04"),
     ("0101-01-01T00:00:00+00:00", None), ("unknown", None), (None, None)],
)
def test_candidate_dates_are_sanitized(pubdate, expected):
    raw = Candidate(source="A", metadata=BookMetadata(title="T", pubdate=pubdate))

    result = lookup_all([FixedSource("A", [raw])], LookupQuery(title="x"))

    assert result.candidates[0].metadata.pubdate == expected


# --- DNB (Deutsche Nationalbibliothek)


def text_fixture(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


class RecordingTextFetch:
    def __init__(self, answer: str):
        self.answer = answer
        self.urls: list[str] = []

    def __call__(self, url: str) -> str:
        self.urls.append(url)
        return self.answer


def test_dnb_isbn_search():
    fetch = RecordingTextFetch(text_fixture("dnb_isbn_heimautomation.xml"))

    (candidate,) = DnbSource(fetch_text=fetch, min_interval=0).search(LookupQuery(title="Heimautomation", isbn="9783868993639"))

    assert "query=num%3D9783868993639" in fetch.urls[0] and "recordSchema=MARC21-xml" in fetch.urls[0]
    assert candidate == Candidate(
        source="DNB",
        metadata=BookMetadata(
            title="Das intelligente Haus: Heimautomation mit Arduino, Android und PC",
            authors=("Mike Riley",),
            isbn="9783868993639",
            publisher="O'Reilly",
            language="ger",
            pubdate="2012",
        ),
        cover_url="https://portal.dnb.de/opac/mvb/cover?isbn=9783868993639",
    )


def test_dnb_title_search_uses_title_and_first_author_surname_and_keeps_only_authors():
    fetch = RecordingTextFetch(text_fixture("dnb_title_pflanzen_der_kelten.xml"))

    candidates = DnbSource(fetch_text=fetch, min_interval=0).search(
        LookupQuery(title="Pflanzen der Kelten: Heilkunde", authors=("Wolf-Dieter Storl",))
    )

    assert 'query=tit%3D%22Pflanzen+der+Kelten%22+and+per%3DStorl' in fetch.urls[0]
    first = candidates[0].metadata
    assert (first.title, first.authors, first.publisher, first.pubdate) == (
        "Pflanzen der Kelten: Heilkunde Pflanzenzauber Baumkalender", ("Wolf-Dieter Storl",), "AT Verlag", "2025"
    )
    assert all(c.metadata.authors == ("Wolf-Dieter Storl",) for c in candidates)


def test_dnb_no_results_and_errors():
    assert DnbSource(fetch_text=RecordingTextFetch(text_fixture("dnb_empty.xml")), min_interval=0).search(
        LookupQuery(title="x", isbn="9780991399765")
    ) == []

    def failing(url):
        raise OSError("connection refused")

    with pytest.raises(SourceError, match="DNB request failed"):
        DnbSource(fetch_text=failing, min_interval=0).search(LookupQuery(title="x"))


# --- Google Books API (the fixture follows the documented Volume resource,
# https://developers.google.com/books/docs/v1/reference/volumes)


def test_google_books_isbn_search_sends_the_api_key():
    fetch = RecordingFetch(fixture("googlebooks_volumes_schema_example.json"))

    (candidate,) = GoogleBooksSource(api_key="KEY", fetch_json=fetch, min_interval=0).search(
        LookupQuery(title="Black Hat Go", isbn="9781593278656")
    )

    assert fetch.urls[0].startswith("https://www.googleapis.com/books/v1/volumes?q=isbn%3A9781593278656")
    assert "key=KEY" in fetch.urls[0]
    assert candidate == Candidate(
        source="Google Books API",
        metadata=BookMetadata(
            title="Black Hat Go: Go Programming For Hackers and Pentesters",
            authors=("Tom Steele", "Chris Patten", "Dan Kottmann"),
            isbn="9781593278656",
            publisher="No Starch Press",
            language="en",
            pubdate="2020-02-04",
        ),
        cover_url="https://books.google.com/books/content?id=example&printsec=frontcover&img=1&zoom=1",
    )


def test_google_books_title_search_like_calibre():
    fetch = RecordingFetch({"kind": "books#volumes", "totalItems": 0})

    assert GoogleBooksSource(api_key="KEY", fetch_json=fetch, min_interval=0).search(
        LookupQuery(title="Black Hat Go: Go Programming", authors=("Tom Steele",))
    ) == []

    assert "q=intitle%3ABlack+intitle%3AHat+intitle%3AGo+inauthor%3ATom+inauthor%3ASteele" in fetch.urls[0]


def test_google_books_without_key_or_on_errors_raises_source_error():
    with pytest.raises(SourceError, match="GOOGLE_BOOKS_API_KEY"):
        GoogleBooksSource(api_key="", fetch_json=RecordingFetch({}), min_interval=0).search(LookupQuery(title="x"))

    def rate_limited(url):
        raise urllib.error.HTTPError(url, 429, "Too Many Requests", None, None)

    with pytest.raises(SourceError, match="429"):
        GoogleBooksSource(api_key="KEY", fetch_json=rate_limited, min_interval=0).search(LookupQuery(title="x"))
