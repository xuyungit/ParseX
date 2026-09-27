#!/usr/bin/env python3
"""Agent runs outside the repository (guide §7.3): a frozen tool snapshot per round, the product command on it.

    uv run python scripts/agent_explore.py snapshot --round s2 --rules r2
    uv run python scripts/agent_explore.py parse    --round s2 --doc a,b [--runtime hybrid] [--engine loop] [--no-agent] [--tag a]

- ``snapshot``: the round's tool snapshot outside the repository — a wheel
  built from ``git archive`` of the (clean) HEAD, installed with the locked
  dependencies into ``<exp_root>/<round>/_toolkit/venv``, the layout model
  copied in, and a ``px-run`` launcher.  Tool code stays fixed for the round.
- ``parse`` (plan P4-1, P4-2): the product command ``parserx parse`` of the round's snapshot on one document, in a
  fresh directory outside the repository (keys from the repository ``.env`` reach the product process only; it
  keeps them from the agent itself); then the hygiene audit of the agent's events, the workspace integrity and the
  scores → ``<round>/_parse/<doc>/record.json``.  ``--no-agent`` hides Codex (PATH without it): the fallback.

The agent always starts from the pipeline's draft (Q85); the Phase 2 exploration mode, in which the agent built the
workspace itself (``run`` / ``verify`` / ``control`` / ``summary``), is retired — see git history before Q85.

Documents are named as in ``configs/phase2_explore.yaml``: ground-truth
documents by name, the others from the legacy sample directory
(``--sample-dir`` or ``PARSERX_SAMPLE_DOCS``).
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tarfile
import tempfile
import time
import zipfile
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from dotenv import dotenv_values  # noqa: E402

from parserx.config.schema import load_config, load_raw_config  # noqa: E402
from parserx.eval.freeze import git_state  # noqa: E402
from parserx.eval.gate import document_scores  # noqa: E402
from parserx.eval.metrics import METRIC_VERSION, evaluate_markdown  # noqa: E402
from parserx.eval.reporting import config_fingerprint  # noqa: E402
from parserx.runtimes.codex import (  # noqa: E402
    audit_events,
    read_events,
    usage_from_events,
)
from parserx.runtimes.experiment import (  # noqa: E402
    doc_config,
    secret_names,
)

DEFAULT_EXP_ROOT = Path.home() / "parserx-exp" / "phase2"
CONFIG = REPO_ROOT / "configs" / "regression_v2.yaml"  # the fixed-sequence v2 config; tools read the same sections
ENV_FILE = REPO_ROOT / ".env"
TEMPLATE = REPO_ROOT / "parserx" / "runtimes" / "agent_task.md"
ADAPTER = REPO_ROOT / "parserx" / "runtimes" / "adapter_cli.md"
GT_DIRS = (REPO_ROOT / "ground_truth", REPO_ROOT / "ground_truth_public", REPO_ROOT / "ground_truth_unseen")
EXPLORE_SET = REPO_ROOT / "configs" / "phase2_explore.yaml"
INPUT_ORDER = (".pdf", ".docx", ".doc")  # as the evaluation runner picks them
MODEL, EFFORT = "gpt-6-sol", "medium"  # Q35 (medium since P2-5): explicit on every run


def _utc() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _sha256(path: Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _run(argv: list[str], **kw) -> subprocess.CompletedProcess:
    return subprocess.run(argv, check=True, text=True, capture_output=True, **kw)


# The snapshot's Python is always started with -P: with -m it would otherwise put the working directory (the
# repository, when the harness runs there) first on sys.path and import the repository's parserx, not the round's.


def _codex_version() -> str:
    return _run(["codex", "--version"]).stdout.strip()


def _round_dir(args) -> Path:
    return args.exp_root.resolve() / args.round


def _toolkit(args) -> Path:
    return _round_dir(args) / "_toolkit"


# ── snapshot ────────────────────────────────────────────────────────────


def cmd_snapshot(args) -> int:
    root = args.exp_root.resolve()
    if root == REPO_ROOT or REPO_ROOT in root.parents:
        sys.exit("the experiment root must be outside the repository")
    toolkit = _toolkit(args)
    if toolkit.exists():
        sys.exit(f"{toolkit} exists: a round's tools never change (start a new round instead)")
    state = git_state()
    if state["dirty"] and not args.allow_dirty:
        sys.exit(f"uncommitted or untracked code {state['untracked_code']}: commit first (or --allow-dirty)")
    toolkit.mkdir(parents=True)
    with tempfile.TemporaryDirectory(prefix="parserx-snapshot-") as tmp:
        src = Path(tmp) / "src"
        src.mkdir()
        archive = Path(tmp) / "head.tar"
        _run(["git", "archive", "--format=tar", "-o", str(archive), state["commit"]], cwd=REPO_ROOT)
        with tarfile.open(archive) as tar:
            tar.extractall(src, filter="data")
        print(f"building wheel of {state['commit'][:12]} …")
        _run(["uv", "build", "--wheel", "--out-dir", str(toolkit / "dist"), str(src)])
        _run(["uv", "export", "--frozen", "--no-dev", "--no-emit-project", "--no-hashes",
              "-o", str(toolkit / "requirements.txt")], cwd=src)
    wheel = next((toolkit / "dist").glob("parserx-*.whl"))
    with zipfile.ZipFile(wheel) as zf:
        shipped = set(zf.namelist())
    for needed in ("parserx/skills/transcription.md", "parserx/prompts/review_table.md",
                   "parserx/runtimes/agent_task.md"):
        if needed not in shipped:
            sys.exit(f"the wheel lacks {needed}")
    venv = toolkit / "venv"
    python = venv / "bin" / "python"
    print("installing the locked dependencies …")
    _run(["uv", "venv", str(venv), "--python", "3.13"])
    _run(["uv", "pip", "install", "--python", str(python), "--compile-bytecode", "-r",
          str(toolkit / "requirements.txt")])
    _run(["uv", "pip", "install", "--python", str(python), "--compile-bytecode", "--no-deps", str(wheel)])
    # The layout model is downloaded into the package on first use; the sandbox cannot write there.
    here = Path(_run([sys.executable, "-c", "import rapid_layout,os;print(os.path.dirname(rapid_layout.__file__))"])
                .stdout.strip()) / "models"
    there = Path(_run([str(python), "-c", "import rapid_layout,os;print(os.path.dirname(rapid_layout.__file__))"])
                 .stdout.strip()) / "models"
    there.mkdir(exist_ok=True)
    for model in sorted(here.glob("*.onnx")):
        shutil.copy2(model, there / model.name)
    px_run = toolkit / "px-run"
    px_run.write_text(
        "#!/bin/sh\n"
        f"# ParserX tool snapshot {state['commit'][:12]} for round {args.round} (scripts/agent_explore.py).\n"
        'doc="$1"; shift\n'
        f'exec "{python}" -P -m parserx.runtimes.px --env-file "{ENV_FILE}" --config "$doc/parserx.yaml" -- "$@"\n',
        encoding="utf-8")
    px_run.chmod(0o755)
    skills = json.loads(_run([str(python), "-P", "-m", "parserx.runtimes.experiment", "skills"]).stdout)
    config = load_config(CONFIG)
    snapshot = {
        "round": args.round, "created": _utc(), "commit": state["commit"], "dirty": state["dirty"],
        "wheel": {"name": wheel.name, "sha256": _sha256(wheel)},
        "requirements_sha256": _sha256(toolkit / "requirements.txt"),
        "python": _run([str(python), "-c", "import platform;print(platform.python_version())"]).stdout.strip(),
        "layout_models": sorted(m.name for m in there.glob("*.onnx")),
        "codex_version": _codex_version(),
        "rules": args.rules,  # which {{#rN}} block of the task template this round uses (plan §2.3)
        "agent": {"runtime": "codex", "model": args.model, "reasoning_effort": args.effort},
        "services": {"vlm": config.services.vlm.model, "ocr": config.builders.ocr.model},
        "config": {"file": str(CONFIG.relative_to(REPO_ROOT)), "fingerprint": config_fingerprint(config)},
        "service_env": secret_names(load_raw_config(CONFIG)),  # supplied by px-run from the repository .env
        "skills": {name: hashlib.sha256(text.encode("utf-8")).hexdigest() for name, text in skills.items()},
        "template_sha256": _sha256(TEMPLATE),
        "adapter_sha256": _sha256(ADAPTER),  # the tool reference comes from the code (commit, wheel)
    }
    (toolkit / "snapshot.json").write_text(json.dumps(snapshot, indent=2, ensure_ascii=False) + "\n")
    print(json.dumps(snapshot, indent=2, ensure_ascii=False))
    return 0


def _snapshot(args) -> dict:
    path = _toolkit(args) / "snapshot.json"
    if not path.is_file():
        sys.exit(f"no snapshot for round {args.round}: run `snapshot --round {args.round}` first")
    return json.loads(path.read_text())


# ── run ─────────────────────────────────────────────────────────────────


def _expected(doc: str) -> Path | None:
    for gt in GT_DIRS:
        if (gt / doc / "expected.md").is_file():
            return gt / doc / "expected.md"
    return None


def _explore_set() -> list[dict]:
    import yaml

    return yaml.safe_load(EXPLORE_SET.read_text(encoding="utf-8"))["documents"]


def _sample_dir(args) -> Path | None:
    value = args.sample_dir or os.environ.get("PARSERX_SAMPLE_DOCS")
    return Path(value).resolve() if value else None


def _find_input(args, doc: str) -> tuple[Path, Path | None]:
    """(input file, expected.md or None)."""
    if getattr(args, "input", None):
        return args.input.resolve(), _expected(doc)
    for gt in GT_DIRS:
        for ext in INPUT_ORDER:
            if (gt / doc / f"input{ext}").is_file():
                return gt / doc / f"input{ext}", _expected(doc)
    entry = next((d for d in _explore_set() if d["name"] == doc and d.get("sample")), None)
    if entry is not None:
        sample_dir = _sample_dir(args)
        if sample_dir is None:
            sys.exit(f"{doc} comes from the sample directory: give --sample-dir or PARSERX_SAMPLE_DOCS")
        return sample_dir / entry["sample"], None
    sys.exit(f"{doc}: no ground truth, not in {EXPLORE_SET.name}; give --input")


def _forbidden(args) -> dict[str, Path]:
    roots = {"repository": REPO_ROOT, "experiment root": args.exp_root.resolve()}
    if _sample_dir(args) is not None:
        roots["sample documents"] = _sample_dir(args)
    return roots


def _pages(path: Path) -> int | None:
    if path.suffix.lower() == ".pdf":
        import pymupdf

        with pymupdf.open(path) as pdf:
            return pdf.page_count
    try:
        with zipfile.ZipFile(path) as zf:
            app = zf.read("docProps/app.xml").decode("utf-8", "replace")
        start = app.find("<Pages>")
        return int(app[start + 7: app.find("</Pages>")]) if start >= 0 else None
    except (KeyError, ValueError, zipfile.BadZipFile):
        return None


# ── verify ──────────────────────────────────────────────────────────────


def _scores(doc: str, expected: Path | None, export: dict) -> dict | None:
    """Metrics of the exported Markdown (same metric version as the frozen runs); cost is recorded apart."""
    if expected is None or not export["exported"]:
        return None
    markdown = Path(export["markdown"]).read_text(encoding="utf-8")
    result = evaluate_markdown(markdown, expected.read_text(encoding="utf-8"), name=doc)
    scores = {"metric_version": METRIC_VERSION, **document_scores(result)}
    for key in ("requests", "ocr_pages", "attempts", "cache_hits", "wall_time_seconds", "cost_usd"):
        scores.pop(key)
    return scores


# ── control ─────────────────────────────────────────────────────────────


# ── parse (the product command) ──────────────────────────────────────────


def cmd_parse(args) -> int:
    _snapshot(args)
    return max(_parse_one(args, doc) for spec in args.doc for doc in spec.split(","))


def _parse_one(args, doc: str) -> int:
    import yaml

    input_path, expected = _find_input(args, doc)
    doc_dir = _round_dir(args) / "_parse" / (doc + (f".{args.tag}" if args.tag else ""))
    if doc_dir.exists():
        if not args.rerun:
            sys.exit(f"{doc_dir} exists (use --rerun)")
        shutil.rmtree(doc_dir)
    doc_dir.mkdir(parents=True)
    source = doc_dir / f"input{input_path.suffix.lower()}"
    shutil.copyfile(input_path, source)
    config = doc_config(load_raw_config(CONFIG), doc_dir)
    agent = config.setdefault("runtime", {}).setdefault("agent", {})
    agent["engine"] = args.engine  # Codex, or our own loop (Q86)
    if args.model:
        agent["model"] = args.model
    for setting in args.agent_set or []:  # e.g. api=chat, vision=agent, endpoint=${OPENAI_BASE_URL_C}
        key, _, value = setting.partition("=")
        agent[key] = yaml.safe_load(value) if value[:1] not in ("$",) else value
    (doc_dir / "parserx.yaml").write_text(yaml.safe_dump(config, allow_unicode=True, sort_keys=False))
    python = _toolkit(args) / "venv" / "bin" / "python"
    env = {**os.environ, **{k: v for k, v in dotenv_values(ENV_FILE).items() if v is not None}}
    if args.no_agent:
        codex_dirs = {str(Path(p).parent) for p in [shutil.which("codex")] if p}
        env["PATH"] = os.pathsep.join(d for d in env.get("PATH", "").split(os.pathsep) if d not in codex_dirs)
    argv = [str(python), "-P", "-m", "parserx.cli", "parse", str(source), "-o", str(doc_dir / "out"), "-c",
            str(doc_dir / "parserx.yaml"), "--runtime", args.runtime, "--keep-work", "--json", "--lang", args.lang]
    print(f"[{_utc()}] parse {doc} ({input_path.name}, {_pages(input_path)} pages, {args.runtime}"
          f"{', no agent' if args.no_agent else ''}) …", flush=True)
    t0 = time.monotonic()
    proc = subprocess.run(argv, cwd=doc_dir, env=env, capture_output=True, text=True, timeout=args.timeout_min * 60)
    wall = round(time.monotonic() - t0, 1)
    (doc_dir / "console.log").write_text(proc.stderr, encoding="utf-8")
    sys.stderr.write(proc.stderr)
    outcome = json.loads(proc.stdout) if proc.stdout.strip() else None
    work = doc_dir / "out" / ".parserx-work"
    audit, usage = None, None
    events_path = work / "agent_run" / "events.jsonl"
    if events_path.is_file():
        events, _ = read_events(events_path)
        usage = usage_from_events(events).model_dump()
        audit = audit_events(events, doc_dir=work / "agent", home=Path.home(), forbidden=_forbidden(args)).model_dump()
    integrity = None
    if (work / "agent" / "ws" / "state.json").is_file():
        integrity = json.loads(_run([str(python), "-P", "-m", "parserx.runtimes.experiment", "verify", "--doc-dir",
                                     str(work / "agent")]).stdout)["integrity"]
    scores = None
    if expected is not None and outcome and not outcome.get("error"):
        scores = _scores(doc, expected, {"exported": True, "markdown": outcome["markdown"]})
    record = {"doc": doc, "runtime_mode": args.runtime, "engine": args.engine, "model": args.model,
              "agent_set": args.agent_set,
              "no_agent": args.no_agent,
              "exit_code": proc.returncode,
              "wall_s": wall, "outcome": outcome, "agent_usage": usage, "audit": audit, "integrity": integrity,
              "scores": scores, "snapshot": _snapshot(args)["commit"]}
    (doc_dir / "record.json").write_text(json.dumps(record, indent=2, ensure_ascii=False) + "\n")
    valid = (audit is None or audit["ok"]) and (integrity is None or integrity["ok"])
    s = scores or {}
    print(f"  exit {proc.returncode} {wall} s runtime {(outcome or {}).get('runtime')} "
          f"({(outcome or {}).get('runtime_note')}) hygiene {'ok' if valid else 'FAILED'} char_f1 {s.get('char_f1')} "
          f"table_f1 {s.get('table_cell_f1')} heading_f1 {s.get('heading_f1')}", flush=True)
    for hit in (audit or {}).get("hits", []):
        print(f"  AUDIT {hit['kind']}: {hit['detail']}")
    return 0 if valid and proc.returncode == 0 else 1


# ── summary ─────────────────────────────────────────────────────────────


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--exp-root", type=Path, default=DEFAULT_EXP_ROOT)
    parser.add_argument("--sample-dir", type=Path, help="legacy sample documents (default: $PARSERX_SAMPLE_DOCS)")
    sub = parser.add_subparsers(dest="command", required=True)
    snap = sub.add_parser("snapshot")
    snap.add_argument("--round", required=True)
    snap.add_argument("--rules", required=True, choices=("r1", "r2"),
                      help="round rules: r1 exploration, r2 close to acceptance (plan §2.3)")
    snap.add_argument("--allow-dirty", action="store_true")
    snap.add_argument("--model", default=MODEL)
    snap.add_argument("--effort", default=EFFORT)
    prs = sub.add_parser("parse", help="the product command parserx parse of the snapshot, audited")
    prs.add_argument("--round", required=True)
    prs.add_argument("--doc", action="append", required=True, help="document name(s), comma-separated")
    prs.add_argument("--input", type=Path)
    prs.add_argument("--runtime", choices=("hybrid", "fixed"), default="hybrid")
    prs.add_argument("--engine", choices=("codex", "loop"), default="codex",
                     help="the agent: Codex, or our own function-calling loop (Q86)")
    prs.add_argument("--model", help="the agent's model (default: the config's, gpt-6-sol)")
    prs.add_argument("--agent-set", action="append", metavar="KEY=VALUE",
                     help="a runtime.agent setting (api, endpoint, api_key, vision, budget_usd, …); repeatable")
    prs.add_argument("--no-agent", action="store_true", help="Codex not on PATH: the fallback")
    prs.add_argument("--lang", choices=("zh", "en"), default="zh")
    prs.add_argument("--tag", help="suffix of the run directory (several runs of one document)")
    prs.add_argument("--rerun", action="store_true")
    prs.add_argument("--timeout-min", type=int, default=120)
    args = parser.parse_args()
    commands = {"snapshot": cmd_snapshot, "parse": cmd_parse}
    return commands[args.command](args)


if __name__ == "__main__":
    sys.exit(main())
