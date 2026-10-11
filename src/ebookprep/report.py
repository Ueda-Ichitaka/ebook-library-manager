"""About this file: the reports of the pipeline, built from the SQLite state.

A report collects the build results (done, failed and flagged, retried after a silent
calibre failure with the removed XMP packets, repaired PDFs, conversions), the books
whose content differs from their filename, the files excluded as duplicates and the
skipped input files. There is a report per build run and a summary with the newest result
of every book across all runs. ReportData holds the content; it is written as Markdown to
the work folder and shown as a page in the web UI.
"""

from dataclasses import dataclass, field
from pathlib import Path

from ebookprep.state import BookRow, ExcludedSource, RecordEntry, StateStore


@dataclass
class ReportData:
    heading: str
    records: list[RecordEntry]
    skipped: list[tuple[str, str]]
    mislabeled: list[BookRow] = field(default_factory=list)
    excluded: list[ExcludedSource] = field(default_factory=list)

    @property
    def done(self) -> list[RecordEntry]:
        return [r for r in self.records if r.status == "done"]

    @property
    def failed(self) -> list[RecordEntry]:
        return [r for r in self.records if r.flagged]

    @property
    def review(self) -> list[RecordEntry]:
        return [r for r in self.done if r.needs_review]

    @property
    def retried(self) -> list[RecordEntry]:
        return [r for r in self.records if r.retried]

    @property
    def repaired(self) -> list[str]:
        return [source for r in self.records for source in r.repaired]

    @property
    def converted(self) -> list[str]:
        return [source for r in self.records for source in r.converted]

    @property
    def counts(self) -> list[tuple[str, int]]:
        return [
            ("Done", len(self.done)),
            ("Failed (flagged)", len(self.failed)),
            ("Needs review", len(self.review)),
            ("Retried after silent calibre failure", len(self.retried)),
            ("Repaired PDFs", len(self.repaired)),
            ("Converted to EPUB", len(self.converted)),
            ("Skipped input files", len(self.skipped)),
        ]


def run_report(store: StateStore, run_id: int) -> ReportData:
    return ReportData(f"ebookprep report, run {run_id}", store.records(run_id), store.skipped(run_id))


def summary_report(store: StateStore) -> ReportData:
    return ReportData(
        "ebookprep report, all runs",
        store.latest_records(),
        store.all_skipped(),
        mislabeled=[b for b in store.books() if b.mislabeled],
        excluded=store.excluded_sources(),
    )


def write_report(store: StateStore, run_id: int, path: Path) -> Path:
    """Report of one run."""
    return _write(render_markdown(run_report(store, run_id)), path)


def write_summary_report(store: StateStore, path: Path) -> Path:
    """Report of the current state: the newest result of every book across all runs."""
    return _write(render_markdown(summary_report(store)), path)


def _write(text: str, path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def render_markdown(data: ReportData) -> str:
    lines = [f"# {data.heading}", "", "| Result | Count |", "|---|---|"]
    lines += [f"| {label} | {count} |" for label, count in data.counts]
    lines.append("")

    lines += ["## Failed (flagged)", ""]
    for r in data.failed:
        lines += [f"- **{r.placement}/{r.record_name}**: {r.error}", *[f"  - source: `{s}`" for s in r.sources]]
    lines.append("")

    lines += ["## Needs review", ""]
    for r in data.review:
        reasons = ([f"grouped as {r.group_reason}"] if r.group_reason == "similar" else []) + list(r.notes)
        lines += [f"- **{r.placement}/{r.record_name}** ({r.title}): {'; '.join(reasons)}"]
        lines += [f"  - source: `{s}`" for s in r.sources]
    lines.append("")

    lines += ["## Retried, embedded XMP metadata removed", ""]
    for r in data.retried:
        lines.append(f"- **{r.placement}/{r.record_name}**: {r.status}")
        for file_name, packet in r.removed_xmp:
            lines += [f"  - removed from `{file_name}`:", "", "```xml", packet.strip(), "```", ""]
    lines.append("")

    lines += ["## Repaired PDFs", ""]
    lines += [f"- `{source}`" for source in data.repaired]
    lines.append("")

    lines += ["## Converted to EPUB", ""]
    lines += [f"- `{source}`" for source in data.converted]
    lines.append("")

    if data.mislabeled:
        lines += ["## Content differs from the filename", ""]
        lines += ["| Title from filename / .opf | Real content | Input file | Output folder |", "|---|---|---|---|"]
        for book in data.mislabeled:
            sources = "<br>".join(f"`{s}`" for s in book.sources)
            built = f"`{book.built_folder}`" if book.built_folder else "not built yet"
            lines.append(f"| {book.local_metadata.title} | {book.metadata.title} | {sources} | {built} |")
        lines.append("")

    if data.excluded:
        lines += ["## Excluded duplicate files", "", "These input files are not built; another file was kept.", ""]
        lines += ["| Excluded file | Kept file |", "|---|---|"]
        lines += [f"| `{e.path}` | `{e.kept}` |" for e in data.excluded]
        lines.append("")

    lines += ["## Skipped input files", ""]
    lines += [f"- `{p}`: {reason}" for p, reason in data.skipped]
    lines.append("")
    return "\n".join(lines)
