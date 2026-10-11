"""About this file: tests for the review web UI (ebookprep.web), using FastAPI's test
client against a state database filled with example books."""

from pathlib import Path

import pytest
from conftest import make_pdf
from fastapi.testclient import TestClient

from ebookprep.metadata import BookMetadata
from ebookprep.state import NewBook, StateStore, StoredCandidate
from ebookprep.web import create_app

LOCAL = BookMetadata(title="Beginning Android Games", authors=("Robert Green",))
GOOD = StoredCandidate(
    source="Open Library",
    metadata=BookMetadata(title="Beginning Android Games: Second Edition", authors=("Mario Zechner", "Robert Green"),
                          isbn="9781430246770", publisher="Apress", pubdate="2012"),
    cover_url="https://covers.openlibrary.org/b/id/1-L.jpg",
    score=3.5, isbn_match=False, title_in_text=True, author_in_text=False, auto_acceptable=False,
)


def new_book(key, sources, status="review", **extra) -> NewBook:
    values = dict(
        key=key, placement="IT/Android", group_reason="single", book_folder=None, sources=sources,
        local_metadata=LOCAL, local_source="filename", local_notes=("metadata from filename only",),
        evidence_excerpt="beginning android games robert green apress", evidence_isbns=("9781430230427",),
        lookup_errors=(("calibre: Google", "HTTP 429"),), status=status, decided_by="proposal: local",
        review_reasons=("no candidate with strict evidence",), metadata=LOCAL, candidates=(GOOD,),
    )
    values.update(extra)
    return NewBook(**values)


@pytest.fixture
def setup(tmp_path):
    input_root = tmp_path / "input"
    (input_root / "IT" / "Android").mkdir(parents=True)
    make_pdf(input_root / "IT/Android/Beginning Android Games - Robert Green.pdf")
    store = StateStore(tmp_path / "work" / "state.db")
    first = store.add_book(new_book("k1", ("IT/Android/Beginning Android Games - Robert Green.pdf",)))
    second = store.add_book(new_book("k2", ("IT/Android/Other.pdf",), metadata=BookMetadata(title="Other Book")))
    done = store.add_book(new_book("k3", ("IT/Android/Done.pdf",), status="accepted", candidates=()))
    client = TestClient(create_app(work_root=tmp_path / "work", input_root=input_root))
    return client, store, first, second, done


def test_overview_lists_review_queue_and_counts(setup):
    client, store, first, second, done = setup

    page = client.get("/")

    assert page.status_code == 200
    assert "Beginning Android Games" in page.text and "Other Book" in page.text
    assert f'href="/books/{first}"' in page.text
    assert "Waiting for review: 2" in page.text and "Accepted: 1" in page.text


def test_book_page_shows_evidence_candidates_and_reasons(setup):
    client, _, first, *_ = setup

    page = client.get(f"/books/{first}")

    assert page.status_code == 200
    for expected in ("Beginning Android Games: Second Edition", "Mario Zechner", "9781430246770", "Open Library",
                     "no candidate with strict evidence", "beginning android games robert green apress",
                     "9781430230427", "HTTP 429", "covers.openlibrary.org/b/id/1-L.jpg"):
        assert expected in page.text, expected
    assert f'href="/books/{first}/files/0"' in page.text


def test_use_candidate_fills_the_form(setup):
    client, _, first, *_ = setup

    form = client.get(f"/books/{first}/use/0")

    assert form.status_code == 200
    assert 'value="Beginning Android Games: Second Edition"' in form.text
    assert "Mario Zechner\nRobert Green" in form.text
    assert 'value="Apress"' in form.text


def test_accept_stores_decision_and_goes_to_next_book(setup):
    client, store, first, second, _ = setup

    response = client.post(
        f"/books/{first}/accept",
        data={"title": "Beginning Android Games", "authors": "Mario Zechner\nRobert Green\n", "isbn": "9781430230427",
              "publisher": "Apress", "language": "eng", "pubdate": "2011", "tags": "Android, Games", "comments": ""},
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert response.headers["location"] == f"/books/{second}"
    book = store.book(first)
    assert book.status == "accepted" and book.decided_by == "user"
    assert book.metadata == BookMetadata(
        title="Beginning Android Games", authors=("Mario Zechner", "Robert Green"), isbn="9781430230427",
        publisher="Apress", language="eng", pubdate="2011", tags=("Android", "Games"),
    )


def test_accept_without_title_is_rejected(setup):
    client, store, first, *_ = setup

    response = client.post(f"/books/{first}/accept", data={"title": "  ", "authors": "X"})

    assert response.status_code == 422
    assert store.book(first).status == "review"


def test_last_accept_returns_to_overview(setup):
    client, store, first, second, _ = setup
    client.post(f"/books/{first}/accept", data={"title": "A", "authors": ""})

    response = client.post(f"/books/{second}/accept", data={"title": "B", "authors": ""}, follow_redirects=False)

    assert response.headers["location"] == "/"


def test_source_file_is_served_for_checking(setup):
    client, _, first, *_ = setup

    response = client.get(f"/books/{first}/files/0")

    assert response.status_code == 200
    assert response.headers["content-type"] == "application/pdf"
    assert response.content.startswith(b"%PDF")
    assert client.get(f"/books/{first}/files/5").status_code == 404


def test_split_creates_separate_books(setup, tmp_path):
    client, store, *_ = setup
    group = store.add_book(new_book("k4", ("IT/Android/A - X.pdf", "IT/Android/A_ Sub - X.pdf"), group_reason="similar"))

    response = client.post(f"/books/{group}/split", follow_redirects=False)

    assert response.status_code == 303
    assert all(b.id != group for b in store.books())
    assert sum(1 for b in store.books() if b.sources[0].startswith("IT/Android/A")) == 2


def test_unknown_book_is_404(setup):
    client, *_ = setup

    assert client.get("/books/999").status_code == 404


def test_review_command_starts_the_web_server(tmp_path, monkeypatch):
    from ebookprep.__main__ import main

    started = {}
    monkeypatch.setattr("uvicorn.run", lambda app, host, port: started.update(app=app, host=host, port=port))

    exit_code = main(["review", "--input", str(tmp_path / "in"), "--work", str(tmp_path / "work"), "--port", "8123"])

    assert exit_code == 0
    assert started["host"] == "127.0.0.1" and started["port"] == 8123
    assert TestClient(started["app"]).get("/").status_code == 200


def test_mislabeled_book_is_marked_and_listed(setup):
    client, store, first, *_ = setup
    store.set_mislabeled(first, True)

    page = client.get(f"/books/{first}")
    listing = client.get("/?status=mislabeled")

    assert "Content differs from the filename" in page.text
    assert "Beginning Android Games" in listing.text and "Other Book" not in listing.text
