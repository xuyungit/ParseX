#!/usr/bin/env python3
"""Regression test — compare current eval scores against best known baseline.

Usage:
    # Fast check (deterministic docs only, no API calls needed):
    uv run python scripts/regression_test.py --gt-dir ground_truth --deterministic-only

    # Full eval (needs OCR/VLM/LLM services):
    uv run python scripts/regression_test.py --gt-dir ground_truth

    # Specific docs:
    uv run python scripts/regression_test.py --gt-dir ground_truth --include text_table01 deepseek

    # Update baseline after confirmed improvement:
    uv run python scripts/regression_test.py --gt-dir ground_truth --deterministic-only --update-baseline
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

# Suppress PyMuPDF layout analyzer suggestion.
import os
os.environ.setdefault("PYMUPDF_SUGGEST_LAYOUT_ANALYZER", "0")

from parserx.config.schema import load_config_with_result
from parserx.eval.runner import EvalRunner


# ── ANSI colors ──────────────────────────────────────────────────────────

_GREEN = "\033[32m"
_RED = "\033[31m"
_YELLOW = "\033[33m"
_BOLD = "\033[1m"
_RESET = "\033[0m"
_DIM = "\033[2m"

METRIC_KEYS = ["edit_distance", "char_f1", "heading_f1", "table_cell_f1"]

# edit_distance: lower is better; others: higher is better.
_HIGHER_IS_BETTER = {"char_f1", "heading_f1", "table_cell_f1"}


def load_baseline(gt_dir: Path) -> dict:
    path = gt_dir / "best_scores.json"
    if not path.exists():
        print(f"{_YELLOW}Warning: {path} not found — no baseline to compare.{_RESET}")
        return {}
    return json.loads(path.read_text())


def save_baseline(gt_dir: Path, baseline: dict) -> None:
    path = gt_dir / "best_scores.json"
    path.write_text(json.dumps(baseline, indent=2, ensure_ascii=False) + "\n")
    print(f"{_GREEN}Baseline updated: {path}{_RESET}")


def result_to_scores(result) -> dict:
    return {
        "edit_distance": round(result.text.edit_distance, 3),
        "char_f1": round(result.text.char_f1, 3),
        "heading_f1": round(result.headings.f1, 3),
        "table_cell_f1": round(result.tables.cell_f1, 3),
    }


def compare_scores(
    current: dict, baseline: dict, tolerance: float,
) -> list[tuple[str, float, float, str]]:
    """Compare current vs baseline. Returns list of (metric, current, baseline, status)."""
    comparisons = []
    for key in METRIC_KEYS:
        cur = current.get(key, 0.0)
        base = baseline.get(key, 0.0)
        delta = cur - base

        if key in _HIGHER_IS_BETTER:
            if delta < -tolerance:
                status = "regression"
            elif delta > tolerance:
                status = "improved"
            else:
                status = "ok"
        else:
            # edit_distance: lower is better
            if delta > tolerance:
                status = "regression"
            elif delta < -tolerance:
                status = "improved"
            else:
                status = "ok"

        comparisons.append((key, cur, base, status))
    return comparisons


def format_delta(cur: float, base: float, key: str) -> str:
    delta = cur - base
    if abs(delta) < 0.0005:
        return f"{_DIM}  ={_RESET}"
    sign = "+" if delta > 0 else ""
    # Color based on whether delta is good or bad
    if key in _HIGHER_IS_BETTER:
        color = _GREEN if delta > 0 else _RED
    else:
        color = _RED if delta > 0 else _GREEN
    return f"{color}{sign}{delta:.3f}{_RESET}"


def main() -> None:
    parser = argparse.ArgumentParser(description="Regression test against best known scores")
    parser.add_argument("--gt-dir", type=Path, default=Path("ground_truth"),
                        help="Ground truth directory (default: ground_truth)")
    parser.add_argument("--include", nargs="*", help="Only test these documents")
    parser.add_argument("--deterministic-only", action="store_true",
                        help="Only test documents that need 0 API calls")
    parser.add_argument("--tolerance", type=float, default=0.005,
                        help="Tolerance for metric comparison (default: 0.005)")
    parser.add_argument("--update-baseline", action="store_true",
                        help="Update baseline with current scores (only for improvements)")
    parser.add_argument("--config", type=Path, default=None,
                        help="Config file (default: auto-detect)")
    args = parser.parse_args()

    gt_dir = args.gt_dir.resolve()
    if not gt_dir.exists():
        print(f"{_RED}Error: {gt_dir} does not exist{_RESET}")
        sys.exit(1)

    # Load baseline
    baseline_data = load_baseline(gt_dir)
    baseline_docs = baseline_data.get("documents", {})

    # Determine which docs to evaluate
    if args.include:
        include_set = set(args.include)
    elif args.deterministic_only:
        # Select documents with requires_services: [] (no API calls needed)
        include_set = {
            name for name, scores in baseline_docs.items()
            if not scores.get("requires_services")
        }
        if not include_set:
            print(f"{_YELLOW}No offline documents found (requires_services: []).{_RESET}")
            sys.exit(1)
    else:
        include_set = None  # all docs

    # Load config and run evaluation
    config_result = load_config_with_result(args.config)
    runner = EvalRunner(config_result.config)

    print(f"{_BOLD}Running evaluation on {gt_dir.name}...{_RESET}")
    if include_set:
        print(f"  Documents: {', '.join(sorted(include_set))}")
    print()

    results = runner.evaluate_dir(gt_dir, include_docs=include_set)

    if not results and not runner.failed_docs:
        print(f"{_YELLOW}No documents evaluated.{_RESET}")
        sys.exit(0)

    # Compare against baseline
    has_regression = False
    has_improvement = False
    updated_docs = {}

    # Header
    print(f"{_BOLD}{'Document':<30} {'Metric':<18} {'Current':>8} {'Best':>8} {'Delta':>10}{_RESET}")
    print("-" * 80)

    for result in sorted(results, key=lambda r: r.document_name):
        name = result.document_name
        current = result_to_scores(result)
        base = baseline_docs.get(name, {})

        if not base:
            # New document, no baseline yet
            print(f"{_BOLD}{name:<30}{_RESET} {_YELLOW}(new — no baseline){_RESET}")
            for key in METRIC_KEYS:
                print(f"{'':>30} {key:<18} {current[key]:>8.3f}")
            updated_docs[name] = current
            continue

        comparisons = compare_scores(current, base, args.tolerance)
        doc_has_regression = any(s == "regression" for _, _, _, s in comparisons)
        doc_has_improvement = any(s == "improved" for _, _, _, s in comparisons)

        if doc_has_regression:
            has_regression = True
        if doc_has_improvement:
            has_improvement = True

        first = True
        for key, cur, bs, status in comparisons:
            doc_label = f"{_BOLD}{name}{_RESET}" if first else ""
            first = False

            delta_str = format_delta(cur, bs, key)

            if status == "regression":
                marker = f"{_RED}REGRESSED{_RESET}"
            elif status == "improved":
                marker = f"{_GREEN}IMPROVED{_RESET}"
            else:
                marker = ""

            print(f"{doc_label:<40} {key:<18} {cur:>8.3f} {bs:>8.3f} {delta_str:>20} {marker}")

        # Track best scores for update
        merged = dict(base)
        for key in METRIC_KEYS:
            cur = current[key]
            bs = base.get(key, 0.0)
            if key in _HIGHER_IS_BETTER:
                merged[key] = max(cur, bs)
            else:
                merged[key] = min(cur, bs)
        updated_docs[name] = merged

        print()

    # Failed docs
    if runner.failed_docs:
        print(f"\n{_RED}Failed documents:{_RESET}")
        for name, error in runner.failed_docs:
            print(f"  {name}: {error}")

    # Summary
    print("-" * 80)
    if has_regression:
        print(f"{_RED}{_BOLD}REGRESSION DETECTED{_RESET}")
    elif has_improvement:
        print(f"{_GREEN}{_BOLD}All clear — some metrics improved!{_RESET}")
    else:
        print(f"{_GREEN}{_BOLD}All clear — no regressions.{_RESET}")

    # Update baseline if requested
    if args.update_baseline and (has_improvement or not baseline_docs):
        from datetime import date
        new_baseline = {
            "_meta": {
                **baseline_data.get("_meta", {}),
                "updated": str(date.today()),
            },
            "documents": {**baseline_docs, **updated_docs},
        }
        save_baseline(gt_dir, new_baseline)

    sys.exit(1 if has_regression else 0)


if __name__ == "__main__":
    main()
