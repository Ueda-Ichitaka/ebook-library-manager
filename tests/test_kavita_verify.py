"""About this file: tests for ebookprep.kavita_verify, which compares the series a real
Kavita shows with the books that were built. Kavita is replaced by a fake client here;
the end-to-end test (test_kavita_e2e.py) runs against a real Kavita."""

from ebookprep.kavita_verify import expected_files, verify
from ebookprep.metadata import BookMetadata
from ebookprep.state import NewBook, StateStore


class FakeKavita:
    def __init__(self, series: dict[str, list[str]]):
        self.series = [{"id": i, "name": name} for i, name in enumerate(series)]
        self.files = {i: paths for i, paths in enumerate(series.values())}

    def all_series(self):
        return self.series

    def series_files(self, series_id):
        return self.files[series_id]


EXPECTED = {
    "IT/Android/A Book - Author/A Book - Author.pdf": "A Book: The Subtitle",
    "IT/Android/A Book - Author/A Book - Author.epub": "A Book: The Subtitle",
    "IT/Android/Other - X/Other - X.pdf": "Other",
}


def test_every_book_on_its_own_is_ok():
    kavita = FakeKavita(
        {
            "A Book: The Subtitle": ["/library/IT/Android/A Book - Author/A Book - Author.pdf"],
            "A Book: The Subtitle ": ["/library/IT/Android/A Book - Author/A Book - Author.epub"],
            "Other": ["/library/IT/Android/Other - X/Other - X.pdf"],
        }
    )

    result = verify(kavita, EXPECTED, library_root="/library")

    assert result.ok
    assert (result.checked_files, result.series) == (3, 3)


def test_folder_named_series_with_several_books_is_reported():
    kavita = FakeKavita(
        {
            "Android": [
                "/library/IT/Android/A Book - Author/A Book - Author.pdf",
                "/library/IT/Android/Other - X/Other - X.pdf",
            ],
            "A Book: The Subtitle": ["/library/IT/Android/A Book - Author/A Book - Author.epub"],
        }
    )

    result = verify(kavita, EXPECTED, library_root="/library")

    assert not result.ok
    assert [name for name, _ in result.merged] == ["Android"]
    assert ("IT/Android/Other - X/Other - X.pdf", "Other", "Android") in result.wrong_name


def test_missing_files_are_reported():
    kavita = FakeKavita({"Other": ["/library/IT/Android/Other - X/Other - X.pdf"]})

    result = verify(kavita, EXPECTED, library_root="/library")

    assert sorted(result.missing) == [
        "IT/Android/A Book - Author/A Book - Author.epub",
        "IT/Android/A Book - Author/A Book - Author.pdf",
    ]


def test_files_kavita_shows_that_were_not_built_are_reported():
    kavita = FakeKavita(
        {
            "Other": ["/library/IT/Android/Other - X/Other - X.pdf"],
            "Stray": ["/library/IT/Android/stray.pdf"],
        }
    )

    result = verify(kavita, {"IT/Android/Other - X/Other - X.pdf": "Other"}, library_root="/library")

    assert result.unexpected == ["IT/Android/stray.pdf"]
    assert not result.ok


def test_expected_files_come_from_built_books_outside_the_mislabeled_folder(tmp_path):
    output = tmp_path / "output"
    store = StateStore(tmp_path / "state.db")
    for key, folder, mislabeled in (("a", "IT/Android/A - X", False), ("b", "_mislabeled/IT/Android/B - Y", True)):
        book_dir = output / folder
        book_dir.mkdir(parents=True)
        name = folder.rsplit("/", 1)[1]
        for suffix in (".pdf", ".epub", ".opf", ".jpg", ".mobi"):
            (book_dir / f"{name}{suffix}").write_bytes(b"x")
        metadata = BookMetadata(title=f"Title {key}")
        book_id = store.add_book(
            NewBook(key=key, placement="IT/Android", group_reason="single", book_folder=None, sources=(f"{key}.pdf",),
                    local_metadata=metadata, local_source="filename", local_notes=(), evidence_excerpt="",
                    evidence_isbns=(), lookup_errors=(), status="accepted", decided_by="user", review_reasons=(),
                    metadata=metadata, mislabeled=mislabeled)
        )
        store.mark_built(book_id, metadata, folder)

    assert expected_files(store, output, mislabeled_folder="_mislabeled") == {
        "IT/Android/A - X/A - X.pdf": "Title a",
        "IT/Android/A - X/A - X.epub": "Title a",
    }


def test_kavita_check_command(tmp_path, monkeypatch, capsys):
    from ebookprep.__main__ import main

    calls = []

    class Client(FakeKavita):
        def __init__(self):
            super().__init__({"Android": ["/library/IT/Android/x.pdf", "/library/IT/Android/y.pdf"]})

        def scan_all_and_wait(self):
            calls.append("scan")

    monkeypatch.setattr("ebookprep.__main__.connect_kavita", lambda url, user, password: calls.append((url, user)) or Client())
    monkeypatch.setenv("KAVITA_USERNAME", "admin")
    monkeypatch.setenv("KAVITA_PASSWORD", "secret")
    config = tmp_path / "config.toml"
    config.write_text("", encoding="utf-8")

    exit_code = main(["kavita-check", "--output", str(tmp_path / "out"), "--work", str(tmp_path / "work"),
                      "--config", str(config), "--url", "http://kavita:5000", "--scan"])

    out = capsys.readouterr().out
    assert exit_code == 3
    assert calls == [("http://kavita:5000", "admin"), "scan"]
    assert "merged" in out and "Android" in out
    assert "secret" not in out


def test_kavita_check_needs_credentials(tmp_path, monkeypatch, capsys):
    from ebookprep.__main__ import main

    monkeypatch.delenv("KAVITA_USERNAME", raising=False)
    monkeypatch.delenv("KAVITA_PASSWORD", raising=False)

    exit_code = main(["kavita-check", "--output", str(tmp_path), "--work", str(tmp_path)])

    assert exit_code == 2
    assert "KAVITA_USERNAME" in capsys.readouterr().err


def test_expected_series_is_the_built_series(tmp_path):
    output = tmp_path / "output"
    store = StateStore(tmp_path / "state.db")
    (output / "S/W/Wicca - A").mkdir(parents=True)
    (output / "S/W/Wicca - A/Wicca - A.pdf").write_bytes(b"x")
    metadata = BookMetadata(title="Wicca", authors=("A",))
    book_id = store.add_book(
        NewBook(key="k", placement="S/W", group_reason="single", book_folder=None, sources=("w.pdf",),
                local_metadata=metadata, local_source="filename", local_notes=(), evidence_excerpt="",
                evidence_isbns=(), lookup_errors=(), status="accepted", decided_by="user", review_reasons=(),
                metadata=metadata)
    )
    store.mark_built(book_id, metadata, "S/W/Wicca - A", series="Wicca (A)")

    assert expected_files(store, output, mislabeled_folder="_mislabeled") == {"S/W/Wicca - A/Wicca - A.pdf": "Wicca (A)"}


def test_older_spelling_kept_by_kavita_is_only_noted():
    kavita = FakeKavita({"Egils Saga": ["/library/S/P/Egils saga - X/Egils saga - X.pdf"]})

    result = verify(kavita, {"S/P/Egils saga - X/Egils saga - X.pdf": "Egils saga"}, library_root="/library")

    assert result.ok
    assert result.wrong_name == []
    assert result.outdated_name == [("S/P/Egils saga - X/Egils saga - X.pdf", "Egils saga", "Egils Saga")]


def built_book(store, folder, title):
    metadata = BookMetadata(title=title)
    book_id = store.add_book(
        NewBook(key=folder, placement=folder.rsplit("/", 1)[0], group_reason="single", book_folder=None,
                sources=(f"{folder}.pdf",), local_metadata=metadata, local_source="filename", local_notes=(),
                evidence_excerpt="", evidence_isbns=(), lookup_errors=(), status="accepted", decided_by="user",
                review_reasons=(), metadata=metadata)
    )
    store.mark_built(book_id, metadata, folder)
    return book_id


def test_names_that_run_words_together_are_problems_but_case_and_punctuation_are_notes():
    expected = {"S/P/Form - X/Form - X.pdf": "Form der wissenschaftlichen Ausarbeitung",
                "S/P/Pro - Y/Pro - Y.pdf": "Pro PHP Programming",
                "S/P/Natur - Z/Natur - Z.pdf": "Naturrituale: Mit Ritualen"}
    kavita = FakeKavita({"FormDerWissenschaftlichenAusarbeitung": ["/library/S/P/Form - X/Form - X.pdf"],
                         "Pro PHP programming": ["/library/S/P/Pro - Y/Pro - Y.pdf"],
                         "Naturrituale · Mit Ritualen": ["/library/S/P/Natur - Z/Natur - Z.pdf"]})

    result = verify(kavita, expected, library_root="/library")

    assert [(f, actual) for f, _, actual in result.spacing] == [("S/P/Form - X/Form - X.pdf", "FormDerWissenschaftlichenAusarbeitung")]
    assert sorted(f for f, _, _ in result.outdated_name) == ["S/P/Natur - Z/Natur - Z.pdf", "S/P/Pro - Y/Pro - Y.pdf"]
    assert not result.ok
    assert result.series_ids["S/P/Form - X/Form - X.pdf"] == 0


def test_check_issues_are_stored_per_book_and_can_be_accepted(tmp_path):
    from ebookprep.kavita_verify import record_check

    store = StateStore(tmp_path / "state.db")
    a = built_book(store, "IT/Android/A Book - Author", "A Book: The Subtitle")
    other = built_book(store, "IT/Android/Other - X", "Other")
    kavita = FakeKavita({
        "A Book: The Subtitle": ["/library/IT/Android/A Book - Author/A Book - Author.pdf",
                                 "/library/IT/Android/A Book - Author/A Book - Author.epub"],
        "Wrong": ["/library/IT/Android/Other - X/Other - X.pdf"],
        "Leftover": ["/library/IT/Android/leftover.pdf"],
    })

    open_issues = record_check(store, EXPECTED, verify(kavita, EXPECTED, library_root="/library"))

    issues = {i.kind: i for i in store.kavita_issues()}
    assert set(issues) == {"wrong_name", "unexpected"} and open_issues == 2
    wrong = issues["wrong_name"]
    assert (wrong.book_id, wrong.expected, wrong.actual, wrong.series_id) == (other, "Other", "Wrong", 1)
    assert store.book(a).kavita_passed and not store.book(other).kavita_passed
    assert "wrong name" in store.book(other).kavita_problem
    assert store.kavita_unexpected() == ["IT/Android/leftover.pdf"]

    store.accept_kavita_issue(wrong.id)
    store.accept_kavita_issue(issues["unexpected"].id)
    assert store.book(other).kavita_passed and store.kavita_unexpected() == []

    assert record_check(store, EXPECTED, verify(kavita, EXPECTED, library_root="/library")) == 0
    assert all(i.accepted for i in store.kavita_issues()) and store.book(other).kavita_passed

    store.unaccept_kavita_issue(next(i.id for i in store.kavita_issues() if i.kind == "wrong_name"))
    assert not store.book(other).kavita_passed


def test_renamed_issue_counts_as_fixed_until_the_next_check(tmp_path):
    from ebookprep.kavita_verify import record_check

    store = StateStore(tmp_path / "state.db")
    book = built_book(store, "S/P/Form - X", "Form der wissenschaftlichen Ausarbeitung")
    expected = {"S/P/Form - X/Form - X.pdf": "Form der wissenschaftlichen Ausarbeitung"}
    kavita = FakeKavita({"FormDerWissenschaftlichenAusarbeitung": ["/library/S/P/Form - X/Form - X.pdf"]})
    record_check(store, expected, verify(kavita, expected, library_root="/library"))
    (issue,) = store.kavita_issues()
    assert issue.kind == "spacing" and not store.book(book).kavita_passed

    store.mark_kavita_issue_fixed(issue.id)

    (issue,) = store.kavita_issues()
    assert issue.fixed and store.book(book).kavita_passed
