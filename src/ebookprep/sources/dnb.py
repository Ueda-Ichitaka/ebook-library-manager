"""About this file: the DNB metadata source (Deutsche Nationalbibliothek, the German
national library), good for German books that other sources don't know.

It uses the DNB's SRU interface (https://www.dnb.de/EN/sru), which is free and needs no
registration; the catalogue data is CC0. Queries are CQL: by ISBN (index "num") when one
is known, otherwise by title (index "tit") and the first author's surname (index "per").
Records come as MARC21-xml. Each record becomes a candidate with title (245 $a, plus $b
up to its first " ; "), authors (100 and the 700 entries with the relator "aut",
"Surname, First" turned into "First Surname"; illustrators, translators etc. are left
out), ISBN (the queried one when the record has it), publisher and year (264, else 260),
language (041) and the cover from the DNB's cover service. MARC's non-filing markers
(e.g. around a leading "Das") are removed. The DNB names no rate limit; requests are
spaced MIN_INTERVAL seconds apart to be polite.
"""

import re
import time
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from collections.abc import Callable

from ebookprep.filenames import is_valid_isbn
from ebookprep.metadata import BookMetadata
from ebookprep.naming import main_title
from ebookprep.sources import Candidate, LookupQuery, SourceError, Throttle

SRU_URL = "https://services.dnb.de/sru/dnb"
COVER_URL = "https://portal.dnb.de/opac/mvb/cover?isbn={}"
TIMEOUT_SECONDS = 30
MIN_INTERVAL = 1.0
USER_AGENT = "ebookprep (personal ebook library tool)"
_MARC = "{http://www.loc.gov/MARC21/slim}"
# MARC non-filing markers (start and end of an article that is ignored for sorting)
_NON_FILING = dict.fromkeys(map(ord, "\x98\x9c\x88\x89"))


class DnbSource:
    name = "DNB"

    def __init__(
        self,
        fetch_text: Callable[[str], str] | None = None,
        limit: int = 5,
        min_interval: float = MIN_INTERVAL,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ):
        self.fetch_text = fetch_text or _http_get_text
        self.limit = limit
        self.throttle = Throttle(min_interval, clock, sleep)

    def search(self, query: LookupQuery) -> list[Candidate]:
        if query.isbn:
            cql = f"num={query.isbn}"
        else:
            cql = f'tit="{main_title(query.title).replace(chr(34), " ")}"'
            surname = query.authors[0].split()[-1] if query.authors and query.authors[0].split() else ""
            if surname:
                cql += f" and per={surname}"
        params = {
            "version": "1.1",
            "operation": "searchRetrieve",
            "query": cql,
            "recordSchema": "MARC21-xml",
            "maximumRecords": str(self.limit),
        }
        self.throttle.wait()
        try:
            root = ET.fromstring(self.fetch_text(f"{SRU_URL}?{urllib.parse.urlencode(params)}"))
        except (OSError, ValueError, ET.ParseError) as error:
            raise SourceError(f"DNB request failed: {error}") from error
        candidates = (_candidate(record, query.isbn) for record in root.iter(f"{_MARC}record"))
        return [c for c in candidates if c is not None]


def _http_get_text(url: str) -> str:
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(request, timeout=TIMEOUT_SECONDS) as response:
        return response.read().decode("utf-8")


def _candidate(record: ET.Element, wanted_isbn: str | None) -> Candidate | None:
    fields: list[tuple[str, dict[str, list[str]]]] = []
    for field in record.iter(f"{_MARC}datafield"):
        subfields: dict[str, list[str]] = {}
        for sub in field.iter(f"{_MARC}subfield"):
            subfields.setdefault(sub.get("code"), []).append((sub.text or "").translate(_NON_FILING).strip())
        fields.append((field.get("tag"), subfields))

    def first(tag: str, code: str) -> str | None:
        return next((subs[code][0] for t, subs in fields if t == tag and subs.get(code)), None)

    title = first("245", "a")
    if not title:
        return None
    title = title.rstrip(" /:;")
    subtitle = (first("245", "b") or "").split(" ; ")[0].rstrip(" /:;")
    if subtitle:
        title = f"{title}: {subtitle}"

    authors = []
    for tag, subs in fields:
        if tag == "100" or (tag == "700" and "aut" in subs.get("4", ()) and "t" not in subs):
            name = _natural_name(subs.get("a", [""])[0])
            if name and name not in authors:
                authors.append(name)

    isbns = [re.sub(r"[^\dX]", "", i) for t, subs in fields if t == "020" for i in subs.get("a", ())]
    valid = [i for i in isbns if is_valid_isbn(i)]
    isbn = wanted_isbn if wanted_isbn in valid else next((i for i in valid if len(i) == 13), valid[0] if valid else None)

    published = first("264", "c") or first("260", "c") or ""
    year = re.search(r"\d{4}", published)
    metadata = BookMetadata(
        title=title,
        authors=tuple(authors),
        isbn=isbn,
        publisher=first("264", "b") or first("260", "b"),
        language=first("041", "a"),
        pubdate=year.group(0) if year else None,
    )
    return Candidate(source=DnbSource.name, metadata=metadata, cover_url=COVER_URL.format(isbn) if isbn else None)


def _natural_name(name: str) -> str:
    """"Riley, Mike" -> "Mike Riley"."""
    name = name.strip().rstrip(".,")
    surname, comma, given = name.partition(", ")
    return f"{given} {surname}" if comma and given else name
