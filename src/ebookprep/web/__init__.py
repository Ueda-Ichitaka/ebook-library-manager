"""About this file: the web UI "Ebook Library Manager" (FastAPI, server-rendered pages with htmx).

It works on the same SQLite state as the pipeline. The overview shows the review queue, and next to it the folder overview (see folders.py):
the input folders with their processed files and a complete mark, the output folders
with books, files, a ready mark and a "live" checkbox to remember which folders were
copied to the live library, and the moved files and books outside the libraries;
a book page shows the book's files (which can be opened in the browser to check them),
why it needs review, the evidence from its first pages, and every online candidate with
its checks. The reviewer can copy a candidate into the form, correct any field and accept,
which stores the decision ("user") and moves on to the next open book of the same
placement (in queue order), or back to the queue when the placement is done; look up an ISBN typed
into the form, which adds the results as candidates and fills the form with the best one; split a group whose
files turned out to be different books; or ignore the book (e.g. a picture saved as PDF),
which moves its files to the skipped files. The review queue is sectioned by placement
and can hide such image files; that choice is kept in a cookie, so it holds on every page
and for choosing the next book after accept or ignore. The Update button brings the queue in step with decisions
made in the duplicates view (see duplicates.reconcile_reviews).

The duplicates view lists groups of files that may be the same book in sections from
certain to possible (see duplicates.py)
with size, format and pages (as Kavita counts them, and estimated printed pages). The
reviewer keeps one file, which excludes the others from the build, or dismisses the
group. Several files can be kept: the others are excluded and the kept ones stay a group
until only one is kept or the group is dismissed. Dismissing a group of one book whose
files were only grouped by similar names splits it into separate books. Excluded and
ignored files are listed and can be restored.

A whole category (a placement on the review queue or the files page) can be flagged as
not books, e.g. magazines or blueprints: its files leave the review and the build and
are copied unchanged to the misplaced folder. Flagged categories are listed with the
excluded files and can be restored.

The mislabeled page lists the books whose file contains a different book than its name
says. Each can be moved to another placement (the next build puts it there instead of
the mislabeled folder; input stays untouched), moved back, or marked as not mislabeled.

The files page also lists the input files recognised as moved (see moves.py). The
Kavita page lists the problems of the last Kavita check per book (see kavita_verify.py):
each can be accepted (and undone), a series with a wrong or run-together name can be
renamed in Kavita (login from KAVITA_USERNAME and KAVITA_PASSWORD), and the book can be
opened to correct its metadata; names that only differ in case or punctuation are listed
as notes. The actions page starts every pipeline command (identify, rescore, build, report, Kavita
check) as a background job (see jobs.py) and shows its live log; the files page shows the
state of every input file and the progress per placement (see progress.py); the report
page shows the report data (see report.py). Accepted books are written to the output by the
build stage. The UI has no login and is meant to be reached only from the local machine.
Because a website open in the same browser could still send forms to it, every POST must
come from the UI's own pages (Origin/Referer check), and placement values given to
commands are validated so they can never be read as command-line options.
"""

import html
import os
import re
from collections.abc import Callable
from dataclasses import replace
from functools import partial
from pathlib import Path
from urllib.parse import urlsplit

from fastapi import FastAPI, Form, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from ebookprep.commands import COMMANDS, CommandSettings, command_argv
from ebookprep.config import Config, load_config
from ebookprep.duplicates import TIERS, DuplicateGroup, find_duplicates, reconcile_reviews
from ebookprep.filenames import is_valid_isbn
from ebookprep.identify import REASON_SIMILAR_GROUP, evidence_of, stored_candidates
from ebookprep.fileinfo import FileInfo, file_info
from ebookprep.folders import folder_overview
from ebookprep.jobs import JobBusy, JobRunner
from ebookprep.metadata import BookMetadata
from ebookprep.progress import STATES, file_progress, summarize
from ebookprep.report import summary_report
from ebookprep.scoring import assess, fill_gaps
from ebookprep.sources import LookupQuery, MetadataSource, lookup_all, plain_date
from ebookprep.sources.registry import default_sources
from ebookprep.kavita import KavitaClient, KavitaError
from ebookprep.state import KAVITA_PROBLEM_KINDS, BookRow, KavitaIssue, StateStore, is_under

_HERE = Path(__file__).parent
_MEDIA_TYPES = {".pdf": "application/pdf", ".epub": "application/epub+zip"}


LOG_TAIL_BYTES = 50_000
HIDE_IMAGES_COOKIE = "hide_images"
# a placement like "IT/Security/Pentesting": path segments starting with a letter or digit
_PLACEMENT = re.compile(r"[^\W_][\w .,&()'+-]*(/[^\W_][\w .,&()'+-]*)*")
# a picture saved as PDF, e.g. "AZ Regal.jpg.pdf"
_IMAGE_FILE = re.compile(r"\.(jpe?g|png|gif|bmp|tiff?|webp|heic)(\.pdf)?$", re.IGNORECASE)


def is_image_file(path: str) -> bool:
    return bool(_IMAGE_FILE.search(path))
# htmx stops polling an element when the response has this status
HTMX_STOP_POLLING = 286
# Kavita issues a rename of the series fixes.
RENAMABLE_KINDS = ("spacing", "wrong_name")
# Job statuses as the pages show them.
JOB_STATUS_LABELS = {"flagged": "done with flagged books"}


def status_label(status: str) -> str:
    return JOB_STATUS_LABELS.get(status, status)


def create_app(
    work_root: Path,
    input_root: Path,
    output_root: Path | None = None,
    config_path: Path = Path("config.toml"),
    kavita_url: str = "http://127.0.0.1:5000",
    command_builder: Callable[..., list[str]] = command_argv,
    sources_factory: Callable[[], list[MetadataSource]] | None = None,
    kavita_factory: Callable[[], KavitaClient] | None = None,
) -> FastAPI:
    store = StateStore(work_root / "state.db")
    runner = JobRunner(store, work_root / "jobs")
    settings = CommandSettings(
        input=input_root,
        output=output_root or input_root.parent / "output",
        work=work_root,
        config=config_path,
        kavita_url=kavita_url,
    )
    templates = Jinja2Templates(directory=_HERE / "templates")
    templates.env.globals["status_label"] = status_label
    app = FastAPI(title="ebookprep review")
    app.mount("/static", StaticFiles(directory=_HERE / "static"), name="static")

    @app.middleware("http")
    async def same_origin_posts_only(request: Request, call_next):
        if request.method not in ("GET", "HEAD", "OPTIONS"):
            source = request.headers.get("origin") or request.headers.get("referer")
            if source and urlsplit(source).netloc != request.headers.get("host"):
                return HTMLResponse("Requests from other sites are not accepted.", status_code=403)
        return await call_next(request)

    def get_book(book_id: int) -> BookRow:
        try:
            return store.book(book_id)
        except KeyError:
            raise HTTPException(status_code=404, detail="no such book") from None

    info_cache: dict[tuple[str, float], FileInfo] = {}

    def info(path: str) -> FileInfo | None:
        full = (input_root / path).resolve()
        if not full.is_relative_to(input_root.resolve()) or not full.is_file():
            return None
        key = (path, full.stat().st_mtime)
        if key not in info_cache:
            info_cache[key] = file_info(full)
        return info_cache[key]

    def duplicate_groups() -> list[DuplicateGroup]:
        return find_duplicates(store.books(), store.unused_paths(), store.dismissed_duplicates())

    def get_group(key: str) -> DuplicateGroup:
        group = next((g for g in duplicate_groups() if g.key == key), None)
        if group is None:
            raise HTTPException(status_code=404, detail="no such duplicate group (resolved or dismissed)")
        return group

    @app.get("/", response_class=HTMLResponse)
    def overview(request: Request, status: str = "review", hide_images: bool = False, apply: bool = False):
        if not apply and "hide_images" not in request.query_params:
            hide_images = _hides_images(request)
        unused = store.unused_paths()
        books = [b for b in store.books() if any(s not in unused for s in b.sources)]
        if status == "mislabeled":
            shown = [b for b in books if b.mislabeled]
        else:
            shown = [b for b in books if status == "all" or b.status == status]
        if hide_images:
            shown = [b for b in shown if not all(is_image_file(s) for s in b.sources)]
        sections: dict[str, list[BookRow]] = {}
        for b in sorted(shown, key=lambda b: (b.placement, b.metadata.title.casefold())):
            sections.setdefault(b.placement, []).append(b)
        review = [b for b in books if b.status == "review"]
        counts = {
            "review": len(review),
            "review_files": sum(len([s for s in b.sources if s not in unused]) for b in review),
            "accepted": sum(b.status == "accepted" for b in books),
            "built": sum(b.built_folder is not None and not b.needs_build for b in books),
            "to_build": sum(b.needs_build for b in books),
            "mislabeled": sum(b.mislabeled for b in books),
            "duplicates": len(duplicate_groups()),
        }
        response = templates.TemplateResponse(
            request,
            "overview.html",
            {
                "sections": sections,
                "shown": len(shown),
                "counts": counts,
                "status": status,
                "hide_images": hide_images,
                "active_files": {b.id: len([s for s in b.sources if s not in unused]) for b in shown},
                "folders": folder_overview(input_root, settings.output, store, config()),
            },
        )
        response.set_cookie(HIDE_IMAGES_COOKIE, "1" if hide_images else "0", max_age=365 * 24 * 3600, samesite="lax")
        return response

    def kavita() -> KavitaClient:
        if kavita_factory is not None:
            return kavita_factory()
        username, password = os.environ.get("KAVITA_USERNAME", ""), os.environ.get("KAVITA_PASSWORD", "")
        if not username or not password:
            raise KavitaError("KAVITA_USERNAME and KAVITA_PASSWORD are not set (see kavita.env)")
        client = KavitaClient(kavita_url)
        client.login(username, password)
        return client

    def get_issue(issue_id: int) -> KavitaIssue:
        try:
            return store.kavita_issue(issue_id)
        except KeyError:
            raise HTTPException(status_code=404, detail="no such Kavita issue (a newer check replaced it)") from None

    @app.get("/kavita", response_class=HTMLResponse)
    def kavita_page(request: Request):
        issues = store.kavita_issues()
        books = {b.id: b for b in store.books()}
        checked = [b.kavita_checked_at for b in books.values() if b.kavita_checked_at]
        problems = [i for i in issues if i.kind in KAVITA_PROBLEM_KINDS]
        return templates.TemplateResponse(
            request,
            "kavita.html",
            {
                "open": {k: [i for i in problems if i.open and i.kind == k] for k in KAVITA_PROBLEM_KINDS},
                "fixed": [i for i in problems if i.fixed and not i.accepted],
                "accepted": [i for i in problems if i.accepted],
                "notes": [i for i in issues if i.kind == "case"],
                "books": books,
                "renamable": RENAMABLE_KINDS,
                "last_check": max(checked) if checked else None,
            },
        )

    @app.post("/kavita/issues/{issue_id}/accept")
    def accept_issue(issue_id: int):
        store.accept_kavita_issue(get_issue(issue_id).id)
        return RedirectResponse("/kavita", status_code=303)

    @app.post("/kavita/issues/{issue_id}/unaccept")
    def unaccept_issue(issue_id: int):
        store.unaccept_kavita_issue(get_issue(issue_id).id)
        return RedirectResponse("/kavita", status_code=303)

    @app.post("/kavita/issues/{issue_id}/rename")
    def rename_issue(issue_id: int):
        issue = get_issue(issue_id)
        if issue.kind not in RENAMABLE_KINDS or issue.series_id is None or not issue.expected:
            return HTMLResponse("Only a series with a wrong or run-together name can be renamed.", status_code=422)
        try:
            kavita().rename_series(issue.series_id, issue.expected)
        except KavitaError as error:
            return HTMLResponse(f"Kavita refused the rename: {html.escape(str(error))}", status_code=502)
        store.mark_kavita_issue_fixed(issue.id)
        return RedirectResponse("/kavita", status_code=303)

    @app.post("/folders/live")
    def mark_live(path: str = Form(""), live: str = Form("1")):
        output = settings.output.resolve()
        folder = (settings.output / path).resolve()
        outside = path.split("/")[0] in {config().mislabeled_folder, config().ignored_folder, config().misplaced_folder}
        valid = _PLACEMENT.fullmatch(path) and ".." not in path.split("/") and folder.is_relative_to(output)
        if not valid or outside or folder == output or not folder.is_dir():
            return HTMLResponse(f"Not a library folder of the output: {html.escape(path)}", status_code=422)
        if live == "1":
            store.mark_live(path)
        else:
            store.unmark_live(path)
        return RedirectResponse("/", status_code=303)

    @app.post("/refresh")
    def refresh(request: Request):
        reconcile_reviews(store)
        return RedirectResponse(request.headers.get("referer") or "/", status_code=303)

    @app.get("/books/{book_id}", response_class=HTMLResponse)
    def book_page(request: Request, book_id: int):
        book = get_book(book_id)
        return templates.TemplateResponse(
            request,
            "book.html",
            {"book": book, "candidates": store.candidates(book_id), "form": book.metadata},
        )

    @app.get("/books/{book_id}/use/{rank}", response_class=HTMLResponse)
    def use_candidate(request: Request, book_id: int, rank: int):
        book = get_book(book_id)
        candidates = store.candidates(book_id)
        if not 0 <= rank < len(candidates):
            raise HTTPException(status_code=404, detail="no such candidate")
        chosen = candidates[rank].metadata
        metadata = fill_gaps(replace(chosen, pubdate=plain_date(chosen.pubdate)), book.local_metadata)
        return templates.TemplateResponse(request, "_form.html", {"book": book, "form": metadata})

    @app.post("/books/{book_id}/accept")
    def accept(
        request: Request,
        book_id: int,
        title: str = Form(""),
        authors: str = Form(""),
        isbn: str = Form(""),
        publisher: str = Form(""),
        language: str = Form(""),
        pubdate: str = Form(""),
        tags: str = Form(""),
        comments: str = Form(""),
    ):
        book = get_book(book_id)
        if not title.strip():
            return HTMLResponse("A title is required.", status_code=422)
        metadata = _form_metadata(title, authors, isbn, publisher, language, pubdate, tags, comments)
        store.decide(book_id, metadata, decided_by="user")
        return RedirectResponse(_next_review(store, book.placement, _hides_images(request)), status_code=303)

    def sources() -> list[MetadataSource]:
        return sources_factory() if sources_factory else default_sources(config())

    @app.post("/books/{book_id}/isbn-lookup", response_class=HTMLResponse)
    def isbn_lookup(
        request: Request,
        book_id: int,
        title: str = Form(""),
        authors: str = Form(""),
        isbn: str = Form(""),
        publisher: str = Form(""),
        language: str = Form(""),
        pubdate: str = Form(""),
        tags: str = Form(""),
        comments: str = Form(""),
    ):
        book = get_book(book_id)
        typed = _form_metadata(title, authors, isbn, publisher, language, pubdate, tags, comments)
        digits = re.sub(r"[\s-]", "", isbn).upper()

        def page(form: BookMetadata, message: str, status: int = 200):
            context = {"book": book, "candidates": store.candidates(book_id), "form": form, "lookup_message": message}
            return templates.TemplateResponse(request, "book.html", context, status_code=status)

        if not is_valid_isbn(digits):
            return page(typed, f"{isbn.strip() or 'empty'} is not a valid ISBN", status=422)

        query = LookupQuery(title=typed.title or book.metadata.title, authors=typed.authors, isbn=digits)
        result = lookup_all(sources(), query)
        errors = "; ".join(f"{name}: {error}" for name, error in result.errors)
        if not result.candidates:
            return page(typed, f"No results for ISBN {digits}" + (f" ({errors})" if errors else ""))

        unused = store.unused_paths()
        evidence = evidence_of([input_root / s for s in book.sources if s not in unused])
        local = replace(typed, isbn=digits)
        ranked = sorted((assess(c, local, evidence) for c in result.candidates), key=lambda a: -a.score)
        store.add_candidates(book_id, stored_candidates(ranked))
        best = ranked[0].candidate.metadata
        filled = fill_gaps(best, local)
        found = f"Found {len(ranked)} result(s) for ISBN {digits}; the form shows the best one, check it and accept."
        return page(filled, found + (f" ({errors})" if errors else ""))

    @app.post("/books/{book_id}/ignore")
    def ignore(request: Request, book_id: int):
        book = get_book(book_id)
        store.ignore_sources(list(book.sources))
        return RedirectResponse(_next_review(store, book.placement, _hides_images(request)), status_code=303)

    @app.post("/ignored/restore")
    def restore_ignored(path: str = Form("")):
        store.restore_ignored(path)
        return RedirectResponse("/excluded", status_code=303)

    @app.post("/books/{book_id}/split")
    def split(book_id: int):
        get_book(book_id)
        new_ids = store.split_book(book_id)
        return RedirectResponse(f"/books/{new_ids[0]}", status_code=303)

    @app.get("/duplicates", response_class=HTMLResponse)
    def duplicates_list(request: Request):
        return templates.TemplateResponse(request, "duplicates.html", {"groups": duplicate_groups(), "tiers": TIERS})

    @app.get("/duplicates/{key}", response_class=HTMLResponse)
    def duplicate_detail(request: Request, key: str):
        group = get_group(key)
        rows = []
        for f in group.files:
            book = store.book(f.book_id)
            rows.append({"file": f, "info": info(f.path), "index": book.sources.index(f.path), "book": book})
        return templates.TemplateResponse(request, "duplicate.html", {"group": group, "rows": rows})

    @app.post("/duplicates/{key}/keep")
    def keep(key: str, keep: list[str] = Form([])):
        group = get_group(key)
        paths = [f.path for f in group.files]
        if not keep or any(k not in paths for k in keep):
            return HTMLResponse("Choose the group's file(s) to keep.", status_code=422)
        others = [p for p in paths if p not in keep]
        if others:
            store.exclude_sources(others, kept=", ".join(keep), group=key)
        reconcile_reviews(store)
        return RedirectResponse("/duplicates", status_code=303)

    @app.post("/duplicates/{key}/dismiss")
    def dismiss(key: str):
        group = get_group(key)
        book_ids = {f.book_id for f in group.files}
        only_book = store.book(next(iter(book_ids))) if len(book_ids) == 1 else None
        if only_book is not None and REASON_SIMILAR_GROUP in only_book.review_reasons:
            store.split_book(only_book.id)
        else:
            store.dismiss_duplicates(key)
        return RedirectResponse("/duplicates", status_code=303)

    @app.get("/excluded", response_class=HTMLResponse)
    def excluded_list(request: Request):
        return templates.TemplateResponse(
            request,
            "excluded.html",
            {
                "excluded": store.excluded_sources(),
                "ignored": store.ignored_sources(),
                "not_books": store.not_book_categories(),
                "misplaced_folder": config().misplaced_folder,
            },
        )

    @app.post("/categories/not-books")
    def flag_not_books(placement: str = Form(""), back: str = Form("/")):
        folder = (input_root / placement).resolve()
        inside = folder.is_relative_to(input_root.resolve()) and folder != input_root.resolve()
        if not _PLACEMENT.fullmatch(placement) or ".." in placement.split("/") or not inside or not folder.is_dir():
            return HTMLResponse(f"Not a category folder of the input: {html.escape(placement)}", status_code=422)
        store.flag_not_books(placement)
        reconcile_reviews(store)
        return RedirectResponse(back if back in ("/", "/files") else "/", status_code=303)

    @app.post("/categories/restore-books")
    def restore_books(placement: str = Form("")):
        store.restore_books(placement)
        return RedirectResponse("/excluded", status_code=303)

    @app.post("/excluded/restore")
    def restore(path: str = Form("")):
        store.restore_source(path)
        return RedirectResponse("/excluded", status_code=303)

    def config() -> Config:
        return load_config(config_path) if config_path.is_file() else Config()

    @app.get("/mislabeled", response_class=HTMLResponse)
    def mislabeled_list(request: Request):
        unused = store.unused_paths()
        books = sorted(
            (b for b in store.books() if b.mislabeled and any(s not in unused for s in b.sources)),
            key=lambda b: (b.placement, b.metadata.title),
        )
        placements = sorted({b.placement for b in store.books()})
        return templates.TemplateResponse(request, "mislabeled.html", {"books": books, "placements": placements})

    @app.post("/books/{book_id}/move")
    def move(book_id: int, placement: str = Form("")):
        get_book(book_id)
        if placement and placement not in {b.placement for b in store.books()}:
            return HTMLResponse(f"Unknown placement: {html.escape(placement)}", status_code=422)
        store.move_book(book_id, placement or None)
        return RedirectResponse("/mislabeled", status_code=303)

    @app.post("/books/{book_id}/not-mislabeled")
    def not_mislabeled(book_id: int):
        book = get_book(book_id)
        store.set_mislabeled(book_id, False)
        store.move_book(book_id, None)
        # the user's word: "rescore" must not flag the book again
        if book.status == "accepted":
            store.decide(book_id, book.metadata, decided_by="user")
        return RedirectResponse("/mislabeled", status_code=303)

    @app.get("/actions", response_class=HTMLResponse)
    def actions(request: Request):
        return templates.TemplateResponse(
            request, "actions.html", {"commands": COMMANDS, "current": runner.current(), "jobs": runner.history(20)}
        )

    @app.post("/actions/start")
    def start(name: str = Form(""), only: str = Form("")):
        if name not in COMMANDS:
            raise HTTPException(status_code=404, detail="no such command")
        placements = tuple(p.strip() for p in only.split(",") if p.strip())
        invalid = [p for p in placements if not _PLACEMENT.fullmatch(p)]
        if invalid:
            return HTMLResponse(f"Not a placement: {html.escape(', '.join(invalid))}", status_code=422)
        try:
            job_id = runner.start(name, command_builder(name, settings, only=placements))
        except JobBusy as busy:
            return HTMLResponse(f"Another job is running: {html.escape(str(busy))}", status_code=409)
        return RedirectResponse(f"/jobs/{job_id}", status_code=303)

    def get_job(job_id: int):
        try:
            return runner.job(job_id)
        except KeyError:
            raise HTTPException(status_code=404, detail="no such job") from None

    @app.get("/jobs/{job_id}", response_class=HTMLResponse)
    def job_page(request: Request, job_id: int):
        job = get_job(job_id)
        label = COMMANDS[job.name].label if job.name in COMMANDS else job.name
        return templates.TemplateResponse(request, "job.html", {"job": job, "label": label})

    @app.get("/jobs/{job_id}/log", response_class=HTMLResponse)
    def job_log(job_id: int):
        job = get_job(job_id)
        text = html.escape(runner.log(job_id, tail_bytes=LOG_TAIL_BYTES), quote=False)
        body = f'<p class="muted">status: {status_label(job.status)}</p><pre class="log">{text}</pre>'
        status = 200 if job.status == "running" else HTMX_STOP_POLLING
        return HTMLResponse(body, status_code=status, headers={"X-Job-Status": job.status})

    @app.post("/jobs/{job_id}/stop")
    def stop_job(job_id: int):
        get_job(job_id)
        runner.stop(job_id)
        return RedirectResponse(f"/jobs/{job_id}", status_code=303)

    @app.get("/files", response_class=HTMLResponse)
    def files(request: Request, state: str = ""):
        progress = file_progress(input_root, store, config())
        shown = [f for f in progress if not state or f.state == state]
        return templates.TemplateResponse(
            request,
            "files.html",
            {
                "summary": summarize(progress),
                "files": shown,
                "states": STATES,
                "state": state,
                "is_not_book": partial(is_under, categories={c.placement for c in store.not_book_categories()}),
                "moved": store.moved_files(),
            },
        )

    @app.get("/report", response_class=HTMLResponse)
    def report(request: Request):
        return templates.TemplateResponse(request, "report.html", {"report": summary_report(store)})

    @app.get("/books/{book_id}/files/{index}")
    def source_file(book_id: int, index: int):
        book = get_book(book_id)
        if not 0 <= index < len(book.sources):
            raise HTTPException(status_code=404, detail="no such file")
        path = (input_root / book.sources[index]).resolve()
        if not path.is_relative_to(input_root.resolve()) or not path.is_file():
            raise HTTPException(status_code=404, detail="file not found")
        media_type = _MEDIA_TYPES.get(path.suffix.lower(), "application/octet-stream")
        return FileResponse(path, media_type=media_type, content_disposition_type="inline", filename=path.name)

    return app


def _form_metadata(title, authors, isbn, publisher, language, pubdate, tags, comments) -> BookMetadata:
    return BookMetadata(
        title=title.strip(),
        authors=tuple(a.strip() for a in authors.splitlines() if a.strip()),
        isbn=re.sub(r"[\s-]", "", isbn) or None,
        publisher=publisher.strip() or None,
        language=language.strip() or None,
        pubdate=pubdate.strip() or None,
        comments=comments.strip() or None,
        tags=tuple(t.strip() for t in tags.split(",") if t.strip()),
    )


def _hides_images(request: Request) -> bool:
    return request.cookies.get(HIDE_IMAGES_COOKIE) == "1"


def _next_review(store: StateStore, placement: str, hide_images: bool = False) -> str:
    """The first open book of the placement in queue order (by title), else the queue;
    image files are skipped while the queue hides them."""
    unused = store.unused_paths()
    waiting = sorted(
        (
            b for b in store.books(status="review")
            if b.placement == placement
            and any(s not in unused for s in b.sources)
            and not (hide_images and all(is_image_file(s) for s in b.sources))
        ),
        key=lambda b: b.metadata.title.casefold(),
    )
    return f"/books/{waiting[0].id}" if waiting else "/"

