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

import json
import logging
import sys
from pathlib import Path
from typing import Any

from parserx.config.schema import load_config

AGENT_TOOLS = ("read_draft", "view_source", "edit_draft", "submit_draft")  # what the agent is given (Q85)
TOOL_NAMES = (*AGENT_TOOLS, "run_pipeline")  # run_pipeline makes the first draft; the program runs it


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


def _read_draft_opts(p):
    p.add_argument("--view", choices=("summary", "issues", "text", "outline", "blocks"), default="summary")
    p.add_argument("--kinds", help="issues: comma-separated kinds")
    p.add_argument("--page", type=int, help="issues or text: one page")
    p.add_argument("--from", dest="start", help="text: read from this block id (default: the beginning)")
    p.add_argument("--after", type=int, default=40, help="text: this block and the ones after it")
    p.add_argument("--before", type=int, default=0, help="text: blocks before --from")
    p.add_argument("--find", help="text: blocks containing this phrase (spacing and case ignored)")
    p.add_argument("--pattern", help="text: blocks matching this regular expression")
    p.add_argument("--cls", help="text: every block of this style class (ids from the outline)")
    p.add_argument("--full", action="store_true", help="text: whole paragraphs instead of their start")
    p.add_argument("--blocks", help="blocks: comma-separated block ids")
    p.add_argument("--sources", action="store_true", help="blocks: each engine's reading of them")


def _view_source_opts(p):
    p.add_argument("--block")
    p.add_argument("--page", type=int)
    p.add_argument("--seam", type=int, help="page N's bottom half above page N+1's top half")
    p.add_argument("--bbox", type=float, nargs=4, metavar=("X0", "Y0", "X1", "Y1"), help="with --page: a region")
    p.add_argument("--rows", type=int, nargs=2, metavar=("FIRST", "LAST"), help="with a table --block: these rows")
    p.add_argument("--as", dest="as_", choices=("image", "answer", "text", "table", "description"), default="image")
    p.add_argument("--question", help="as answer: what to ask")
    p.add_argument("--looks", help="JSON file (or -) with a list of looks: several in one call")


def _edit_draft_opts(p):
    p.add_argument("--ops", help="JSON file (or -) with the list of operations")
    p.add_argument("--atomic", action="store_true", help="any refused operation: none applied")


def _submit_draft_opts(p):
    p.add_argument("--out", type=Path, help="export to this directory when accepted")
    p.add_argument("--name")


_TOOL_OPTIONS = {
    "read_draft": _read_draft_opts, "view_source": _view_source_opts, "edit_draft": _edit_draft_opts,
    "submit_draft": _submit_draft_opts, "run_pipeline": lambda p: None,
}


def _load_json(value: str) -> Any:
    text = sys.stdin.read() if value == "-" else Path(value).read_text(encoding="utf-8")
    if value == "-" and not text.strip():
        raise ValueError("standard input is empty: pipe the JSON into the command (e.g. a heredoc) or give a file")
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
    if name == "read_draft":
        request = dict(view=args.view, kinds=args.kinds.split(",") if args.kinds else None, page=args.page,
                       start=args.start, after=args.after, before=args.before, find=args.find, pattern=args.pattern,
                       cls=args.cls, full=args.full, blocks=args.blocks.split(",") if args.blocks else None,
                       sources=args.sources)
        return {k: v for k, v in request.items() if v is not None}
    if name == "view_source":
        if args.looks:
            return {"looks": _load_json(args.looks)}
        one = dict(block=args.block, page=args.page, seam=args.seam, bbox=args.bbox, rows=args.rows,
                   question=args.question)
        return {"looks": [{"as": args.as_, **{k: v for k, v in one.items() if v is not None}}]}
    if name == "edit_draft":
        return {"ops": _load_json(args.ops) if args.ops else [], "atomic": args.atomic}
    if name == "submit_draft":
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
