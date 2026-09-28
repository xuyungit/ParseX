"""``parserx parse`` with pipeline v2 (plan P4-1; Q13, Q57, Q61): the hybrid runtime.

1. ``workspace init`` (stage *read*);
2. the standard processing, ``process`` (stage *process*);
3. export of the fixed-sequence result into the work directory — the fallback;
4. routing: open review items (``review.open > 0``) or a status other than complete → the agent; otherwise the
   fixed result is the result (and always with ``runtime.mode: fixed``); ``runtime.agent_when: always`` hands every
   document to the agent (Q135);
5. the agent works on the same workspace through the tools (stage *agent*);
6. the workspace is verified (``verify_workspace``: every change made by a tool call) and exported again
   (stage *export*); the document summary records the runtime and what the agent did.

Fallback to the result of step 3, with the reason in the summary and on the console: the agent is not available
(Codex missing or not logged in), fails, runs past its deadline (Q39), or the workspace no longer verifies.

The work directory ``<out>/.parserx-work/`` is fixed: an interrupted run (Ctrl-C) continues from its workspace —
the steps already done are not done again.  It is removed after a run that needs nothing more; it stays after a
fallback of an agent that could not run or finish, so the same command later continues with the agent.

Service keys never reach the agent: its environment is stripped (``agent.agent_env``), its config names them as
``${…}`` references, and ``px`` loads them from a private file outside the agent's directory, inside the tool
process only.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

import yaml

from parserx.config.schema import ParserXConfig
from parserx.eval.reporting import config_fingerprint
from parserx.ir.base import IRModel
from parserx.ir.enums import DocumentStatus, PageStatus
from parserx.ir.state import Missing
from parserx.render.export import IMAGE_DIR
from parserx.render.summary import AgentRecord, DocumentSummary
from parserx.runtimes.actions import AgentTally, CallFollower, actions
from parserx.runtimes.agent import AgentRuntime, CodexAgent, agent_env
from parserx.runtimes.events import (
    DocEnd,
    DocStart,
    Notice,
    Reporter,
    ReviewCount,
    StageEnd,
    StageStart,
    null_reporter,
)
from parserx.runtimes.px import _SECRET_NAME
from parserx.tools import ToolContext, call_tool, workspace_init
from parserx.workspace import Workspace, verify_workspace

WORK_DIR = ".parserx-work"
_RUN_FILE = "run.json"


class ParseFailure(RuntimeError):
    """No document could be produced (unreadable input, a failed standard step, a check that does not pass)."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


class ParseOutcome(IRModel):
    name: str
    source: str
    format: str  # pdf · docx
    out_dir: str
    markdown: str
    summary: str | None  # the summary file, when written (--report, Q116)
    blocks: str | None  # the block-level sidecar, when written (--sidecar)
    status: DocumentStatus
    runtime: str  # fixed · hybrid:agent · hybrid:fallback
    runtime_note: str | None = None  # why: a notice code (no_review_items, mode_fixed, codex_not_logged_in …)
    runtime_detail: str | None = None
    pages: int
    tables: int
    images: int  # shown
    titles: int
    review_open: int
    review_by_kind: dict[str, int]
    missing: list[Missing]  # what could not be read, and why (status partial)
    wall_s: float
    service_usd: float | None
    agent: AgentRecord | None = None
    work_dir: str | None = None  # kept for a later run to continue from


# ── The run ──────────────────────────────────────────────────────────────


def parse_document(input_path: Path | str, out_dir: Path | str, config: ParserXConfig, *,
                   agent: AgentRuntime | None = None, reporter: Reporter = null_reporter,
                   context_class: type[ToolContext] = ToolContext, keep_work: bool = False) -> ParseOutcome:
    """The hybrid runtime (``runtime.mode: hybrid``) or the fixed sequence alone (``fixed``); *agent* defaults to
    Codex as configured (``runtime.agent``).  *keep_work* keeps the work directory in every case (debugging,
    the hygiene audit of validation runs)."""
    keep_always = keep_work
    started = time.monotonic()
    source, out_dir = Path(input_path).resolve(), Path(out_dir).resolve()
    name = source.stem
    work = out_dir / WORK_DIR
    agent_dir = work / "agent"
    ws_dir = agent_dir / "ws"
    key = {"input_sha256": hashlib.sha256(source.read_bytes()).hexdigest(), "config": config_fingerprint(config)}

    # 1. read
    reporter(StageStart("read"))
    t = time.monotonic()
    resumed = _resumable(work, ws_dir, key)
    if not resumed:
        if work.exists():
            shutil.rmtree(work)
        agent_dir.mkdir(parents=True)
        _write_run(work, key, "read")
        envelope, _ = workspace_init(source, ws_dir, config=config)
        if not envelope.ok:
            shutil.rmtree(work, ignore_errors=True)
            raise ParseFailure("unreadable", envelope.failures[0].message)
    state = Workspace.open(ws_dir).load()
    scanned = sum(1 for p in state.pages if p.status == PageStatus.PENDING)
    reporter(DocStart(name=name, source=source.name, format=state.format, pages=len(state.pages), scanned=scanned,
                      resumed=resumed))
    reporter(StageEnd("read", round(time.monotonic() - t, 1), detail={"resumed": resumed}))

    # 2. the standard processing
    _write_run(work, key, "process")
    reporter(StageStart("process"))
    t = time.monotonic()
    context = _Session(reporter, context_class)
    envelope, code = call_tool("run_pipeline", ws_dir, {}, config=config, context_factory=context)
    if code == 1 or not envelope.ok:
        raise ParseFailure("process_failed", envelope.failures[0].message if envelope.failures else "process failed")
    if envelope.failures:
        reporter(Notice("tool_failures", "warning", {"failures": [f.model_dump(mode="json")
                                                                  for f in envelope.failures]}))
    # 3. the fixed-sequence result, kept as the fallback
    fixed = work / "fixed"
    if fixed.exists():
        shutil.rmtree(fixed)
    _export(ws_dir, fixed, name, config, context)
    before = _summary(fixed, name)
    reporter(StageEnd("process", round(time.monotonic() - t, 1)))
    reporter(ReviewCount(before.review.open, before.review.by_kind))

    # 4. routing
    runtime, note, detail, record = "fixed", None, None, None
    keep_work = False
    if config.runtime.mode == "fixed":
        note = "mode_fixed"
    elif before.review.open == 0 and before.status == DocumentStatus.COMPLETE and config.runtime.agent_when == "open":
        note = "no_review_items"
    else:
        agent = agent or make_agent(config, context_class)
        runtime, note, detail, record, keep_work = _agent_stage(agent, agent_dir, ws_dir, work, out_dir, name,
                                                                config, before, source, reporter, context_class)
    if note in ("mode_fixed", "no_review_items"):
        reporter(StageEnd("agent", 0.0, skipped=note))

    # 6. the result in place, with the runtime recorded
    reporter(StageStart("export"))
    t = time.monotonic()
    if runtime != "hybrid:agent":
        _clear_package(out_dir, name)
        _install(fixed, out_dir, name)
    summary = _record_runtime(out_dir, name, runtime, note, detail, record)
    written = _hand_over(out_dir, name, config)
    reporter(StageEnd("export", round(time.monotonic() - t, 1)))
    if keep_work or keep_always:
        _write_run(work, key, "agent_pending" if keep_work else "done")
    else:
        shutil.rmtree(work, ignore_errors=True)
    outcome = ParseOutcome(
        name=name, source=source.name, format=summary.format, out_dir=str(out_dir), markdown=str(out_dir / f"{name}.md"),
        summary=written.get("summary"), blocks=written.get("blocks"), status=summary.status,
        runtime=runtime, runtime_note=note, runtime_detail=detail, pages=summary.pages, tables=summary.tables,
        images=sum(1 for i in summary.images if i.shown), titles=len(summary.outline),
        review_open=summary.review.open, review_by_kind=summary.review.by_kind, missing=summary.missing,
        wall_s=round(time.monotonic() - started, 1), agent=record,
        service_usd=summary.processing.cost_usd if any(summary.processing.requests.values()) else 0.0,
        work_dir=str(work) if keep_work or keep_always else None,
    )
    reporter(DocEnd(outcome))
    return outcome


def _agent_stage(agent: AgentRuntime, agent_dir: Path, ws_dir: Path, work: Path, out_dir: Path, name: str,
                 config: ParserXConfig, before: DocumentSummary, source: Path, reporter: Reporter,
                 context_class: type[ToolContext]):
    """(runtime, note, detail, agent record, keep the work directory)."""
    reporter(StageStart("agent", {"engine": agent.engine, "model": agent.model}))
    t = time.monotonic()
    usable, why = agent.available()
    if not usable:
        reporter(StageEnd("agent", 0.0, ok=False, skipped=why))
        return "hybrid:fallback", why, None, None, True
    minutes = deadline_minutes(config, before.pages)
    keys_dir = Path(tempfile.mkdtemp(prefix="parserx-keys-"))
    tally = AgentTally()
    try:
        if agent.adapter == "cli":
            prepare_agent_dir(agent_dir, config, keys_dir / "services.env", input_name=source.name, minutes=minutes)
        if isinstance(agent, CodexAgent):
            agent.forbidden = {**agent.forbidden, "service keys": keys_dir}

        def on_record(record: dict) -> None:
            tally.add(record)
            for action in actions(record, _text_lookup(ws_dir)):
                reporter(action)

        with CallFollower(ws_dir / "calls.jsonl", on_record):
            outcome = agent.run(agent_dir, minutes * 60, work / "agent_run")
    finally:
        shutil.rmtree(keys_dir, ignore_errors=True)
    seconds = round(time.monotonic() - t, 1)
    integrity = verify_workspace(ws_dir)
    reason, detail = outcome.reason, outcome.detail
    if outcome.ok and not integrity.ok:
        reason, detail = "workspace_tampered", "; ".join(integrity.problems)[:300]
    exported = None
    if reason is None:
        try:
            _clear_package(out_dir, name)
            _export(ws_dir, out_dir, name, config, context_class)
            exported = _summary(out_dir, name)
        except ParseFailure as exc:
            reason, detail = "export_failed", str(exc)
    after = exported.review.open if exported is not None else before.review.open
    record = AgentRecord(
        engine=agent.engine, model=agent.model, effort=agent.effort, wall_s=outcome.wall_s,
        usd_at_list_price=outcome.usd_at_list_price, tool_calls=tally.tool_calls, changes=tally.changes,
        added=tally.added, closed=tally.closed, review_open_before=before.review.open, review_open_after=after,
        audit=[f"{h.kind}: {h.detail}" for h in (outcome.audit.hits if outcome.audit else [])],
    )
    if record.audit:
        reporter(Notice("agent_audit", "warning", {"hits": record.audit}))
    if reason is not None:
        reporter(StageEnd("agent", seconds, ok=False, detail={"reason": reason, "detail": detail,
                                                               "open": before.review.open}))
        # a workspace changed outside the tools cannot be continued from; otherwise a later run can
        return "hybrid:fallback", reason, detail, record, reason != "workspace_tampered"
    reporter(StageEnd("agent", seconds, detail={"changes": record.changes, "added": record.added,
                                                "closed": record.closed, "open": after}))
    return "hybrid:agent", None, None, record, False


def make_agent(config: ParserXConfig, context_class: type[ToolContext] = ToolContext) -> AgentRuntime:
    """The agent the config names (``runtime.agent.engine``): Codex, or our own loop."""
    cfg = config.runtime.agent
    if cfg.engine == "loop":
        from parserx.runtimes.loop import LoopAgent

        return LoopAgent(cfg.model, cfg.effort, config=config, price=config.scheduling.prices.get(cfg.model),
                         context_class=context_class)
    return codex_agent(config)


def codex_agent(config: ParserXConfig) -> CodexAgent:
    cfg = config.runtime.agent
    from parserx.config.schema import config_dir

    secrets = _secret_values(config)
    env = agent_env(dict(os.environ), set(), secrets)
    return CodexAgent(cfg.model, cfg.effort, env=env, price=config.scheduling.prices.get(cfg.model),
                      forbidden={"parserx config": config_dir()}, vision=cfg.vision)


def deadline_minutes(config: ParserXConfig, pages: int) -> int:
    cfg = config.runtime.agent
    return cfg.large_deadline_min if pages > cfg.large_pages else cfg.deadline_min


# ── The agent's directory ────────────────────────────────────────────────


def prepare_agent_dir(agent_dir: Path, config: ParserXConfig, keys_file: Path, *, input_name: str,
                      minutes: int) -> None:
    """``px``, ``parserx.yaml``, ``AGENTS.md`` and ``skills/`` next to the workspace (written afresh each run);
    the service keys go to *keys_file* (mode 0600, outside the directory)."""
    from parserx.runtimes.experiment import SKILL_FILES, compose_task, shipped_skills

    text, secrets = agent_config(config, agent_dir)
    keys_file.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(keys_file, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        handle.writelines(f"{k}={json.dumps(v)}\n" for k, v in secrets.items())
    (agent_dir / "parserx.yaml").write_text(text, encoding="utf-8")
    px = agent_dir / "px"
    px.write_text("#!/bin/sh\n# The ParserX document tools: ./px --help\n"
                  f'exec "{sys.executable}" -P -m parserx.runtimes.px --env-file "{keys_file}" '
                  '--config "$(cd "$(dirname "$0")" && pwd)/parserx.yaml" -- "$@"\n', encoding="utf-8")
    px.chmod(0o755)
    skills = shipped_skills()
    (agent_dir / "skills").mkdir(exist_ok=True)
    for file in SKILL_FILES:
        (agent_dir / "skills" / file).write_text(skills[file.removesuffix(".md")], encoding="utf-8")
    task = compose_task("cli", input_name=input_name, minutes=minutes, vision=config.runtime.agent.vision)
    (agent_dir / "AGENTS.md").write_text(task, encoding="utf-8")


def agent_config(config: ParserXConfig, agent_dir: Path) -> tuple[str, dict[str, str]]:
    """The effective config for the agent's tools, with every credential replaced by a ``${…}`` reference, and
    those credentials by name.  Its response cache lives in the agent's directory (the sandbox writes only there).
    The model entries stay behind (Q100): the places that use a model are already filled from its entry, so the
    other models' keys never reach the agent's side."""
    data = config.model_dump(mode="json")
    data.pop("models", None)
    for section, key in (("services", "vlm"), ("runtime", "agent")):
        data.get(section, {}).get(key, {}).pop("use", None)
    secrets: dict[str, str] = {}

    def walk(node: dict[str, Any]) -> None:
        for key, value in node.items():
            if isinstance(value, dict):
                walk(value)
            elif isinstance(value, str) and value and _SECRET_NAME.search(key):
                ref = f"PARSERX_SECRET_{len(secrets) + 1}"
                secrets[ref] = value
                node[key] = "${" + ref + "}"

    walk(data)
    if config.cache.mode != "off":
        data["cache"] = {"mode": config.cache.mode, "dir": str(Path(agent_dir) / ".parserx_cache")}
    text = ("# The agent's tool config (generated by parserx parse; keys are supplied by px when a tool runs)\n"
            + yaml.safe_dump(data, allow_unicode=True, sort_keys=False))
    return text, secrets


def _secret_values(config: ParserXConfig) -> list[str]:
    return list(agent_config(config, Path("."))[1].values())


def _text_lookup(ws_dir: Path):
    cache: dict[str, str] = {}
    loaded = False

    def text_of(block: str) -> str | None:
        nonlocal loaded
        if block not in cache and not loaded:
            try:
                cache.update({b.id: b.text for b in Workspace.open(ws_dir).load().blocks})
            except (OSError, ValueError):
                return None
            loaded = True
        return cache.get(block)

    return text_of


# ── Work directory, export, summary ──────────────────────────────────────


class _Session:
    """One tool context for the in-process calls (shared meter and budget); it carries the reporter for
    progress events of the standard steps (P4-2)."""

    def __init__(self, reporter: Reporter = null_reporter, context_class: type[ToolContext] = ToolContext) -> None:
        self.context: ToolContext | None = None
        self.reporter, self.context_class = reporter, context_class

    def __call__(self, ws: Workspace, config: ParserXConfig) -> ToolContext:
        if self.context is None:
            self.context = self.context_class(ws, config)
            self.context.reporter = self.reporter
        return self.context


def _resumable(work: Path, ws_dir: Path, key: dict) -> bool:
    run_file = work / _RUN_FILE
    if not run_file.is_file() or not (ws_dir / "state.json").is_file():
        return False
    try:
        recorded = json.loads(run_file.read_text(encoding="utf-8"))
    except ValueError:
        return False
    if any(recorded.get(k) != v for k, v in key.items()):
        return False
    return verify_workspace(ws_dir).ok


def _write_run(work: Path, key: dict, stage: str) -> None:
    (work / _RUN_FILE).write_text(json.dumps({**key, "stage": stage}, indent=2) + "\n", encoding="utf-8")


def _export(ws_dir: Path, out: Path, name: str, config: ParserXConfig, context=None) -> None:
    envelope, _ = call_tool("export", ws_dir, {"out": str(out), "name": name}, config=config,
                            context_factory=context or ToolContext)
    if not envelope.ok or not envelope.result.accepted:
        why = (envelope.failures[0].message if envelope.failures else "submit failed") if not envelope.ok \
            else "; ".join(envelope.result.blockers)
        raise ParseFailure("export_failed", why)


def _summary(package: Path, name: str) -> DocumentSummary:
    return DocumentSummary.model_validate_json((package / f"{name}.json").read_text(encoding="utf-8"))


def _hand_over(out_dir: Path, name: str, config: ParserXConfig) -> dict[str, str]:
    """What the user gets (Q116): the Markdown and the images it links; the summary and the sidecar only when asked
    for.  The work directory keeps the whole package while the run needs it."""
    written = {}
    for key, suffix, wanted in (("summary", ".json", config.output.report), ("blocks", ".blocks.json",
                                                                            config.output.sidecar)):
        path = out_dir / f"{name}{suffix}"
        if wanted:
            written[key] = str(path)
        else:
            path.unlink(missing_ok=True)
    return written


def _clear_package(out_dir: Path, name: str) -> None:
    for suffix in (".md", ".json", ".blocks.json"):
        (out_dir / f"{name}{suffix}").unlink(missing_ok=True)
    shutil.rmtree(out_dir / IMAGE_DIR, ignore_errors=True)


def _install(package: Path, out_dir: Path, name: str) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    for suffix in (".md", ".json", ".blocks.json"):
        shutil.copyfile(package / f"{name}{suffix}", out_dir / f"{name}{suffix}")
    if (package / IMAGE_DIR).is_dir():
        shutil.copytree(package / IMAGE_DIR, out_dir / IMAGE_DIR)


def _record_runtime(out_dir: Path, name: str, runtime: str, note: str | None, detail: str | None,
                    record: AgentRecord | None) -> DocumentSummary:
    summary = _summary(out_dir, name)
    summary.processing.runtime = runtime
    summary.processing.runtime_note = f"{note}: {detail}" if note and detail else note
    summary.processing.agent = record
    (out_dir / f"{name}.json").write_text(
        json.dumps(summary.model_dump(mode="json"), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return summary
