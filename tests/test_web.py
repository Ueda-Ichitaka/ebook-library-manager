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


# --- duplicates view


@pytest.fixture
def duplicates(tmp_path):
    from conftest import make_epub

    input_root = tmp_path / "input"
    folder = input_root / "IT" / "Android"
    folder.mkdir(parents=True)
    make_pdf(folder / "Spark.pdf", title="Spark")
    body = "<p>" + " ".join(["word"] * 1000) + "</p>"
    make_epub(folder / "Spark.epub", title="Spark", body=body)
    store = StateStore(tmp_path / "work" / "state.db")
    spark = BookMetadata(title="Apache Spark Graph Processing", authors=("Rindra Ramamonjison",))
    book_id = store.add_book(
        new_book("dup", ("IT/Android/Spark.pdf", "IT/Android/Spark.epub"), status="accepted", candidates=(),
                 metadata=spark, local_metadata=spark)
    )
    client = TestClient(create_app(work_root=tmp_path / "work", input_root=input_root))
    return client, store, book_id


def group_url(client):
    page = client.get("/duplicates")
    return page.text.split('href="/duplicates/')[1].split('"')[0]


def test_duplicates_are_listed_and_counted(duplicates):
    client, *_ = duplicates

    assert "Possible duplicates: 1" in client.get("/").text
    listing = client.get("/duplicates")
    assert "Apache Spark Graph Processing" in listing.text and "files of one book" in listing.text


def test_duplicate_detail_shows_file_facts(duplicates):
    client, _, book_id = duplicates

    page = client.get(f"/duplicates/{group_url(client)}")

    assert page.status_code == 200
    for expected in ("IT/Android/Spark.pdf", "IT/Android/Spark.epub", "PDF", "EPUB", "1000 words",
                     f'href="/books/{book_id}/files/0"', 'name="keep"', "Not duplicates"):
        assert expected in page.text, expected


def test_keeping_one_file_excludes_the_others(duplicates):
    client, store, _ = duplicates
    key = group_url(client)

    response = client.post(f"/duplicates/{key}/keep", data={"keep": "IT/Android/Spark.pdf"}, follow_redirects=False)

    assert response.status_code == 303 and response.headers["location"] == "/duplicates"
    assert store.excluded_paths() == {"IT/Android/Spark.epub"}
    assert "Possible duplicates: 0" in client.get("/").text


def test_keep_needs_a_file_of_the_group(duplicates):
    client, store, _ = duplicates
    key = group_url(client)

    assert client.post(f"/duplicates/{key}/keep", data={"keep": "IT/elsewhere.pdf"}).status_code == 422
    assert store.excluded_paths() == set()


def test_dismissed_group_disappears(duplicates):
    client, store, _ = duplicates
    key = group_url(client)

    client.post(f"/duplicates/{key}/dismiss")

    assert store.dismissed_duplicates() == {key}
    assert client.get(f"/duplicates/{key}").status_code == 404


def test_excluded_files_can_be_restored(duplicates):
    client, store, _ = duplicates
    client.post(f"/duplicates/{group_url(client)}/keep", data={"keep": "IT/Android/Spark.pdf"})

    page = client.get("/excluded")
    assert "IT/Android/Spark.epub" in page.text and "IT/Android/Spark.pdf" in page.text

    response = client.post("/excluded/restore", data={"path": "IT/Android/Spark.epub"}, follow_redirects=False)

    assert response.status_code == 303
    assert store.excluded_paths() == set()


def test_books_whose_files_are_all_excluded_leave_the_review_queue(setup):
    client, store, first, second, _ = setup

    store.exclude_sources(["IT/Android/Other.pdf"], kept="IT/Android/x.pdf", group="g")

    assert "Other Book" not in client.get("/").text


# --- actions, jobs, files overview, report


def fake_commands(name, settings, only=()):
    import sys

    if name == "identify":
        return [sys.executable, "-c", "import time; time.sleep(5)"]
    return [sys.executable, "-c", f"print('ran {name}', {list(only)!r})"]


@pytest.fixture
def control(tmp_path):
    input_root = tmp_path / "input"
    (input_root / "IT" / "Android").mkdir(parents=True)
    make_pdf(input_root / "IT/Android/Beginning Android Games - Robert Green.pdf")
    (input_root / "IT/Android/new.pdf").write_bytes(b"%PDF-1.4")
    store = StateStore(tmp_path / "work" / "state.db")
    store.add_book(new_book("k1", ("IT/Android/Beginning Android Games - Robert Green.pdf",)))
    app = create_app(work_root=tmp_path / "work", input_root=input_root, output_root=tmp_path / "output",
                     command_builder=fake_commands)
    return TestClient(app), store


def wait_for_job(client, job_id, timeout=20):
    import time

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        page = client.get(f"/jobs/{job_id}/log")
        if "running" not in page.headers.get("x-job-status", "running"):
            return page
        time.sleep(0.05)
    raise AssertionError("job did not finish")


def test_actions_page_offers_every_command(control):
    client, _ = control

    page = client.get("/actions")

    for name in ("identify", "rescore", "build", "report", "kavita-check", "kavita-scan"):
        assert f'value="{name}"' in page.text, name


def test_starting_a_command_runs_it_as_a_job(control):
    client, _ = control

    response = client.post("/actions/start", data={"name": "build", "only": "IT/Android"}, follow_redirects=False)

    assert response.status_code == 303
    job_id = int(response.headers["location"].rsplit("/", 1)[1])
    log = wait_for_job(client, job_id)
    assert "ran build ['IT/Android']" in log.text
    assert log.headers["x-job-status"] == "succeeded"
    assert "<h2>Build</h2>" in client.get(f"/jobs/{job_id}").text


def test_only_one_job_at_a_time_and_stop(control):
    client, _ = control
    first = client.post("/actions/start", data={"name": "identify"}, follow_redirects=False)
    job_id = int(first.headers["location"].rsplit("/", 1)[1])

    busy = client.post("/actions/start", data={"name": "build"})
    assert busy.status_code == 409 and "identify" in busy.text

    client.post(f"/jobs/{job_id}/stop")
    assert wait_for_job(client, job_id).headers["x-job-status"] == "stopped"


def test_unknown_command_is_rejected(control):
    client, _ = control

    assert client.post("/actions/start", data={"name": "rm -rf"}).status_code == 404


def test_files_overview_shows_progress_and_states(control):
    client, _ = control

    page = client.get("/files")

    assert "waiting for review" in page.text and "not identified yet" in page.text
    assert "IT/Android/new.pdf" in page.text
    assert "2 files" in page.text
    filtered = client.get("/files?state=not identified yet")
    assert "IT/Android/new.pdf" in filtered.text
    assert "Beginning Android Games - Robert Green.pdf" not in filtered.text


def test_report_page_shows_report_data(control):
    client, store = control
    store.exclude_sources(["IT/Android/x.epub"], kept="IT/Android/x.pdf", group="g")

    page = client.get("/report")

    assert "Failed (flagged)" in page.text and "Excluded Duplicate Files" in page.text
    assert "IT/Android/x.epub" in page.text


def test_review_command_passes_paths_for_the_actions(tmp_path, monkeypatch):
    from ebookprep.__main__ import main

    started = {}
    monkeypatch.setattr("uvicorn.run", lambda app, host, port: started.update(app=app))

    main(["review", "--input", str(tmp_path / "in"), "--work", str(tmp_path / "work"), "--output",
          str(tmp_path / "out"), "--config", str(tmp_path / "config.toml"), "--kavita-url", "http://kavita:5000"])

    assert TestClient(started["app"]).get("/actions").status_code == 200


# --- multi-select keep, review reconciliation, ignore, queue sections, branding


@pytest.fixture
def three_files(tmp_path):
    from conftest import make_epub

    from ebookprep.identify import REASON_SIMILAR_GROUP

    input_root = tmp_path / "input"
    folder = input_root / "BDSM" / "Shibari"
    folder.mkdir(parents=True)
    make_pdf(folder / "Bondage.pdf")
    make_epub(folder / "Bondage.epub", title="Bondage")
    (folder / "Bondage.azw").write_bytes(b"azw")
    store = StateStore(tmp_path / "work" / "state.db")
    meta = BookMetadata(title="Better Bondage for Every Body", authors=("Evie Vane",))
    book_id = store.add_book(new_book(
        "bondage", ("BDSM/Shibari/Bondage.pdf", "BDSM/Shibari/Bondage.epub", "BDSM/Shibari/Bondage.azw"),
        placement="BDSM/Shibari", group_reason="similar", review_reasons=(REASON_SIMILAR_GROUP,),
        metadata=meta, local_metadata=meta, candidates=(), decided_by="auto: Open Library"))
    client = TestClient(create_app(work_root=tmp_path / "work", input_root=input_root))
    return client, store, book_id


def test_keeping_several_files_leaves_a_smaller_group(three_files):
    client, store, book_id = three_files
    key = group_url(client)

    client.post(f"/duplicates/{key}/keep", data={"keep": ["BDSM/Shibari/Bondage.pdf", "BDSM/Shibari/Bondage.epub"]})

    assert store.excluded_paths() == {"BDSM/Shibari/Bondage.azw"}
    assert store.book(book_id).status == "review"
    smaller = client.get(f"/duplicates/{group_url(client)}").text
    assert "Bondage.pdf" in smaller and "Bondage.epub" in smaller and "Bondage.azw" not in smaller

    client.post(f"/duplicates/{group_url(client)}/keep", data={"keep": ["BDSM/Shibari/Bondage.pdf"]})

    assert store.book(book_id).status == "accepted"
    assert "Possible duplicates: 0" in client.get("/").text


def test_duplicate_detail_uses_checkboxes(three_files):
    client, *_ = three_files

    page = client.get(f"/duplicates/{group_url(client)}")

    assert 'type="checkbox" name="keep"' in page.text


def test_not_duplicates_splits_a_similar_names_group(three_files):
    client, store, book_id = three_files

    client.post(f"/duplicates/{group_url(client)}/dismiss")

    assert book_id not in {b.id for b in store.books()}
    assert sorted(len(b.sources) for b in store.books()) == [1, 1, 1]


def test_update_button_reconciles_the_review_queue(three_files):
    client, store, book_id = three_files
    store.exclude_sources(["BDSM/Shibari/Bondage.epub", "BDSM/Shibari/Bondage.azw"], kept="BDSM/Shibari/Bondage.pdf", group="g")

    response = client.post("/refresh", follow_redirects=False)

    assert response.status_code == 303
    assert store.book(book_id).status == "accepted"


def test_ignore_moves_a_book_to_skipped_and_restore_brings_it_back(setup):
    client, store, first, second, _ = setup

    response = client.post(f"/books/{first}/ignore", follow_redirects=False)

    assert response.status_code == 303 and response.headers["location"] == f"/books/{second}"
    assert store.ignored_paths() == {"IT/Android/Beginning Android Games - Robert Green.pdf"}
    assert "Beginning Android Games" not in client.get("/").text
    assert "Beginning Android Games - Robert Green.pdf" in client.get("/excluded").text

    client.post("/ignored/restore", data={"path": "IT/Android/Beginning Android Games - Robert Green.pdf"})
    assert store.ignored_paths() == set()


def test_review_queue_is_sectioned_by_placement_and_can_hide_image_files(tmp_path):
    store = StateStore(tmp_path / "work" / "state.db")
    store.add_book(new_book("a", ("DIY/Plans/AZ Regal.jpg.pdf",), placement="DIY/Plans", metadata=BookMetadata(title="AZ Regal.jpg")))
    store.add_book(new_book("b", ("DIY/Plans/Workbench.pdf",), placement="DIY/Plans", metadata=BookMetadata(title="Workbench")))
    store.add_book(new_book("c", ("IT/Android/x.pdf", "IT/Android/x.epub"), metadata=BookMetadata(title="Android X")))
    client = TestClient(create_app(work_root=tmp_path / "work", input_root=tmp_path / "input"))

    page = client.get("/").text
    filtered = client.get("/?hide_images=1").text

    assert "<details" in page and "<summary>DIY/Plans" in page and "<summary>IT/Android" in page
    assert "Waiting for review: 3 books (4 files)" in page
    assert "AZ Regal.jpg" in page and "AZ Regal.jpg" not in filtered
    assert "Workbench" in filtered
    assert 'name="hide_images"' in page


def test_branding_and_capitalized_navigation(setup):
    client, *_ = setup

    page = client.get("/").text

    assert "<title>Ebook Library Manager</title>" in page
    assert '<link rel="icon"' in page
    assert "\N{BOOKS} Ebook Library Manager" in page
    for label in (">Review Queue<", ">Accepted<", ">All<", ">Duplicates<", ">Excluded Files<", ">Actions<", ">Files<", ">Report<"):
        assert label in page, label


def test_report_has_a_table_of_contents(setup):
    client, *_ = setup

    page = client.get("/report").text

    assert 'class="toc"' in page
    for anchor in ("failed", "mislabeled", "excluded", "ignored", "retried", "repaired", "converted", "skipped"):
        assert f'href="#{anchor}"' in page and f'id="{anchor}"' in page, anchor


# --- security: argument injection and cross-site requests


@pytest.mark.parametrize("only", ["--output /tmp", "-x", "IT/../..", "IT/Android; rm -rf /", "IT\nX"])
def test_placement_values_that_could_be_options_or_escapes_are_rejected(control, only):
    client, store = control

    response = client.post("/actions/start", data={"name": "build", "only": only})

    assert response.status_code == 422
    assert store.jobs() == []


def test_normal_placements_with_umlauts_and_spaces_are_accepted(control):
    client, _ = control

    response = client.post("/actions/start", data={"name": "report", "only": "Spiritualität/Witchcraft, DIY/Baupläne"},
                           follow_redirects=False)

    assert response.status_code == 303


def test_posts_from_other_sites_are_rejected(control):
    client, store = control

    evil = client.post("/actions/start", data={"name": "report"}, headers={"Origin": "http://evil.example"})
    evil_referer = client.post("/refresh", headers={"Referer": "http://evil.example/page"})
    own = client.post("/actions/start", data={"name": "report"}, headers={"Origin": "http://testserver"},
                      follow_redirects=False)

    assert evil.status_code == 403 and evil_referer.status_code == 403
    assert own.status_code == 303


def test_review_queue_counts_only_files_still_in_use(three_files):
    client, store, _ = three_files
    store.exclude_sources(["BDSM/Shibari/Bondage.azw"], kept="BDSM/Shibari/Bondage.pdf, BDSM/Shibari/Bondage.epub", group="g")

    page = client.get("/").text

    row = page.split("Better Bondage for Every Body</a>")[1].split("</tr>")[0]
    assert "<td>2</td>" in row


def test_update_button_is_on_every_page(setup):
    client, *_ = setup

    for path in ("/", "/files", "/report", "/duplicates", "/actions", "/excluded"):
        page = client.get(path).text
        assert 'action="/refresh"' in page.split("</header>")[0], path


# --- ISBN lookup from the book page


class FakeIsbnSource:
    name = "Fake Library"

    def __init__(self, answers, error=None):
        self.answers, self.error, self.queries = answers, error, []

    def search(self, query):
        from ebookprep.sources import SourceError

        self.queries.append(query)
        if self.error:
            raise SourceError(self.error)
        return list(self.answers.get(query.isbn, []))


def isbn_client(tmp_path, source):
    from ebookprep.sources import Candidate

    input_root = tmp_path / "input"
    (input_root / "IT" / "Android").mkdir(parents=True)
    make_pdf(input_root / "IT/Android/Games.pdf")
    store = StateStore(tmp_path / "work" / "state.db")
    book_id = store.add_book(new_book("g", ("IT/Android/Games.pdf",), candidates=()))
    app = create_app(work_root=tmp_path / "work", input_root=input_root, sources_factory=lambda: [source])
    return TestClient(app), store, book_id


FORM = {"title": "Beginning Android Games", "authors": "Robert Green", "isbn": "978-1-4302-4677-0",
        "publisher": "", "language": "", "pubdate": "", "tags": "", "comments": "my note"}


def test_isbn_lookup_adds_candidate_and_fills_the_form(tmp_path):
    from ebookprep.sources import Candidate

    found = Candidate(source="Fake Library", metadata=BookMetadata(
        title="Beginning Android Games: Second Edition", authors=("Mario Zechner", "Robert Green"),
        isbn="9781430246770", publisher="Apress", pubdate="2012"), cover_url="https://example.org/c.jpg")
    source = FakeIsbnSource({"9781430246770": [found]})
    client, store, book_id = isbn_client(tmp_path, source)

    page = client.post(f"/books/{book_id}/isbn-lookup", data=FORM)

    assert page.status_code == 200
    assert source.queries[0].isbn == "9781430246770"
    assert 'value="Beginning Android Games: Second Edition"' in page.text
    assert "Mario Zechner\nRobert Green" in page.text
    assert 'value="Apress"' in page.text
    assert "my note" in page.text
    stored = store.candidates(book_id)
    assert stored[0].metadata.title == "Beginning Android Games: Second Edition" and stored[0].isbn_match
    assert store.book(book_id).status == "review"


def test_isbn_lookup_without_result_keeps_the_input(tmp_path):
    client, store, book_id = isbn_client(tmp_path, FakeIsbnSource({}, error="HTTP 429"))

    page = client.post(f"/books/{book_id}/isbn-lookup", data=FORM)

    assert "No results for ISBN 9781430246770" in page.text and "HTTP 429" in page.text
    assert 'value="Beginning Android Games"' in page.text
    assert store.candidates(book_id) == []


def test_invalid_isbn_is_not_looked_up(tmp_path):
    source = FakeIsbnSource({})
    client, _, book_id = isbn_client(tmp_path, source)

    page = client.post(f"/books/{book_id}/isbn-lookup", data={**FORM, "isbn": "978-1-23456-789-0"})

    assert page.status_code == 422 and "not a valid ISBN" in page.text
    assert source.queries == []


def test_book_form_has_the_lookup_button(setup):
    client, _, first, *_ = setup

    assert f'formaction="/books/{first}/isbn-lookup"' in client.get(f"/books/{first}").text


def test_candidates_show_the_year_and_scroll_with_use_always_visible(tmp_path):
    long_date = StoredCandidate(
        source="calibre: Google", metadata=BookMetadata(title="Long", pubdate="2010-09-21T07:00:00+00:00"),
        cover_url=None, score=1, isbn_match=False, title_in_text=False, author_in_text=False, auto_acceptable=False)
    store = StateStore(tmp_path / "work" / "state.db")
    book_id = store.add_book(new_book("x", ("IT/Android/x.pdf",), candidates=(long_date,)))
    client = TestClient(create_app(work_root=tmp_path / "work", input_root=tmp_path / "input"))

    page = client.get(f"/books/{book_id}").text

    assert "2010-09-21T07:00:00+00:00" not in page.split('id="metadata-form"')[0]
    assert "<td>2010</td>" in page
    assert '<div class="scroll candidates">' in page
    assert 'class="sticky-right"' in page


def test_use_cleans_an_older_long_date(tmp_path):
    long_date = StoredCandidate(
        source="calibre: Google", metadata=BookMetadata(title="Long", pubdate="2010-09-21T07:00:00+00:00"),
        cover_url=None, score=1, isbn_match=False, title_in_text=False, author_in_text=False, auto_acceptable=False)
    store = StateStore(tmp_path / "work" / "state.db")
    book_id = store.add_book(new_book("x", ("IT/Android/x.pdf",), candidates=(long_date,)))
    client = TestClient(create_app(work_root=tmp_path / "work", input_root=tmp_path / "input"))

    form = client.get(f"/books/{book_id}/use/0").text

    assert 'name="pubdate" value="2010-09-21"' in form


def test_accept_continues_in_the_same_category_then_returns_to_the_queue(tmp_path):
    store = StateStore(tmp_path / "work" / "state.db")
    other_category = store.add_book(new_book("o", ("Woodworking/joinery/a.pdf",), placement="Woodworking/joinery",
                                              metadata=BookMetadata(title="Aaa Joinery")))
    zebra = store.add_book(new_book("z", ("IT/Android/z.pdf",), metadata=BookMetadata(title="Zebra Android")))
    alpha = store.add_book(new_book("a", ("IT/Android/a.pdf",), metadata=BookMetadata(title="Alpha Android")))
    middle = store.add_book(new_book("m", ("IT/Android/m.pdf",), metadata=BookMetadata(title="Middle Android")))
    client = TestClient(create_app(work_root=tmp_path / "work", input_root=tmp_path / "input"))

    def accept(book_id):
        return client.post(f"/books/{book_id}/accept", data={"title": "T", "authors": ""},
                           follow_redirects=False).headers["location"]

    assert accept(middle) == f"/books/{alpha}"
    assert accept(alpha) == f"/books/{zebra}"
    assert accept(zebra) == "/"
    assert store.book(other_category).status == "review"


def test_ignore_also_continues_in_the_same_category(tmp_path):
    store = StateStore(tmp_path / "work" / "state.db")
    store.add_book(new_book("o", ("Woodworking/joinery/a.pdf",), placement="Woodworking/joinery",
                            metadata=BookMetadata(title="Aaa Joinery")))
    plan = store.add_book(new_book("p", ("DIY/Plans/p.jpg.pdf",), placement="DIY/Plans", metadata=BookMetadata(title="P")))
    client = TestClient(create_app(work_root=tmp_path / "work", input_root=tmp_path / "input"))

    assert client.post(f"/books/{plan}/ignore", follow_redirects=False).headers["location"] == "/"


def test_hide_images_choice_is_remembered_across_pages(tmp_path):
    store = StateStore(tmp_path / "work" / "state.db")
    plan = store.add_book(new_book("p", ("DIY/Plans/AZ Regal.jpg.pdf",), placement="DIY/Plans",
                                   metadata=BookMetadata(title="AZ Regal.jpg")))
    first = store.add_book(new_book("a", ("DIY/Plans/Bench.pdf",), placement="DIY/Plans", metadata=BookMetadata(title="Bench")))
    client = TestClient(create_app(work_root=tmp_path / "work", input_root=tmp_path / "input"))

    assert "AZ Regal.jpg" not in client.get("/?apply=1&hide_images=1").text
    assert "AZ Regal.jpg" not in client.get("/").text
    assert "checked" in client.get("/").text.split('name="hide_images"')[1].split(">")[0]

    next_page = client.post(f"/books/{first}/accept", data={"title": "Bench", "authors": ""}, follow_redirects=False)
    assert next_page.headers["location"] == "/"
    assert store.book(plan).status == "review"

    assert "AZ Regal.jpg" in client.get("/?apply=1").text
    assert "AZ Regal.jpg" in client.get("/").text


# --- mislabeled books


@pytest.fixture
def mislabeled(tmp_path):
    store = StateStore(tmp_path / "work" / "state.db")
    real = BookMetadata(title="Moral Design and Technology", authors=("Bart Wernaart",))
    book_id = store.add_book(new_book(
        "m", ("IT/Physical Security/The Complete Guide to Physical Security.pdf",), status="accepted",
        placement="IT/Physical Security", metadata=real, candidates=(),
        local_metadata=BookMetadata(title="The Complete Guide to Physical Security"), mislabeled=True))
    store.add_book(new_book("o", ("Scientific Writing/PhD/x.pdf",), placement="Scientific Writing/PhD"))
    client = TestClient(create_app(work_root=tmp_path / "work", input_root=tmp_path / "input"))
    return client, store, book_id


def test_mislabeled_page_lists_books_with_both_titles_and_categories(mislabeled):
    client, _, book_id = mislabeled

    page = client.get("/mislabeled").text

    assert ">Mislabeled<" in page.split("</header>")[0]
    assert "The Complete Guide to Physical Security" in page and "Moral Design and Technology" in page
    assert "IT/Physical Security/The Complete Guide to Physical Security.pdf" in page
    assert '<option value="Scientific Writing/PhD">' in page
    assert f'action="/books/{book_id}/move"' in page


def test_move_and_undo_and_not_mislabeled(mislabeled):
    client, store, book_id = mislabeled

    moved = client.post(f"/books/{book_id}/move", data={"placement": "Scientific Writing/PhD"}, follow_redirects=False)
    assert moved.status_code == 303 and moved.headers["location"] == "/mislabeled"
    assert store.book(book_id).target_placement == "Scientific Writing/PhD"
    assert "moved to Scientific Writing/PhD" in client.get("/mislabeled").text

    client.post(f"/books/{book_id}/move", data={"placement": ""})
    assert store.book(book_id).target_placement is None

    client.post(f"/books/{book_id}/not-mislabeled")
    assert not store.book(book_id).mislabeled
    assert "Moral Design" not in client.get("/mislabeled").text


def test_move_only_to_known_placements(mislabeled):
    client, store, book_id = mislabeled

    response = client.post(f"/books/{book_id}/move", data={"placement": "../../etc"})

    assert response.status_code == 422
    assert store.book(book_id).target_placement is None


def test_not_mislabeled_is_kept_by_rescore(mislabeled):
    client, store, book_id = mislabeled

    client.post(f"/books/{book_id}/not-mislabeled")

    assert store.book(book_id).decided_by == "user"


def test_duplicates_page_has_sections_per_tier(tmp_path):
    store = StateStore(tmp_path / "work" / "state.db")
    for key, src, title in (("a", "IT/S/a.pdf", "Serious Cryptography"),
                            ("b", "IT/S/b.pdf", "Serious Cryptography, 2nd Edition: A Practical Introduction"),
                            ("c", "IT/S/c.pdf", "Windows Security Internals"),
                            ("d", "IT/S/d.pdf", "Windows Security Internals with PowerShell")):
        store.add_book(new_book(key, (src,), status="accepted", candidates=(),
                                metadata=BookMetadata(title=title, authors=("Same Author",))))
    client = TestClient(create_app(work_root=tmp_path / "work", input_root=tmp_path / "input"))

    page = client.get("/duplicates").text

    certain, likely, possible = (page.split(f'id="{t}"')[1] for t in ("certain", "likely", "possible"))
    assert "Serious Cryptography" in likely.split('id="possible"')[0]
    assert "Windows Security Internals" in possible
    assert "Certain (0)" in page and "Likely (1)" in page and "Possible (1)" in page
    key = possible.split('href="/duplicates/')[1].split('"')[0]
    assert "Possible duplicate" in client.get(f"/duplicates/{key}").text
