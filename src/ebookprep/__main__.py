"""About this file: command line of the ebookprep tool.

  python -m ebookprep identify --input DIR --work DIR [--config FILE] [--only PATH ...]
  python -m ebookprep detect-moves --input DIR --work DIR [--config FILE]
  python -m ebookprep relookup --input DIR --work DIR [--config FILE] [--only PATH ...]
  python -m ebookprep rescore --input DIR --work DIR
  python -m ebookprep build --input DIR --output DIR --work DIR [--config FILE] [--only PATH ...]
  python -m ebookprep report --work DIR
  python -m ebookprep review --input DIR --work DIR [--output DIR] [--config FILE] [--kavita-url URL]
                             [--host HOST] [--port PORT]
  python -m ebookprep kavita-check --output DIR --work DIR [--config FILE] [--url URL] [--scan]

"identify" decides every new book's metadata (local sources, online lookup checked
against the book's own text) and stores it as accepted or waiting for review.
"detect-moves" recognises known files that were moved or renamed in the input and carries
their books over (see moves.py), and checks the input for damaged downloads (see
damaged.py); identify and build do both first, too.
"relookup" asks the online sources again for the books waiting for review (e.g. after
sources were added) and re-decides them with old and new candidates.
"rescore" re-decides stored books with the current rules (no online requests); decisions
made by the user and calibre .opf files are kept.
"build" writes accepted books into the output library (see pipeline.py); its exit code
is 3 (FLAGGED_EXIT_CODE) when it ran through but some books were flagged (see the
report), 0 when everything was built; 1 means it crashed.
"report" writes WORK/report.md with the newest result of every book across all runs.
"review" starts the review web UI (default http://127.0.0.1:8000); its actions page runs
the other commands with the given paths.
"kavita-check" asks a running Kavita (login from the environment variables
KAVITA_USERNAME and KAVITA_PASSWORD) whether every built book is its own series named
after its title; --scan rescans all libraries first. The issues are stored (see the
Kavita page of the web UI). Exit code 3 (FLAGGED_EXIT_CODE) when problems are open.
The work folder holds the SQLite state, temporary files and the reports. PATH limits the
run to placements such as IT/Android.
"""

import argparse
import os
import sys
from pathlib import Path

from ebookprep.config import load_config
from ebookprep.identify import identify, relookup, rescore
from ebookprep.damaged import check_downloads
from ebookprep.jobs import FLAGGED_EXIT_CODE
from ebookprep.kavita import KavitaClient
from ebookprep.moves import detect_moves
from ebookprep.kavita_verify import expected_files, record_check, verify
from ebookprep.pipeline import build_accepted
from ebookprep.sources.registry import default_sources
from ebookprep.report import write_summary_report
from ebookprep.scan import scan_input
from ebookprep.state import StateStore


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="ebookprep", description="Prepares an ebook library for Kavita.")
    commands = parser.add_subparsers(dest="command", required=True)

    identify_cmd = commands.add_parser("identify", help="decide the metadata of new books")
    _add_paths(identify_cmd, output=False)
    moves_cmd = commands.add_parser("detect-moves", help="recognise known files that were moved in the input")
    moves_cmd.add_argument("--input", type=Path, required=True, help="input library (only read)")
    moves_cmd.add_argument("--work", type=Path, required=True, help="folder for state, temporary files and reports")
    moves_cmd.add_argument("--config", type=Path, default=Path("config.toml"), help="config file (default: config.toml)")
    relookup_cmd = commands.add_parser("relookup", help="look up the books waiting for review again")
    _add_paths(relookup_cmd, output=False)
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
    review_cmd.add_argument("--output", type=Path, default=None, help="output library (for the actions)")
    review_cmd.add_argument("--config", type=Path, default=Path("config.toml"), help="config file")
    review_cmd.add_argument("--kavita-url", default=os.environ.get("KAVITA_URL", "http://127.0.0.1:5000"),
                            help="Kavita URL (for the Kavita check action)")
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

        app = create_app(
            work_root=args.work,
            input_root=args.input,
            output_root=args.output,
            config_path=args.config,
            kavita_url=args.kavita_url,
        )
        uvicorn.run(app, host=args.host, port=args.port)
        return 0

    if args.command == "report":
        path = write_summary_report(StateStore(args.work / "state.db"), args.work / "report.md")
        print(f"report: {path}")
        return 0

    config = load_config(args.config)
    if args.command in ("detect-moves", "identify", "build"):
        store = StateStore(args.work / "state.db")
        detect_moves(args.input, store, config)
        scanned = [f.path.relative_to(args.input).as_posix() for u in scan_input(args.input, config).units for f in u.files]
        for path, damage in check_downloads(args.input, store, scanned).items():
            kind = "repairable" if damage.repairable else "not repairable"
            print(f"damaged download ({kind}): {path}: {damage.note}")
    if args.command == "detect-moves":
        return 0
    only = tuple(args.only)
    if args.command == "identify":
        identify(args.input, args.work, config, default_sources(config), only=only)
        return 0
    if args.command == "relookup":
        relookup(args.input, args.work, default_sources(config), only=only)
        return 0

    run_id = build_accepted(args.input, args.output, args.work, config, only=only)
    failed = [r for r in StateStore(args.work / "state.db").records(run_id) if r.flagged]
    return FLAGGED_EXIT_CODE if failed else 0


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
    store = StateStore(args.work / "state.db")
    expected = expected_files(store, args.output, mislabeled_folder=mislabeled)
    result = verify(kavita, expected, library_root=args.library_root)
    open_problems = record_check(store, expected, result)

    print(f"Kavita shows {result.series} series; {result.checked_files} built files checked")
    for name, files in result.merged:
        print(f"merged: series {name!r} contains files of several books: {files}")
    for file, wanted, actual in result.wrong_name:
        print(f"wrong name: {file} is in series {actual!r}, expected {wanted!r}")
    for file in result.missing:
        print(f"missing: {file}")
    for file, wanted, actual in result.spacing:
        print(f"spaces differ: {file} is in series {actual!r}, expected {wanted!r}")
    for file, wanted, actual in result.outdated_name:
        print(f"note: Kavita shows the older spelling {actual!r} instead of {wanted!r} for {file}")
    for file in result.unexpected:
        print(f"unexpected: {file}")
    if open_problems:
        print(f"{open_problems} open problems: see the Kavita page of the web UI to fix or accept them")
        return FLAGGED_EXIT_CODE
    print("OK: every built book is its own series named after its title (accepted problems aside)")
    return 0


def _add_paths(parser: argparse.ArgumentParser, output: bool) -> None:
    parser.add_argument("--input", type=Path, required=True, help="input library (only read)")
    if output:
        parser.add_argument("--output", type=Path, required=True, help="output library")
    parser.add_argument("--work", type=Path, required=True, help="folder for state, temporary files and reports")
    parser.add_argument("--config", type=Path, default=Path("config.toml"), help="config file (default: config.toml)")
    parser.add_argument("--only", nargs="+", default=(), metavar="PATH", help="only these placements, e.g. IT/Android")


if __name__ == "__main__":
    sys.exit(main())
