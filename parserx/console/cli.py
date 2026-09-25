"""``parserx parse`` with pipeline v2 (plan P4-1, P4-2): inputs, the hybrid runtime, the console, the exit code.

Progress goes to stderr (``ConsoleReporter``); stdout carries only what was asked for: the Markdown
(``--stdout``) or the result summary (``--json``).  Exit code 0 when every document was written (complete or
partial), 1 when one failed, 130 on Ctrl-C.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
import tempfile
import time
import traceback
from pathlib import Path

from parserx.config.schema import ConfigLoadResult, ParserXConfig
from parserx.console.messages import t
from parserx.console.reporter import ConsoleReporter
from parserx.runtimes.events import Notice

SUFFIXES = (".pdf", ".docx", ".doc")


def expand_inputs(paths: list[Path]) -> list[Path]:
    """Files as given; a directory contributes its PDF, DOCX and DOC files (not its subdirectories), sorted."""
    found: list[Path] = []
    for path in paths:
        if path.is_dir():
            found += sorted(p for p in path.iterdir() if p.is_file() and p.suffix.lower() in SUFFIXES
                            and not p.name.startswith((".", "~$")))
        else:
            found.append(path)
    return found


def parse_v2(args: argparse.Namespace, config: ParserXConfig, loaded: ConfigLoadResult) -> int:
    from parserx.runtimes.hybrid import WORK_DIR, ParseFailure, parse_document

    _quiet_logging(args.verbose)
    files = expand_inputs(args.input)
    several = len(files) > 1
    reporter = ConsoleReporter(sys.stderr, lang=args.lang, quiet=args.quiet, verbose=args.verbose,
                               compact=several, tty=False if args.verbose else None)
    if not files:
        reporter.line(t(args.lang, "no_inputs", paths=" ".join(str(p) for p in args.input)))
        return 2
    if loaded.source in ("defaults", "missing"):
        reporter(Notice("config_defaults", "warning"))
    outcomes, results, failed = [], [], 0
    started = time.monotonic()
    try:
        for i, path in enumerate(files, 1):
            reporter.start_document(i, len(files))
            if args.stdout:
                out_dir = Path(tempfile.mkdtemp(prefix="parserx-stdout-"))
            elif several:
                out_dir = (args.output or Path("output")) / path.stem
            else:
                out_dir = args.output or Path("output") / path.stem
            try:
                outcome = parse_document(path, out_dir, config, reporter=reporter, keep_work=args.keep_work)
            except KeyboardInterrupt:
                reporter.interrupted(out_dir / WORK_DIR)
                return 130
            except ParseFailure as exc:
                failed += 1
                reporter.failure(path.name, exc.code, str(exc))
                results.append({"source": path.name, "error": {"code": exc.code, "message": str(exc)}})
                continue
            except Exception as exc:  # noqa: BLE001 - reported per document; -v shows the traceback
                failed += 1
                reporter.failure(path.name, "other", f"{type(exc).__name__}: {exc}")
                if args.verbose:
                    traceback.print_exc()
                results.append({"source": path.name, "error": {"code": "internal", "message": str(exc)}})
                continue
            outcomes.append(outcome)
            results.append(outcome.model_dump(mode="json"))
            if args.stdout:
                sys.stdout.write(Path(outcome.markdown).read_text(encoding="utf-8"))
    finally:
        reporter.close()
    if several:
        reporter.total(outcomes, failed, time.monotonic() - started)
    if args.json:
        sys.stdout.write(json.dumps(results if several else results[0], ensure_ascii=False, indent=2) + "\n")
    return 1 if failed else 0


def _quiet_logging(verbose: bool) -> None:
    """Library logs are for ``-v``; otherwise the console shows events only (no stack traces)."""
    root = logging.getLogger()
    if verbose:
        root.setLevel(logging.DEBUG)
        return
    logging.disable(logging.CRITICAL)
