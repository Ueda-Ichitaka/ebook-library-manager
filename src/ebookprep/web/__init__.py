"""About this file: the review web UI (FastAPI, server-rendered pages with htmx).

It works on the same SQLite state as the pipeline. The overview shows the review queue;
a book page shows the book's files (which can be opened in the browser to check them),
why it needs review, the evidence from its first pages, and every online candidate with
its checks. The reviewer can copy a candidate into the form, correct any field and accept,
which stores the decision ("user") and moves on to the next book; or split a group whose
files turned out to be different books.

The duplicates view lists groups of files that may be the same book (see duplicates.py)
with size, format and pages (as Kavita counts them, and estimated printed pages). The
reviewer keeps one file, which excludes the others from the build, or dismisses the
group. Excluded files are listed and can be restored.

The actions page starts every pipeline command (identify, rescore, build, report, Kavita
check) as a background job (see jobs.py) and shows its live log; the files page shows the
state of every input file and the progress per placement (see progress.py); the report
page shows the report data (see report.py). Accepted books are written to the output by the
build stage. The UI has no login and is meant to be reached only from the local machine.
"""

import html
from collections.abc import Callable
from pathlib import Path

from fastapi import FastAPI, Form, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from ebookprep.commands import COMMANDS, CommandSettings, command_argv
from ebookprep.config import Config, load_config
from ebookprep.duplicates import DuplicateGroup, find_duplicates
from ebookprep.fileinfo import FileInfo, file_info
from ebookprep.jobs import JobBusy, JobRunner
from ebookprep.metadata import BookMetadata
from ebookprep.progress import STATES, file_progress, summarize
from ebookprep.report import summary_report
from ebookprep.scoring import fill_gaps
from ebookprep.state import BookRow, StateStore

_HERE = Path(__file__).parent
_MEDIA_TYPES = {".pdf": "application/pdf", ".epub": "application/epub+zip"}


LOG_TAIL_BYTES = 50_000
# htmx stops polling an element when the response has this status
HTMX_STOP_POLLING = 286


def create_app(
    work_root: Path,
    input_root: Path,
    output_root: Path | None = None,
    config_path: Path = Path("config.toml"),
    kavita_url: str = "http://127.0.0.1:5000",
    command_builder: Callable[..., list[str]] = command_argv,
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
    app = FastAPI(title="ebookprep review")
    app.mount("/static", StaticFiles(directory=_HERE / "static"), name="static")

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
        return find_duplicates(store.books(), store.excluded_paths(), store.dismissed_duplicates())

    def get_group(key: str) -> DuplicateGroup:
        group = next((g for g in duplicate_groups() if g.key == key), None)
        if group is None:
            raise HTTPException(status_code=404, detail="no such duplicate group (resolved or dismissed)")
        return group

    @app.get("/", response_class=HTMLResponse)
    def overview(request: Request, status: str = "review"):
        excluded = store.excluded_paths()
        books = [b for b in store.books() if any(s not in excluded for s in b.sources)]
        if status == "mislabeled":
            shown = [b for b in books if b.mislabeled]
        else:
            shown = [b for b in books if status == "all" or b.status == status]
        counts = {
            "review": sum(b.status == "review" for b in books),
            "accepted": sum(b.status == "accepted" for b in books),
            "built": sum(b.built_folder is not None and not b.needs_build for b in books),
            "to_build": sum(b.needs_build for b in books),
            "mislabeled": sum(b.mislabeled for b in books),
            "duplicates": len(duplicate_groups()),
        }
        return templates.TemplateResponse(
            request, "overview.html", {"books": shown, "counts": counts, "status": status}
        )

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
        metadata = fill_gaps(candidates[rank].metadata, book.local_metadata)
        return templates.TemplateResponse(request, "_form.html", {"book": book, "form": metadata})

    @app.post("/books/{book_id}/accept")
    def accept(
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
        get_book(book_id)
        if not title.strip():
            return HTMLResponse("A title is required.", status_code=422)
        metadata = BookMetadata(
            title=title.strip(),
            authors=tuple(a.strip() for a in authors.splitlines() if a.strip()),
            isbn=isbn.strip().replace("-", "") or None,
            publisher=publisher.strip() or None,
            language=language.strip() or None,
            pubdate=pubdate.strip() or None,
            comments=comments.strip() or None,
            tags=tuple(t.strip() for t in tags.split(",") if t.strip()),
        )
        store.decide(book_id, metadata, decided_by="user")
        return RedirectResponse(_next_review(store), status_code=303)

    @app.post("/books/{book_id}/split")
    def split(book_id: int):
        get_book(book_id)
        new_ids = store.split_book(book_id)
        return RedirectResponse(f"/books/{new_ids[0]}", status_code=303)

    @app.get("/duplicates", response_class=HTMLResponse)
    def duplicates_list(request: Request):
        return templates.TemplateResponse(request, "duplicates.html", {"groups": duplicate_groups()})

    @app.get("/duplicates/{key}", response_class=HTMLResponse)
    def duplicate_detail(request: Request, key: str):
        group = get_group(key)
        rows = []
        for f in group.files:
            book = store.book(f.book_id)
            rows.append({"file": f, "info": info(f.path), "index": book.sources.index(f.path), "book": book})
        return templates.TemplateResponse(request, "duplicate.html", {"group": group, "rows": rows})

    @app.post("/duplicates/{key}/keep")
    def keep(key: str, keep: str = Form("")):
        group = get_group(key)
        paths = [f.path for f in group.files]
        if keep not in paths:
            return HTMLResponse("Choose one of the group's files to keep.", status_code=422)
        store.exclude_sources([p for p in paths if p != keep], kept=keep, group=key)
        return RedirectResponse("/duplicates", status_code=303)

    @app.post("/duplicates/{key}/dismiss")
    def dismiss(key: str):
        get_group(key)
        store.dismiss_duplicates(key)
        return RedirectResponse("/duplicates", status_code=303)

    @app.get("/excluded", response_class=HTMLResponse)
    def excluded_list(request: Request):
        return templates.TemplateResponse(request, "excluded.html", {"excluded": store.excluded_sources()})

    @app.post("/excluded/restore")
    def restore(path: str = Form("")):
        store.restore_source(path)
        return RedirectResponse("/excluded", status_code=303)

    def config() -> Config:
        return load_config(config_path) if config_path.is_file() else Config()

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
        body = f'<p class="muted">status: {job.status}</p><pre class="log">{text}</pre>'
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
            {"summary": summarize(progress), "files": shown, "states": STATES, "state": state},
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


def _next_review(store: StateStore) -> str:
    waiting = store.books(status="review")
    return f"/books/{waiting[0].id}" if waiting else "/"

