"""The main agent of the hybrid runtime (plan P4-1, Q13, Q57): one interface; Codex, or our own loop (Q86).

An ``AgentRuntime`` works on the document's workspace until it ends or its deadline passes, and changes it only
through the tools; the hybrid runtime checks that afterwards (``verify_workspace``) and decides what is used.  How it
calls the tools is its ``adapter``: ``cli`` — Codex in a prepared directory (the workspace, ``px``, the task
``AGENTS.md``, the skills; ``hybrid.prepare_agent_dir``); ``call`` — function calls in this process
(``runtimes/loop.py``).  Other agent frameworks (Q62–Q64) plug in behind the same interface.

Codex: the command line of the experiments (``codex.exec_command``: model and effort explicit, isolated from the
user's Codex setup, the ``workspace-write`` sandbox with network for the tools' services, image viewing only
through ``ask_image``).  Whether it can run is decided from command-line return codes only; nothing under
``~/.codex`` is read.  Its process gets no service keys: they are removed from its environment, and only ``px``
loads them, inside the tool process.
"""

from __future__ import annotations

import os
import shutil
import signal
import subprocess
import threading
import time
from pathlib import Path
from typing import Callable, Iterable, Protocol

from parserx.ir.base import IRModel
from parserx.runtimes.codex import AgentUsage, AuditResult, audit_events, exec_command, read_events, usage_from_events
from parserx.runtimes.px import _SECRET_NAME

PROMPT = "Follow the task in AGENTS.md in the current directory. Work only with the files in this directory."


class AgentOutcome(IRModel):
    ok: bool  # the agent ended by itself, without error
    reason: str | None = None  # when not ok: failed · timeout (a notice code)
    detail: str | None = None  # e.g. the exit code or the agent's error message
    wall_s: float
    usage: AgentUsage | None = None
    usd_at_list_price: float | None = None
    audit: AuditResult | None = None
    last_message: str | None = None


class AgentRuntime(Protocol):
    engine: str
    model: str
    effort: str
    adapter: str  # how it calls the tools: "cli" (a prepared directory with ./px) or "call" (in-process)

    def available(self) -> tuple[bool, str | None]:
        """(usable, why not: a notice code) — cheap, no model request."""

    def run(self, work_dir: Path, deadline_s: float, log_dir: Path,
            on_event: Callable[[dict], None] | None = None) -> AgentOutcome:
        """Work in *work_dir* until done or *deadline_s* passes; logs go to *log_dir* (outside *work_dir*).
        Ctrl-C (KeyboardInterrupt) stops the agent and is raised again."""


def agent_env(environ: dict[str, str], secret_names: Iterable[str], secret_values: Iterable[str]) -> dict[str, str]:
    """The agent's environment: without the service settings, anything that looks like a secret, and any
    variable holding a secret value."""
    names, values = set(secret_names), {v for v in secret_values if v}
    return {k: v for k, v in environ.items()
            if k not in names and not _SECRET_NAME.search(k) and v not in values}


class CodexAgent:
    engine, adapter = "codex", "cli"

    def __init__(self, model: str, effort: str, *, env: dict[str, str], price=None, forbidden: dict[str, Path] | None = None,
                 executable: str = "codex", vision: str = "tool"):
        self.model, self.effort, self.vision = model, effort, vision  # vision: agent — its own image viewing on
        self.env = env  # already without secrets (agent_env)
        self.price = price  # PriceConfig of the model, for the list-price cost
        self.forbidden = forbidden or {}  # paths the agent must not name (the secrets file, the user's config)
        self.executable = executable

    def available(self) -> tuple[bool, str | None]:
        if shutil.which(self.executable, path=self.env.get("PATH")) is None:
            return False, "codex_not_found"
        try:
            proc = subprocess.run([self.executable, "login", "status"], env=self.env, stdin=subprocess.DEVNULL,
                                  capture_output=True, timeout=60)
        except (OSError, subprocess.TimeoutExpired):
            return False, "codex_not_working"
        return (True, None) if proc.returncode == 0 else (False, "codex_not_logged_in")

    def run(self, work_dir: Path, deadline_s: float, log_dir: Path,
            on_event: Callable[[dict], None] | None = None) -> AgentOutcome:
        work_dir, log_dir = Path(work_dir), Path(log_dir)
        log_dir.mkdir(parents=True, exist_ok=True)
        argv = exec_command(model=self.model, effort=self.effort, doc_dir=work_dir,
                            last_message=log_dir / "last_message.md", prompt=PROMPT, vision=self.vision)
        argv[0] = self.executable
        env = dict(self.env, RUST_LOG="codex_core=info")
        t0 = time.monotonic()
        timed_out = False
        with open(log_dir / "events.jsonl", "wb") as out, open(log_dir / "stderr.log", "wb") as err:
            proc = subprocess.Popen(argv, cwd=work_dir, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                    stderr=err, env=env, start_new_session=True)

            def pump() -> None:
                import json

                for line in proc.stdout:
                    out.write(line)
                    out.flush()
                    if on_event is not None:
                        try:
                            on_event(json.loads(line))
                        except ValueError:
                            pass

            reader = threading.Thread(target=pump, daemon=True)
            reader.start()
            try:
                exit_code = proc.wait(timeout=deadline_s)
            except subprocess.TimeoutExpired:
                timed_out = True
                _stop(proc)
                exit_code = proc.returncode
            except BaseException:  # Ctrl-C: the agent runs in its own session, so stop it here
                _stop(proc)
                raise
            reader.join(timeout=30)
        wall = round(time.monotonic() - t0, 1)
        events, _ = read_events(log_dir / "events.jsonl")
        usage = usage_from_events(events)
        audit = audit_events(events, doc_dir=work_dir, home=Path.home(), forbidden=self.forbidden)
        last = log_dir / "last_message.md"
        common = dict(wall_s=wall, usage=usage, usd_at_list_price=list_price(self.price, usage), audit=audit,
                      last_message=last.read_text(encoding="utf-8") if last.is_file() else None)
        if timed_out:
            return AgentOutcome(ok=False, reason="agent_timeout", detail=f"{deadline_s / 60:.0f} min", **common)
        if exit_code != 0 or usage.failed_turns:
            detail = usage.errors[-1] if usage.errors else f"exit code {exit_code}"
            return AgentOutcome(ok=False, reason="agent_failed", detail=detail[:300], **common)
        return AgentOutcome(ok=True, **common)


def _stop(proc: subprocess.Popen) -> None:
    try:
        os.killpg(proc.pid, signal.SIGTERM)
    except ProcessLookupError:
        return
    try:
        proc.wait(timeout=15)
    except subprocess.TimeoutExpired:
        os.killpg(proc.pid, signal.SIGKILL)
        proc.wait()


def list_price(price, usage: AgentUsage | None) -> float | None:
    """The agent's tokens at the model's API list price (it runs on the Codex account; for comparison, Q57)."""
    if price is None or usage is None:
        return None
    fresh = usage.input_tokens - usage.cached_input_tokens
    return round((fresh * price.input + usage.cached_input_tokens * price.cached_input
                  + usage.output_tokens * price.output) / 1e6, 4)
