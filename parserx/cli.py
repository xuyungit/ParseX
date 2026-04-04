"""ParserX command-line interface."""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

from parserx.config import load_config
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
    parse_cmd.add_argument("-v", "--verbose", action="store_true", help="Verbose logging")
    parse_cmd.add_argument(
        "--split-chapters", action="store_true",
        help="Split into chapter files (output must be a directory)",
    )

    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        sys.exit(1)

    # Setup logging
    level = logging.DEBUG if getattr(args, "verbose", False) else logging.INFO
    logging.basicConfig(level=level, format="%(levelname)s: %(message)s")

    if args.command == "parse":
        _cmd_parse(args)


def _cmd_parse(args: argparse.Namespace) -> None:
    config = load_config(args.config)
    pipeline = Pipeline(config)

    if args.split_chapters and args.output:
        # Directory output with chapter splitting
        final_path = pipeline.parse_to_dir(args.input, args.output)
        logging.info("Written to %s", args.output)
    elif args.output:
        # Single file output
        result = pipeline.parse(args.input)
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(result, encoding="utf-8")
        logging.info("Written to %s (%d chars)", args.output, len(result))
    else:
        # Stdout
        result = pipeline.parse(args.input)
        print(result)


if __name__ == "__main__":
    main()
