"""Run records, hard checks and exit codes (guide §9.2 item 2, §14 Q16).

Exit codes: 0 pass · 1 metric regression against the baseline · 2 hard check
failed.  Hard checks:

- absolute: any failed document, any requested-but-not-executed document,
  a baseline with a different metric version;
- relative: per-document missing / extra tables above the baseline (without a
  baseline they are only recorded, so the first freeze is never blocked).
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

from parserx.eval.metrics import METRIC_VERSION, EvalResult

EXIT_OK = 0
EXIT_REGRESSION = 1
EXIT_HARD_FAILURE = 2

# Compared against the baseline: name → (higher is better, tolerance scale).
# Counts use an absolute tolerance of zero.
_SCORES_HIGHER = ("table_cell_f1", "char_f1", "order_tau", "heading_f1")
_SCORES_LOWER = ("edit_distance",)
_COUNTS_LOWER = ("key_errors",)
_TABLE_HARD = ("missing_tables", "extra_tables")


def document_scores(result: EvalResult) -> dict:
    """Flat, JSON-friendly per-document record in report order."""
    return {
        "missing_tables": result.tables.missing_tables,
        "extra_tables": result.tables.extra_tables,
        "tables_expected": result.tables.expected_count,
        "tables_detected": result.tables.detected_count,
        "table_cell_f1": result.tables.cell_f1,
        "table_header_association": result.tables.header_association,
        "table_merged_cell_accuracy": result.tables.merged_cell_accuracy,
        "char_f1": result.text.char_f1,
        "char_bag_f1": result.text.char_bag_f1,
        "edit_distance": result.text.edit_distance,
        "images_expected": result.text.images_expected,
        "images_output": result.text.images_output,
        "order_tau": result.order.tau,
        "order_coverage": result.order.coverage,
        "heading_f1": result.headings.f1,
        "key_errors": result.key_content.total,
        "key_missing": dict(result.key_content.missing),
        "key_extra": dict(result.key_content.extra),
        "requests": {
            "ocr": result.cost.ocr_calls,
            "vlm": result.cost.vlm_calls,
            "llm": result.cost.llm_calls,
        },
        "ocr_pages": result.cost.ocr_pages,
        "attempts": dict(result.cost.attempts),
        "cache_hits": dict(result.cost.cache_hits),
        "wall_time_seconds": result.cost.wall_time_seconds,
        "cost_usd": result.cost.cost_usd,
    }


def run_record(
    results: list[EvalResult],
    *,
    failed: list[tuple[str, str]],
    not_executed: list[tuple[str, str]],
) -> dict:
    return {
        "metric_version": METRIC_VERSION,
        "documents": {r.document_name: document_scores(r) for r in results},
        "failed": [{"document": n, "error": e} for n, e in failed],
        "not_executed": [{"document": n, "reason": r} for n, r in not_executed],
    }


def load_record(path: Path) -> dict:
    """A results JSON, or a frozen run directory containing ``metrics.json``."""
    path = Path(path)
    if path.is_dir():
        path = path / "metrics.json"
    return json.loads(path.read_text(encoding="utf-8"))


@dataclass
class GateOutcome:
    exit_code: int = EXIT_OK
    hard_failures: list[str] = field(default_factory=list)
    regressions: list[str] = field(default_factory=list)
    improvements: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


def evaluate_gate(record: dict, baseline: dict | None, tolerance: float = 0.005) -> GateOutcome:
    outcome = GateOutcome()
    for item in record.get("failed", []):
        outcome.hard_failures.append(f"failed: {item['document']} — {item['error']}")
    for item in record.get("not_executed", []):
        outcome.hard_failures.append(f"not executed: {item['document']} — {item['reason']}")

    if baseline is not None and baseline.get("metric_version") != record.get("metric_version"):
        outcome.hard_failures.append(
            f"baseline metric_version {baseline.get('metric_version')!r} "
            f"!= current {record.get('metric_version')!r}; recompute the baseline"
        )
        baseline = None

    documents = record.get("documents", {})
    if baseline is None:
        for name, scores in sorted(documents.items()):
            for key in _TABLE_HARD:
                if scores.get(key):
                    outcome.notes.append(f"{name}: {key}={scores[key]} (recorded; no baseline)")
    else:
        base_docs = baseline.get("documents", {})
        for name, scores in sorted(documents.items()):
            base = base_docs.get(name)
            if base is None:
                outcome.notes.append(f"{name}: not in baseline")
                continue
            _compare_document(name, scores, base, tolerance, outcome)

    if outcome.hard_failures:
        outcome.exit_code = EXIT_HARD_FAILURE
    elif outcome.regressions:
        outcome.exit_code = EXIT_REGRESSION
    return outcome


def _compare_document(name: str, cur: dict, base: dict, tolerance: float, outcome: GateOutcome) -> None:
    for key in _TABLE_HARD:
        if cur.get(key, 0) > base.get(key, 0):
            outcome.hard_failures.append(f"{name}: {key} {base.get(key, 0)} → {cur.get(key, 0)}")
    for key in _SCORES_HIGHER + _SCORES_LOWER + _COUNTS_LOWER:
        now, then = cur.get(key), base.get(key)
        if now is None or then is None:
            continue
        higher_is_better = key in _SCORES_HIGHER
        tol = 0 if key in _COUNTS_LOWER else tolerance
        delta = now - then if higher_is_better else then - now  # > 0 means better
        line = f"{name}: {key} {then} → {now}"
        if delta < -tol:
            outcome.regressions.append(line)
        elif delta > tol:
            outcome.improvements.append(line)
