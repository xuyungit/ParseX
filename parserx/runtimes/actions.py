"""What the agent did, from the tools' call records (plan P4-1, P4-2).

Every tool call appends a structured record to ``calls.jsonl`` when it ends (request, envelope, result).  The
hybrid runtime follows that file while the agent works: ``actions`` turns one record into the user's terms (look,
edit, add, set a title, close …) for the console, ``AgentTally`` counts them for the document summary.  Reading,
checking and exporting are not actions: they change nothing.
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


def _look(block: str | None, page: int | None, seam: int | None, question: str | None) -> AgentAction:
    target = block or (f"p{page}" if page is not None else f"p{seam}" if seam is not None else None)
    return AgentAction("look", target=target, page=page if page is not None else seam if seam is not None
                       else _page_of(block), detail=_short(question))


def actions(record: dict, text_of: Lookup = lambda _: None) -> list[AgentAction]:
    if record.get("type") != "call":
        return []
    tool, req = record.get("tool"), record.get("request") or {}
    result = record.get("result") or {}
    ok = (record.get("envelope") or {}).get("ok", False)
    if tool == "ask_image":
        questions = req.get("questions") or [req]
        return [_look(q.get("block"), q.get("page"), q.get("seam"), q.get("question")) for q in questions]
    if tool == "read" and req.get("image") in ("page", "crop"):
        return [_look(req.get("block"), req.get("page"), None, None)]
    if not ok:
        return []
    if tool == "correct":
        add = req.get("add")
        if not result.get("adopted"):
            target = req.get("block") or (f"p{add['page']}" if add else None)
            return [AgentAction("rejected", target=target, page=_page_of(target), detail="correct")]
        if add:
            return [AgentAction("add", target=f"p{add['page']}", page=add["page"], text=_short(add.get("text")))]
        edits = req.get("edits") or []
        cells = req.get("cells") or []
        after = edits[0].get("replace") if edits else cells[0].get("content") if cells else None
        return [AgentAction("edit", target=req.get("block"), page=_page_of(req.get("block")), text=_short(after))]
    if tool == "close":
        if not result.get("closed"):
            return []
        return [AgentAction("close", target=req.get("target"), page=_page_of(req.get("target")),
                            detail=_short(req.get("reason")))]
    if tool == "review_table":
        name = "table_fix" if result.get("adopted") else "rejected"
        return [AgentAction(name, target=req.get("block"), page=_page_of(req.get("block")),
                            detail=None if result.get("adopted") else "review_table")]
    if tool == "apply_structure":
        changes = req.get("changes") or []
        out = [_structure(changes[i], text_of) for i in result.get("accepted", []) if i < len(changes)]
        rejected = len(result.get("rejected", []))
        if rejected:
            out.append(AgentAction("rejected", detail=f"apply_structure×{rejected}"))
        return [a for a in out if a is not None]
    if tool == "recognize":
        pages = req.get("pages") or []
        return [AgentAction("recognize", target=",".join(f"p{p}" for p in pages) or None,
                            page=pages[0] if pages else None)]
    if tool == "describe_figure":
        blocks = req.get("blocks") or ([req["block"]] if req.get("block") else [])
        return [AgentAction("describe", target=b, page=_page_of(b)) for b in blocks]
    return []


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
    if op in ("split", "exclude", "restore", "move_after"):
        return AgentAction(op, **common)
    if op == "add_relation" and change.get("kind") == "continues":
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
        if not (record.get("envelope") or {}).get("ok"):
            return
        tool, req, result = record.get("tool"), record.get("request") or {}, record.get("result") or {}
        if tool == "correct" and result.get("adopted"):
            if req.get("add"):
                self.added += 1
            else:
                self.changes += 1
        elif tool == "review_table" and result.get("adopted"):
            self.changes += 1
        elif tool == "apply_structure":
            self.changes += len(result.get("accepted", []))
        elif tool == "close" and result.get("closed"):
            self.closed += 1


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
