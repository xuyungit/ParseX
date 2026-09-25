"""``px``: what an agent in an experiment directory may run (plan §2.2–§2.3).

    ./px workspace init <input> --ws ws --json
    ./px tool <name> --ws ws [options] --json      (the seven tools; ./px tool schema <name>)
    ./px python <script.py | -c code | ->          (the snapshot's Python, for read-only analysis)

The experiment's config is fixed (``--config`` is refused); service keys are
loaded from an env file outside the experiment directory and exist only in the
tool process — ``px python`` runs without them.  Every other ParserX command
(``parse``, ``eval`` …) is refused: results come from the workspace through
``export``.

The launcher script calls ``python -m parserx.runtimes.px --env-file F --config C -- <args>``.
"""

from __future__ import annotations

import argparse
import os
import re
import signal
import sys
from pathlib import Path

from parserx.tools.cli import TOOL_NAMES

USAGE = """px — the ParserX document tools of this experiment
  ./px workspace init <input> --ws ws --json
  ./px tool <name> --ws ws [options] --json     name: {tools}
  ./px tool schema <name>                       request and envelope JSON Schemas
  ./px tool <name> --help
  ./px python <script.py | -c code | ->         Python 3.13 with PyMuPDF, Pillow, NumPy (analysis only)
The configuration is fixed: --config is not accepted.
"""
_SECRET_NAME = re.compile(r"KEY|SECRET|TOKEN|PASSWORD|CREDENTIAL", re.I)


class PxRefused(ValueError):
    pass


def px_argv(args: list[str], config: Path) -> list[str]:
    """The ``parserx`` arguments for a px call, with the experiment's config; raises PxRefused."""
    if any(a in ("-c", "--config") or a.startswith("--config=") for a in args):
        raise PxRefused("the configuration is fixed for this experiment; drop --config")
    if len(args) >= 2 and args[0] == "tool" and args[1] == "schema":
        return list(args)
    if len(args) >= 2 and args[0] == "tool" and args[1] in TOOL_NAMES:
        return [*args, "--config", str(config)]
    if len(args) >= 2 and args[0] == "workspace" and args[1] == "init":
        return [*args, "--config", str(config)]
    raise PxRefused(f"not available here: {' '.join(args[:2]) or '(nothing)'}")


def python_env(env: dict[str, str], secret_names: set[str]) -> dict[str, str]:
    """The environment for ``px python``: without the service settings and anything that looks like a secret."""
    return {k: v for k, v in env.items() if k not in secret_names and not _SECRET_NAME.search(k)}


def _load_env_file(path: Path) -> dict[str, str]:
    from dotenv import dotenv_values

    return {k: v for k, v in dotenv_values(path).items() if v is not None}


def _terminated(signum, frame):
    raise KeyboardInterrupt


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="px", add_help=False)
    parser.add_argument("--env-file", type=Path, required=True)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("rest", nargs=argparse.REMAINDER)
    ns = parser.parse_args(argv)
    args = ns.rest[1:] if ns.rest[:1] == ["--"] else ns.rest
    usage = USAGE.format(tools=" ".join(TOOL_NAMES))
    if not args or args[0] in ("-h", "--help", "help"):
        print(usage)
        return 0
    secrets = _load_env_file(ns.env_file) if ns.env_file.is_file() else {}
    if args[0] == "python":
        env = python_env(dict(os.environ), set(secrets))
        os.execve(sys.executable, [sys.executable, *args[1:]], env)
    try:
        parserx_args = px_argv(args, ns.config)
    except PxRefused as exc:
        print(f"px: {exc}\n\n{usage}", file=sys.stderr)
        return 2
    os.environ.update(secrets)
    # A deadline or Ctrl-C stops the agent with SIGTERM: raise, so the running tool records its call (P4-1).
    signal.signal(signal.SIGTERM, _terminated)
    sys.argv = ["parserx", *parserx_args]
    from parserx.cli import main as parserx_main

    parserx_main()  # exits with the tool's code
    return 0


if __name__ == "__main__":
    sys.exit(main())
