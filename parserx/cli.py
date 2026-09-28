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


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="parserx",
        description="ParserX — high-fidelity document parsing for knowledge bases and retrieval",
    )
    sub = parser.add_subparsers(dest="command")

    # parserx parse
    parse_cmd = sub.add_parser("parse", help="Parse documents to Markdown")
    parse_cmd.add_argument("input", nargs="+",
                           help="documents (PDF, DOCX, DOC, or images: JPG, PNG, TIFF …), directories holding them, "
                                "or web addresses (http, https)")
    parse_cmd.add_argument("-r", "--recursive", action="store_true",
                           help="take the documents of subdirectories too; the output keeps their relative paths")
    parse_cmd.add_argument(
        "-o", "--output", type=Path,
        help="Output directory (default: ./output/<filename>/; with several inputs: the parent, default ./output/)",
    )
    parse_cmd.add_argument("-c", "--config", type=Path, help="Config YAML path")
    parse_cmd.add_argument("--runtime", choices=("hybrid", "fixed"),
                           help="hybrid (default) hands documents with open review items to the agent; "
                                "fixed runs the standard processing only")
    parse_cmd.add_argument("--lang", choices=("zh", "en"), default=os.environ.get("PARSERX_LANG"),
                           help="language of the console and of the text ParserX adds to the Markdown (figure notes, "
                                "notes on missing content); default zh (output.lang)")
    parse_cmd.add_argument("--report", action="store_true",
                           help="also write the summary <name>.json (status, what is missing, outline, cost …)")
    parse_cmd.add_argument("--sidecar", action="store_true",
                           help="also write the block-level record <name>.blocks.json (for development and audit)")
    parse_cmd.add_argument("--json", action="store_true",
                           help="write the result summary as JSON to stdout at the end")
    parse_cmd.add_argument("-q", "--quiet", action="store_true", help="only errors and the result")
    parse_cmd.add_argument("--keep-work", action="store_true",
                           help="keep the work directory (<output>/.parserx-work/) after the run")
    parse_cmd.add_argument(
        "--set", dest="overrides", action="append", default=[],
        help="Override config with dotted.path=value (repeatable)",
    )
    parse_cmd.add_argument("-v", "--verbose", action="store_true", help="Verbose logging")
    parse_cmd.add_argument(
        "--stdout", action="store_true",
        help="Print Markdown to stdout instead of writing files",
    )
    # ── Convenience flags ──────────────────────────────────────────────
    parse_cmd.add_argument(
        "--no-vlm", action="store_true",
        help="Disable the VLM (figure descriptions, table and formula review)",
    )
    parse_cmd.add_argument(
        "--no-ocr", action="store_true",
        help="Disable the scan engine (native text only; scanned pages stay unrecognised)",
    )
    parse_cmd.add_argument("--vlm", metavar="MODEL",
                           help="the service model (figure descriptions, formula and table review): an entry of the "
                                "config's models; `parserx check` lists them")
    parse_cmd.add_argument("--agent", metavar="AGENT",
                           help="the agent that reviews what the program left open: codex (Codex on this machine) "
                                "or an entry of the config's models (our own loop with that model, billed by its API)")
    parse_cmd.add_argument("--no-agent", action="store_true",
                           help="the standard processing only (the same as --runtime fixed)")

    # parserx eval
    check_cmd = sub.add_parser("check", help="Check the setup: scan engine, service model, agent, LibreOffice, "
                                             "layout model")
    from parserx.check import add_arguments as _check_arguments

    _check_arguments(check_cmd)

    init_cmd = sub.add_parser("init", help="Write the personal config (~/.config/parserx/config.yaml): keys and model choices")
    init_cmd.add_argument("--force", action="store_true",
                          help="Write a new personal config even if one exists (the old one kept as config.yaml.bak)")
    init_cmd.add_argument("--no-download", action="store_true",
                          help="do not fetch the layout model now (it is fetched on first use)")

    # parserx dev … (Q114): evaluation, comparison and the document toolkit, for development
    dev_cmd = sub.add_parser("dev", help="Developer tools: evaluation, comparison, the document toolkit")
    dev = dev_cmd.add_subparsers(dest="dev_command")

    eval_cmd = dev.add_parser("eval", help="Evaluate parsing against ground truth")
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
    eval_cmd.add_argument(
        "--cache-mode", choices=["off", "read_write", "read_only", "refresh"],
        help="Response cache mode (default: from config)",
    )
    eval_cmd.add_argument("-o", "--output", type=Path, help="Output report path")
    eval_cmd.add_argument("-v", "--verbose", action="store_true", help="Verbose logging")

    # parserx compare
    compare_cmd = dev.add_parser("compare", help="Compare two parsing configs on the same ground truth")
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

    # parserx dev tool-eval run | score | view (docs/v2_benchmark_plan.md)
    tool_eval_cmd = dev.add_parser(
        "tool-eval",
        help="Compare external tools with ParserX: run them, score them, view them side by side",
    )
    tool_eval_sub = tool_eval_cmd.add_subparsers(dest="tool_eval_command", required=True)
    te_run = tool_eval_sub.add_parser("run", help="Run tools on ground-truth documents (kept results are not redone)")
    te_run.add_argument("--tools", default="parserx,llamaparse,mineru,datalab,paddleocr",
                        help="comma-separated: parserx, parserx-hybrid, parserx-agent, llamaparse, mineru, datalab, paddleocr")
    te_run.add_argument("--docs", default="", help="comma-separated document names")
    te_run.add_argument("--docs-file", type=Path, help="document names, one per line (# comments allowed)")
    te_run.add_argument("--force", action="store_true", help="redo results already on disk (requests again)")
    te_run.add_argument("--parserx-run", type=Path, default=Path("eval_runs/2026-09-29_bench2e_fixed_full"),
                        help="frozen run whose cache replays ParserX's fixed pipeline (no requests)")
    te_score = tool_eval_sub.add_parser("score", help="Score every result and write scores.json and report.md")
    te_view = tool_eval_sub.add_parser("view", help="Serve the comparison page")
    te_view.add_argument("--port", type=int, default=8765)
    for sub in (te_run, te_score, te_view):
        sub.add_argument("--out", type=Path, default=Path("eval_runs/bench"), help="results directory")
        sub.add_argument("--gt-dir", type=Path, action="append", default=None,
                         help="ground-truth directory (repeatable; default ground_truth and ground_truth_public)")
        sub.add_argument("-v", "--verbose", action="store_true", help="Verbose logging")

    # parserx init
    # parserx workspace … / parserx tool … (v2 document toolkit, JSON in and out)
    from parserx.tools.cli import add_parsers as _add_tool_parsers

    _add_tool_parsers(dev)

    parser.parse_command = parse_cmd  # inputs may come before, between and after options (main)
    return parser


def main() -> None:
    parser = build_parser()
    argv = sys.argv[1:]
    if argv[:1] == ["parse"] and not ({"-h", "--help"} & set(argv)):
        # `parserx parse in -r https://… -o out`: inputs between options (argparse wants them together)
        args = parser.parse_command.parse_intermixed_args(argv[1:])
        args.command = "parse"
    else:
        args = parser.parse_args()

    if not args.command:
        parser.print_help()
        sys.exit(1)
    if args.command == "dev":
        if not args.dev_command:
            parser.parse_args(["dev", "--help"])
        args.command = args.dev_command

    if args.command in ("tool", "workspace"):
        from parserx.tools.cli import main as _tool_main

        sys.exit(_tool_main(args))

    level = logging.DEBUG if getattr(args, "verbose", False) else logging.INFO
    logging.basicConfig(level=level, format="%(levelname)s: %(message)s")
    logging.getLogger("pdfminer").setLevel(logging.INFO)

    if args.command == "parse":
        sys.exit(_cmd_parse(args))
    if args.command == "check":
        from parserx.check import run as _check

        sys.exit(_check(args))
    if args.command == "init":
        _cmd_init(force=args.force, download=not args.no_download)
    elif args.command == "eval":
        _cmd_eval(args)
    elif args.command == "compare":
        _cmd_compare(args)
    elif args.command == "tool-eval":
        _cmd_tool_eval(args)


_RETIRED = ("providers", "processors", "verification", "pipeline")  # sections of the v1 config


def personal_template(values: dict[str, str] | None = None) -> str:
    """The personal config ``parserx init`` writes, with the values an old ``.env`` gave (Q109)."""
    import json

    values = values or {}

    def value(key: str) -> str:
        return json.dumps(values.get(key, ""), ensure_ascii=False)

    def endpoint(key: str) -> str:
        return f"\n    endpoint: {value(key)}" if values.get(key) else ""

    text = (Path(__file__).parent / "config" / "personal.yaml").read_text(encoding="utf-8")
    return text.format(openai_key=value("OPENAI_API_KEY"), openai_endpoint=endpoint("OPENAI_BASE_URL"),
                       ocr_token=value("PADDLE_OCR_TOKEN"), ocr_endpoint=endpoint("PADDLE_OCR_ENDPOINT"))


def _old_format(path: Path) -> bool:
    """A personal config of v1, or of the time keys lived in .env: it would override the built-in services."""
    import yaml

    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError):
        return True
    vlm = (data.get("services") or {}).get("vlm") or {}
    return any(k in data for k in _RETIRED) or "llm" in (data.get("services") or {}) or bool(vlm and "use" not in vlm)


def _cmd_init(force: bool = False, config_dir: Path | None = None, download: bool = False) -> None:
    """Write the personal config (Q107, Q109): keys and choices over the built-in defaults.  An old config (v1, or
    one that spells out the services) is kept as ``config.yaml.v1.bak``; the values of an old ``.env`` beside it
    are carried over once, and ``.env`` is not read any more."""
    from dotenv import dotenv_values

    from parserx.config.schema import config_dir as _config_dir

    config_dir = config_dir or _config_dir()
    config_path, env_path = config_dir / "config.yaml", config_dir / ".env"
    config_dir.mkdir(parents=True, exist_ok=True)
    if download:
        from parserx.check import fetch_layout_model
        from parserx.config.schema import load_config

        fetch_layout_model(load_config())
    if config_path.exists():
        old = _old_format(config_path)
        if not (old or force):
            print(f"  exists: {config_path} (--force writes a new one, keeping this as config.yaml.bak)", file=sys.stderr)
            return
        backup = config_path.with_name("config.yaml.v1.bak" if old else "config.yaml.bak")
        config_path.replace(backup)
        print(f"  kept the old config as {backup}", file=sys.stderr)
    values = {k: v for k, v in dotenv_values(env_path).items() if v} if env_path.is_file() else {}
    fd = os.open(config_path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        handle.write(personal_template(values))
    print(f"Created: {config_path}", file=sys.stderr)
    if values:
        carried = sorted(k for k in values if k in {"OPENAI_API_KEY", "OPENAI_BASE_URL", "PADDLE_OCR_TOKEN",
                                                    "PADDLE_OCR_ENDPOINT"})
        print(f"  carried over from {env_path}: {', '.join(carried) or 'nothing'}; .env is not read any more",
              file=sys.stderr)
    print("\nNext: fill in the keys you use, then run `parserx check`.", file=sys.stderr)


def _cmd_parse(args: argparse.Namespace) -> int:
    # Build overrides from convenience flags (applied before --set)
    flag_overrides = _collect_flag_overrides(args)
    all_overrides = flag_overrides + list(args.overrides)

    loaded = load_config_with_result(args.config)
    if getattr(args, "agent", None) and getattr(args, "no_agent", False):
        print("parserx: --agent and --no-agent contradict each other", file=sys.stderr)
        return 2
    try:
        config = apply_overrides(loaded.config, all_overrides)
    except ValueError as exc:  # a model name the config does not have
        print(f"parserx: {exc}", file=sys.stderr)
        return 2
    args.lang = config.output.lang  # the console speaks the output's language (Q120)
    from parserx.console.cli import parse_v2

    return parse_v2(args, config, loaded)


def _collect_flag_overrides(args: argparse.Namespace) -> list[str]:
    """Convert convenience flags to dotted-path config overrides."""
    overrides: list[str] = []
    if getattr(args, "no_vlm", False):
        overrides.append("services.vlm.endpoint=")
    if getattr(args, "no_ocr", False):
        overrides.append("builders.ocr.engine=none")
    if getattr(args, "vlm", None):
        overrides.append(f"services.vlm.use={args.vlm}")
    agent = getattr(args, "agent", None)
    if agent == "codex":
        overrides.append("runtime.agent.engine=codex")
    elif agent:
        overrides += ["runtime.agent.engine=loop", f"runtime.agent.use={agent}"]
    if agent or getattr(args, "runtime", None) == "hybrid":
        overrides.append("runtime.mode=hybrid")
    if getattr(args, "no_agent", False) or getattr(args, "runtime", None) == "fixed":
        overrides.append("runtime.mode=fixed")
    if getattr(args, "report", False):
        overrides.append("output.report=true")
    if getattr(args, "sidecar", False):
        overrides.append("output.sidecar=true")
    if getattr(args, "lang", None):
        overrides.append(f"output.lang={args.lang}")
    return overrides


def _cmd_eval(args: argparse.Namespace) -> None:
    from parserx.eval.runner import EvalRunner

    overrides = list(args.overrides)
    cache_mode = getattr(args, "cache_mode", None)
    if cache_mode:
        overrides.append(f"cache.mode={cache_mode}")
    config, metadata = _load_cli_config(args.config, overrides, label="Eval")
    runner = EvalRunner(config)
    include_docs = _resolve_include_docs(
        getattr(args, "include_docs", None),
        getattr(args, "include_list", None),
    )
    results = runner.evaluate_dir(args.ground_truth, include_docs=include_docs)
    report = EvalRunner.format_report(
        results,
        metadata=metadata,
        failed_docs=runner.failed_docs,
        not_executed=runner.not_executed,
    )

    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(report, encoding="utf-8")
        logging.info("Report written to %s", args.output)
    else:
        print(report)

    # Hard checks (failed or not-executed documents) fail the command (guide §9.2).
    from parserx.eval.gate import evaluate_gate, run_record

    outcome = evaluate_gate(
        run_record(results, failed=runner.failed_docs, not_executed=runner.not_executed),
        baseline=None,
    )
    if outcome.exit_code:
        sys.exit(outcome.exit_code)


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
    from parserx.tool_eval import runner

    gt_dirs = args.gt_dir or list(runner.DEFAULT_GT_DIRS)
    if args.tool_eval_command == "view":
        from parserx.tool_eval.viewer import serve

        serve(args.out, gt_dirs, port=args.port)
        return
    if args.tool_eval_command == "score":
        record = runner.score(args.out, gt_dirs)
        print(f"Scored {sum(len(v) for v in record['scores'].values())} results: {args.out / 'report.md'}")
        return

    from parserx.tool_eval import adapters

    makers = {
        "parserx": lambda: adapters.ParserXFixedAdapter(args.parserx_run),
        "parserx-hybrid": lambda: adapters.ParserXHybridAdapter(),
        "parserx-agent": lambda: adapters.ParserXHybridAdapter(always=True),
        "llamaparse": lambda: adapters.LlamaParseAdapter("agentic"),
        "mineru": lambda: adapters.MinerUAdapter("vlm"),
        "datalab": lambda: adapters.DatalabAdapter("accurate"),
        "paddleocr": lambda: adapters.PaddleOCRVLAdapter(),
    }
    names = [t.strip() for t in args.tools.split(",") if t.strip()]
    unknown = [t for t in names if t not in makers]
    if unknown:
        sys.exit(f"unknown tools: {', '.join(unknown)} (known: {', '.join(makers)})")
    doc_names = [d.strip() for d in args.docs.split(",") if d.strip()]
    if args.docs_file:
        doc_names += runner.read_doc_names(args.docs_file)
    docs = runner.find_documents(gt_dirs, doc_names or None)
    runner.run_tools([makers[t]() for t in names], docs, args.out, force=args.force)
    record = runner.score(args.out, gt_dirs)
    print(f"Scored {sum(len(v) for v in record['scores'].values())} results: {args.out / 'report.md'}")


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
    if loaded.source == "missing" and loaded.resolved_path is not None:
        logging.warning("%s config file not found: %s; using the other layers", label, loaded.resolved_path)
    if len(loaded.layers) > 1:
        logging.info("%s config: %s", label, " + ".join(str(p.resolve()) for p in loaded.layers[1:]))
        return
    logging.warning("%s config: no project parserx.yaml or personal config (%s); using the built-in defaults "
                    "(`parserx init` writes a personal config)", label, Path.cwd())


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
