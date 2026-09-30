"""C1: the hybrid runtime's agent on V's drafts (common plan §6.3, execution plan §7).

Run with a tool snapshot's Python (``scripts/agent_explore.py snapshot``), so the agent's ``px`` is the snapshot's
frozen tools, not the repository:

    <exp-root>/<round>/_toolkit/venv/bin/python -P scripts/vision_first/c1_run.py \\
        --v-run eval_runs/2026-09-30_v6_vision_first --configuration luna-medium-r1 --doc paper_chn01 \\
        --vision agent --effort medium --tag r1 --out <exp-root>/<round>/_c1

Per run: V's workspace of the document (``<v-run>/work/<configuration>/<document>``: the pipeline with the service
model's page allocations) is copied into a fresh agent directory; the draft is exported as the result before the
agent (the fixed-sequence package of the hybrid runtime); then the product's agent stage (``runtimes.hybrid.
_agent_stage``: the agent's directory with ``px``, the skills and the task, Codex with the model, effort and vision
mode asked, the calls followed, the workspace verified, the package exported) runs as ``parserx parse`` would run it
after its own pipeline.  The record — runtime, agent record, token usage, the hygiene audit — goes to
``record.json``; scores are computed afterwards by the repository's evaluator (``c1_score.py``).
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]

from parserx.config.schema import apply_overrides, load_config  # noqa: E402  (the snapshot's parserx)
from parserx.runtimes.codex import audit_events, read_events, usage_from_events  # noqa: E402
from parserx.runtimes.hybrid import _agent_stage, _export, _summary, make_agent  # noqa: E402
from parserx.tools.context import ToolContext  # noqa: E402

MODEL = "gpt-6.1-sol"  # the user, 2026-09-30


def _claim_allocations(ws_dir: Path) -> None:
    """V's page allocations were committed by the experiment adapter without a call record (before 2026-09-30):
    claimed here by one, so the integrity check after the agent tells the agent's changes from V's step."""
    from parserx.workspace import Workspace
    from parserx.workspace.store import read_records

    ws = Workspace.open(ws_dir)
    records = read_records(ws.calls_path)
    claimed = {v for r in records if r.get("type") == "call" for v in r.get("txns", [])}
    ws._unclaimed = [r["version"] for r in records if r.get("type") == "txn" and r["version"] not in claimed
                     and str(r.get("actor", "")).startswith("tool:vision_first")]
    if ws._unclaimed:
        ws.log_call({"tool": "vision_first_allocate", "request": {},
                     "note": "V's allocations (experiment adapter), claimed when handed to the agent (C1)"})


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--v-run", type=Path, required=True)
    parser.add_argument("--configuration", required=True)
    parser.add_argument("--doc", required=True)
    parser.add_argument("--vision", choices=("agent", "tool"), required=True)
    parser.add_argument("--effort", choices=("medium", "high"), required=True)
    parser.add_argument("--tag", required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    v_run = args.v_run if args.v_run.is_absolute() else REPO_ROOT / args.v_run
    label = f"{args.doc}.{args.vision}.{args.effort}.{args.tag}"
    doc_dir = args.out.resolve() / label
    if doc_dir.exists():
        sys.exit(f"{doc_dir} exists")
    work = doc_dir / "work"
    agent_dir, out_dir = work / "agent", doc_dir / "out"
    ws_dir = agent_dir / "ws"
    agent_dir.mkdir(parents=True)
    shutil.copytree(v_run / "work" / args.configuration / args.doc, ws_dir)
    _claim_allocations(ws_dir)
    source = doc_dir / "input.pdf"
    shutil.copyfile(next(p for root in ("ground_truth", "ground_truth_public")
                         if (p := REPO_ROOT / root / args.doc / "input.pdf").exists()), source)
    config = apply_overrides(load_config(REPO_ROOT / "configs" / "regression.yaml"), [
        f"cache.dir={doc_dir / '.cache'}", "cache.mode=read_write", "runtime.mode=hybrid",
        "runtime.agent.engine=codex", f"runtime.agent.model={MODEL}", f"runtime.agent.effort={args.effort}",
        f"runtime.agent.vision={args.vision}"])
    fixed = work / "fixed"
    _export(ws_dir, fixed, args.doc, config, ToolContext)
    before = _summary(fixed, args.doc)
    agent = make_agent(config, ToolContext)
    started = time.monotonic()
    runtime, note, detail, record, _keep = _agent_stage(agent, agent_dir, ws_dir, work, out_dir, args.doc, config,
                                                        before, source, lambda event: None, ToolContext)
    if runtime != "hybrid:agent":  # the fixed-sequence result stands, as parserx parse installs it
        shutil.rmtree(out_dir, ignore_errors=True)
        shutil.copytree(fixed, out_dir)
    events_path = work / "agent_run" / "events.jsonl"
    usage = audit = None
    if events_path.is_file():
        events, _ = read_events(events_path)
        usage = usage_from_events(events).model_dump()
        audit = audit_events(events, doc_dir=agent_dir, home=Path.home(),
                             forbidden={"repository": REPO_ROOT, "V run": v_run}).model_dump()
    result = {"doc": args.doc, "configuration": args.configuration, "v_run": str(v_run), "vision": args.vision,
              "effort": args.effort, "model": MODEL, "tag": args.tag, "runtime": runtime, "note": note,
              "detail": detail, "seconds": round(time.monotonic() - started, 1),
              "before": {"open": before.review.open, "by_kind": before.review.by_kind},
              "agent": record.model_dump() if record else None, "usage": usage, "audit": audit,
              "markdown": str(out_dir / f"{args.doc}.md"), "summary": str(out_dir / f"{args.doc}.json")}
    (doc_dir / "record.json").write_text(json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"{label}: {runtime} ({note}) {result['seconds']} s, changes {record.changes if record else '-'}, "
          f"closed {record.closed if record else '-'}, open {before.review.open} → "
          f"{record.review_open_after if record else '-'}, audit {'ok' if not audit or audit.get('ok') else 'HITS'}",
          flush=True)
    return 0 if runtime == "hybrid:agent" else 1


if __name__ == "__main__":
    sys.exit(main())
