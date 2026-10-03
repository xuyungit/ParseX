"""``parserx parse`` with pipeline v2 (plan P4-1, P4-2): inputs, the hybrid runtime, the console, the exit code.

Progress goes to stderr (``ConsoleReporter``); stdout carries only what was asked for: the Markdown
(``--stdout``) or the result summary (``--json``).  Exit code 0 when every document was written (complete or
partial), 1 when one failed, 130 on Ctrl-C.
"""

from __future__ import annotations

import argparse
import json
import shutil
import logging
import signal
import sys
import tempfile
import time
import traceback
from dataclasses import dataclass, field
from pathlib import Path

from parserx.config.schema import ConfigLoadResult, ParserXConfig
from parserx.content.convert import IMAGE_SUFFIXES
from parserx.content.fetch import FetchError, fetch, is_address
from parserx.console.messages import t
from parserx.console.reporter import ConsoleReporter
from parserx.runtimes.events import Notice

SUFFIXES = (".pdf", ".docx", ".doc", *IMAGE_SUFFIXES)


@dataclass
class Input:
    """One document to parse: a local file, or a web address fetched when its turn comes (Q119)."""

    given: str  # as the user wrote it
    path: Path | None  # the local file; None for an address until it is fetched
    place: Path = field(default_factory=Path)  # where under the output root (a subdirectory with -r)


def expand_inputs(items: list[str], recursive: bool = False) -> list[Input]:
    """Files as given; a directory contributes its PDF, Word and image files — with ``recursive`` those of its
    subdirectories too, placed under the same relative path in the output; an address is kept to be fetched."""
    found: list[Input] = []
    for item in map(str, items):
        if is_address(item):
            found.append(Input(item, None))
            continue
        path = Path(item)
        if not path.is_dir():
            found.append(Input(item, path))
            continue
        candidates = path.rglob("*") if recursive else path.iterdir()
        for file in sorted(candidates):
            rel = file.relative_to(path)
            if file.is_file() and file.suffix.lower() in SUFFIXES and not any(
                    part.startswith((".", "~$")) for part in rel.parts):
                found.append(Input(str(file), file, rel.parent))
    return found


def output_dir(entry: Input, name: str, args: argparse.Namespace, several: bool, taken: set[Path]) -> Path:
    """Where a document's output goes: ``-o`` itself for a single input, else ``<-o or output>/<place>/<name>``;
    a name already used in this run gets ``-2``, ``-3`` … (Q122)."""
    if not several and args.output is not None:
        return args.output
    base = (args.output or Path("output")) / entry.place
    candidate, n = base / name, 1
    while candidate in taken:
        n += 1
        candidate = base / f"{name}-{n}"
    taken.add(candidate)
    return candidate


def preflight(config: ParserXConfig) -> list[Notice]:
    """Warnings for roles that cannot work as configured: no token for the scan engine, no key for the service model
    or for the loop agent's model.  A role switched off on purpose (--no-ocr, --no-vlm, --no-agent) is not warned."""
    from parserx.services.ocr import scan_engine_configured

    out = []
    ocr, vlm, agent = config.builders.ocr, config.services.vlm, config.runtime.agent
    if ocr.engine != "none" and not scan_engine_configured(config):
        out.append(Notice("preflight_ocr", "warning"))
    if vlm.endpoint and not vlm.api_key:
        out.append(Notice("preflight_vlm", "warning", {"model": vlm.model}))
    if config.runtime.mode == "hybrid" and agent.engine == "loop" and agent.endpoint and not agent.api_key:
        out.append(Notice("preflight_loop", "warning", {"model": agent.model}))
    return out


def parse_v2(args: argparse.Namespace, config: ParserXConfig, loaded: ConfigLoadResult) -> int:
    from parserx.runtimes.hybrid import WORK_DIR, ParseFailure, parse_document

    _quiet_logging(args.verbose)
    # A termination request stops like Ctrl-C: the agent (in its own session) is stopped, the work is kept.
    signal.signal(signal.SIGTERM, _interrupt)
    files = expand_inputs(args.input, recursive=getattr(args, "recursive", False))
    several = len(files) > 1
    reporter = ConsoleReporter(sys.stderr, lang=args.lang, quiet=args.quiet, verbose=args.verbose,
                               compact=several, tty=False if args.verbose else None)
    if not files:
        reporter.line(t(args.lang, "no_inputs", paths=" ".join(str(p) for p in args.input)))
        return 2
    from parserx.config.schema import personal_config

    if not personal_config().is_file() and loaded.source != "explicit":
        reporter(Notice("config_defaults", "warning"))
    for notice in preflight(config):  # what cannot work, said before the first document (R4)
        reporter(notice)
    if config.runtime.layout_shadow:  # the layout model, fetched before the first document when missing (R5)
        from parserx.check import fetch_layout_model
        from parserx.layout.detector import model_file

        if not model_file(config.layout).is_file():
            fetch_layout_model(config, lang=args.lang)
    outcomes, results, failed = [], [], 0
    started = time.monotonic()
    taken: set[Path] = set()
    downloads = Path(tempfile.mkdtemp(prefix="parserx-fetch-"))
    try:
        for i, entry in enumerate(files, 1):
            reporter.start_document(i, len(files))
            path = entry.path
            if path is None:  # an address: fetched now, a failure is this document's
                try:
                    path = fetch(entry.given, downloads / str(i), max_mb=config.input.max_download_mb,
                                 timeout_s=config.input.download_timeout_s)
                except FetchError as exc:
                    failed += 1
                    reporter.failure(entry.given, "unreadable", str(exc))
                    results.append({"source": entry.given, "error": {"code": "unreadable", "message": str(exc)}})
                    continue
            if args.stdout:
                out_dir = Path(tempfile.mkdtemp(prefix="parserx-stdout-"))
            else:
                out_dir = output_dir(entry, path.stem, args, several, taken)
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
        shutil.rmtree(downloads, ignore_errors=True)
    if several:
        reporter.total(outcomes, failed, time.monotonic() - started)
    if args.json:
        sys.stdout.write(json.dumps(results if several else results[0], ensure_ascii=False, indent=2) + "\n")
    return 1 if failed else 0


def _interrupt(signum, frame):
    raise KeyboardInterrupt


def _quiet_logging(verbose: bool) -> None:
    """Library logs are for ``-v``; otherwise the console shows events only (no stack traces)."""
    root = logging.getLogger()
    if verbose:
        root.setLevel(logging.DEBUG)
        return
    logging.disable(logging.CRITICAL)
