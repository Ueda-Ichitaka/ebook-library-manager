"""About this file: chooses the Kavita series name of every book to build.

Normally the series is the book's title, so Kavita lists each book on its own. Kavita
identifies a series by its normalized name (see kavita_normalize) and format within a
library, so different books with the same title and a common format would be merged into
one series (three different "Wicca" PDFs became one). Only for such clashes the series
becomes "Title (First author)", with a counter if even that is not unique. The title
itself stays unchanged. A library is the first folder of a book's placement; books
waiting for review are not built and mislabeled books are outside the libraries, so they
don't take part in clashes.
"""

from collections import defaultdict

from ebookprep.state import BookRow

# Kavita's EPUB is also what MOBI/AZW files become.
_KAVITA_FORMAT = {".pdf": "pdf", ".epub": "epub", ".mobi": "epub", ".azw": "epub", ".azw3": "epub"}
# Characters Kavita keeps when normalizing (besides letters): Parser.Normalize / ToNormalized.
_KEPT = set("0123456789+!＊！＋")


def kavita_normalize(name: str) -> str:
    return "".join(c for c in name if c.isalpha() or c in _KEPT).strip().lower()


def series_names(books: list[BookRow]) -> dict[int, str]:
    names = {b.id: b.metadata.title for b in books if b.status == "accepted"}
    by_title: dict[tuple[str, str], list[BookRow]] = defaultdict(list)
    for b in books:
        if b.status == "accepted" and not b.mislabeled:
            library = b.placement.split("/")[0]
            by_title[(library, kavita_normalize(b.metadata.title))].append(b)

    for group in by_title.values():
        clashing = [b for b in group if any(_formats(b) & _formats(o) for o in group if o is not b)]
        used: set[str] = set()
        for b in sorted(clashing, key=lambda b: b.id):
            author = b.metadata.authors[0] if b.metadata.authors else "unknown author"
            name, counter = f"{b.metadata.title} ({author})", 2
            while kavita_normalize(name) in used:
                name, counter = f"{b.metadata.title} ({author}, {counter})", counter + 1
            used.add(kavita_normalize(name))
            names[b.id] = name
    return names


def _formats(book: BookRow) -> set[str]:
    return {_KAVITA_FORMAT[s[s.rfind("."):].lower()] for s in book.sources if s[s.rfind("."):].lower() in _KAVITA_FORMAT}
