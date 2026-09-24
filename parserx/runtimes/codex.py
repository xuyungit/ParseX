"""Codex CLI as an agent runtime (guide §7.1, §7.3; plan P2-1 / §2.1, §2.4, §2.5).

- ``exec_command``: the ``codex exec`` command line.  Model and reasoning
  effort are always explicit (never the local Codex default, Q35).  The
  session is isolated from the user's Codex setup: no user config, rules,
  memories, plugins, apps, browser, computer use, image generation,
  sub-agents or web search, and no session files are kept (``--ephemeral``);
  what is left is the shell inside the ``workspace-write`` sandbox, with
  network for the tools' services.
- ``usage_from_events``: turns, items, commands and tokens from the ``--json``
  event stream — the main agent's usage, counted apart from the tools'
  service requests (guide §5.2).
- ``audit_events``: the hygiene audit.  Codex's sandbox restricts writes, not
  reads, so hygiene rests on isolation plus this check: every path a command
  names must lie in the experiment directory or a system location; answer
  keywords (``expected.md``, ``ground_truth`` …), other documents' directories,
  the tool snapshot and Codex's own home are forbidden; tools other than the
  shell (web search, MCP, sub-agents) and file edits inside the workspace or
  export directory fail the run.
"""

from __future__ import annotations

import json
import os
import re
from collections import Counter
from pathlib import Path
from typing import Iterable, Literal

from parserx.ir.base import IRModel

DISABLED_FEATURES = (
    "memories", "plugins", "apps", "remote_plugin", "browser_use", "browser_use_external", "in_app_browser",
    "computer_use", "image_generation", "multi_agent", "goals", "hooks", "tool_suggest", "skill_search",
)


def exec_command(*, model: str, effort: str, doc_dir: Path, last_message: Path, prompt: str) -> list[str]:
    disabled = [arg for feature in DISABLED_FEATURES for arg in ("--disable", feature)]
    return [
        "codex", "exec", "-m", model, "-c", f"model_reasoning_effort={effort}",
        "--sandbox", "workspace-write", "-c", "sandbox_workspace_write.network_access=true",
        "--skip-git-repo-check", "--ephemeral", "--ignore-user-config", "--ignore-rules", *disabled,
        "-c", 'web_search="disabled"',
        "--json", "-o", str(last_message), "-C", str(doc_dir), prompt,
    ]


# ── Usage ────────────────────────────────────────────────────────────────


class AgentUsage(IRModel):
    thread_id: str | None = None
    turns: int = 0
    failed_turns: int = 0
    items: dict[str, int] = {}  # completed items by type
    commands: int = 0
    failed_commands: int = 0  # non-zero exit code
    input_tokens: int = 0
    cached_input_tokens: int = 0
    output_tokens: int = 0
    reasoning_output_tokens: int = 0
    errors: list[str] = []


def read_events(path: Path) -> tuple[list[dict], int]:
    """The JSON events of a ``--json`` stream and the number of lines that were not JSON."""
    events, bad = [], 0
    for line in Path(path).read_text(encoding="utf-8", errors="replace").splitlines():
        if not line.strip():
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            bad += 1
            continue
        if isinstance(event, dict):
            events.append(event)
        else:
            bad += 1
    return events, bad


def usage_from_events(events: Iterable[dict]) -> AgentUsage:
    usage = AgentUsage()
    items: Counter[str] = Counter()
    for event in events:
        kind = event.get("type")
        if kind == "thread.started":
            usage.thread_id = event.get("thread_id")
        elif kind == "turn.completed":
            usage.turns += 1
            tokens = event.get("usage") or {}
            usage.input_tokens += int(tokens.get("input_tokens") or 0)
            usage.cached_input_tokens += int(tokens.get("cached_input_tokens") or 0)
            usage.output_tokens += int(tokens.get("output_tokens") or 0)
            usage.reasoning_output_tokens += int(tokens.get("reasoning_output_tokens") or 0)
        elif kind == "turn.failed":
            usage.turns += 1
            usage.failed_turns += 1
            usage.errors.append(str((event.get("error") or {}).get("message", event))[:500])
        elif kind == "error":
            usage.errors.append(str(event.get("message", event))[:500])
        elif kind == "item.completed":
            item = event.get("item") or {}
            items[str(item.get("type"))] += 1
            if item.get("type") == "command_execution":
                usage.commands += 1
                if item.get("exit_code") not in (0, None):
                    usage.failed_commands += 1
    usage.items = dict(sorted(items.items()))
    return usage


# ── Audit ────────────────────────────────────────────────────────────────

HitKind = Literal["forbidden", "outside", "workspace_write", "tool_type", "unknown_type"]


class AuditHit(IRModel):
    item: str
    kind: HitKind
    detail: str
    evidence: str


class AuditResult(IRModel):
    ok: bool
    hits: list[AuditHit]  # each fails the run
    notes: list[AuditHit]  # for review; do not fail the run


SHELL_TYPES = frozenset({"agent_message", "reasoning", "command_execution", "file_change", "todo_list", "error"})
_TOOL_TYPE = re.compile(r"mcp|collab|search|browser|computer|image_gen", re.I)
# Words that point at answers or scores wherever they appear in a command.
FORBIDDEN_WORDS = ("expected.md", "ground_truth", "eval_runs", "eval_reports", "best_scores", ".codex", ".claude")
SYSTEM_ROOTS = tuple(Path(p) for p in (
    "/tmp", "/private/tmp", "/var/folders", "/private/var/folders", "/dev", "/usr", "/bin", "/sbin",
    "/opt/homebrew", "/opt/local", "/System", "/Library", "/etc", "/private/etc", "/Applications",
))
_STOP = r"\s'\"`;|&<>(){}\[\],=\\"
_ABSOLUTE = re.compile(rf"(?<![\w.:/~$)\]}}-])/[A-Za-z._~][^{_STOP}]*")
_PARENT = re.compile(rf"(?<![\w./])\.\.(?![.\w])(?:/[^{_STOP}]*)?")
_HOME = re.compile(rf"(?<![\w.~/$])(?:~|\$HOME|\$\{{HOME\}})(?=/|[{_STOP}]|$)(?:/[^{_STOP}]*)?")


def _within(path: Path, root: Path) -> bool:
    return path == root or root in path.parents


def _normal(path: str | Path) -> Path:
    return Path(os.path.normpath(str(path)))


def classify_path(path: Path, *, doc_dir: Path, home: Path, forbidden: dict[str, Path]) -> tuple[str, str] | None:
    """(kind, why) for a path the experiment must not touch, or None when it is allowed."""
    path = _normal(path)
    if _within(path, doc_dir):
        return None
    for label, root in forbidden.items():
        if _within(path, _normal(root)):
            return "forbidden", label
    for name in (".codex", ".claude", ".config"):
        if _within(path, home / name):
            return "forbidden", f"~/{name}"
    if any(_within(path, root) for root in SYSTEM_ROOTS):
        return None
    return "outside", "outside the experiment directory"


def _paths_in(command: str, doc_dir: Path, home: Path) -> list[Path]:
    paths = [Path(m.group(0)) for m in _ABSOLUTE.finditer(command)]
    paths += [doc_dir / m.group(0) for m in _PARENT.finditer(command)]
    for m in _HOME.finditer(command):
        rest = m.group(0).split("/", 1)
        paths.append(home / rest[1] if len(rest) == 2 else home)
    return paths


def audit_events(events: Iterable[dict], *, doc_dir: Path, home: Path, forbidden: dict[str, Path]) -> AuditResult:
    doc_dir, home = _normal(doc_dir), _normal(home)
    items: dict[str, dict] = {}
    for event in events:  # completed items, plus items that started and never completed (e.g. a timeout)
        if event.get("type") in ("item.started", "item.completed"):
            item = event.get("item") or {}
            if item.get("id") is not None and (event["type"] == "item.completed" or item["id"] not in items):
                items[item["id"]] = item
    hits: list[AuditHit] = []
    notes: list[AuditHit] = []
    for item_id, item in items.items():
        kind = str(item.get("type"))
        if kind == "command_execution":
            hit = _audit_command(item_id, str(item.get("command", "")), doc_dir, home, forbidden)
            if hit is not None:
                hits.append(hit)
        elif kind == "file_change":
            for change in item.get("changes") or []:
                path = _normal(doc_dir / str(change.get("path", "")))
                if _within(path, doc_dir / "ws") or _within(path, doc_dir / "out"):
                    hits.append(AuditHit(item=item_id, kind="workspace_write", evidence=str(path),
                                         detail="a file in the workspace or export directory edited directly"))
                    break
                problem = classify_path(path, doc_dir=doc_dir, home=home, forbidden=forbidden)
                if problem is not None:
                    hits.append(AuditHit(item=item_id, kind=problem[0], detail=problem[1], evidence=str(path)))
                    break
        elif kind not in SHELL_TYPES:
            if _TOOL_TYPE.search(kind):
                hits.append(AuditHit(item=item_id, kind="tool_type", detail=f"a tool other than the shell: {kind}",
                                     evidence=json.dumps(item, ensure_ascii=False)[:300]))
            else:
                notes.append(AuditHit(item=item_id, kind="unknown_type", detail=f"unexpected item type {kind}",
                                      evidence=json.dumps(item, ensure_ascii=False)[:300]))
    return AuditResult(ok=not hits, hits=hits, notes=notes)


def _audit_command(item_id: str, command: str, doc_dir: Path, home: Path,
                   forbidden: dict[str, Path]) -> AuditHit | None:
    evidence = command[:300]
    words = [w for w in FORBIDDEN_WORDS if w in command]
    if words:
        return AuditHit(item=item_id, kind="forbidden", detail=f"names {', '.join(words)}", evidence=evidence)
    outside: list[str] = []
    for path in _paths_in(command, doc_dir, home):
        problem = classify_path(path, doc_dir=doc_dir, home=home, forbidden=forbidden)
        if problem is not None and problem[0] == "forbidden":
            return AuditHit(item=item_id, kind="forbidden", detail=f"{problem[1]}: {path}", evidence=evidence)
        if problem is not None:
            outside.append(str(_normal(path)))
    if outside:
        return AuditHit(item=item_id, kind="outside", detail=f"outside the experiment directory: {outside[0]}",
                        evidence=evidence)
    return None
