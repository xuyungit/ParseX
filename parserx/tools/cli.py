"""JSON CLI (guide §5.3, Q86).

    parserx workspace init <input> --ws DIR [--config C] --json
    parserx tool <name> --ws DIR [--<field> VALUE … | --request FILE|-] [--expect-version N] [--config C] --json
    parserx tool schema <name>

A tool's options are its request's fields, named as in the JSON request — the request model is the contract (Q86):
a number or a word as it is, a list of words comma-separated, a pair or a box as that many numbers, a flag for
true, a list of objects as JSON (a file, or ``-`` for standard input).  A request made of one kind of object
(``view_source``'s looks) also takes that object's fields as options, for one of them.  ``--request`` gives the
whole request as JSON.

stdout carries exactly one JSON document (the envelope, or the schema); logs go to stderr.  Exit code 0: an
envelope was returned (see ``ok`` and ``failures``); 2: the request was invalid (an envelope with
``invalid_request`` is still printed); 1: internal error.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
import types
import typing
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel

from parserx.config.schema import load_config

AGENT_TOOLS = ("read_draft", "view_source", "edit_draft", "submit_draft")  # what the agent is given (Q85)
TOOL_NAMES = (*AGENT_TOOLS, "run_pipeline")  # run_pipeline makes the first draft; the program runs it


def add_parsers(sub) -> None:
    from parserx.tools import TOOLS

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
        spec = TOOLS[name]
        p = tool_sub.add_parser(name, help=spec.description or None, description=spec.description or None)
        _common(p)
        p.add_argument("--request", help="the whole request as JSON (a file, or - for standard input)")
        p.add_argument("--expect-version", type=int, help="refuse with version_conflict if the workspace moved on")
        for field, option in options(spec.request):
            _add(p, field, option)


def _common(p) -> None:
    p.add_argument("--ws", type=Path, required=True, help="Workspace directory")
    p.add_argument("-c", "--config", type=Path, help="Config YAML")
    p.add_argument("--json", action="store_true", help="JSON output (always on)")


# ── options from the request model ──────────────────────────────────────


class Option(typing.NamedTuple):
    name: str  # as in the JSON request
    shape: Literal["value", "words", "numbers", "flag", "json"]
    type: Any  # the value's Python type (value, numbers)
    count: int  # numbers: how many
    item: bool  # a field of the one object a request's list is made of
    help: str | None


def options(model: type[BaseModel], *, item: bool = False) -> list[tuple[str, Option]]:
    """(dest, option) per field of *model*; a list of one kind of object also offers that object's fields."""
    out = []
    for field_name, field in model.model_fields.items():
        name = field.alias or field_name
        annotation = _bare(field.annotation)
        origin, args = typing.get_origin(annotation), typing.get_args(annotation)
        if annotation is bool:
            option = Option(name, "flag", bool, 0, item, field.description)
        elif origin is tuple:
            option = Option(name, "numbers", _bare(args[0]), len(args), item, field.description)
        elif origin is list and _scalar(_bare(args[0])):
            option = Option(name, "words", _bare(args[0]), 0, item, field.description)
        elif origin is list or _is_model(annotation):
            option = Option(name, "json", None, 0, item, field.description)
            one = _bare(args[0]) if origin is list else None
            if not item and _is_model(one):
                out += options(one, item=True)
        else:
            option = Option(name, "value", annotation, 0, item, field.description)
        out.append((field_name, option))
    return out


def _bare(annotation):
    """X from X | None (and from Annotated[X, …])."""
    if typing.get_origin(annotation) is typing.Annotated:
        annotation = typing.get_args(annotation)[0]
    if typing.get_origin(annotation) in (typing.Union, types.UnionType):
        args = [a for a in typing.get_args(annotation) if a is not type(None)]
        if len(args) == 1:
            return _bare(args[0])
    return annotation


def _is_model(annotation) -> bool:
    return isinstance(annotation, type) and issubclass(annotation, BaseModel)


def _scalar(annotation) -> bool:
    return annotation in (str, int, float) or typing.get_origin(annotation) is Literal or (
        isinstance(annotation, type) and issubclass(annotation, str))


def _add(p, dest: str, option: Option) -> None:
    flag, dest, meta = "--" + option.name, ("item." if option.item else "") + dest, option.name.upper()
    if option.shape == "flag":
        p.add_argument(flag, dest=dest, action="store_true", default=None, help=option.help)
    elif option.shape == "numbers":
        p.add_argument(flag, dest=dest, type=option.type, nargs=option.count, metavar=meta, help=option.help)
    elif option.shape == "value" and typing.get_origin(option.type) is Literal:
        p.add_argument(flag, dest=dest, choices=typing.get_args(option.type), help=option.help)
    elif option.shape == "value":
        p.add_argument(flag, dest=dest, type=option.type if option.type in (int, float) else str, metavar=meta,
                       help=option.help)
    else:  # words, json
        p.add_argument(flag, dest=dest, metavar=meta, help=option.help)


def _request(name: str, args) -> dict[str, Any]:
    from parserx.tools import TOOLS

    if args.request:
        return _load_json(args.request)
    request: dict[str, Any] = {}
    item: dict[str, Any] = {}
    listed = None
    for dest, option in options(TOOLS[name].request):
        value = getattr(args, ("item." if option.item else "") + dest, None)
        if option.shape == "json" and not option.item:
            listed = option.name
        if value is None:
            continue
        if option.shape == "words":
            value = [v.strip() for v in value.split(",") if v.strip()]
        elif option.shape == "json":
            value = _load_json(value)
        (item if option.item else request)[option.name] = value
    if item:
        if listed in request:
            raise ValueError(f"give --{listed} or the options of one item, not both")
        request[listed] = [item]
    return request


def _load_json(value: str) -> Any:
    text = sys.stdin.read() if value == "-" else Path(value).read_text(encoding="utf-8")
    if value == "-" and not text.strip():
        raise ValueError("standard input is empty: pipe the JSON into the command (e.g. a heredoc) or give a file")
    return json.loads(text)


def main(args: argparse.Namespace) -> int:
    # stdout is reserved for the JSON document; everything else goes to stderr.
    logging.basicConfig(level=logging.WARNING, stream=sys.stderr, format="%(levelname)s: %(message)s", force=True)
    from parserx.tools import agent_json, call_tool, tool_schema, workspace_init

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
            print(agent_json(envelope))
            return 2
        envelope, code = call_tool(args.tool_name, args.ws, request, config=config,
                                   expect_version=args.expect_version)
    print(agent_json(envelope))
    return code
