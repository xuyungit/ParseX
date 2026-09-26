"""Frozen runs (guide §8.4, §9.2 item 6).

A frozen run is the acceptance evidence for a phase: everything needed to
trace and replay one complete evaluation lives in ``eval_runs/<run_id>/``
(local only, never in git — guide §14 Q15):

- ``manifest.json``: git commit and working-tree state, redacted resolved
  config and its fingerprint, metric / cache schema versions, service
  identities, and for every document its ground-truth directory, input and
  ``expected.md`` hashes and whether it belongs to the isolation set;
- ``metrics.json``: the run record (the same format ``--json-out`` writes);
- ``outputs/<doc>.md``: every Markdown output;
- ``cache/``: every OCR / VLM response the run used, so the run replays
  offline (``scripts/regression_test.py --replay eval_runs/<run_id>``).
"""

from __future__ import annotations

import hashlib
import json
import platform
import subprocess
from datetime import date
from importlib import metadata as importlib_metadata
from pathlib import Path
from typing import Any

from parserx.cache import CACHE_SCHEMA_VERSION, endpoint_identity
from parserx.config.schema import ParserXConfig
from parserx.eval.metrics import METRIC_VERSION
from parserx.eval.reporting import config_fingerprint, redacted_config
from parserx.eval.suite import REPO_ROOT

EVAL_RUNS = REPO_ROOT / "eval_runs"
ISOLATION_LIST = REPO_ROOT / "configs" / "isolation_set.txt"

# Record fields that legitimately differ between a live run and its replay.
_VOLATILE = frozenset({"wall_time_seconds", "requests", "attempts", "cache_hits", "ocr_pages", "cost_usd"})
_PACKAGES = ("pymupdf", "openai", "rapidfuzz", "pydantic", "rapidocr", "rapid-layout")
_CODE_DIRS = ("parserx", "scripts", "configs", "parserx.yaml", "pyproject.toml")


def run_id_for(label: str, today: str | None = None) -> str:
    return f"{today or date.today().isoformat()}_{label}"


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def git_state(repo: Path = REPO_ROOT) -> dict[str, Any]:
    def git(*args: str) -> str | None:
        try:
            out = subprocess.run(["git", *args], cwd=repo, capture_output=True, text=True, check=True)
        except (OSError, subprocess.CalledProcessError):
            return None
        return out.stdout

    commit = git("rev-parse", "HEAD")
    if commit is None:
        return {"commit": None, "branch": None, "dirty": None, "diff_sha256": None, "untracked_code": None}
    diff = git("diff", "HEAD") or ""
    # Untracked files are invisible to `git diff`; list the ones that can change behaviour.
    untracked = (git("ls-files", "--others", "--exclude-standard", "--", *_CODE_DIRS) or "").split()
    return {
        "commit": commit.strip(),
        "branch": (git("rev-parse", "--abbrev-ref", "HEAD") or "").strip(),
        "dirty": bool(diff or untracked),
        "diff_sha256": hashlib.sha256(diff.encode("utf-8")).hexdigest() if diff else None,
        "untracked_code": untracked,
    }


def document_inventory(sources: dict[str, Path], isolation: set[str]) -> dict[str, dict[str, Any]]:
    inventory: dict[str, dict[str, Any]] = {}
    for name, gt_dir in sorted(sources.items()):
        doc_dir = gt_dir if (gt_dir / "expected.md").exists() and gt_dir.name == name else gt_dir / name
        inputs = sorted(doc_dir.glob("input.*"))
        inventory[name] = {
            "gt_dir": gt_dir.name,
            "isolation": name in isolation,
            "input": inputs[0].name if inputs else None,
            "input_sha256": _sha256(inputs[0]) if inputs else None,
            "expected_sha256": _sha256(doc_dir / "expected.md"),
        }
    return inventory


def build_manifest(
    *,
    run_id: str,
    label: str,
    config: ParserXConfig,
    config_path: Path,
    gt_dirs: list[Path],
    inventory: dict[str, dict[str, Any]],
    command: list[str],
) -> dict[str, Any]:
    versions = {}
    for package in _PACKAGES:
        try:
            versions[package] = importlib_metadata.version(package)
        except importlib_metadata.PackageNotFoundError:
            versions[package] = None
    return {
        "run_id": run_id,
        "label": label,
        "created": date.today().isoformat(),
        "git": git_state(),
        "command": command,
        "config": {
            "path": str(config_path),
            "fingerprint": config_fingerprint(config),
            "resolved": redacted_config(config),
        },
        "metric_version": METRIC_VERSION,
        "cache_schema_version": CACHE_SCHEMA_VERSION,
        "services": {
            "ocr": {"endpoint": endpoint_identity(config.builders.ocr.endpoint), "model": config.builders.ocr.model},
            "vlm": {"endpoint": endpoint_identity(config.services.vlm.endpoint), "model": config.services.vlm.model},
        },
        "environment": {"python": platform.python_version(), "packages": versions},
        "gt_dirs": [str(d.relative_to(REPO_ROOT)) if d.is_relative_to(REPO_ROOT) else str(d) for d in gt_dirs],
        "documents": inventory,
    }


def write_frozen_run(run_dir: Path, *, record: dict, outputs: dict[str, str], manifest: dict,
                     sidecars: dict[str, str] | None = None) -> None:
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "metrics.json").write_text(json.dumps(record, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    (run_dir / "manifest.json").write_text(json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    out_dir = run_dir / "outputs"
    out_dir.mkdir(exist_ok=True)
    for name, markdown in outputs.items():
        (out_dir / f"{name}.md").write_text(markdown, encoding="utf-8")
    for name, sidecar in (sidecars or {}).items():
        (out_dir / f"{name}.blocks.json").write_text(sidecar, encoding="utf-8")


def replay_differences(record: dict, frozen: dict) -> list[str]:
    """Everything a faithful replay must reproduce but did not (*frozen* as ``load_record`` returns it).

    A frozen run scored under an older metric version is rescored: then only
    its outputs must be reproduced, the scores are expected to move.
    """
    rescored = record.get("metric_version") != frozen.get("metric_version")
    diffs: list[str] = []
    if record.get("config_fingerprint") != frozen.get("config_fingerprint"):
        diffs.append(
            f"config fingerprint {frozen.get('config_fingerprint')} → {record.get('config_fingerprint')}"
        )
    current = record.get("documents", {})
    for name, then in sorted(frozen.get("documents", {}).items()):
        now = current.get(name)
        if now is None:
            diffs.append(f"{name}: missing from the replay")
            continue
        for key in sorted(set(then) | set(now)):
            if key in _VOLATILE or (rescored and key != "output_sha256"):
                continue
            if then.get(key) != now.get(key):
                diffs.append(f"{name}: {key} {then.get(key)!r} → {now.get(key)!r}")
    return diffs
