#!/usr/bin/env python3
"""Regression test with hard checks (metric version 2.0, guide §9).

Exit codes: 0 pass · 1 metric regression against --baseline · 2 hard check
failed (failed / not-executed documents, more missing or extra tables than the
baseline, or a baseline computed with a different metric version).

Baselines are result records (``--json-out``) or frozen run directories; the
old ``ground_truth/best_scores.json`` is never written and no longer gates.

Usage:
    # Specific docs, report only (no baseline yet):
    uv run python scripts/regression_test.py --include text_table01 deepseek

    # Save the run record, then compare a later run against it:
    uv run python scripts/regression_test.py --include deepseek --json-out /tmp/run.json
    uv run python scripts/regression_test.py --include deepseek --baseline /tmp/run.json

    # Markdown report (L2 reports go to eval_reports/, named with date and phase):
    uv run python scripts/regression_test.py --report eval_reports/2026-09-23_p0-1_x.md
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

# Suppress PyMuPDF layout analyzer suggestion.
os.environ.setdefault("PYMUPDF_SUGGEST_LAYOUT_ANALYZER", "0")

from parserx.config.schema import load_config_with_result
from parserx.eval.gate import EXIT_HARD_FAILURE, evaluate_gate, load_record, run_record
from parserx.eval.metrics import fmt_metric
from parserx.eval.reporting import build_config_report_metadata
from parserx.eval.runner import EvalRunner

_GREEN = "\033[32m"
_RED = "\033[31m"
_YELLOW = "\033[33m"
_BOLD = "\033[1m"
_RESET = "\033[0m"


def _deterministic_docs(gt_dir: Path) -> set[str]:
    """Deprecated selector: docs marked ``requires_services: []`` in best_scores.json (read-only)."""
    path = gt_dir / "best_scores.json"
    if not path.exists():
        return set()
    docs = json.loads(path.read_text()).get("documents", {})
    return {name for name, scores in docs.items() if not scores.get("requires_services")}


def _print_results(record: dict) -> None:
    header = (
        f"{'Document':<28} {'miss/extra':>10} {'table_f1':>8} {'char_f1':>8} {'edit':>6} "
        f"{'order_τ':>8} {'head_f1':>8} {'key_err':>7} {'O/V/L':>9} {'time':>7}"
    )
    print(f"{_BOLD}{header}{_RESET}")
    print("-" * len(header))
    for name, s in sorted(record["documents"].items()):
        req = s["requests"]
        print(
            f"{name:<28} {s['missing_tables']:>4}/{s['extra_tables']:<5} "
            f"{fmt_metric(s['table_cell_f1']):>8} {s['char_f1']:>8.3f} {s['edit_distance']:>6.3f} "
            f"{fmt_metric(s['order_tau']):>8} {fmt_metric(s['heading_f1']):>8} {s['key_errors']:>7} "
            f"{req['ocr']:>3}/{req['vlm']}/{req['llm']:<3} {s['wall_time_seconds']:>6.1f}s"
        )


def main() -> None:
    parser = argparse.ArgumentParser(description="Regression test with hard checks (metric v2.0)")
    parser.add_argument("--gt-dir", type=Path, default=Path("ground_truth"),
                        help="Ground truth directory (default: ground_truth)")
    parser.add_argument("--include", nargs="*", help="Only test these documents")
    parser.add_argument("--deterministic-only", action="store_true",
                        help="Deprecated: documents marked requires_services: [] in best_scores.json")
    parser.add_argument("--tolerance", type=float, default=0.005,
                        help="Tolerance for score comparison against the baseline (default: 0.005)")
    parser.add_argument("--config", type=Path, default=None,
                        help="Config file (default: auto-detect)")
    parser.add_argument("--baseline", type=Path, default=None,
                        help="Result record JSON or frozen run directory to compare against")
    parser.add_argument("--json-out", type=Path, default=None, help="Write this run's result record")
    parser.add_argument("--report", type=Path, default=None, help="Write the Markdown report")
    args = parser.parse_args()

    gt_dir = args.gt_dir.resolve()
    if not gt_dir.exists():
        print(f"{_RED}Error: {gt_dir} does not exist{_RESET}")
        sys.exit(EXIT_HARD_FAILURE)

    if args.include:
        include_set: set[str] | None = set(args.include)
    elif args.deterministic_only:
        include_set = _deterministic_docs(gt_dir)
        if not include_set:
            print(f"{_YELLOW}No offline documents found (requires_services: []).{_RESET}")
            sys.exit(EXIT_HARD_FAILURE)
    else:
        include_set = None

    baseline = load_record(args.baseline) if args.baseline else None

    config_result = load_config_with_result(args.config)
    runner = EvalRunner(config_result.config)
    print(f"{_BOLD}Running evaluation on {gt_dir.name}...{_RESET}")
    if include_set:
        print(f"  Documents: {', '.join(sorted(include_set))}")
    print()

    results = runner.evaluate_dir(gt_dir, include_docs=include_set)
    record = run_record(results, failed=runner.failed_docs, not_executed=runner.not_executed)

    if args.json_out:
        args.json_out.parent.mkdir(parents=True, exist_ok=True)
        args.json_out.write_text(json.dumps(record, indent=2, ensure_ascii=False) + "\n")
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        metadata = build_config_report_metadata(config_result.config, loaded=config_result)
        args.report.write_text(
            EvalRunner.format_report(
                results,
                metadata=metadata,
                failed_docs=runner.failed_docs,
                not_executed=runner.not_executed,
            ),
            encoding="utf-8",
        )

    if record["documents"]:
        _print_results(record)

    outcome = evaluate_gate(record, baseline, tolerance=args.tolerance)
    print("-" * 80)
    print(f"{_BOLD}Hard checks{_RESET}")
    if outcome.hard_failures:
        for line in outcome.hard_failures:
            print(f"  {_RED}✗ {line}{_RESET}")
    else:
        print(f"  {_GREEN}✓ no failed or unexecuted documents{_RESET}")
    for line in outcome.notes:
        print(f"  {_YELLOW}· {line}{_RESET}")
    if baseline is None:
        print(f"{_YELLOW}No baseline given: scores reported, not compared.{_RESET}")
    for line in outcome.regressions:
        print(f"  {_RED}REGRESSED {line}{_RESET}")
    for line in outcome.improvements:
        print(f"  {_GREEN}IMPROVED  {line}{_RESET}")

    verdict = {0: f"{_GREEN}PASS", 1: f"{_RED}REGRESSION DETECTED", 2: f"{_RED}HARD CHECK FAILED"}
    print(f"{_BOLD}{verdict[outcome.exit_code]}{_RESET} (exit {outcome.exit_code})")
    sys.exit(outcome.exit_code)


if __name__ == "__main__":
    main()
