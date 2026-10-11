"""About this file: the review web UI (FastAPI, server-rendered pages with htmx).

It works on the same SQLite state as the pipeline. The overview shows the review queue;
a book page shows the book's files (which can be opened in the browser to check them),
why it needs review, the evidence from its first pages, and every online candidate with
its checks. The reviewer can copy a candidate into the form, correct any field and accept,
which stores the decision ("user") and moves on to the next book; or split a group whose
files turned out to be different books. Accepted books are written to the output by the
build stage. The UI has no login and is meant to be reached only from the local machine.
"""

from pathlib import Path

from fastapi import FastAPI, Form, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from ebookprep.metadata import BookMetadata
from ebookprep.scoring import fill_gaps
from ebookprep.state import BookRow, StateStore

_HERE = Path(__file__).parent
_MEDIA_TYPES = {".pdf": "application/pdf", ".epub": "application/epub+zip"}


def create_app(work_root: Path, input_root: Path) -> FastAPI:
    store = StateStore(work_root / "state.db")
    templates = Jinja2Templates(directory=_HERE / "templates")
    app = FastAPI(title="ebookprep review")
    app.mount("/static", StaticFiles(directory=_HERE / "static"), name="static")

    def get_book(book_id: int) -> BookRow:
        try:
            return store.book(book_id)
        except KeyError:
            raise HTTPException(status_code=404, detail="no such book") from None

    @app.get("/", response_class=HTMLResponse)
    def overview(request: Request, status: str = "review"):
        books = store.books()
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

