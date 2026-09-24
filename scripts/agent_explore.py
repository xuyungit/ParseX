#!/usr/bin/env python3
"""Agent exploration harness (docs/v2_phase2_plan.md P2-1, §2.2–§2.5).

    uv run python scripts/agent_explore.py snapshot --round r1 --rules r1
    uv run python scripts/agent_explore.py run      --round r1 --doc text_table01 [--input PATH]
    uv run python scripts/agent_explore.py verify   --round r1 --doc text_table01
    uv run python scripts/agent_explore.py summary  --round r1

- ``snapshot``: the round's tool snapshot outside the repository — a wheel
  built from ``git archive`` of the (clean) HEAD, installed with the locked
  dependencies into ``<exp_root>/<round>/_toolkit/venv``, the layout model
  copied in, and a ``px-run`` launcher.  Tool code stays fixed for the round.
- ``run``: one fresh experiment directory for the document, one ``codex exec``
  session in it (model and reasoning effort explicit, isolated from the local
  Codex setup), deadline per Q39; then ``verify``.
- ``verify``: integrity and export check inside the snapshot, hygiene audit of
  the event stream, usage of the agent and of the tools, scores for documents
  with ground truth (computed here; the agent never sees them) →
  ``<doc>/run/record.json``.
- ``summary``: one table over a round's records.

A voided run (failed audit or integrity, infrastructure failure) is kept as
``<doc>.void<N>``; ``run --rerun REASON`` starts over and records the reason.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import signal
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

from parserx.config.schema import ParserXConfig, _resolve_env_vars, load_config, load_raw_config  # noqa: E402
from parserx.eval.freeze import git_state  # noqa: E402
from parserx.eval.gate import document_scores  # noqa: E402
from parserx.eval.metrics import METRIC_VERSION, evaluate_markdown  # noqa: E402
from parserx.eval.reporting import config_fingerprint  # noqa: E402
from parserx.runtimes.codex import audit_events, exec_command, read_events, usage_from_events  # noqa: E402
from parserx.runtimes.px import _SECRET_NAME  # noqa: E402
from parserx.runtimes.experiment import (  # noqa: E402
    config_problems,
    doc_config,
    listing_problems,
    prepare_doc_dir,
    render_task,
    secret_names,
)

DEFAULT_EXP_ROOT = Path.home() / "parserx-exp" / "phase2"
CONFIG = REPO_ROOT / "configs" / "regression_v2.yaml"  # the fixed-sequence v2 config; tools read the same sections
ENV_FILE = REPO_ROOT / ".env"
TEMPLATE = REPO_ROOT / "parserx" / "runtimes" / "agent_task.md"
GT_DIRS = (REPO_ROOT / "ground_truth", REPO_ROOT / "ground_truth_public")
MODEL, EFFORT = "gpt-6-sol", "high"  # Q35: explicit on every run
PROMPT = "Follow the task in AGENTS.md in the current directory. Work only with the files in this directory."
LARGE_PAGES, SMALL_MIN, LARGE_MIN = 100, 30, 90  # Q39


def _utc() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _sha256(path: Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _run(argv: list[str], **kw) -> subprocess.CompletedProcess:
    return subprocess.run(argv, check=True, text=True, capture_output=True, **kw)


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
        f'exec "{python}" -m parserx.runtimes.px --env-file "{ENV_FILE}" --config "$doc/parserx.yaml" -- "$@"\n',
        encoding="utf-8")
    px_run.chmod(0o755)
    skills = json.loads(_run([str(python), "-m", "parserx.runtimes.experiment", "skills"]).stdout)
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
        "services": {"vlm": config.services.vlm.model, "llm": config.services.llm.model,
                     "ocr": config.builders.ocr.model},
        "config": {"file": str(CONFIG.relative_to(REPO_ROOT)), "fingerprint": config_fingerprint(config)},
        "service_env": secret_names(load_raw_config(CONFIG)),  # supplied by px-run from the repository .env
        "skills": {name: hashlib.sha256(text.encode("utf-8")).hexdigest() for name, text in skills.items()},
        "template_sha256": _sha256(TEMPLATE),
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


def _find_input(doc: str, explicit: Path | None) -> tuple[Path, Path | None]:
    """(input file, expected.md or None)."""
    for gt in GT_DIRS:
        inputs = sorted((gt / doc).glob("input.*")) if (gt / doc).is_dir() else []
        if inputs:
            return (explicit or inputs[0]), _expected(doc)
    if explicit is None:
        sys.exit(f"{doc} has no ground-truth directory: give --input")
    return explicit, None


def _pages(path: Path) -> int | None:
    if path.suffix.lower() == ".pdf":
        import fitz

        with fitz.open(path) as pdf:
            return pdf.page_count
    try:
        with zipfile.ZipFile(path) as zf:
            app = zf.read("docProps/app.xml").decode("utf-8", "replace")
        start = app.find("<Pages>")
        return int(app[start + 7: app.find("</Pages>")]) if start >= 0 else None
    except (KeyError, ValueError, zipfile.BadZipFile):
        return None


def _void(doc_dir: Path) -> Path:
    n = 1
    while doc_dir.with_name(f"{doc_dir.name}.void{n}").exists():
        n += 1
    target = doc_dir.with_name(f"{doc_dir.name}.void{n}")
    doc_dir.rename(target)
    return target


def cmd_run(args) -> int:
    snapshot = _snapshot(args)
    codex_version = _codex_version()
    if codex_version != snapshot["codex_version"]:
        sys.exit(f"Codex is {codex_version}, the round started with {snapshot['codex_version']}: "
                 "a round never mixes versions (start a new round)")
    if (args.model, args.effort) != (snapshot["agent"]["model"], snapshot["agent"]["reasoning_effort"]):
        sys.exit(f"the round uses {snapshot['agent']}: a round never mixes models (guide §14 Q35)")
    input_path, expected = _find_input(args.doc, args.input)
    round_dir = _round_dir(args)
    doc_dir = round_dir / args.doc
    interventions = list(args.intervention or [])
    if doc_dir.exists():
        if not args.rerun:
            sys.exit(f"{doc_dir} exists: an earlier run; use --rerun REASON to void it and start over")
        voided = _void(doc_dir)
        interventions.append(f"rerun: {args.rerun} (earlier run kept as {voided.name})")

    pages = _pages(input_path)
    minutes = args.timeout_min or (LARGE_MIN if pages and pages > LARGE_PAGES else SMALL_MIN)
    raw = load_raw_config(CONFIG)
    config = doc_config(raw, doc_dir)
    template = TEMPLATE.read_text(encoding="utf-8")
    agents_md = render_task(template, round_name=snapshot["rules"],
                            values={"input_name": f"input{input_path.suffix.lower()}", "budget_minutes": minutes})
    skills_json = _run([str(_toolkit(args) / "venv" / "bin" / "python"), "-m", "parserx.runtimes.experiment",
                        "skills"]).stdout
    px_text = ("#!/bin/sh\n# The ParserX tools of this experiment: ./px --help\n"
               f'exec "{_toolkit(args) / "px-run"}" "$(cd "$(dirname "$0")" && pwd)" "$@"\n')
    files = prepare_doc_dir(doc_dir, input_path=input_path, config=config, px_text=px_text, agents_md=agents_md,
                            skills=json.loads(skills_json))
    ParserXConfig.model_validate(_resolve_env_vars(config))  # the file keeps ${VAR}; resolved, it must be valid
    secrets = [v for k, v in dotenv_values(ENV_FILE).items() if v and _SECRET_NAME.search(k)]
    problems = listing_problems(doc_dir) + config_problems(
        (doc_dir / "parserx.yaml").read_text(encoding="utf-8"), forbidden=[REPO_ROOT], secret_values=secrets)
    if problems:
        sys.exit(f"experiment directory not clean: {problems}")

    runs = round_dir / "_runs" / args.doc
    if runs.exists():
        shutil.rmtree(runs)
    runs.mkdir(parents=True)
    argv = exec_command(model=args.model, effort=args.effort, doc_dir=doc_dir,
                        last_message=runs / "last_message.md", prompt=PROMPT)
    print(f"[{_utc()}] {args.doc}: {pages} pages, deadline {minutes} min, codex {codex_version}, "
          f"{args.model} ({args.effort})", flush=True)
    # Codex and the agent's shell get no service settings: only px-run loads them, inside the tool process.
    dotenv_names = set(dotenv_values(ENV_FILE)) | set(dotenv_values(Path.home() / ".config" / "parserx" / ".env"))
    env = {k: v for k, v in os.environ.items() if k not in dotenv_names and not _SECRET_NAME.search(k)}
    started, t0 = _utc(), time.monotonic()
    timed_out = False
    with open(runs / "events.jsonl", "wb") as out, open(runs / "stderr.log", "wb") as err:
        proc = subprocess.Popen(argv, cwd=doc_dir, stdin=subprocess.DEVNULL, stdout=out, stderr=err, env=env,
                                start_new_session=True)
        try:
            exit_code = proc.wait(timeout=minutes * 60)
        except subprocess.TimeoutExpired:
            timed_out = True
            os.killpg(proc.pid, signal.SIGTERM)
            try:
                proc.wait(timeout=15)
            except subprocess.TimeoutExpired:
                os.killpg(proc.pid, signal.SIGKILL)
                proc.wait()
            exit_code = proc.returncode
    ended, wall = _utc(), time.monotonic() - t0
    shutil.move(str(runs), str(doc_dir / "run"))
    meta = {
        "round": args.round, "doc": args.doc, "input": input_path.name, "input_sha256": _sha256(input_path),
        "pages": pages, "deadline_min": minutes, "started": started, "ended": ended, "wall_s": round(wall, 1),
        "exit_code": exit_code, "timed_out": timed_out, "command": argv[:-1] + ["<prompt>"], "prompt": PROMPT,
        "files": files, "interventions": interventions, "has_ground_truth": expected is not None,
    }
    (doc_dir / "run" / "meta.json").write_text(json.dumps(meta, indent=2, ensure_ascii=False) + "\n")
    print(f"[{ended}] codex exited {exit_code}{' (deadline)' if timed_out else ''} after {wall:.0f} s", flush=True)
    return _verify(args, doc_dir, expected)


# ── verify ──────────────────────────────────────────────────────────────


def _verify(args, doc_dir: Path, expected: Path | None) -> int:
    snapshot = _snapshot(args)
    meta = json.loads((doc_dir / "run" / "meta.json").read_text())
    python = _toolkit(args) / "venv" / "bin" / "python"
    verification = json.loads(_run([str(python), "-m", "parserx.runtimes.experiment", "verify",
                                    "--doc-dir", str(doc_dir)]).stdout)
    events, bad_lines = read_events(doc_dir / "run" / "events.jsonl")
    usage = usage_from_events(events)
    audit = audit_events(events, doc_dir=doc_dir, home=Path.home(),
                         forbidden={"repository": REPO_ROOT, "experiment root": args.exp_root.resolve()})
    scratch = sorted(
        str(p.relative_to(doc_dir)) for p in doc_dir.rglob("*")
        if p.is_file() and p.relative_to(doc_dir).parts[0] not in ("ws", "out", "run", ".parserx_cache")
        and str(p.relative_to(doc_dir)) not in meta["files"]
    )
    scores = None
    export = verification["export"]
    if expected is not None and export["exported"]:
        markdown = Path(export["markdown"]).read_text(encoding="utf-8")
        result = evaluate_markdown(markdown, expected.read_text(encoding="utf-8"), name=args.doc)
        scores = {"metric_version": METRIC_VERSION, **document_scores(result)}
        for key in ("requests", "ocr_pages", "attempts", "cache_hits", "wall_time_seconds", "cost_usd"):
            scores.pop(key)  # cost comes from the tools' own accounting below
    record = {
        "conditions": {
            "round": args.round, "rules": snapshot["rules"], "doc": args.doc, "toolkit_commit": snapshot["commit"],
            "config_fingerprint": snapshot["config"]["fingerprint"], "skills": snapshot["skills"],
            "agents_md_sha256": meta["files"]["AGENTS.md"], "codex_version": snapshot["codex_version"],
            "agent_model": snapshot["agent"]["model"], "agent_reasoning_effort": snapshot["agent"]["reasoning_effort"],
            "service_models": snapshot["services"], "started": meta["started"], "ended": meta["ended"],
            "deadline_min": meta["deadline_min"], "exit_code": meta["exit_code"], "timed_out": meta["timed_out"],
            "input": meta["input"], "input_sha256": meta["input_sha256"], "pages": meta["pages"],
        },
        "agent": {**usage.model_dump(), "wall_s": meta["wall_s"], "event_lines_not_json": bad_lines},
        "tools": verification["tools"],
        "result": {"check": verification["check"], "export": export, "scratch_files": scratch},
        "scores": scores,
        "hygiene": {"valid": audit.ok and verification["integrity"]["ok"], "audit": audit.model_dump(),
                    "integrity": verification["integrity"]},
        "interventions": meta["interventions"],
    }
    (doc_dir / "run" / "record.json").write_text(json.dumps(record, indent=2, ensure_ascii=False) + "\n")
    _print_record(record)
    return 0 if record["hygiene"]["valid"] else 1


def cmd_verify(args) -> int:
    doc_dir = _round_dir(args) / args.doc
    if not (doc_dir / "run" / "meta.json").is_file():
        sys.exit(f"no finished run in {doc_dir}")
    return _verify(args, doc_dir, _expected(args.doc))


def _print_record(record: dict) -> None:
    c, a, t, r = record["conditions"], record["agent"], record["tools"] or {}, record["result"]
    hygiene = record["hygiene"]
    print(f"{c['doc']}: valid={hygiene['valid']} exported={r['export']['exported']} "
          f"current={r['export']['current']} status={(r['check'] or {}).get('document_status')}")
    print(f"  agent  {a['turns']} turns, {a['commands']} commands ({a['failed_commands']} failed), "
          f"tokens in {a['input_tokens']} (cached {a['cached_input_tokens']}) out {a['output_tokens']}, "
          f"{a['wall_s']} s")
    print(f"  tools  calls {t.get('calls')} failures {t.get('failures')} requests {t.get('requests')} "
          f"cost {t.get('cost_usd')}")
    if record["scores"]:
        s = record["scores"]
        print(f"  scores char_f1 {s['char_f1']} table_f1 {s['table_cell_f1']} heading_f1 {s['heading_f1']} "
              f"order_tau {s['order_tau']} miss/extra {s['missing_tables']}/{s['extra_tables']}")
    for hit in hygiene["audit"]["hits"]:
        print(f"  AUDIT  {hit['kind']}: {hit['detail']} [{hit['item']}]")
    for problem in hygiene["integrity"]["problems"]:
        print(f"  INTEGRITY {problem}")
    for note in hygiene["audit"]["notes"]:
        print(f"  note   {note['detail']} [{note['item']}]")


# ── summary ─────────────────────────────────────────────────────────────


def cmd_summary(args) -> int:
    rows = []
    for path in sorted(_round_dir(args).glob("*/run/record.json")):
        rec = json.loads(path.read_text())
        c, a, t, r, s = rec["conditions"], rec["agent"], rec["tools"] or {}, rec["result"], rec["scores"] or {}

        def f(v, d=3):
            return "—" if v is None else (f"{v:.{d}f}" if isinstance(v, float) else str(v))

        rows.append("| " + " | ".join([
            c["doc"], "✅" if rec["hygiene"]["valid"] else "❌", f((r["check"] or {}).get("document_status")),
            "✅" if r["export"]["exported"] and r["export"]["current"] else ("stale" if r["export"]["exported"] else "—"),
            f"{a['wall_s']:.0f} s" + (" ⏱" if c["timed_out"] else ""), str(a["commands"]),
            f"{a['input_tokens']:,}/{a['cached_input_tokens']:,}/{a['output_tokens']:,}",
            json.dumps(t.get("requests", {})), f(t.get("cost_usd"), 4),
            f(s.get("char_f1")), f(s.get("table_cell_f1")), f(s.get("heading_f1")),
            str(len(rec["interventions"])),
        ]) + " |")
    print("| 文档 | 卫生 | 状态 | 导出 | 耗时 | 命令 | Agent token 输入/缓存/输出 | 工具请求 | 工具费用 | char_f1 | "
          "表格 F1 | heading_f1 | 人工介入 |")
    print("|---|---|---|---|---|---|---|---|---|---|---|---|---|")
    print("\n".join(rows))
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--exp-root", type=Path, default=DEFAULT_EXP_ROOT)
    sub = parser.add_subparsers(dest="command", required=True)
    snap = sub.add_parser("snapshot")
    snap.add_argument("--round", required=True)
    snap.add_argument("--rules", required=True, choices=("r1", "r2"),
                      help="round rules: r1 exploration, r2 close to acceptance (plan §2.3)")
    snap.add_argument("--allow-dirty", action="store_true")
    snap.add_argument("--model", default=MODEL)
    snap.add_argument("--effort", default=EFFORT)
    run = sub.add_parser("run")
    run.add_argument("--round", required=True)
    run.add_argument("--doc", required=True)
    run.add_argument("--input", type=Path, help="input file for a document without ground truth")
    run.add_argument("--timeout-min", type=int, help="override the Q39 deadline")
    run.add_argument("--rerun", metavar="REASON", help="void the existing run of this document and start over")
    run.add_argument("--intervention", action="append", help="record a human intervention")
    run.add_argument("--model", default=MODEL)
    run.add_argument("--effort", default=EFFORT)
    ver = sub.add_parser("verify")
    ver.add_argument("--round", required=True)
    ver.add_argument("--doc", required=True)
    ver.add_argument("--input", type=Path)
    summ = sub.add_parser("summary")
    summ.add_argument("--round", required=True)
    args = parser.parse_args()
    return {"snapshot": cmd_snapshot, "run": cmd_run, "verify": cmd_verify, "summary": cmd_summary}[args.command](args)


if __name__ == "__main__":
    sys.exit(main())
