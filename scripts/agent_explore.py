#!/usr/bin/env python3
"""Agent exploration harness (docs/v2_phase2_plan.md P2-1, §2.2–§2.5).

    uv run python scripts/agent_explore.py snapshot --round r1 --rules r1
    uv run python scripts/agent_explore.py run      --round r1 --doc a,b [--jobs 1] (or --all; --input PATH)
    uv run python scripts/agent_explore.py verify   --round r1 --doc text_table01
    uv run python scripts/agent_explore.py control  --round r1 --doc ocr01      (or --all)
    uv run python scripts/agent_explore.py summary  --round r1 [--control]
    uv run python scripts/agent_explore.py parse    --round p4a --doc a,b [--runtime hybrid] [--no-agent]

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
- ``control``: the fixed-sequence runtime v2 on the same snapshot, input and
  config, in ``<round>/_control/<doc>/`` (plan P2-3); verified and scored the
  same way → ``record.json`` there.
- ``summary``: one table over a round's records, with the v1 frozen baseline.
- ``parse`` (plan P4-1, P4-2): the product command ``parserx parse`` of the round's snapshot on one document, in a
  fresh directory outside the repository (keys from the repository ``.env`` reach the product process only; it
  keeps them from the agent itself); then the hygiene audit of the agent's events, the workspace integrity and the
  scores → ``<round>/_parse/<doc>/record.json``.  ``--no-agent`` hides Codex (PATH without it): the fallback.

Documents are named as in ``configs/phase2_explore.yaml``: ground-truth
documents by name, the others from the legacy sample directory
(``--sample-dir`` or ``PARSERX_SAMPLE_DOCS``).

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
from parserx.runtimes.codex import (  # noqa: E402
    audit_events,
    exec_command,
    read_events,
    timing_from_events,
    usage_from_events,
)
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
GT_DIRS = (REPO_ROOT / "ground_truth", REPO_ROOT / "ground_truth_public", REPO_ROOT / "ground_truth_unseen")
EXPLORE_SET = REPO_ROOT / "configs" / "phase2_explore.yaml"
V1_BASELINE = REPO_ROOT / "eval_runs" / "2026-09-23_p0_v1_gpt-6-luna.rescored-2.1.json"
INPUT_ORDER = (".pdf", ".docx", ".doc")  # as the evaluation runner picks them
MODEL, EFFORT = "gpt-6-sol", "medium"  # Q35 (medium since P2-5): explicit on every run
PROMPT = "Follow the task in AGENTS.md in the current directory. Work only with the files in this directory."
LARGE_PAGES, SMALL_MIN, LARGE_MIN = 100, 30, 90  # Q39


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
    if (args.model, args.effort) != (snapshot["agent"]["model"], snapshot["agent"]["reasoning_effort"]):
        sys.exit(f"the round uses {snapshot['agent']}: a round never mixes models (guide §14 Q35)")
    docs = [d["name"] for d in _explore_set()] if args.all else [d for spec in args.doc for d in spec.split(",")]
    if len(docs) > 1 and args.input:
        sys.exit("--input names one document")
    for doc in docs:  # refuse before anything runs
        if (_round_dir(args) / doc).exists() and not args.rerun:
            sys.exit(f"{_round_dir(args) / doc} exists: an earlier run; use --rerun REASON to void it")
    if args.jobs <= 1 or len(docs) == 1:
        return max(_run_one(args, snapshot, doc) for doc in docs)
    from concurrent.futures import ThreadPoolExecutor

    with ThreadPoolExecutor(max_workers=args.jobs) as pool:  # one Codex session per document
        return max(pool.map(lambda doc: _run_one(args, snapshot, doc), docs))


def _run_one(args, snapshot: dict, doc: str) -> int:
    codex_version = _codex_version()  # any current or newer version (Q41); recorded per run
    input_path, expected = _find_input(args, doc)
    round_dir = _round_dir(args)
    doc_dir = round_dir / doc
    interventions = list(args.intervention or [])
    if doc_dir.exists():
        voided = _void(doc_dir)  # --rerun given (checked in cmd_run)
        interventions.append(f"rerun: {args.rerun} (earlier run kept as {voided.name})")

    pages = _pages(input_path)
    minutes = args.timeout_min or (LARGE_MIN if pages and pages > LARGE_PAGES else SMALL_MIN)
    raw = load_raw_config(CONFIG)
    config = doc_config(raw, doc_dir)
    # the round's own template (shipped in the snapshot), so every run of a round gets the same task
    template = _run([str(_toolkit(args) / "venv" / "bin" / "python"), "-P", "-m", "parserx.runtimes.experiment",
                     "template"]).stdout
    skills_json = _run([str(_toolkit(args) / "venv" / "bin" / "python"), "-P", "-m", "parserx.runtimes.experiment",
                        "skills"]).stdout
    skills = json.loads(skills_json)  # the snapshot's skills, also inlined in the task (one step fewer)
    agents_md = render_task(template, round_name=snapshot["rules"], options={f"vision_{args.vision}", "experiment"},
                            values={"input_name": f"input{input_path.suffix.lower()}", "budget_minutes": minutes,
                                    "skills": "\n\n".join(skills[name].strip() for name in
                                                             ("transcription", "figure", "structure"))})
    px_text = ("#!/bin/sh\n# The ParserX tools of this experiment: ./px --help\n"
               f'exec "{_toolkit(args) / "px-run"}" "$(cd "$(dirname "$0")" && pwd)" "$@"\n')
    files = prepare_doc_dir(doc_dir, input_path=input_path, config=config, px_text=px_text, agents_md=agents_md,
                            skills=skills)
    ParserXConfig.model_validate(_resolve_env_vars(config))  # the file keeps ${VAR}; resolved, it must be valid
    secrets = [v for k, v in dotenv_values(ENV_FILE).items() if v and _SECRET_NAME.search(k)]
    problems = listing_problems(doc_dir) + config_problems(
        (doc_dir / "parserx.yaml").read_text(encoding="utf-8"), forbidden=[REPO_ROOT], secret_values=secrets)
    if problems:
        print(f"{doc}: experiment directory not clean: {problems}", file=sys.stderr)
        return 2

    runs = round_dir / "_runs" / doc
    if runs.exists():
        shutil.rmtree(runs)
    runs.mkdir(parents=True)
    argv = exec_command(model=args.model, effort=args.effort, doc_dir=doc_dir,
                        last_message=runs / "last_message.md", prompt=PROMPT, vision=args.vision)
    print(f"[{_utc()}] {doc}: {pages} pages, deadline {minutes} min, codex {codex_version}, "
          f"{args.model} ({args.effort}), vision {args.vision}", flush=True)
    # Codex and the agent's shell get no service settings: only px-run loads them, inside the tool process.
    dotenv_names = set(dotenv_values(ENV_FILE)) | set(dotenv_values(Path.home() / ".config" / "parserx" / ".env"))
    env = {k: v for k, v in os.environ.items() if k not in dotenv_names and not _SECRET_NAME.search(k)}
    env["RUST_LOG"] = "codex_core=info"  # stalls, stream retries and reconnects go to run/stderr.log (P2-5)
    started, t0 = _utc(), time.monotonic()
    timed_out = False
    with open(runs / "events.jsonl", "wb") as out, open(runs / "event_times.txt", "w") as times, \
            open(runs / "stderr.log", "wb") as err:
        proc = subprocess.Popen(argv, cwd=doc_dir, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=err,
                                env=env, start_new_session=True)

        def pump() -> None:  # every event line with the second it arrived (P2-5: per-step timing)
            for line in proc.stdout:
                out.write(line)
                out.flush()
                times.write(f"{time.monotonic() - t0:.3f}\n")
                times.flush()

        import threading

        reader = threading.Thread(target=pump, daemon=True)
        reader.start()
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
        reader.join(timeout=30)
    ended, wall = _utc(), time.monotonic() - t0
    shutil.move(str(runs), str(doc_dir / "run"))
    meta = {
        "round": args.round, "doc": doc, "codex_version": codex_version, "vision": args.vision,
        "input": input_path.name, "input_sha256": _sha256(input_path),
        "pages": pages, "deadline_min": minutes, "started": started, "ended": ended, "wall_s": round(wall, 1),
        "exit_code": exit_code, "timed_out": timed_out, "command": argv[:-1] + ["<prompt>"], "prompt": PROMPT,
        "files": files, "interventions": interventions, "has_ground_truth": expected is not None,
    }
    (doc_dir / "run" / "meta.json").write_text(json.dumps(meta, indent=2, ensure_ascii=False) + "\n")
    print(f"[{ended}] {doc}: codex exited {exit_code}{' (deadline)' if timed_out else ''} after {wall:.0f} s",
          flush=True)
    return _verify(args, doc_dir, expected, doc)


# ── verify ──────────────────────────────────────────────────────────────


def _timed_events(run_dir: Path) -> tuple[list[dict], list[float]] | None:
    """Events with their arrival times (runs from P2-5 on); None for runs without event_times.txt."""
    path = run_dir / "event_times.txt"
    if not path.is_file():
        return None
    events, times = [], []
    lines = (run_dir / "events.jsonl").read_text(encoding="utf-8", errors="replace").splitlines()
    for line, t in zip(lines, path.read_text().split()):
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(event, dict):
            events.append(event)
            times.append(float(t))
    return events, times


def _verify(args, doc_dir: Path, expected: Path | None, doc: str | None = None) -> int:
    doc = doc or args.doc
    snapshot = _snapshot(args)
    meta = json.loads((doc_dir / "run" / "meta.json").read_text())
    verification = _snapshot_verify(args, doc_dir)
    events, bad_lines = read_events(doc_dir / "run" / "events.jsonl")
    usage = usage_from_events(events)
    timed = _timed_events(doc_dir / "run")
    timing = timing_from_events(*timed) if timed is not None else None
    audit = audit_events(events, doc_dir=doc_dir, home=Path.home(),
                         forbidden=_forbidden(args))
    scratch = sorted(
        str(p.relative_to(doc_dir)) for p in doc_dir.rglob("*")
        if p.is_file() and p.relative_to(doc_dir).parts[0] not in ("ws", "out", "run", ".parserx_cache")
        and str(p.relative_to(doc_dir)) not in meta["files"]
    )
    export = verification["export"]
    scores = _scores(doc, expected, export)
    record = {
        "conditions": {
            "round": args.round, "rules": snapshot["rules"], "doc": doc, "toolkit_commit": snapshot["commit"],
            "config_fingerprint": snapshot["config"]["fingerprint"], "skills": snapshot["skills"],
            "agents_md_sha256": meta["files"]["AGENTS.md"],
            "codex_version": meta.get("codex_version", snapshot["codex_version"]),
            "agent_model": snapshot["agent"]["model"], "agent_reasoning_effort": snapshot["agent"]["reasoning_effort"],
            "vision": meta.get("vision", "agent"),
            "service_models": snapshot["services"], "started": meta["started"], "ended": meta["ended"],
            "deadline_min": meta["deadline_min"], "exit_code": meta["exit_code"], "timed_out": meta["timed_out"],
            "input": meta["input"], "input_sha256": meta["input_sha256"], "pages": meta["pages"],
        },
        "agent": {**usage.model_dump(), "wall_s": meta["wall_s"], "event_lines_not_json": bad_lines,
                  "timing": timing.model_dump() if timing is not None else None,
                  "usd_at_list_price": _list_price(snapshot["agent"]["model"], usage)},
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


def _snapshot_verify(args, doc_dir: Path) -> dict:
    python = _toolkit(args) / "venv" / "bin" / "python"
    return json.loads(_run([str(python), "-P", "-m", "parserx.runtimes.experiment", "verify",
                            "--doc-dir", str(doc_dir)]).stdout)


def _list_price(model: str, usage) -> float | None:
    """The agent's tokens at the model's API list price (it runs on the Codex account; for comparison only)."""
    price = load_config(CONFIG).scheduling.prices.get(model)
    if price is None:
        return None
    fresh = usage.input_tokens - usage.cached_input_tokens
    return round((fresh * price.input + usage.cached_input_tokens * price.cached_input
                  + usage.output_tokens * price.output) / 1e6, 4)


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
    if a.get("timing"):
        tm = a["timing"]
        print(f"  time   model {tm['model_s']} s in {tm['steps']} steps (median {tm['median_step_s']} s, "
              f"longest {tm['longest_step_s']} s), commands {tm['command_s']} s"
              + (f" ({tm['yielded']} yielded before finishing)" if tm.get("yielded") else ""))
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


# ── control ─────────────────────────────────────────────────────────────


def cmd_control(args) -> int:
    snapshot = _snapshot(args)
    docs = [d["name"] for d in _explore_set()] if args.all else [args.doc]
    failed = 0
    for doc in docs:
        failed += _control(args, snapshot, doc)
    return 1 if failed else 0


def _control(args, snapshot: dict, doc: str) -> int:
    import yaml

    input_path, expected = _find_input(args, doc)
    doc_dir = _round_dir(args) / "_control" / doc
    if (doc_dir / "record.json").is_file() and not args.rerun:
        print(f"{doc}: control already recorded (use --rerun to repeat it)")
        return 0
    if doc_dir.exists():
        shutil.rmtree(doc_dir)
    doc_dir.mkdir(parents=True)
    shutil.copyfile(input_path, doc_dir / f"input{input_path.suffix.lower()}")
    (doc_dir / "parserx.yaml").write_text(
        yaml.safe_dump(doc_config(load_raw_config(CONFIG), doc_dir), allow_unicode=True, sort_keys=False))
    python = _toolkit(args) / "venv" / "bin" / "python"
    print(f"[{_utc()}] control {doc} ({input_path.name}, {_pages(input_path)} pages) …", flush=True)
    started, t0 = _utc(), time.monotonic()
    try:
        proc = subprocess.run([str(python), "-P", "-m", "parserx.runtimes.experiment", "control", "--doc-dir",
                               str(doc_dir), "--env-file", str(ENV_FILE)], capture_output=True, text=True,
                              timeout=args.timeout_min * 60)
        (doc_dir / "control.log").write_text(proc.stderr, encoding="utf-8")
        lines = proc.stdout.strip().splitlines()
        outcome = json.loads(lines[-1]) if proc.returncode == 0 and lines else {
            "status": None, "error": f"exit {proc.returncode}: {proc.stderr[-1500:]}"}
    except subprocess.TimeoutExpired:
        outcome = {"status": None, "error": f"deadline of {args.timeout_min} min"}
    wall = round(time.monotonic() - t0, 1)
    verification = _snapshot_verify(args, doc_dir)
    record = {
        "conditions": {"round": args.round, "doc": doc, "runtime": "pipeline v2 (fixed sequence)",
                       "toolkit_commit": snapshot["commit"], "config_fingerprint": snapshot["config"]["fingerprint"],
                       "service_models": snapshot["services"], "started": started, "ended": _utc(),
                       "input": input_path.name, "input_sha256": _sha256(input_path), "pages": _pages(input_path)},
        "outcome": {**outcome, "wall_s": wall},
        "tools": verification["tools"],
        "result": {"check": verification["check"], "export": verification["export"]},
        "scores": _scores(doc, expected, verification["export"]),
        "integrity": verification["integrity"],
    }
    (doc_dir / "record.json").write_text(json.dumps(record, indent=2, ensure_ascii=False) + "\n")
    t, sc = record["tools"] or {}, record["scores"] or {}
    print(f"  status {outcome.get('status')} error {outcome.get('error')} {wall} s requests {t.get('requests')} "
          f"cost {t.get('cost_usd')} char_f1 {sc.get('char_f1')} table_f1 {sc.get('table_cell_f1')} "
          f"heading_f1 {sc.get('heading_f1')}", flush=True)
    return 0 if outcome.get("status") else 1


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
    (doc_dir / "parserx.yaml").write_text(
        yaml.safe_dump(doc_config(load_raw_config(CONFIG), doc_dir), allow_unicode=True, sort_keys=False))
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
    record = {"doc": doc, "runtime_mode": args.runtime, "no_agent": args.no_agent, "exit_code": proc.returncode,
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


def _fmt(value, digits: int = 3) -> str:
    if value is None:
        return "—"
    return f"{value:.{digits}f}" if isinstance(value, float) else str(value)


def cmd_summary(args) -> int:
    v1 = json.loads(V1_BASELINE.read_text())["documents"] if V1_BASELINE.is_file() else {}
    order = [d["name"] for d in _explore_set()]
    pattern = "_control/*/record.json" if args.control else "*/run/record.json"
    # voided runs (<doc>.voidN, kept after --rerun) are not results
    records = [json.loads(p.read_text()) for p in _round_dir(args).glob(pattern) if ".void" not in p.parts[-3]]
    records.sort(key=lambda r: (order.index(r["conditions"]["doc"]) if r["conditions"]["doc"] in order else 99,
                                r["conditions"]["doc"]))

    def scores(rec) -> str:
        s, base = rec["scores"] or {}, v1.get(rec["conditions"]["doc"], {})
        return " | ".join(f"{_fmt(s.get(k))} ({_fmt(base.get(k))})" for k in ("char_f1", "table_cell_f1", "heading_f1"))

    if args.control:
        print("| 文档 | 状态 | 耗时 | 工具请求 | 费用 | char_f1（v1） | 表格 F1（v1） | heading_f1（v1） |")
        print("|---|---|---|---|---|---|---|---|")
        for rec in records:
            t = rec["tools"] or {}
            print(f"| {rec['conditions']['doc']} | {_fmt(rec['outcome'].get('status') or rec['outcome'].get('error'))} "
                  f"| {rec['outcome']['wall_s']:.0f} s | {json.dumps(t.get('requests', {}))} | {_fmt(t.get('cost_usd'), 4)} "
                  f"| {scores(rec)} |")
        return 0
    print("| 文档 | 看图 | 卫生 | 状态 | 导出 | 耗时 | 模型步骤 | 命令 | Agent token 输入/缓存/输出 | Agent 标价 "
          "| 工具请求 | 工具费用 | char_f1（v1） | 表格 F1（v1） | heading_f1（v1） | 人工介入 |")
    print("|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|")
    for rec in records:
        c, a, t, r = rec["conditions"], rec["agent"], rec["tools"] or {}, rec["result"]
        export = "✅" if r["export"]["exported"] and r["export"]["current"] else (
            "旧" if r["export"]["exported"] else "—")
        tm = a.get("timing") or {}
        steps = f"{tm['steps']} 步 / {tm['model_s']:.0f} s" if tm else "—"
        print(f"| {c['doc']} | {c.get('vision', 'agent')} | {'✅' if rec['hygiene']['valid'] else '❌'} "
              f"| {_fmt((r['check'] or {}).get('document_status'))} "
              f"| {export} | {a['wall_s']:.0f} s{' ⏱' if c['timed_out'] else ''} | {steps} | {a['commands']} "
              f"| {a['input_tokens']:,}/{a['cached_input_tokens']:,}/{a['output_tokens']:,} "
              f"| {_fmt(a.get('usd_at_list_price'), 2)} | {json.dumps(t.get('requests', {}))} | {_fmt(t.get('cost_usd'), 4)} "
              f"| {scores(rec)} | {len(rec['interventions'])} |")
    return 0


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
    run = sub.add_parser("run")
    run.add_argument("--round", required=True)
    which_run = run.add_mutually_exclusive_group(required=True)
    which_run.add_argument("--doc", action="append", help="document name(s), repeatable or comma-separated")
    which_run.add_argument("--all", action="store_true", help="every document of the exploration set")
    run.add_argument("--jobs", type=int, default=1, help="documents run at the same time (default 1)")
    run.add_argument("--vision", choices=("agent", "tool"), default="tool",  # Q47
                     help="agent: the agent opens images itself; tool: only through ask_image (its viewing is off)")
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
    ctl = sub.add_parser("control")
    ctl.add_argument("--round", required=True)
    which = ctl.add_mutually_exclusive_group(required=True)
    which.add_argument("--doc")
    which.add_argument("--all", action="store_true", help="every document of the exploration set")
    ctl.add_argument("--rerun", action="store_true", help="repeat a recorded control")
    ctl.add_argument("--timeout-min", type=int, default=120)
    prs = sub.add_parser("parse", help="the product command parserx parse of the snapshot, audited")
    prs.add_argument("--round", required=True)
    prs.add_argument("--doc", action="append", required=True, help="document name(s), comma-separated")
    prs.add_argument("--input", type=Path)
    prs.add_argument("--runtime", choices=("hybrid", "fixed"), default="hybrid")
    prs.add_argument("--no-agent", action="store_true", help="Codex not on PATH: the fallback")
    prs.add_argument("--lang", choices=("zh", "en"), default="zh")
    prs.add_argument("--tag", help="suffix of the run directory (several runs of one document)")
    prs.add_argument("--rerun", action="store_true")
    prs.add_argument("--timeout-min", type=int, default=120)
    summ = sub.add_parser("summary")
    summ.add_argument("--round", required=True)
    summ.add_argument("--control", action="store_true", help="the fixed-sequence controls instead of agent runs")
    args = parser.parse_args()
    commands = {"snapshot": cmd_snapshot, "run": cmd_run, "verify": cmd_verify, "control": cmd_control,
                "parse": cmd_parse,
                "summary": cmd_summary}
    return commands[args.command](args)


if __name__ == "__main__":
    sys.exit(main())
