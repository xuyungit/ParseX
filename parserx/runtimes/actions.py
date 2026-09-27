"""What the agent did, from the tools' call records (plan P4-1, P4-2).

Every tool call appends a structured record to ``calls.jsonl`` when it ends (request, envelope, result).  The
hybrid runtime follows that file while the agent works: ``actions`` turns one record into the user's terms (look,
edit, add, set a title, close …) for the console, ``AgentTally`` counts them for the document summary.  Reading the
draft and submitting it are not actions: they change nothing.
"""

from __future__ import annotations

import json
import re
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from parserx.runtimes.events import AgentAction

TEXT_MAX = 40
_BLOCK_PAGE = re.compile(r"^b-p(\d+)-")

Lookup = Callable[[str], str | None]  # block id → its text (None when unknown)


def _short(text: str | None) -> str | None:
    if not text:
        return None
    text = " ".join(text.split())
    return text if len(text) <= TEXT_MAX else text[:TEXT_MAX - 1] + "…"


def _page_of(target: str | None) -> int | None:
    if not target:
        return None
    if target.startswith("p") and target[1:].isdigit():
        return int(target[1:])
    m = _BLOCK_PAGE.match(target)
    return int(m.group(1)) if m else None


def _look(block: str | None, page: int | None, seam: int | None, question: str | None,
          text_of: Lookup) -> AgentAction:
    target = block or (f"p{page}" if page is not None else f"p{seam}" if seam is not None else None)
    where = page if page is not None else seam if seam is not None else _page_of(block)
    # a block without a page (DOCX) is named by its text
    text = _short(text_of(block)) if block and where is None else None
    return AgentAction("look", target=target, page=where, text=text, detail=_short(question))


GROUP_MIN = 4  # this many same actions from one call are shown as one line with a count
GROUPED = frozenset({"join", "set_role", "exclude", "include", "move", "split"})  # titles, edits stay listed


def actions(record: dict, text_of: Lookup = lambda _: None) -> list[AgentAction]:
    """The record's actions; runs of one kind from one call (joining many lines of a page) as one with a count."""
    out = _actions(record, text_of)
    grouped: list[AgentAction] = []
    i = 0
    while i < len(out):
        j = i
        while j < len(out) and out[j].action == out[i].action:
            j += 1
        run = out[i:j]
        if len(run) >= GROUP_MIN and run[0].action in GROUPED:
            pages = sorted({a.page for a in run if a.page is not None})
            grouped.append(AgentAction(run[0].action, page=pages[0] if len(pages) == 1 else None,
                                       target=None if len(pages) == 1 else ",".join(f"p{p}" for p in pages) or None,
                                       count=len(run)))
        else:
            grouped += run
        i = j
    return grouped


def _actions(record: dict, text_of: Lookup) -> list[AgentAction]:
    if record.get("type") != "call":
        return []
    tool, req = record.get("tool"), record.get("request") or {}
    result = record.get("result") or {}
    if tool == "view_source":
        return [_look(one.get("block"), one.get("page"), one.get("seam"), one.get("question"), text_of)
                for one in req.get("looks") or []]
    if tool != "edit_draft" or not (record.get("envelope") or {}).get("ok", False):
        return []
    ops = req.get("ops") or []
    out: list[AgentAction | None] = []
    refused = 0
    for outcome in result.get("outcomes") or []:
        op = ops[outcome["index"]] if outcome.get("index", -1) < len(ops) else {}
        if not outcome.get("accepted"):
            refused += 1
            continue
        out.append(_edit(op, outcome, text_of))
    if refused:
        out.append(AgentAction("rejected", detail=f"edit_draft×{refused}"))
    return [a for a in out if a is not None]


def _edit(op: dict, outcome: dict, text_of: Lookup) -> AgentAction | None:
    kind = op.get("op")
    block = op.get("block")
    if kind in ("replace_text", "set_cells"):
        after = op.get("replace") if kind == "replace_text" else (op.get("cells") or [{}])[0].get("content")
        return AgentAction("edit", target=block, page=_page_of(block), text=_short(after))
    if kind == "insert_text":
        return AgentAction("add", target=f"p{op.get('page')}", page=op.get("page"), text=_short(op.get("text")))
    if kind == "adopt":
        target = block or f"p{op.get('page')}"
        return AgentAction("table_fix" if block else "recognize", target=target, page=_page_of(target))
    if kind == "dismiss":
        target = outcome.get("target")
        return AgentAction("close", target=target, page=_page_of(target), detail=_short(op.get("reason")))
    return _structure(op, text_of)


def _structure(change: dict, text_of: Lookup) -> AgentAction | None:
    op = change.get("op")
    block = change.get("block") or change.get("first") or change.get("src")
    common = dict(target=block, page=_page_of(block), text=_short(text_of(block) if block else None))
    if op == "set_role":
        if change.get("kind") == "title":
            return AgentAction("set_title", level=change.get("level"), **common)
        return AgentAction("set_role", detail=change.get("kind"), **common)
    if op == "set_level":
        return AgentAction("set_level", level=change.get("level"), **common)
    if op == "merge_tables":
        return AgentAction("merge", **common)
    if op in ("split", "exclude", "include", "move"):
        return AgentAction(op, **common)
    if op == "link" and change.get("kind") == "continues":
        return AgentAction("join", **common)
    return None


@dataclass
class AgentTally:
    tool_calls: int = 0
    changes: int = 0
    added: int = 0
    closed: int = 0

    def add(self, record: dict) -> None:
        if record.get("type") != "call":
            return
        self.tool_calls += 1
        if record.get("tool") != "edit_draft" or not (record.get("envelope") or {}).get("ok"):
            return
        ops = (record.get("request") or {}).get("ops") or []
        for outcome in (record.get("result") or {}).get("outcomes") or []:
            if not outcome.get("accepted"):
                continue
            kind = ops[outcome["index"]].get("op") if outcome.get("index", -1) < len(ops) else None
            if kind == "insert_text":
                self.added += 1
            elif kind == "dismiss":
                self.closed += 1
            else:
                self.changes += 1


class CallFollower:
    """Follows a ``calls.jsonl`` from its current end: each new record goes to *on_record* (a thread)."""

    def __init__(self, path: Path, on_record: Callable[[dict], None], interval_s: float = 0.5):
        self.path, self.on_record, self.interval_s = Path(path), on_record, interval_s
        self.offset = self.path.stat().st_size if self.path.is_file() else 0
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._loop, daemon=True)

    def __enter__(self) -> "CallFollower":
        self._thread.start()
        return self

    def __exit__(self, *exc) -> None:
        self._stop.set()
        self._thread.join(timeout=5)
        self.poll()  # what arrived since the last look

    def _loop(self) -> None:
        while not self._stop.wait(self.interval_s):
            self.poll()

    def poll(self) -> None:
        if not self.path.is_file():
            return
        with open(self.path, "rb") as handle:
            handle.seek(self.offset)
            data = handle.read()
        end = data.rfind(b"\n")
        if end < 0:
            return
        self.offset += end + 1
        for line in data[:end].splitlines():
            try:
                record = json.loads(line)
            except ValueError:
                continue
            if isinstance(record, dict):
                self.on_record(record)
