"""What the agent-run harness (scripts/agent_explore.py) needs inside a round's snapshot (guide §7.3).

- the agent's task and skills as this installation ships them (``render_task``, ``shipped_skills``);
- a round's config for one run directory: its own response cache, empty at the start (``doc_config``);
- the post-run check of an agent's workspace: integrity, accounts, tool usage (``verify_run``).

    python -m parserx.runtimes.experiment verify --doc-dir DIR   → JSON on stdout (DIR holds ws/)
    python -m parserx.runtimes.experiment skills | template

The agent always starts from the pipeline's draft, in the product's runtime (``parserx parse``, Q85); the
exploration mode in which the agent built the workspace and exported itself (Phase 2) is retired.
"""

from __future__ import annotations

import argparse
import copy
import json
import re
import sys
from collections import Counter
from pathlib import Path
from typing import Any


from parserx.accounting import check
from parserx.ir.base import IRModel
from parserx.ir.enums import DocumentStatus
from parserx.ir.state import AccountingSummary
from parserx.workspace import IntegrityReport, Workspace, verify_workspace
from parserx.workspace.store import read_records

SKILL_FILES = ("transcription.md", "figure.md", "structure.md")
_ENV_REF = re.compile(r"\$\{([^}:]+)(?::[^}]*)?\}")


# ── A run's config and task ───────────────────────────────────────────────


def secret_names(raw: dict[str, Any]) -> list[str]:
    """Environment variables a raw config refers to (``${VAR}``): supplied when a tool runs, never written down."""
    return sorted(set(_ENV_REF.findall(json.dumps(raw))))


def doc_config(raw: dict[str, Any], doc_dir: Path) -> dict[str, Any]:
    """The project config for one experiment directory: its own response cache, empty at the start."""
    config = copy.deepcopy(raw)
    config.pop("extends", None)
    config["cache"] = {"mode": "read_write", "dir": str(Path(doc_dir) / ".parserx_cache")}
    return config


_BLOCK = re.compile(r"\{\{#(\w+)\}\}(.*?)\{\{/\1\}\}", re.S)
_VALUE = re.compile(r"\{\{(\w+)\}\}")


_ROUND = re.compile(r"r\d+")


def render_task(template: str, *, round_name: str | None, values: dict[str, Any],
                options: set[str] = frozenset()) -> str:
    """The task (AGENTS.md) for one round: ``{{#rN}}…{{/rN}}`` blocks of other rounds are dropped, and so are the
    other ``{{#name}}…{{/name}}`` blocks unless *name* is one of the chosen *options* (e.g. ``vision_tool``,
    ``experiment`` or ``product``).  *round_name* None drops every round block (the product's task, P4-1)."""
    names = {m.group(1) for m in _BLOCK.finditer(template)}
    rounds = {n for n in names if _ROUND.fullmatch(n)}
    if round_name is not None and round_name not in rounds:
        raise KeyError(f"the template has no block for round {round_name!r} (has {sorted(rounds)})")

    def keep(m: re.Match) -> str:
        name = m.group(1)
        return m.group(2) if (name == round_name if _ROUND.fullmatch(name) else name in options) else ""

    text = _BLOCK.sub(keep, template)

    def value(m: re.Match) -> str:
        if m.group(1) not in values:
            raise KeyError(f"no value for {{{{{m.group(1)}}}}}")
        return str(values[m.group(1)])

    return _VALUE.sub(value, text)


TASK = Path(__file__).parent / "agent_task.md"
ADAPTERS = {"cli": Path(__file__).parent / "adapter_cli.md",  # how an agent calls the tools, per runtime (Q86)
            "call": Path(__file__).parent / "adapter_call.md"}


def compose_task(adapter: str, *, input_name: str, minutes: int, vision: str = "tool") -> str:
    """The agent's task: the task and method (``agent_task.md``, whatever the agent), how this runtime calls the
    tools (*adapter*), the tool reference made from the request models, and the skills."""
    from parserx.tools.reference import reference

    skills = shipped_skills()
    template = TASK.read_text(encoding="utf-8").replace("{{adapter}}", ADAPTERS[adapter].read_text(encoding="utf-8")
                                                        .rstrip() if adapter in ADAPTERS else "")
    return render_task(template, round_name=None, options={"product", f"vision_{vision}"},
                       values={"input_name": input_name, "budget_minutes": minutes, "tools": reference(),
                               "skills": "\n\n".join(skills[n].strip() for n in ("transcription", "figure", "structure"))})


# ── After a run ──────────────────────────────────────────────────────────


class ToolUsage(IRModel):
    calls: dict[str, int]
    failures: dict[str, int]  # failure codes over all envelopes
    requests: dict[str, int]
    attempts: dict[str, int]
    cache_hits: dict[str, int]
    tokens: dict[str, dict[str, int]]
    cost_usd: float | None
    wall_time_s: float


class CheckSummary(IRModel):
    accounting: AccountingSummary
    exportable: bool
    document_status: DocumentStatus
    pages: dict[str, int]  # page status → count
    missing: int
    unresolved: dict[str, int]


class Verification(IRModel):
    integrity: IntegrityReport
    check: CheckSummary | None
    tools: ToolUsage | None


def verify_run(doc_dir: Path) -> Verification:
    from parserx.tools.views import unresolved_items

    doc_dir = Path(doc_dir)
    ws_root = doc_dir / "ws"
    if not (ws_root / "state.json").is_file():
        empty = IntegrityReport(ok=False, problems=["no workspace"], transactions=0, calls=0)
        return Verification(integrity=empty, check=None, tools=None)
    integrity = verify_workspace(ws_root)
    state = Workspace.open(ws_root).load()
    records = read_records(ws_root / "calls.jsonl")
    calls = [r for r in records if r.get("type") == "call"]
    result = check(state, ws_root)
    summary = CheckSummary(
        accounting=result.accounting, exportable=result.exportable, document_status=result.document_status,
        pages=dict(sorted(Counter(p.status.value for p in result.pages).items())), missing=len(result.missing),
        unresolved=dict(sorted(Counter(u.kind.value for u in unresolved_items(state)).items())),
    )
    failures = Counter(f["code"] for c in calls for f in (c.get("envelope") or {}).get("failures", []))
    stats = state.stats
    tools = ToolUsage(
        calls=dict(sorted(Counter(c.get("tool") for c in calls).items())), failures=dict(sorted(failures.items())),
        requests=stats.requests, attempts=stats.attempts, cache_hits=stats.cache_hits,
        tokens={k: v.model_dump() for k, v in stats.tokens.items()}, cost_usd=stats.cost_usd,
        wall_time_s=stats.wall_time_s,
    )
    return Verification(integrity=integrity, check=summary, tools=tools)


def shipped_skills() -> dict[str, str]:
    """The skills as shipped in this installation (text by name)."""
    from parserx.skills import SKILLS, load_skill

    return {name: load_skill(name).text for name in SKILLS}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m parserx.runtimes.experiment")
    sub = parser.add_subparsers(dest="command", required=True)
    verify = sub.add_parser("verify", help="Post-run check of an agent's directory (JSON on stdout)")
    verify.add_argument("--doc-dir", type=Path, required=True)
    sub.add_parser("skills", help="The skills shipped in this installation (JSON on stdout)")
    sub.add_parser("template", help="The task template shipped in this installation (text on stdout)")
    args = parser.parse_args(argv)
    if args.command == "verify":
        print(verify_run(args.doc_dir).model_dump_json())
    elif args.command == "template":
        sys.stdout.write((Path(__file__).parent / "agent_task.md").read_text(encoding="utf-8"))
    else:
        print(json.dumps(shipped_skills(), ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
