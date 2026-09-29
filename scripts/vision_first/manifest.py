"""Run manifests for the vision-first work (execution plan §4.2, §6 C0.7; E0).

A result is comparable only when everything it depends on is fixed and named.  ``run_manifest`` is the template
every P0 / V / C1 run writes next to its results:

- ``candidate``: commit, branch, dirty state and the hash of the uncommitted diff (or a patch file's hash);
- ``inputs`` and ``annotations``: SHA-256 per document;
- ``skills`` and ``prompts``: SHA-256 per file (what the service model and the agent were told);
- ``models``: per role (service, agent) the entry name, model, endpoint host, reasoning effort, structured output
  mode, output floor — exactly what was sent;
- ``tools``: what the agent was allowed to call;
- ``cache``: mode and directory (a replay is not a live run);
- ``evaluator``: metric version and the hash of its code;
- ``outputs``: SHA-256 per document of every output.

``baseline`` writes the manifest of the benchmark table (``eval_runs/bench``): per arm where its outputs come from
(a frozen run and its commit, a live run and the commit it ran at, an external tool and its settings) and the hash
of every output, so the table can be recomputed and checked.

    uv run python scripts/vision_first/manifest.py baseline [--bench eval_runs/bench]
"""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
from datetime import datetime
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

from parserx.eval.freeze import git_state  # noqa: E402
from parserx.eval.metrics import METRIC_VERSION  # noqa: E402


def sha256(path: Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def text_sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def evaluator() -> dict:
    """The metric version and one hash over the evaluation code (``parserx/eval``)."""
    digest = hashlib.sha256()
    for path in sorted((REPO_ROOT / "parserx" / "eval").glob("*.py")):
        digest.update(path.name.encode() + b"\0" + path.read_bytes())
    return {"metric_version": METRIC_VERSION, "code_sha256": digest.hexdigest()}


def documents(gt_dirs: list[Path], names: list[str]) -> dict[str, dict]:
    found = {}
    for name in names:
        doc = next((d / name for d in gt_dirs if (d / name / "expected.md").exists()), None)
        if doc is None:
            continue
        inputs = sorted(p for p in doc.glob("input.*") if p.suffix.lower() in (".pdf", ".docx", ".doc"))
        found[name] = {"gt_dir": doc.parent.name, "input": inputs[0].name if inputs else None,
                       "input_sha256": sha256(inputs[0]) if inputs else None,
                       "expected_sha256": sha256(doc / "expected.md")}
    return found


def run_manifest(*, run_id: str, purpose: str, gt_dirs: list[Path], docs: list[str], models: dict,
                 tools: list[str], cache: dict, skills: list[Path] = (), prompts: dict[str, str] | None = None,
                 outputs: dict[str, str] | None = None, patch: Path | None = None, notes: list[str] = ()) -> dict:
    """The manifest of one run (see the module).  *prompts*: name → the exact text sent (its hash is kept);
    *outputs*: document → output text."""
    candidate = git_state()
    if patch is not None:
        candidate["patch_sha256"] = sha256(patch)
    by_doc = documents(gt_dirs, docs)
    return {
        "run_id": run_id,
        "purpose": purpose,
        "created": datetime.now().isoformat(timespec="seconds"),
        "candidate": candidate,
        "inputs": {n: {"file": d["input"], "sha256": d["input_sha256"]} for n, d in by_doc.items()},
        "annotations": {n: {"gt_dir": d["gt_dir"], "sha256": d["expected_sha256"]} for n, d in by_doc.items()},
        "skills": {str(Path(p).relative_to(REPO_ROOT)): sha256(p) for p in skills},
        "prompts": {name: text_sha256(text) for name, text in (prompts or {}).items()},
        "models": models,
        "tools": list(tools),
        "cache": cache,
        "evaluator": evaluator(),
        "outputs": {n: text_sha256(t) for n, t in (outputs or {}).items()},
        "notes": list(notes),
    }


def _commit_at(when: str) -> str | None:
    """The commit that was HEAD of main at *when* (a live run records its start time, not its code)."""
    out = subprocess.run(["git", "log", "-1", "--format=%H", f"--before={when}", "main"], cwd=REPO_ROOT,
                         capture_output=True, text=True)
    return out.stdout.strip() or None


def baseline(bench: Path, gt_dirs: list[Path]) -> dict:
    """The manifest of the benchmark table: every arm's source and every output's hash."""
    arms: dict[str, dict] = {}
    names: set[str] = set()
    for tool_dir in sorted(p for p in bench.iterdir() if p.is_dir() and not p.name.startswith("_")):
        metas = {m.parent.name: json.loads(m.read_text(encoding="utf-8")) for m in sorted(tool_dir.glob("*/meta.json"))}
        ok = {n: m for n, m in metas.items() if m.get("status") == "ok"}
        names |= set(ok)
        configs = {json.dumps(m.get("config"), sort_keys=True, ensure_ascii=False) for m in ok.values()}
        started = sorted(m.get("started") for m in ok.values() if m.get("started"))
        arm: dict = {"label": next(iter(ok.values()), {}).get("label"), "documents": len(ok),
                     "configs": [json.loads(c) for c in sorted(configs)], "started": [started[0], started[-1]] if started else None,
                     "outputs": {n: sha256(tool_dir / n / "output.md") for n in sorted(ok)}}
        frozen_runs = {(m.get("config") or {}).get("frozen_run") for m in ok.values()} - {None}
        if frozen_runs:
            arm["source"] = "frozen run (replayed offline; outputs are the frozen Markdown)"
            arm["frozen_runs"] = {}
            for run in sorted(frozen_runs):
                manifest = json.loads((REPO_ROOT / "eval_runs" / run / "manifest.json").read_text(encoding="utf-8"))
                arm["frozen_runs"][run] = {"commit": manifest["git"]["commit"], "branch": manifest["git"]["branch"],
                                           "dirty": manifest["git"]["dirty"],
                                           "config_fingerprint": manifest["config"]["fingerprint"]}
        elif any((m.get("config") or {}).get("runtime") == "hybrid" for m in ok.values()):
            arm["source"] = "live run of ParserX (fixed pipeline, then Codex); the code is the main commit at its start"
            arm["commits"] = sorted({c for c in (_commit_at(m["started"]) for m in ok.values()) if c})
        else:
            arm["source"] = "external tool, live run"
        arms[tool_dir.name] = arm
    return {
        "purpose": "E0 same-basis baseline table (execution plan §4.2)",
        "created": datetime.now().isoformat(timespec="seconds"),
        "scored_by": git_state(),
        "evaluator": evaluator(),
        "documents": documents(gt_dirs, sorted(names)),
        "arms": arms,
        "report": {name: sha256(bench / name) for name in ("report.md", "scores.json", "pages.md", "page_scores.json")
                   if (bench / name).exists()},
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)
    base = sub.add_parser("baseline", help="write the manifest of the benchmark table")
    base.add_argument("--bench", type=Path, default=REPO_ROOT / "eval_runs" / "bench")
    base.add_argument("--gt-dir", type=Path, action="append")
    base.add_argument("--out", type=Path, help="default: <bench>/e0_manifest.json")
    args = parser.parse_args()
    gt_dirs = args.gt_dir or [REPO_ROOT / "ground_truth", REPO_ROOT / "ground_truth_public"]
    record = baseline(args.bench, gt_dirs)
    out = args.out or args.bench / "e0_manifest.json"
    out.write_text(json.dumps(record, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"{out}: {len(record['arms'])} arms, {len(record['documents'])} documents, "
          f"evaluator {record['evaluator']['metric_version']} {record['evaluator']['code_sha256'][:12]}")


if __name__ == "__main__":
    main()
