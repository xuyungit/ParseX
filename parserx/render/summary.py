"""The document summary of an output package (guide §4.5, Q42): ``<name>.json``.

What a reader or a downstream program needs to know about the document without parsing the Markdown or the
block sidecar: source, status and what is missing, the outline, tables, every extracted image (whether it is
shown, what it is), the engines and models that read it, the cost and the warnings.  It is derived from the
workspace state only, so it is byte-stable except for the processing statistics (as the sidecar).
"""

from __future__ import annotations

import json
from typing import Literal

from parserx.ir.anchor import AssetAnchor
from parserx.ir.base import IRModel
from parserx.ir.block import Block
from parserx.ir.enums import BlockKind, BlockStatus, DocumentStatus
from parserx.ir.semantic import ChartSemantic, DiagramSemantic, GenericSemantic
from parserx.ir.state import DocumentState, Missing
from parserx.render.markdown import image_file
from parserx.workspace.queries import HIDDEN, block_unit, ordered

_SHOWN = frozenset({BlockStatus.OK, BlockStatus.DEGRADED})
_IMAGE_ROLES = frozenset({"original", "crop"})  # page renders are working images, not document content


class OutlineEntry(IRModel):
    level: int | None
    text: str
    block: str
    page: int | None


class ImageEntry(IRModel):
    file: str  # path inside the package
    block: str | None
    page: int | None
    type: str | None  # chart / diagram / photo / seal / other, when described
    route: str | None  # image route (SCAN / FIGURE / MIXED / UNCERTAIN / DECORATIVE), when routed
    shown: bool  # linked from the Markdown
    summary: str | None


class OpenItem(IRModel):
    target: str  # block id or page ("p3")
    kind: str
    detail: str
    quotes: list[str] = []  # the document text the item is about


class Review(IRModel):
    """What is left to check (Q30): the status says whether processing finished; this says whether anything is
    still open — pages pending, failed blocks, titles without a level, possible table continuations."""

    open: int
    by_kind: dict[str, int]
    items: list[OpenItem]  # in page / block order, at most REVIEW_ITEMS_MAX
    checked: int = 0  # signals the agent checked against the image and closed (``close``)
    checked_by_kind: dict[str, int] = {}


REVIEW_ITEMS_MAX = 200


class AgentRecord(IRModel):
    """What the main agent of the hybrid runtime did (P4-1); its cost is the model's list price (Q57)."""

    engine: str
    model: str
    effort: str
    wall_s: float
    usd_at_list_price: float | None
    tool_calls: int
    changes: int  # accepted structure changes, corrections and table reviews
    added: int  # text added from the page image (``correct add``)
    closed: int  # review items checked on the image and closed
    review_open_before: int
    review_open_after: int
    audit: list[str] = []  # hygiene notes: paths outside the agent's directory it named, other tools it used


class Processing(IRModel):
    engines: dict[str, str]  # engine → version / model, from the readings the document uses
    requests: dict[str, int]
    cost_usd: float | None
    wall_time_s: float
    # ``parserx parse`` (P4-1): fixed · hybrid:agent · hybrid:fallback (with the reason); None from other entries
    runtime: str | None = None
    runtime_note: str | None = None
    agent: AgentRecord | None = None


class DocumentSummary(IRModel):
    schema_version: Literal[1] = 1
    name: str
    source: str
    source_sha256: str
    format: Literal["pdf", "docx"]
    status: DocumentStatus
    pages: int  # PDF pages or DOCX segments
    missing: list[Missing]
    outline: list[OutlineEntry]
    tables: int
    images: list[ImageEntry]
    processing: Processing
    review: Review
    warnings: list[str]
    files: dict[str, str]


def document_summary(state: DocumentState, name: str, image_dir: str = "images") -> DocumentSummary:
    shown = [b for b in ordered(state) if b.status not in HIDDEN]
    outline = [OutlineEntry(level=b.level, text=b.text.replace("\n", " ").strip(), block=b.id,
                            page=block_unit(state, b) if state.format == "pdf" else None)
               for b in shown if b.kind == BlockKind.TITLE]
    return DocumentSummary(
        name=name, source=state.source, source_sha256=state.source_sha256, format=state.format, status=state.status,
        pages=len(state.pages), missing=state.missing, outline=outline,
        tables=sum(1 for b in shown if b.kind == BlockKind.TABLE),
        images=image_entries(state, image_dir),
        processing=Processing(engines=_engines(state), requests=dict(sorted(state.stats.requests.items())),
                              cost_usd=state.stats.cost_usd, wall_time_s=state.stats.wall_time_s),
        review=_review(state),
        warnings=state.warnings,
        files={"markdown": f"{name}.md", "blocks": f"{name}.blocks.json", "images": f"{image_dir}/"},
    )


def summary_json(state: DocumentState, name: str, image_dir: str = "images") -> str:
    return json.dumps(document_summary(state, name, image_dir).model_dump(mode="json"), ensure_ascii=False,
                      indent=2) + "\n"


def package_images(state: DocumentState) -> list:
    """Every extracted image of the document (Q42): embedded originals and crops, not page renders."""
    return [a for a in state.assets if a.role in _IMAGE_ROLES]


def image_entries(state: DocumentState, image_dir: str = "images") -> list[ImageEntry]:
    users: dict[str, Block] = {}
    for block in ordered(state):
        for anchor in block.anchors:
            if isinstance(anchor, AssetAnchor):
                current = users.get(anchor.asset)
                # the block that shows the image wins over one that merely refers to it
                if current is None or (current.status not in _SHOWN and block.status in _SHOWN):
                    users[anchor.asset] = block
    routes = {r.id: r.route.value for r in state.images}
    entries = []
    for asset in package_images(state):
        block = users.get(asset.id)
        visible = block is not None and block.status in _SHOWN and block.kind in (BlockKind.FIGURE, BlockKind.SCAN)
        entries.append(ImageEntry(
            file=f"{image_dir}/{image_file(asset)}", block=block.id if block else None,
            page=block_unit(state, block) if block is not None and state.format == "pdf" else None,
            type=block.semantic.type if block is not None and block.semantic is not None else None,
            route=routes.get(asset.id), shown=visible, summary=_summary(block)))
    return entries


def _review(state: DocumentState) -> Review:
    from parserx.tools.views import unresolved_items  # the tools import this module (export)

    items = unresolved_items(state)
    by_kind: dict[str, int] = {}
    for item in items:
        by_kind[item.kind.value] = by_kind.get(item.kind.value, 0) + 1
    checked: dict[str, int] = {}
    for c in state.closed:
        checked[c.kind] = checked.get(c.kind, 0) + 1
    return Review(open=len(items), by_kind=dict(sorted(by_kind.items())), checked=len(state.closed),
                  checked_by_kind=dict(sorted(checked.items())),
                  items=[OpenItem(target=i.target, kind=i.kind.value, detail=i.detail, quotes=[q.doc_text for q in i.quotes])
                         for i in items[:REVIEW_ITEMS_MAX]])


def _summary(block: Block | None) -> str | None:
    semantic = block.semantic if block is not None else None
    if isinstance(semantic, GenericSemantic):
        return semantic.summary.value
    if isinstance(semantic, ChartSemantic):
        return semantic.title.value if semantic.title is not None else None
    if isinstance(semantic, DiagramSemantic):
        return semantic.diagram_type.value
    return None


def _engines(state: DocumentState) -> dict[str, str]:
    engines = dict(state.engines)
    for block in state.blocks:
        for observation in block.observations:
            if observation.engine not in ("agent",):
                engines.setdefault(observation.engine, observation.engine_version)
    return dict(sorted(engines.items()))
