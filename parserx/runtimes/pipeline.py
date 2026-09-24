"""Fixed-sequence runtime (guide §7, plan P1-10): the toolkit in a fixed order, in process.

1. ``workspace init`` — extraction and ledger;
2. ``recognize`` (paddleocr) — only pages whose native layer failed;
3. ``recognize`` (layout) — shadow detection of pages, routing of figures;
4. ``describe_figure`` — every shown figure, within the budget;
5. structure (all through ``apply_structure``) — confirmed cross-page table
   continuations (``tables.merge``); then titles: DOCX styles and outline
   levels; PDF ``adapter:v1`` on native text and the scan engine's title
   labels, unified as one outline (a level rejected only because it depends
   on a title of the other source is sent again once both are in place);
6. ``check`` and 7. ``export``.

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
from parserx.hierarchy.docx_styles import ACTOR as DOCX_ACTOR, propose_docx_structure
from parserx.hierarchy.engine_titles import ACTOR as ENGINE_ACTOR, REASON as ENGINE_REASON, engine_titles
from parserx.hierarchy.levels import title_changes, unify_levels
from parserx.ir.enums import BlockKind, PageStatus
from parserx.models.results import ParseResult
from parserx.runtimes import v1_structure
from parserx.tables.merge import propose_merges
from parserx.tools import ToolContext, call_tool, workspace_init
from parserx.tools.envelope import Envelope
from parserx.workspace import Workspace
from parserx.workspace.queries import HIDDEN, ordered

log = logging.getLogger(__name__)
MERGE_ACTOR = "program:tables.merge"


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
        if code == 1:
            raise RuntimeFailure(f"{tool}: {envelope.failures[0].message}")
        return envelope

    envelope, _ = workspace_init(source, ws_dir, config=config)
    envelopes.append(envelope)
    if not envelope.ok:
        raise RuntimeFailure(f"workspace init: {envelope.failures[0].message}")
    state = Workspace.open(ws_dir).load()

    pending = [p.n for p in state.pages if p.status == PageStatus.PENDING]
    if pending:
        call("recognize", {"pages": pending, "engine": "paddleocr"})

    state = Workspace.open(ws_dir).load()
    figures = [b.id for b in state.blocks if b.kind == BlockKind.FIGURE and b.status not in HIDDEN]
    if config.runtime.layout_shadow and (figures or state.format == "pdf"):
        pages = [p.n for p in state.pages] if state.format == "pdf" else []
        call("recognize", {"pages": pages, "blocks": figures, "engine": "layout"})

    if config.runtime.describe_figures:
        state = Workspace.open(ws_dir).load()
        for block in state.blocks:
            if block.kind != BlockKind.FIGURE or block.status in HIDDEN or block.semantic is not None:
                continue
            envelope = call("describe_figure", {"block": block.id})
            if any(f.code == "budget_exhausted" for f in envelope.failures):
                break

    state = Workspace.open(ws_dir).load()
    merges = propose_merges(state)
    if merges:
        call("apply_structure", {"changes": merges, "actor": MERGE_ACTOR})

    state = Workspace.open(ws_dir).load()
    if state.format == "docx":
        steps = [(DOCX_ACTOR, propose_docx_structure(state))]
    else:
        steps = _pdf_titles(Workspace.open(ws_dir).source_path, state, config)
    held: list[tuple[str, list[dict]]] = []
    for actor, changes in steps:
        if changes:
            envelope = call("apply_structure", {"changes": changes, "actor": actor})
            held.append((actor, [changes[r.index] for r in envelope.result.rejected if r.rule == "level_skip"]))
    for actor, changes in held:  # a level can depend on a title of the other source, now in place
        if changes:
            call("apply_structure", {"changes": changes, "actor": actor})

    call("check")
    envelope = call("export", {"out": str(out_dir), "name": name or state.id})
    if not envelope.ok:
        raise RuntimeFailure(f"export: {envelope.failures[0].message}")
    md_path, sidecar_path = Path(envelope.result.markdown), Path(envelope.result.sidecar)
    return RunOutcome(markdown=md_path.read_text(encoding="utf-8"), sidecar_json=sidecar_path.read_text(encoding="utf-8"),
                      markdown_path=md_path, sidecar_path=sidecar_path, status=envelope.result.status.value,
                      envelopes=envelopes, context=session.context if isinstance(session, _Session) else None)


def _pdf_titles(source: Path, state, config: ParserXConfig) -> list[tuple[str, list[dict]]]:
    """PDF titles from two sources, unified as one outline: v1's detection on native text, the scan engine's labels."""
    native = v1_structure.matched_titles(source, state, config)
    scanned = engine_titles(state)
    position = {b.id: i for i, b in enumerate(ordered(state))}
    combined = sorted(native + scanned, key=lambda t: position[t[0]])
    levels = unify_levels([t[:3] for t in combined])
    return [(v1_structure.ACTOR, title_changes(native, levels, reason=v1_structure.REASON)),
            (ENGINE_ACTOR, title_changes(scanned, levels, reason=ENGINE_REASON))]


def parse_result(path: Path | str, config: ParserXConfig) -> ParseResult:
    """``Pipeline.parse_result`` for ``pipeline: v2``: same result type, plus the sidecar."""
    path = Path(path)
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
            outcome = run(path, ws_dir, Path(scratch) / "out", config, name=path.stem, context_factory=session)
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
    )
