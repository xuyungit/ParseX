"""ParserX command-line interface."""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

from parserx.config.schema import ConfigLoadResult, apply_overrides, load_config_with_result
from parserx.pipeline import Pipeline


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="parserx",
        description="High-fidelity document parsing for knowledge bases and retrieval",
    )
    sub = parser.add_subparsers(dest="command")

    # parserx parse
    parse_cmd = sub.add_parser("parse", help="Parse a document to Markdown")
    parse_cmd.add_argument("input", type=Path, help="Input document path")
    parse_cmd.add_argument("-o", "--output", type=Path, help="Output file or directory")
    parse_cmd.add_argument("-c", "--config", type=Path, help="Config YAML path")
    parse_cmd.add_argument(
        "--set", dest="overrides", action="append", default=[],
        help="Override config with dotted.path=value (repeatable)",
    )
    parse_cmd.add_argument("-v", "--verbose", action="store_true", help="Verbose logging")
    parse_cmd.add_argument(
        "--split-chapters", action="store_true",
        help="Split into chapter files (output must be a directory)",
    )

    # parserx eval
    eval_cmd = sub.add_parser("eval", help="Evaluate parsing against ground truth")
    eval_cmd.add_argument("ground_truth", type=Path, help="Ground truth directory")
    eval_cmd.add_argument("-c", "--config", type=Path, help="Config YAML path")
    eval_cmd.add_argument(
        "--set", dest="overrides", action="append", default=[],
        help="Override config with dotted.path=value (repeatable)",
    )
    eval_cmd.add_argument(
        "--include-doc", dest="include_docs", action="append", default=[],
        help="Only evaluate the named document directory (repeatable)",
    )
    eval_cmd.add_argument(
        "--include-list", type=Path,
        help="Path to newline-delimited document names to evaluate",
    )
    eval_cmd.add_argument("-o", "--output", type=Path, help="Output report path")
    eval_cmd.add_argument("-v", "--verbose", action="store_true", help="Verbose logging")

    # parserx compare
    compare_cmd = sub.add_parser("compare", help="Compare two parsing configs on the same ground truth")
    compare_cmd.add_argument("ground_truth", type=Path, help="Ground truth directory")
    compare_cmd.add_argument("--config-a", type=Path, help="Base config path")
    compare_cmd.add_argument("--config-b", type=Path, help="Experiment config path")
    compare_cmd.add_argument("--label-a", default="A", help="Label for config A")
    compare_cmd.add_argument("--label-b", default="B", help="Label for config B")
    compare_cmd.add_argument(
        "--set-a", dest="overrides_a", action="append", default=[],
        help="Override config A with dotted.path=value (repeatable)",
    )
    compare_cmd.add_argument(
        "--set-b", dest="overrides_b", action="append", default=[],
        help="Override config B with dotted.path=value (repeatable)",
    )
    compare_cmd.add_argument(
        "--include-doc", dest="include_docs", action="append", default=[],
        help="Only compare the named document directory (repeatable)",
    )
    compare_cmd.add_argument(
        "--include-list", type=Path,
        help="Path to newline-delimited document names to compare",
    )
    compare_cmd.add_argument("-o", "--output", type=Path, help="Output report path")
    compare_cmd.add_argument("-v", "--verbose", action="store_true", help="Verbose logging")

    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        sys.exit(1)

    level = logging.DEBUG if getattr(args, "verbose", False) else logging.INFO
    logging.basicConfig(level=level, format="%(levelname)s: %(message)s")

    if args.command == "parse":
        _cmd_parse(args)
    elif args.command == "eval":
        _cmd_eval(args)
    elif args.command == "compare":
        _cmd_compare(args)


def _cmd_parse(args: argparse.Namespace) -> None:
    config = apply_overrides(load_config_with_result(args.config).config, args.overrides)
    pipeline = Pipeline(config)

    if args.split_chapters and args.output:
        pipeline.parse_to_dir(args.input, args.output)
        logging.info("Written to %s", args.output)
    elif args.output:
        result = pipeline.parse(args.input)
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(result, encoding="utf-8")
        logging.info("Written to %s (%d chars)", args.output, len(result))
    else:
        result = pipeline.parse(args.input)
        print(result)


def _cmd_eval(args: argparse.Namespace) -> None:
    from parserx.eval.runner import EvalRunner

    config = _load_cli_config(args.config, args.overrides, label="Eval")
    runner = EvalRunner(config)
    include_docs = _resolve_include_docs(
        getattr(args, "include_docs", None),
        getattr(args, "include_list", None),
    )
    results = runner.evaluate_dir(args.ground_truth, include_docs=include_docs)
    report = EvalRunner.format_report(results)

    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(report, encoding="utf-8")
        logging.info("Report written to %s", args.output)
    else:
        print(report)


def _cmd_compare(args: argparse.Namespace) -> None:
    from parserx.eval.compare import compare_results, format_compare_report
    from parserx.eval.runner import EvalRunner

    config_a = _load_cli_config(args.config_a, args.overrides_a, label=f"Compare {args.label_a}")
    config_b = _load_cli_config(args.config_b, args.overrides_b, label=f"Compare {args.label_b}")
    include_docs = _resolve_include_docs(
        getattr(args, "include_docs", None),
        getattr(args, "include_list", None),
    )

    results_a = EvalRunner(config_a).evaluate_dir(args.ground_truth, include_docs=include_docs)
    results_b = EvalRunner(config_b).evaluate_dir(args.ground_truth, include_docs=include_docs)
    rows = compare_results(results_a, results_b)
    report = format_compare_report(
        rows,
        label_a=args.label_a,
        label_b=args.label_b,
    )

    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(report, encoding="utf-8")
        logging.info("Compare report written to %s", args.output)
    else:
        print(report)


def _load_cli_config(
    path: Path | None,
    overrides: list[str],
    *,
    label: str,
):
    loaded = load_config_with_result(path)
    _log_config_resolution(label, loaded)
    return apply_overrides(loaded.config, overrides)


def _log_config_resolution(label: str, loaded: ConfigLoadResult) -> None:
    if loaded.source in {"explicit", "project"} and loaded.resolved_path is not None:
        logging.info("%s config: %s", label, loaded.resolved_path.resolve())
        return

    if loaded.source == "missing" and loaded.resolved_path is not None:
        logging.warning(
            "%s config file not found: %s; using built-in defaults",
            label,
            loaded.resolved_path,
        )
        return

    logging.warning(
        "%s config: no project parserx.yaml found in %s; using built-in defaults",
        label,
        Path.cwd(),
    )


def _resolve_include_docs(
    include_docs: list[str] | None,
    include_list: Path | None,
) -> set[str] | None:
    names = {name.strip() for name in include_docs or [] if name.strip()}

    if include_list is not None:
        for line in include_list.read_text(encoding="utf-8").splitlines():
            cleaned = line.strip()
            if cleaned and not cleaned.startswith("#"):
                names.add(cleaned)

    if not names:
        return None

    logging.info("Doc filter active: %d document(s)", len(names))
    return names


if __name__ == "__main__":
    main()
