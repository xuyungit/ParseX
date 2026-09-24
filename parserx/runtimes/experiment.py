"""Agent experiments (guide §7.2–§7.3; plan P2-1, §2.2–§2.5): one isolated directory per document and run.

Layout of ``<exp_root>/<round>/<doc>/`` while the agent runs — nothing else:

    input.<ext>     the document
    px              calls this round's tool snapshot (no service keys in the directory)
    parserx.yaml    self-contained config: no repository paths; the response cache lives here, empty at start
    AGENTS.md       the task (P2-2)
    skills/         the three skills, as shipped in the snapshot
    ws/  out/       workspace and export, created by the tools

After the run the harness adds ``run/`` (events, last message, record).
``verify_run`` is the post-run check.  It runs inside the round's snapshot, so
the export is compared with that snapshot's own rendering.

    python -m parserx.runtimes.experiment verify --doc-dir DIR   → JSON on stdout

The control (plan P2-3) is the fixed-sequence runtime on the same snapshot,
input and config, in a directory of the same layout (``run_control``).
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import os
import re
import sys
import time
from collections import Counter
from pathlib import Path
from typing import Any

import yaml

from parserx.accounting import check
from parserx.ir.base import IRModel
from parserx.ir.enums import DocumentStatus
from parserx.ir.state import AccountingSummary
from parserx.render.markdown import render_markdown
from parserx.workspace import IntegrityReport, Workspace, verify_workspace
from parserx.workspace.store import read_records

SKILL_FILES = ("transcription.md", "figure.md", "structure.md")
_ENV_REF = re.compile(r"\$\{([^}:]+)(?::[^}]*)?\}")


# ── Preparing a directory ────────────────────────────────────────────────


def secret_names(raw: dict[str, Any]) -> list[str]:
    """Environment variables a raw config refers to (``${VAR}``): px supplies them when a tool runs."""
    return sorted(set(_ENV_REF.findall(json.dumps(raw))))


def doc_config(raw: dict[str, Any], doc_dir: Path) -> dict[str, Any]:
    """The project config for one experiment directory: its own response cache, empty at the start."""
    config = copy.deepcopy(raw)
    config.pop("extends", None)
    config["cache"] = {"mode": "read_write", "dir": str(Path(doc_dir) / ".parserx_cache")}
    return config


def prepare_doc_dir(doc_dir: Path, *, input_path: Path, config: dict[str, Any], px_text: str, agents_md: str,
                    skills: dict[str, str]) -> dict[str, str]:
    """Create the directory; returns the SHA-256 of every file written (relative path → digest)."""
    doc_dir = Path(doc_dir)
    if doc_dir.exists():
        raise FileExistsError(f"{doc_dir} exists; every run gets a fresh directory")
    (doc_dir / "skills").mkdir(parents=True)
    files = {
        f"input{Path(input_path).suffix.lower()}": Path(input_path).read_bytes(),
        "parserx.yaml": ("# Experiment config (generated; secrets are supplied by px when a tool runs)\n"
                         + yaml.safe_dump(config, allow_unicode=True, sort_keys=False)).encode("utf-8"),
        "px": px_text.encode("utf-8"),
        "AGENTS.md": agents_md.encode("utf-8"),
        **{f"skills/{name}.md": text.encode("utf-8") for name, text in sorted(skills.items())},
    }
    for rel, data in files.items():
        (doc_dir / rel).write_bytes(data)
    (doc_dir / "px").chmod(0o755)
    return {rel: hashlib.sha256(data).hexdigest() for rel, data in sorted(files.items())}


_BLOCK = re.compile(r"\{\{#(\w+)\}\}(.*?)\{\{/\1\}\}", re.S)
_VALUE = re.compile(r"\{\{(\w+)\}\}")


def render_task(template: str, *, round_name: str, values: dict[str, Any]) -> str:
    """The task (AGENTS.md) for one round: ``{{#rN}}…{{/rN}}`` blocks of other rounds are dropped."""
    rounds = {m.group(1) for m in _BLOCK.finditer(template)}
    if round_name not in rounds:
        raise KeyError(f"the template has no block for round {round_name!r} (has {sorted(rounds)})")
    text = _BLOCK.sub(lambda m: m.group(2) if m.group(1) == round_name else "", template)

    def value(m: re.Match) -> str:
        if m.group(1) not in values:
            raise KeyError(f"no value for {{{{{m.group(1)}}}}}")
        return str(values[m.group(1)])

    return _VALUE.sub(value, text)


def listing_problems(doc_dir: Path) -> list[str]:
    """Before a run: the directory holds exactly the listed files (plan §2.2)."""
    doc_dir = Path(doc_dir)
    expected = {"AGENTS.md", "parserx.yaml", "px", "skills"}
    problems = []
    inputs = [p.name for p in doc_dir.glob("input.*")]
    if len(inputs) != 1:
        problems.append(f"expected one input file, found {inputs}")
    for entry in sorted(doc_dir.iterdir()):
        if entry.name not in expected and entry.name not in inputs:
            problems.append(f"unexpected entry: {entry.name}")
    skills = sorted(p.name for p in (doc_dir / "skills").iterdir()) if (doc_dir / "skills").is_dir() else []
    if skills != sorted(SKILL_FILES):
        problems.append(f"skills/ holds {skills}")
    return problems


def config_problems(text: str, *, forbidden: list[Path], secret_values: list[str]) -> list[str]:
    """A config must not name forbidden paths (the repository) nor carry resolved secrets."""
    problems = [f"names {root}" for root in forbidden if str(root) in text]
    problems += ["carries a secret value" for value in secret_values if value and value in text]
    return problems


# ── After a run ──────────────────────────────────────────────────────────


class ExportCheck(IRModel):
    exported: bool  # a successful export wrote into out/
    markdown: str | None = None  # the last such export
    current: bool = False  # its Markdown equals the rendering of the final workspace state
    extra_files: list[str] = []  # files in out/ that no export wrote
    problems: list[str] = []


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
    export: ExportCheck
    check: CheckSummary | None
    tools: ToolUsage | None


def verify_run(doc_dir: Path) -> Verification:
    from parserx.tools.views import unresolved_items

    doc_dir = Path(doc_dir)
    ws_root = doc_dir / "ws"
    if not (ws_root / "state.json").is_file():
        empty = IntegrityReport(ok=False, problems=["no workspace"], transactions=0, calls=0)
        return Verification(integrity=empty, export=ExportCheck(exported=False, problems=["no workspace"]),
                            check=None, tools=None)
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
    export = _export_check(doc_dir, calls, state, integrity.ok)
    return Verification(integrity=integrity, export=export, check=summary, tools=tools)


def _export_check(doc_dir: Path, calls: list[dict], state, integrity_ok: bool) -> ExportCheck:
    out_dir = (doc_dir / "out").resolve()
    written: list[Path] = []
    last: dict | None = None
    for call in calls:
        envelope = call.get("envelope") or {}
        result = call.get("result") or {}
        if call.get("tool") != "export" or not envelope.get("ok") or not result:
            continue
        md, sidecar = Path(result["markdown"]), Path(result["sidecar"])
        written += [md.resolve(), sidecar.resolve()]
        if md.resolve().parent == out_dir:
            last = result
    problems: list[str] = []
    if not integrity_ok:
        problems.append("the workspace was changed outside the tools; its export does not count")
    if last is None:
        problems.append("no successful export into out/")
        return ExportCheck(exported=False, problems=problems, extra_files=_extra(out_dir, written))
    md_path = Path(last["markdown"])
    if not md_path.is_file():
        problems.append(f"{md_path.name} no longer exists")
        return ExportCheck(exported=False, markdown=str(md_path), problems=problems,
                           extra_files=_extra(out_dir, written))
    current = md_path.read_text(encoding="utf-8") == render_markdown(state)
    if not current:
        problems.append("the exported Markdown differs from the final workspace state (changed after export)")
    return ExportCheck(exported=integrity_ok, markdown=str(md_path), current=current, problems=problems,
                       extra_files=_extra(out_dir, written))


def _extra(out_dir: Path, written: list[Path]) -> list[str]:
    """Files in out/ that no export wrote (exported images live in out/images/)."""
    if not out_dir.is_dir():
        return []
    known = set(written)
    extra = []
    for path in sorted(out_dir.rglob("*")):
        if path.is_file() and path.resolve() not in known and path.parent.name != "images":
            extra.append(str(path.relative_to(out_dir)))
    return extra


def shipped_skills() -> dict[str, str]:
    """The skills as shipped in this installation (text by name)."""
    from parserx.skills import SKILLS, load_skill

    return {name: load_skill(name).text for name in SKILLS}


def run_control(doc_dir: Path) -> dict[str, Any]:
    """The fixed-sequence runtime on an experiment directory's input and config (plan P2-3)."""
    from parserx.config.schema import load_config
    from parserx.runtimes.pipeline import RuntimeFailure, run

    doc_dir = Path(doc_dir)
    source = next(doc_dir.glob("input.*"))
    config = load_config(doc_dir / "parserx.yaml")
    started = time.monotonic()
    try:
        outcome = run(source, doc_dir / "ws", doc_dir / "out", config)
        status, error = outcome.status, None
    except RuntimeFailure as exc:
        status, error = None, str(exc)
    return {"status": status, "error": error, "wall_s": round(time.monotonic() - started, 1)}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m parserx.runtimes.experiment")
    sub = parser.add_subparsers(dest="command", required=True)
    verify = sub.add_parser("verify", help="Post-run check of one experiment directory (JSON on stdout)")
    verify.add_argument("--doc-dir", type=Path, required=True)
    sub.add_parser("skills", help="The skills shipped in this installation (JSON on stdout)")
    control = sub.add_parser("control", help="Fixed-sequence runtime on an experiment directory (JSON on stdout)")
    control.add_argument("--doc-dir", type=Path, required=True)
    control.add_argument("--env-file", type=Path, required=True, help="service settings (kept outside the directory)")
    args = parser.parse_args(argv)
    if args.command == "verify":
        print(verify_run(args.doc_dir).model_dump_json())
    elif args.command == "control":
        from dotenv import dotenv_values

        os.environ.update({k: v for k, v in dotenv_values(args.env_file).items() if v is not None})
        print(json.dumps(run_control(args.doc_dir)))
    else:
        print(json.dumps(shipped_skills(), ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
