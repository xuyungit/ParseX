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
    parse_cmd.add_argument("-o", "--output", type=Path, help="Output Markdown path")
    parse_cmd.add_argument("-c", "--config", type=Path, help="Config YAML path")
    parse_cmd.add_argument("-v", "--verbose", action="store_true", help="Verbose logging")

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

    result = pipeline.parse(args.input)

    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(result, encoding="utf-8")
        logging.info("Written to %s (%d chars)", args.output, len(result))
    else:
        print(result)


if __name__ == "__main__":
    main()
