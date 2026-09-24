"""Regression suites (guide §9.1): which documents to run, where, and how often.

- document lists (``configs/regression_core.txt`` for L1) with comments;
- several ground-truth directories in one run (L2 covers ``ground_truth/``
  and ``ground_truth_public/``); a requested name found nowhere is reported
  once as not executed, a name present in two directories is an error;
- repeated runs in one process, compared by output bytes, to prove replay is
  deterministic.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from pathlib import Path

from parserx.eval.metrics import EvalResult
from parserx.eval.runner import EvalRunner

REPO_ROOT = Path(__file__).resolve().parents[2]
CORE_LIST = REPO_ROOT / "configs" / "regression_core.txt"


def read_doc_list(path: Path) -> list[str]:
    """Document names, one per line; ``#`` starts a comment."""
    names: list[str] = []
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        name = line.split("#", 1)[0].strip()
        if name:
            names.append(name)
    return names


@dataclass
class SuiteRun:
    results: list[EvalResult] = field(default_factory=list)
    failed: list[tuple[str, str]] = field(default_factory=list)
    not_executed: list[tuple[str, str]] = field(default_factory=list)
    outputs: dict[str, str] = field(default_factory=dict)
    sidecars: dict[str, str] = field(default_factory=dict)  # v2: <name>.blocks.json text
    sources: dict[str, Path] = field(default_factory=dict)  # document → its ground-truth dir


def _doc_names(gt_dir: Path) -> set[str]:
    if (gt_dir / "expected.md").exists():
        return {gt_dir.name}
    if not gt_dir.is_dir():
        return set()
    return {d.name for d in gt_dir.iterdir() if d.is_dir()}


def run_suite(runner: EvalRunner, gt_dirs: list[Path], include: set[str] | None) -> SuiteRun:
    names_by_dir = [(d, _doc_names(d)) for d in gt_dirs]
    seen: dict[str, Path] = {}
    for gt_dir, names in names_by_dir:
        for name in names if include is None else names & include:
            if name in seen:
                raise ValueError(f"document {name!r} exists in both {seen[name]} and {gt_dir}")
            seen[name] = gt_dir

    run = SuiteRun(sources=dict(seen))
    for gt_dir, names in names_by_dir:
        wanted = None if include is None else names & include
        if wanted is not None and not wanted:
            continue
        run.results += runner.evaluate_dir(gt_dir, include_docs=wanted)
        run.failed += runner.failed_docs
        run.not_executed += runner.not_executed
        run.outputs.update(runner.outputs)
        run.sidecars.update(runner.sidecars)

    where = ", ".join(str(d) for d in gt_dirs)
    run.not_executed += [(name, f"not found in {where}") for name in sorted((include or set()) - set(seen))]
    return run


def output_digest(markdown: str) -> str:
    return hashlib.sha256(markdown.encode("utf-8")).hexdigest()


def repeat_mismatches(first: dict[str, str], again: dict[str, str]) -> list[str]:
    """Documents whose output differs between two runs (or ran only once)."""
    return sorted(
        name
        for name in set(first) | set(again)
        if name not in first or name not in again or output_digest(first[name]) != output_digest(again[name])
    )
