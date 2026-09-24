"""JSON CLI (guide §5.3, interfaces §5.1).

    parserx workspace init <input> --ws DIR [--config C] --json
    parserx tool <name> --ws DIR [tool options | --request FILE|-] [--expect-version N] [--config C] --json
    parserx tool schema <name>

stdout carries exactly one JSON document (the envelope, or the schema); logs
go to stderr.  Exit code 0: an envelope was returned (see ``ok`` and
``failures``); 2: the request was invalid (an envelope with
``invalid_request`` is still printed); 1: internal error.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path
from typing import Any

from parserx.config.schema import load_config

TOOL_NAMES = ("overview", "read", "recognize", "review_table", "describe_figure", "apply_structure", "check",
              "export")


def add_parsers(sub) -> None:
    ws_cmd = sub.add_parser("workspace", help="Document workspace (v2 toolkit)")
    ws_sub = ws_cmd.add_subparsers(dest="ws_command", required=True)
    init = ws_sub.add_parser("init", help="Extract a document into a new workspace")
    init.add_argument("input", type=Path)
    _common(init)

    tool_cmd = sub.add_parser("tool", help="Run a document tool (v2 toolkit), JSON in and out")
    tool_sub = tool_cmd.add_subparsers(dest="tool_name", required=True)
    schema = tool_sub.add_parser("schema", help="JSON Schema of a tool's request and envelope")
    schema.add_argument("name", choices=(*TOOL_NAMES, "workspace_init"))
    for name in TOOL_NAMES:
        p = tool_sub.add_parser(name)
        _common(p)
        p.add_argument("--request", help="Full request as JSON (file path or - for stdin); overrides options")
        p.add_argument("--expect-version", type=int, help="Refuse with version_conflict if the workspace moved on")
        _TOOL_OPTIONS[name](p)


def _common(p) -> None:
    p.add_argument("--ws", type=Path, required=True, help="Workspace directory")
    p.add_argument("-c", "--config", type=Path, help="Config YAML")
    p.add_argument("--json", action="store_true", help="JSON output (always on)")


def _read_opts(p):
    p.add_argument("--page", type=int)
    p.add_argument("--block")
    p.add_argument("--image", choices=("none", "page", "crop"), default="none")
    p.add_argument("--context", type=int, default=0)
    p.add_argument("--dpi", type=int)
    p.add_argument("--pad-pt", type=float)
    p.add_argument("--observations", action="store_true")


def _recognize_opts(p):
    p.add_argument("--pages", help="e.g. 1,3-5")
    p.add_argument("--blocks", help="comma-separated block ids")
    p.add_argument("--regions", help="JSON file with a list of RegionRef")
    p.add_argument("--engine", choices=("paddleocr", "vlm", "native", "layout"), required=False)
    p.add_argument("--force", action="store_true")


def _review_opts(p):
    p.add_argument("--block")
    p.add_argument("--issues", help="JSON file (or -) with a list of TableIssue")
    p.add_argument("--context", choices=("table", "table+caption", "page"), default="table")


def _describe_opts(p):
    p.add_argument("--block")
    p.add_argument("--schema", choices=("auto", "chart", "diagram", "photo", "seal", "other"), default="auto")


def _structure_opts(p):
    p.add_argument("--changes", help="JSON file (or -) with a list of structure changes")
    p.add_argument("--atomic", action="store_true")
    p.add_argument("--actor", default="agent")


def _export_opts(p):
    p.add_argument("--out", type=Path)
    p.add_argument("--name")


_TOOL_OPTIONS = {
    "overview": lambda p: None, "read": _read_opts, "recognize": _recognize_opts, "review_table": _review_opts,
    "describe_figure": _describe_opts, "apply_structure": _structure_opts, "check": lambda p: None,
    "export": _export_opts,
}


def _load_json(value: str) -> Any:
    text = sys.stdin.read() if value == "-" else Path(value).read_text(encoding="utf-8")
    return json.loads(text)


def _pages(spec: str) -> list[int]:
    pages: list[int] = []
    for part in spec.split(","):
        if "-" in part:
            a, b = part.split("-", 1)
            pages.extend(range(int(a), int(b) + 1))
        elif part.strip():
            pages.append(int(part))
    return pages


def _request(name: str, args) -> dict[str, Any]:
    if args.request:
        return _load_json(args.request)
    if name == "read":
        return {k: v for k, v in dict(page=args.page, block=args.block, image=args.image, context=args.context,
                                      dpi=args.dpi, pad_pt=args.pad_pt, observations=args.observations).items()
                if v is not None}
    if name == "recognize":
        return {"pages": _pages(args.pages) if args.pages else [],
                "blocks": args.blocks.split(",") if args.blocks else [],
                "regions": _load_json(args.regions) if args.regions else [],
                "engine": args.engine, "force": args.force}
    if name == "review_table":
        return {"block": args.block, "issues": _load_json(args.issues) if args.issues else [],
                "context": args.context}
    if name == "describe_figure":
        return {"block": args.block, "schema": args.schema}
    if name == "apply_structure":
        return {"changes": _load_json(args.changes) if args.changes else [], "atomic": args.atomic,
                "actor": args.actor}
    if name == "export":
        return {"out": str(args.out) if args.out else None, "name": args.name}
    return {}


def main(args) -> int:
    # stdout is reserved for the JSON document; everything else goes to stderr.
    logging.basicConfig(level=logging.WARNING, stream=sys.stderr, format="%(levelname)s: %(message)s", force=True)
    from parserx.tools import call_tool, tool_schema, workspace_init

    if args.command == "tool" and args.tool_name == "schema":
        print(json.dumps(tool_schema(args.name), ensure_ascii=False))
        return 0
    config = load_config(args.config)
    if args.command == "workspace":
        envelope, code = workspace_init(args.input, args.ws, config=config)
    else:
        try:
            request = _request(args.tool_name, args)
        except (OSError, ValueError) as exc:
            from parserx.tools.envelope import Envelope, Failure, FailureCode

            envelope = Envelope(tool=args.tool_name, doc="", ws_version=0, ok=False, failures=[
                Failure(code=FailureCode.INVALID_REQUEST, message=f"request not readable: {exc}", retryable=False)])
            print(envelope.model_dump_json())
            return 2
        envelope, code = call_tool(args.tool_name, args.ws, request, config=config,
                                   expect_version=args.expect_version)
    print(envelope.model_dump_json(by_alias=True))
    return code
