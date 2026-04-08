"""ParserX command-line interface."""

from __future__ import annotations

import argparse
import logging
import os
import sys
from pathlib import Path

# Suppress PyMuPDF's unsolicited recommendation print — ParserX has its own
# layout analysis pipeline.
os.environ.setdefault("PYMUPDF_SUGGEST_LAYOUT_ANALYZER", "0")

from parserx.config.schema import ConfigLoadResult, apply_overrides, load_config_with_result
from parserx.eval.reporting import build_config_report_metadata
from parserx.models.results import ParseResult
from parserx.pipeline import Pipeline


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="psx",
        description="ParserX — high-fidelity document parsing for knowledge bases and retrieval",
    )
    sub = parser.add_subparsers(dest="command")

    # parserx parse
    parse_cmd = sub.add_parser("parse", help="Parse a document to Markdown")
    parse_cmd.add_argument("input", type=Path, help="Input document path (PDF, DOCX)")
    parse_cmd.add_argument(
        "-o", "--output", type=Path,
        help="Output directory (default: ./output/<filename>/)",
    )
    parse_cmd.add_argument("-c", "--config", type=Path, help="Config YAML path")
    parse_cmd.add_argument(
        "--set", dest="overrides", action="append", default=[],
        help="Override config with dotted.path=value (repeatable)",
    )
    parse_cmd.add_argument("-v", "--verbose", action="store_true", help="Verbose logging")
    parse_cmd.add_argument(
        "--stdout", action="store_true",
        help="Print Markdown to stdout instead of writing files",
    )
    parse_cmd.add_argument(
        "--split-chapters", action="store_true",
        help="Also generate index.md and per-chapter files",
    )
    # ── Convenience flags ──────────────────────────────────────────────
    parse_cmd.add_argument(
        "--no-vlm", action="store_true",
        help="Disable VLM (image description, table/formula correction)",
    )
    parse_cmd.add_argument(
        "--no-ocr", action="store_true",
        help="Disable OCR (process native text only)",
    )
    parse_cmd.add_argument(
        "--no-llm", action="store_true",
        help="Disable LLM fallback (pure rule-based processing)",
    )
    parse_cmd.add_argument("--vlm-model", help="Override VLM model name")
    parse_cmd.add_argument("--llm-model", help="Override LLM model name")
    parse_cmd.add_argument(
        "--no-formula", action="store_true",
        help="Skip formula detection",
    )
    parse_cmd.add_argument(
        "--no-table-vlm", action="store_true",
        help="Disable VLM table correction",
    )
    parse_cmd.add_argument("--ocr-lang", help="OCR language (default: ch_sim+en)")

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

    # parserx tool-eval
    tool_eval_cmd = sub.add_parser(
        "tool-eval",
        help="Run llamaparse/liteparse/builtin/ParserX and score all Markdown outputs",
    )
    tool_eval_cmd.add_argument("ground_truth", type=Path, help="Ground truth directory")
    tool_eval_cmd.add_argument("-c", "--config", type=Path, help="ParserX config YAML path")
    tool_eval_cmd.add_argument(
        "--set", dest="overrides", action="append", default=[],
        help="Override ParserX config with dotted.path=value (repeatable)",
    )
    tool_eval_cmd.add_argument(
        "--include-doc", dest="include_docs", action="append", default=[],
        help="Only evaluate the named document directory (repeatable)",
    )
    tool_eval_cmd.add_argument(
        "--include-list", type=Path,
        help="Path to newline-delimited document names to evaluate",
    )
    tool_eval_cmd.add_argument(
        "--artifacts-dir",
        type=Path,
        default=Path("reports/tool_eval_artifacts"),
        help="Directory where per-tool Markdown artifacts will be written",
    )
    tool_eval_cmd.add_argument(
        "--llamaparse-tier",
        default="agentic",
        help="LlamaParse tier (default: agentic)",
    )
    tool_eval_cmd.add_argument(
        "--llamaparse-version",
        default="latest",
        help="LlamaParse API version (default: latest)",
    )
    tool_eval_cmd.add_argument(
        "--liteparse-ocr-language",
        help="Optional LiteParse OCR language override",
    )
    tool_eval_cmd.add_argument(
        "--liteparse-dpi",
        type=int,
        default=150,
        help="LiteParse render DPI (default: 150)",
    )
    tool_eval_cmd.add_argument("-o", "--output", type=Path, help="Output report path")
    tool_eval_cmd.add_argument("-v", "--verbose", action="store_true", help="Verbose logging")

    # parserx init
    sub.add_parser("init", help="Create global config directory (~/.config/parserx/)")

    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        sys.exit(1)

    level = logging.DEBUG if getattr(args, "verbose", False) else logging.INFO
    logging.basicConfig(level=level, format="%(levelname)s: %(message)s")
    logging.getLogger("pdfminer").setLevel(logging.INFO)

    if args.command == "init":
        _cmd_init()
    elif args.command == "parse":
        _cmd_parse(args)
    elif args.command == "eval":
        _cmd_eval(args)
    elif args.command == "compare":
        _cmd_compare(args)
    elif args.command == "tool-eval":
        _cmd_tool_eval(args)


_CONFIG_TEMPLATE = """\
# ParserX Global Configuration
# Credentials are resolved from environment variables (set in .env alongside this file).

providers:
  pdf:
    engine: pymupdf
  docx:
    engine: docling

builders:
  ocr:
    engine: paddleocr
    lang: ch_sim+en
    endpoint: ${PADDLE_OCR_ENDPOINT}
    token: ${PADDLE_OCR_TOKEN}
    model: ${PADDLE_OCR_MODEL:PaddleOCR-VL-1.5}
    selective: true

processors:
  header_footer:
    enabled: true
  chapter:
    enabled: true
    llm_fallback: true
  table:
    enabled: true
    vlm_fallback: true
  image:
    enabled: true
    vlm_description: true
    skip_decorative: true
  formula:
    enabled: true
  line_unwrap:
    enabled: true
  text_clean:
    enabled: true

services:
  vlm:
    provider: openai
    endpoint: ${OPENAI_BASE_URL}
    model: ${VLM_MODEL:gpt-4o-mini}
    api_key: ${OPENAI_API_KEY}
  llm:
    provider: openai
    endpoint: ${OPENAI_BASE_URL}
    model: ${LLM_MODEL:gpt-4o-mini}
    api_key: ${OPENAI_API_KEY}

output:
  format: markdown
  image_dir: images
"""

_ENV_TEMPLATE = """\
# ParserX API credentials
# Fill in the values below, then they will be picked up automatically.

OPENAI_API_KEY=
OPENAI_BASE_URL=

# Optional: PaddleOCR service (needed for scanned PDF pages)
PADDLE_OCR_ENDPOINT=
PADDLE_OCR_TOKEN=

# Optional: override model names
# VLM_MODEL=gpt-4o-mini
# LLM_MODEL=gpt-4o-mini
"""


def _cmd_init() -> None:
    from parserx.config.schema import _GLOBAL_CONFIG_DIR

    config_dir = _GLOBAL_CONFIG_DIR
    config_path = config_dir / "config.yaml"
    env_path = config_dir / ".env"

    config_dir.mkdir(parents=True, exist_ok=True)

    created = []
    if not config_path.exists():
        config_path.write_text(_CONFIG_TEMPLATE, encoding="utf-8")
        created.append(str(config_path))
    else:
        print(f"  exists: {config_path}", file=sys.stderr)

    if not env_path.exists():
        env_path.write_text(_ENV_TEMPLATE, encoding="utf-8")
        created.append(str(env_path))
    else:
        print(f"  exists: {env_path}", file=sys.stderr)

    if created:
        print(f"Created:", file=sys.stderr)
        for p in created:
            print(f"  {p}", file=sys.stderr)
        print(f"\nNext: edit {env_path} to fill in your API keys.", file=sys.stderr)
    else:
        print("Global config already exists. Nothing to do.", file=sys.stderr)


def _cmd_parse(args: argparse.Namespace) -> None:
    # Build overrides from convenience flags (applied before --set)
    flag_overrides = _collect_flag_overrides(args)
    all_overrides = flag_overrides + list(args.overrides)

    loaded = load_config_with_result(args.config)
    config = apply_overrides(loaded.config, all_overrides)

    # Chapter splitting: honour both --split-chapters flag and config default
    if args.split_chapters:
        config.output.chapter_split = True
    elif not args.split_chapters and not any("chapter_split" in o for o in args.overrides):
        # Default off for CLI usage (config default is True for eval pipelines)
        config.output.chapter_split = False

    pipeline = Pipeline(config)

    if args.stdout:
        # Legacy stdout mode
        result = pipeline.parse(args.input)
        print(result)
        return

    # Directory output mode (default)
    output_dir = args.output or Path("output") / args.input.stem
    result = pipeline.parse_result_to_dir(args.input, output_dir)
    _print_summary(args.input, output_dir, result)


def _collect_flag_overrides(args: argparse.Namespace) -> list[str]:
    """Convert convenience flags to dotted-path config overrides."""
    overrides: list[str] = []
    if getattr(args, "no_vlm", False):
        overrides.append("services.vlm.endpoint=")
    if getattr(args, "no_ocr", False):
        overrides.append("builders.ocr.engine=none")
    if getattr(args, "no_llm", False):
        overrides.append("services.llm.endpoint=")
    if getattr(args, "vlm_model", None):
        overrides.append(f"services.vlm.model={args.vlm_model}")
    if getattr(args, "llm_model", None):
        overrides.append(f"services.llm.model={args.llm_model}")
    if getattr(args, "no_formula", False):
        overrides.append("processors.formula.enabled=false")
    if getattr(args, "no_table_vlm", False):
        overrides.append("processors.table.vlm_fallback=false")
    if getattr(args, "ocr_lang", None):
        overrides.append(f"builders.ocr.lang={args.ocr_lang}")
    return overrides


def _print_summary(
    input_path: Path,
    output_dir: Path,
    result: ParseResult,
) -> None:
    """Print a human-readable processing summary to stderr."""

    # Page type breakdown
    pt = result.page_types
    page_parts = []
    for key in ("native", "scanned", "mixed"):
        if pt.get(key, 0) > 0:
            page_parts.append(f"{pt[key]} {key}")
    page_detail = f" ({', '.join(page_parts)})" if page_parts else ""

    # API calls
    api = result.api_calls
    api_parts = []
    for key in ("ocr", "vlm", "llm"):
        if api.get(key, 0) > 0:
            api_parts.append(f"{key.upper()} {api[key]}")
    api_line = " \u00b7 ".join(api_parts) if api_parts else "none"

    images_extracted = result.images_total - result.images_skipped

    lines = [
        f"Done: {input_path.name} \u2192 {output_dir}/",
        f"  Pages:      {result.page_count}{page_detail}",
        f"  Images:     {images_extracted} extracted, {result.images_skipped} skipped",
        f"  API calls:  {api_line}",
    ]
    if result.warnings:
        lines.append(f"  Warnings:   {len(result.warnings)}")
    print("\n".join(lines), file=sys.stderr)


def _cmd_eval(args: argparse.Namespace) -> None:
    from parserx.eval.runner import EvalRunner

    config, metadata = _load_cli_config(args.config, args.overrides, label="Eval")
    runner = EvalRunner(config)
    include_docs = _resolve_include_docs(
        getattr(args, "include_docs", None),
        getattr(args, "include_list", None),
    )
    results = runner.evaluate_dir(args.ground_truth, include_docs=include_docs)
    report = EvalRunner.format_report(
        results, metadata=metadata, failed_docs=runner.failed_docs,
    )

    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(report, encoding="utf-8")
        logging.info("Report written to %s", args.output)
    else:
        print(report)


def _cmd_compare(args: argparse.Namespace) -> None:
    from parserx.eval.compare import compare_results, format_compare_report
    from parserx.eval.runner import EvalRunner

    config_a, metadata_a = _load_cli_config(args.config_a, args.overrides_a, label=f"Compare {args.label_a}")
    config_b, metadata_b = _load_cli_config(args.config_b, args.overrides_b, label=f"Compare {args.label_b}")
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
        metadata_a=metadata_a,
        metadata_b=metadata_b,
    )

    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(report, encoding="utf-8")
        logging.info("Compare report written to %s", args.output)
    else:
        print(report)


def _cmd_tool_eval(args: argparse.Namespace) -> None:
    from parserx.tool_eval.adapters import (
        BuiltinDocPdfAdapter,
        LlamaParseAdapter,
        LiteParseAdapter,
        ParserXAdapter,
    )
    from parserx.tool_eval.runner import MultiToolEvalRunner

    config, _metadata = _load_cli_config(args.config, args.overrides, label="Tool Eval")
    include_docs = _resolve_include_docs(
        getattr(args, "include_docs", None),
        getattr(args, "include_list", None),
    )
    runner = MultiToolEvalRunner(
        tools=[
            LlamaParseAdapter(
                tier=args.llamaparse_tier,
                version=args.llamaparse_version,
            ),
            LiteParseAdapter(
                ocr_language=args.liteparse_ocr_language,
                dpi=args.liteparse_dpi,
            ),
            BuiltinDocPdfAdapter(),
            ParserXAdapter(config),
        ]
    )
    records = runner.evaluate_dir(
        args.ground_truth,
        args.artifacts_dir,
        include_docs=include_docs,
    )
    report = MultiToolEvalRunner.format_report(
        records,
        ground_truth_dir=args.ground_truth,
        artifacts_dir=args.artifacts_dir,
    )

    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(report, encoding="utf-8")
        logging.info("Tool-eval report written to %s", args.output)
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
    config = apply_overrides(loaded.config, overrides)
    metadata = build_config_report_metadata(
        config,
        loaded=loaded,
        overrides=overrides,
    )
    return config, metadata


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
