#!/usr/bin/env python3
"""Regression test with hard checks (metric version 2.0, guide §9).

Tiers (guide §9.1):
    # L1 core regression: offline replay from the response cache, run twice
    uv run python scripts/regression_test.py --core --repeat 2

    # ...when the cache lacks responses (new code paths, new prompts):
    uv run python scripts/regression_test.py --core --allow-calls

    # L2 full regression: every ground-truth document, public set included
    uv run python scripts/regression_test.py --gt-dir ground_truth --gt-dir ground_truth_public \\
        --report eval_reports/<date>_<phase>_<topic>.md

Exit codes: 0 pass · 1 metric regression against --baseline · 2 hard check
failed (failed / not-executed documents, offline cache misses, output that
differs between --repeat runs, more missing or extra tables than the
baseline, or a baseline computed with a different metric version).

Baselines are result records (``--json-out``) or frozen run directories; the
old ``ground_truth/best_scores.json`` is never written and no longer gates.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

# Suppress PyMuPDF layout analyzer suggestion.
os.environ.setdefault("PYMUPDF_SUGGEST_LAYOUT_ANALYZER", "0")

from parserx.config.schema import apply_overrides, load_config_with_result
from parserx.eval.gate import EXIT_HARD_FAILURE, evaluate_gate, load_record, run_record
from parserx.eval.metrics import fmt_metric
from parserx.eval.reporting import build_config_report_metadata, config_fingerprint
from parserx.eval.runner import EvalRunner
from parserx.eval.suite import CORE_LIST, REPO_ROOT, output_digest, read_doc_list, repeat_mismatches, run_suite

# Regression runs use the production config with every LLM call turned off.
_DEFAULT_CONFIG = REPO_ROOT / "configs" / "regression.yaml"

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
        f"{'order_τ':>8} {'head_f1':>8} {'key_err':>7} {'O/V/L':>9} {'hits':>5} {'time':>7}"
    )
    print(f"{_BOLD}{header}{_RESET}")
    print("-" * len(header))
    for name, s in sorted(record["documents"].items()):
        req = s["requests"]
        print(
            f"{name:<28} {s['missing_tables']:>4}/{s['extra_tables']:<5} "
            f"{fmt_metric(s['table_cell_f1']):>8} {s['char_f1']:>8.3f} {s['edit_distance']:>6.3f} "
            f"{fmt_metric(s['order_tau']):>8} {fmt_metric(s['heading_f1']):>8} {s['key_errors']:>7} "
            f"{req['ocr']:>3}/{req['vlm']}/{req['llm']:<3} {sum(s['cache_hits'].values()):>5} "
            f"{s['wall_time_seconds']:>6.1f}s"
        )


def _select_documents(args: argparse.Namespace, gt_dirs: list[Path]) -> set[str] | None:
    if args.core:
        return set(read_doc_list(CORE_LIST))
    if args.list:
        return set(read_doc_list(args.list))
    if args.include:
        return set(args.include)
    if args.deterministic_only:
        print(f"{_YELLOW}--deterministic-only is deprecated; use --core (offline replay).{_RESET}")
        names = set().union(*(_deterministic_docs(d) for d in gt_dirs))
        if not names:
            print(f"{_YELLOW}No offline documents found (requires_services: []).{_RESET}")
            sys.exit(EXIT_HARD_FAILURE)
        return names
    return None


def _cache_mode(args: argparse.Namespace) -> str | None:
    """Explicit --cache-mode wins; --allow-calls records; --core replays offline."""
    if args.cache_mode:
        return args.cache_mode
    if args.allow_calls:
        return "read_write"
    if args.core:
        return "read_only"
    return None


def main() -> None:
    parser = argparse.ArgumentParser(description="Regression test with hard checks (metric v2.0)")
    parser.add_argument("--gt-dir", type=Path, action="append", default=None,
                        help="Ground truth directory, repeatable (default: ground_truth)")
    selection = parser.add_mutually_exclusive_group()
    selection.add_argument("--core", action="store_true",
                           help="L1: documents in configs/regression_core.txt, offline replay by default")
    selection.add_argument("--list", type=Path, help="Documents listed in this file (# comments allowed)")
    selection.add_argument("--include", nargs="*", help="Only test these documents")
    selection.add_argument("--deterministic-only", action="store_true",
                           help="Deprecated: documents marked requires_services: [] in best_scores.json")
    parser.add_argument("--allow-calls", action="store_true",
                        help="Allow real service calls on cache misses (cache mode read_write)")
    parser.add_argument("--cache-mode", choices=["off", "read_write", "read_only", "refresh"],
                        default=None, help="Response cache mode (overrides --core / --allow-calls)")
    parser.add_argument("--cache-dir", type=Path, default=None,
                        help="Response cache directory (default: from config)")
    parser.add_argument("--repeat", type=int, default=1,
                        help="Run N times in one process; differing outputs are a hard failure")
    parser.add_argument("--tolerance", type=float, default=0.005,
                        help="Tolerance for score comparison against the baseline (default: 0.005)")
    parser.add_argument("--config", type=Path, default=_DEFAULT_CONFIG,
                        help="Config file (default: configs/regression.yaml)")
    parser.add_argument("--baseline", type=Path, default=None,
                        help="Result record JSON or frozen run directory to compare against")
    parser.add_argument("--json-out", type=Path, default=None, help="Write this run's result record")
    parser.add_argument("--report", type=Path, default=None, help="Write the Markdown report")
    parser.add_argument("--outputs-dir", type=Path, default=None,
                        help="Write each document's Markdown output here")
    args = parser.parse_args()

    gt_dirs = [d.resolve() for d in (args.gt_dir or [REPO_ROOT / "ground_truth"])]
    for gt_dir in gt_dirs:
        if not gt_dir.exists():
            print(f"{_RED}Error: {gt_dir} does not exist{_RESET}")
            sys.exit(EXIT_HARD_FAILURE)
    if not args.config.exists():
        print(f"{_RED}Error: config {args.config} does not exist{_RESET}")
        sys.exit(EXIT_HARD_FAILURE)

    include = _select_documents(args, gt_dirs)
    baseline = load_record(args.baseline) if args.baseline else None

    config_result = load_config_with_result(args.config)
    overrides = []
    mode = _cache_mode(args)
    if mode:
        overrides.append(f"cache.mode={mode}")
    if args.cache_dir:
        overrides.append(f"cache.dir={args.cache_dir}")
    config = apply_overrides(config_result.config, overrides)
    runner = EvalRunner(config)
    fingerprint = config_fingerprint(config)

    print(f"{_BOLD}Running evaluation on {', '.join(d.name for d in gt_dirs)}...{_RESET}")
    print(f"  Config: {args.config} (fingerprint {fingerprint}); cache {config.cache.mode} at {config.cache.dir}")
    if include:
        print(f"  Documents: {', '.join(sorted(include))}")
    print()

    started = time.time()
    try:
        run = run_suite(runner, gt_dirs, include)
        mismatches: set[str] = set()
        for _ in range(args.repeat - 1):
            again = run_suite(runner, gt_dirs, include)
            mismatches |= set(repeat_mismatches(run.outputs, again.outputs))
    except ValueError as exc:
        print(f"{_RED}Error: {exc}{_RESET}")
        sys.exit(EXIT_HARD_FAILURE)
    elapsed = time.time() - started

    record = run_record(run.results, failed=run.failed, not_executed=run.not_executed)
    record["config_fingerprint"] = fingerprint
    if args.repeat > 1:
        record["not_reproducible"] = sorted(mismatches)
    for name, markdown in run.outputs.items():
        if name in record["documents"]:
            record["documents"][name]["output_sha256"] = output_digest(markdown)
        if args.outputs_dir:
            args.outputs_dir.mkdir(parents=True, exist_ok=True)
            (args.outputs_dir / f"{name}.md").write_text(markdown, encoding="utf-8")

    if args.json_out:
        args.json_out.parent.mkdir(parents=True, exist_ok=True)
        args.json_out.write_text(json.dumps(record, indent=2, ensure_ascii=False) + "\n")
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        metadata = build_config_report_metadata(config, loaded=config_result)
        args.report.write_text(
            EvalRunner.format_report(
                run.results,
                metadata=metadata,
                failed_docs=run.failed,
                not_executed=run.not_executed,
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
        print(f"  {_GREEN}✓ no failed, unexecuted or non-reproducible documents{_RESET}")
    if any("cache miss" in reason for _, reason in run.not_executed):
        print(f"  {_YELLOW}→ the cache lacks responses: rerun with --allow-calls to record them{_RESET}")
    for line in outcome.notes:
        print(f"  {_YELLOW}· {line}{_RESET}")
    if args.repeat > 1 and not mismatches:
        print(f"  {_GREEN}✓ {args.repeat} runs produced identical outputs{_RESET}")
    if baseline is None:
        print(f"{_YELLOW}No baseline given: scores reported, not compared.{_RESET}")
    for line in outcome.regressions:
        print(f"  {_RED}REGRESSED {line}{_RESET}")
    for line in outcome.improvements:
        print(f"  {_GREEN}IMPROVED  {line}{_RESET}")

    verdict = {0: f"{_GREEN}PASS", 1: f"{_RED}REGRESSION DETECTED", 2: f"{_RED}HARD CHECK FAILED"}
    print(f"{_BOLD}{verdict[outcome.exit_code]}{_RESET} (exit {outcome.exit_code}, {elapsed:.1f}s)")
    sys.exit(outcome.exit_code)


if __name__ == "__main__":
    main()
