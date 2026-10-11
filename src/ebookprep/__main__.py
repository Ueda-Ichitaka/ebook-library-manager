"""About this file: command line of the ebookprep tool.

  python -m ebookprep identify --input DIR --work DIR [--config FILE] [--only PATH ...]
  python -m ebookprep rescore --input DIR --work DIR
  python -m ebookprep build --input DIR --output DIR --work DIR [--config FILE] [--only PATH ...]
  python -m ebookprep report --work DIR
  python -m ebookprep review --input DIR --work DIR [--host HOST] [--port PORT]
  python -m ebookprep kavita-check --output DIR --work DIR [--config FILE] [--url URL] [--scan]

"identify" decides every new book's metadata (local sources, online lookup checked
against the book's own text) and stores it as accepted or waiting for review.
"rescore" re-decides stored books with the current rules (no online requests); decisions
made by the user and calibre .opf files are kept.
"build" writes accepted books into the output library (see pipeline.py); its exit code
is 1 when some books were skipped and flagged (see the report), else 0.
"report" writes WORK/report.md with the newest result of every book across all runs.
"review" starts the review web UI (default http://127.0.0.1:8000).
"kavita-check" asks a running Kavita (login from the environment variables
KAVITA_USERNAME and KAVITA_PASSWORD) whether every built book is its own series named
after its title; --scan rescans all libraries first. Exit code 1 on problems.
The work folder holds the SQLite state, temporary files and the reports. PATH limits the
run to placements such as IT/Android.
"""

import argparse
import os
import sys
from pathlib import Path

from ebookprep.config import load_config
from ebookprep.identify import identify, rescore
from ebookprep.kavita import KavitaClient
from ebookprep.kavita_verify import expected_files, verify
from ebookprep.pipeline import build_accepted
from ebookprep.sources.registry import default_sources
from ebookprep.state import StateStore, write_summary_report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="ebookprep", description="Prepares an ebook library for Kavita.")
    commands = parser.add_subparsers(dest="command", required=True)

    identify_cmd = commands.add_parser("identify", help="decide the metadata of new books")
    _add_paths(identify_cmd, output=False)
    rescore_cmd = commands.add_parser("rescore", help="re-decide stored books with the current rules")
    rescore_cmd.add_argument("--input", type=Path, required=True, help="input library (only read)")
    rescore_cmd.add_argument("--work", type=Path, required=True, help="work folder of the pipeline")
    build_cmd = commands.add_parser("build", help="write accepted books into the output library")
    _add_paths(build_cmd, output=True)
    report_cmd = commands.add_parser("report", help="write a report of all runs to WORK/report.md")
    report_cmd.add_argument("--work", type=Path, required=True, help="work folder of the pipeline")
    review_cmd = commands.add_parser("review", help="start the review web UI")
    review_cmd.add_argument("--input", type=Path, required=True, help="input library (only read)")
    review_cmd.add_argument("--work", type=Path, required=True, help="work folder of the pipeline")
    review_cmd.add_argument("--host", default="127.0.0.1", help="address to listen on (default: 127.0.0.1)")
    review_cmd.add_argument("--port", type=int, default=8000, help="port (default: 8000)")
    check_cmd = commands.add_parser("kavita-check", help="check the series a running Kavita shows")
    check_cmd.add_argument("--output", type=Path, required=True, help="output library")
    check_cmd.add_argument("--work", type=Path, required=True, help="work folder of the pipeline")
    check_cmd.add_argument("--config", type=Path, default=Path("config.toml"), help="config file")
    check_cmd.add_argument("--url", default=os.environ.get("KAVITA_URL", "http://127.0.0.1:5000"), help="Kavita URL")
    check_cmd.add_argument("--library-root", default="/library", help="where Kavita sees the output folder")
    check_cmd.add_argument("--scan", action="store_true", help="rescan all libraries first")
    args = parser.parse_args(argv)

    if args.command == "kavita-check":
        return _kavita_check(args)

    if args.command == "rescore":
        rescore(args.input, args.work)
        return 0

    if args.command == "review":
        import uvicorn

        from ebookprep.web import create_app

        uvicorn.run(create_app(work_root=args.work, input_root=args.input), host=args.host, port=args.port)
        return 0

    if args.command == "report":
        path = write_summary_report(StateStore(args.work / "state.db"), args.work / "report.md")
        print(f"report: {path}")
        return 0

    config = load_config(args.config)
    only = tuple(args.only)
    if args.command == "identify":
        identify(args.input, args.work, config, default_sources(config), only=only)
        return 0

    run_id = build_accepted(args.input, args.output, args.work, config, only=only)
    failed = [r for r in StateStore(args.work / "state.db").records(run_id) if r.flagged]
    return 1 if failed else 0


def connect_kavita(url: str, username: str, password: str) -> KavitaClient:
    client = KavitaClient(url)
    client.login(username, password)
    return client


def _kavita_check(args: argparse.Namespace) -> int:
    username, password = os.environ.get("KAVITA_USERNAME", ""), os.environ.get("KAVITA_PASSWORD", "")
    if not username or not password:
        print("KAVITA_USERNAME and KAVITA_PASSWORD must be set (see kavita.env)", file=sys.stderr)
        return 2
    config = load_config(args.config) if args.config.exists() else None
    mislabeled = config.mislabeled_folder if config else "_mislabeled"

    kavita = connect_kavita(args.url, username, password)
    if args.scan:
        print("scanning all Kavita libraries ...")
        kavita.scan_all_and_wait()
    expected = expected_files(StateStore(args.work / "state.db"), args.output, mislabeled_folder=mislabeled)
    result = verify(kavita, expected, library_root=args.library_root)

    print(f"Kavita shows {result.series} series; {result.checked_files} built files checked")
    for name, files in result.merged:
        print(f"merged: series {name!r} contains files of several books: {files}")
    for file, wanted, actual in result.wrong_name:
        print(f"wrong name: {file} is in series {actual!r}, expected {wanted!r}")
    for file in result.missing:
        print(f"missing: {file}")
    for file, wanted, actual in result.outdated_name:
        print(f"note: Kavita shows the older spelling {actual!r} instead of {wanted!r} for {file}")
    for file in result.unexpected:
        print(f"unexpected: {file}")
    print("OK: every built book is its own series named after its title" if result.ok else "PROBLEMS found")
    return 0 if result.ok else 1


def _add_paths(parser: argparse.ArgumentParser, output: bool) -> None:
    parser.add_argument("--input", type=Path, required=True, help="input library (only read)")
    if output:
        parser.add_argument("--output", type=Path, required=True, help="output library")
    parser.add_argument("--work", type=Path, required=True, help="folder for state, temporary files and reports")
    parser.add_argument("--config", type=Path, default=Path("config.toml"), help="config file (default: config.toml)")
    parser.add_argument("--only", nargs="+", default=(), metavar="PATH", help="only these placements, e.g. IT/Android")


if __name__ == "__main__":
    sys.exit(main())
