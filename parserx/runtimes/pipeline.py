"""Fixed-sequence runtime (guide §7, plan P1-10): the toolkit in a fixed order, in process.

``workspace init`` → ``process`` (the standard steps: scan engine, layout,
figure descriptions, confirmed table continuations, titles, check — see
``tools/process.py``; since P2-5 the agent's first tool runs the very same
steps) → ``export``.

All tool calls of one document share one context: one meter and one budget.
The runtime only sequences tools; every decision is made and recorded inside
them.
"""

from __future__ import annotations

import hashlib
import logging
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path

from parserx.config.schema import ParserXConfig
from parserx.ir.enums import BlockKind
from parserx.models.results import ParseResult
from parserx.tools import ToolContext, call_tool, workspace_init
from parserx.tools.envelope import Envelope
from parserx.workspace import Workspace
from parserx.workspace.queries import HIDDEN

log = logging.getLogger(__name__)


class RuntimeFailure(RuntimeError):
    """A step failed in a way that leaves no exportable document."""


@dataclass
class RunOutcome:
    markdown: str
    sidecar_json: str
    markdown_path: Path
    sidecar_path: Path
    status: str
    envelopes: list[Envelope] = field(default_factory=list)
    context: ToolContext | None = None


class _Session:
    """One tool context for all calls on one document (shared meter and budget)."""

    def __init__(self) -> None:
        self.context: ToolContext | None = None

    def __call__(self, ws: Workspace, config: ParserXConfig) -> ToolContext:
        if self.context is None:
            self.context = ToolContext(ws, config)
        return self.context


def run(input_path: Path | str, ws_dir: Path | str, out_dir: Path | str, config: ParserXConfig,
        *, name: str | None = None, context_factory=None) -> RunOutcome:
    source, ws_dir, out_dir = Path(input_path), Path(ws_dir), Path(out_dir)
    envelopes: list[Envelope] = []
    session = context_factory or _Session()

    def call(tool: str, request: dict | None = None) -> Envelope:
        envelope, code = call_tool(tool, ws_dir, request or {}, config=config, context_factory=session)
        envelopes.append(envelope)
        if code == 1 or (not envelope.ok and tool == "run_pipeline"):
            raise RuntimeFailure(f"{tool}: {envelope.failures[0].message}")
        return envelope

    envelope, _ = workspace_init(source, ws_dir, config=config)
    envelopes.append(envelope)
    if not envelope.ok:
        raise RuntimeFailure(f"workspace init: {envelope.failures[0].message}")
    call("run_pipeline")  # the first draft (tools/process.py): what the agent starts from in the hybrid runtime
    envelope = call("export", {"out": str(out_dir), "name": name or Workspace.open(ws_dir).load().id})
    if not envelope.ok or not envelope.result.accepted:
        why = envelope.failures[0].message if not envelope.ok else "; ".join(envelope.result.blockers)
        raise RuntimeFailure(f"submit: {why}")
    md_path, sidecar_path = Path(envelope.result.markdown), Path(envelope.result.sidecar)
    return RunOutcome(markdown=md_path.read_text(encoding="utf-8"), sidecar_json=sidecar_path.read_text(encoding="utf-8"),
                      markdown_path=md_path, sidecar_path=sidecar_path, status=envelope.result.status.value,
                      envelopes=envelopes, context=session.context if isinstance(session, _Session) else None)


def parse_result(path: Path | str, config: ParserXConfig) -> ParseResult:
    """``Pipeline.parse_result`` for ``pipeline: v2``: same result type, plus the sidecar."""
    return _parse(Path(path), config, None)


def parse_to_dir(path: Path | str, out_dir: Path | str, config: ParserXConfig) -> ParseResult:
    """``parserx parse <doc> -o <dir>`` for ``pipeline: v2``: the output package in *out_dir* (guide §4.5, Q42)."""
    return _parse(Path(path), config, Path(out_dir))


def _parse(path: Path, config: ParserXConfig, out_dir: Path | None) -> ParseResult:
    started = time.monotonic()
    root = config.runtime.workspace_root
    with tempfile.TemporaryDirectory(prefix="parserx-v2-") as scratch:
        if root:
            digest = hashlib.sha256(path.read_bytes()).hexdigest()[:8]
            ws_dir = Path(root) / f"{path.stem}-{digest}"
            if ws_dir.exists():
                import shutil

                shutil.rmtree(ws_dir)
        else:
            ws_dir = Path(scratch) / "ws"
        session = _Session()
        try:
            outcome = run(path, ws_dir, out_dir or Path(scratch) / "out", config, name=path.stem,
                          context_factory=session)
        except RuntimeFailure:
            snap = session.context.meter.snapshot() if session.context else None
            if snap and snap.cache_misses:  # offline replay without recorded responses: not a parse failure
                return ParseResult(markdown="", cache_misses=dict(snap.cache_misses))
            raise
        state = Workspace.open(ws_dir).load()
    snap = session.context.meter.snapshot() if session.context else None
    figures = [b for b in state.blocks if b.kind == BlockKind.FIGURE]
    warnings = list(state.warnings) + [f"{m.block}: {m.reason}" for m in state.missing]
    log.info("v2 %s: %s in %.1fs", path.name, outcome.status, time.monotonic() - started)
    return ParseResult(
        markdown=outcome.markdown, page_count=len(state.pages),
        element_count=sum(1 for b in state.blocks if b.status not in HIDDEN),
        api_calls={svc: (snap.requests.get(svc, 0) if snap else 0) for svc in ("ocr", "vlm", "llm")},
        api_attempts=dict(snap.attempts) if snap else {}, ocr_pages=snap.pages.get("ocr", 0) if snap else 0,
        cache_hits=dict(snap.cache_hits) if snap else {}, cache_misses=dict(snap.cache_misses) if snap else {},
        tokens=snap.tokens if snap else {}, cost_usd=snap.cost_usd if snap else None,
        images_total=len(figures), images_skipped=sum(1 for b in figures if b.status in HIDDEN),
        warnings=warnings, sidecar_json=outcome.sidecar_json, document_status=outcome.status,
        markdown_path=outcome.markdown_path if out_dir is not None else None,
    )
