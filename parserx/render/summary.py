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
from parserx.ir.semantic import note_of
from parserx.ir.state import DocumentState, Missing
from parserx.render.markdown import image_file
from parserx.workspace.queries import HIDDEN, block_unit, ordered

_SHOWN = frozenset({BlockStatus.OK, BlockStatus.DEGRADED})


class OutlineEntry(IRModel):
    level: int | None
    text: str
    block: str
    page: int | None


class ImageEntry(IRModel):
    file: str  # path inside the package
    block: str | None
    page: int | None
    type: str | None  # one of FIGURE_TYPES (content, screenshot, chart …), when described
    route: str | None  # image route (SCAN / FIGURE / MIXED / UNCERTAIN / DECORATIVE), when routed
    shown: bool  # linked from the Markdown
    summary: str | None


class OpenItem(IRModel):
    target: str  # block id or page ("p3")
    kind: str
    detail: str
    quotes: list[str] = []  # the document text the item is about


class OccludedText(IRModel):
    """Text the page draws under another element: kept in the output, named here (Q71)."""

    target: str  # block id
    page: int | None
    quotes: list[str]
    reason: str


class AgentOverride(IRModel):
    """A change the agent made against a program's comparison (execution plan §3.4): adopted on its reading of the
    image, the comparison kept as a signal — a native number changed, a region reading that drops characters or
    numbers, text the local reading does not show.  Listed for review; the decision holds the whole record."""

    target: str  # block id
    page: int | None
    signals: list[str]  # native_numbers_changed · native_text_changed · region_numbers_changed · region_characters_lost · text_not_in_reading
    detail: str  # what the comparison found (before, after, the local reading)
    reason: str  # the agent's reason
    evidence: str  # the evidence id the agent cited


class Review(IRModel):
    """What is left to check (Q30): the status says whether processing finished; this says whether anything is
    still open — pages pending, failed blocks, titles without a level, possible table continuations."""

    open: int
    by_kind: dict[str, int]
    items: list[OpenItem]  # in page / block order, at most REVIEW_ITEMS_MAX
    checked: int = 0  # signals the agent checked against the image and closed (``close``)
    checked_by_kind: dict[str, int] = {}
    occluded: list[OccludedText] = []  # checked on the image: covered on the page, kept in the output
    agent_overrides: list[AgentOverride] = []  # changes the agent made against a comparison (§3.4)


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
    retries: int = 0  # sessions started again after a capacity failure


class Processing(IRModel):
    engines: dict[str, str]  # engine → version / model, from the readings the document uses
    requests: dict[str, int]
    cost_usd: float | None
    wall_time_s: float
    # ``parserx parse`` (P4-1): fixed · hybrid:agent · hybrid:fallback (with the reason); None from other entries
    runtime: str | None = None
    runtime_note: str | None = None
    agent: AgentRecord | None = None


class DoubtEntry(IRModel):
    """A place where the original itself may be wrong (``ir.state.Doubt``): the Markdown has it as printed."""

    block: str
    page: int | None  # PDF page; None for DOCX
    printed: str
    suggested: str
    reason: str
    refused: bool  # from a correction the program refused because the page prints it so


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
    doubts: list[DoubtEntry] = []  # for a proofreading of the original (user 2026-10-01)
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
        doubts=_doubts(state),
        warnings=state.warnings,
        files={"markdown": f"{name}.md", "blocks": f"{name}.blocks.json", "images": f"{image_dir}/"},
    )


def summary_json(state: DocumentState, name: str, image_dir: str = "images") -> str:
    return json.dumps(document_summary(state, name, image_dir).model_dump(mode="json"), ensure_ascii=False,
                      indent=2) + "\n"


def package_images(state: DocumentState) -> list:
    """Every image of the document (Q42): the embedded originals (shown or not), the image each figure stands on
    (cut from a scanned page, or rendered from the page where the embedded bytes were unusable), and every image
    the Markdown links.  Working images (page renders, crops made to look at a region) are not document content."""
    wanted = {a.id for a in state.assets if a.role == "original"}
    for block in state.blocks:
        if block.kind == BlockKind.FIGURE or (block.kind == BlockKind.SCAN and block.status in _SHOWN):
            wanted |= {a.asset for a in block.anchors if isinstance(a, AssetAnchor)}
    return [a for a in state.assets if a.id in wanted]


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
                  occluded=_occluded(state), agent_overrides=_overrides(state),
                  items=[OpenItem(target=i.target, kind=i.kind.value, detail=i.detail, quotes=[q.doc_text for q in i.quotes])
                         for i in items[:REVIEW_ITEMS_MAX]])


def _doubts(state: DocumentState) -> list[DoubtEntry]:
    blocks = {b.id: b for b in state.blocks}
    out = []
    for d in state.doubts:
        block = blocks.get(d.block)
        page = block_unit(state, block) if block is not None and state.format == "pdf" else None
        out.append(DoubtEntry(block=d.block, page=page, printed=d.printed, suggested=d.suggested, reason=d.reason,
                              refused=d.refused))
    return out


def _occluded(state: DocumentState) -> list[OccludedText]:
    blocks = {b.id: b for b in state.blocks}
    out = []
    for c in state.closed:
        if c.occluded:
            block = blocks.get(c.target)
            out.append(OccludedText(target=c.target, quotes=c.quotes, reason=c.reason,
                                    page=block_unit(state, block) if block is not None and state.format == "pdf" else None))
    return out


_ADOPTED = frozenset({"adopted", "region_reading", "added"})


def _overrides(state: DocumentState) -> list[AgentOverride]:
    """Adopted changes whose decision carries a signal, on blocks still shown; one per region reading."""
    out, seen = [], set()
    for block in ordered(state):
        if block.status in HIDDEN:
            continue
        for d in block.decisions:
            if d.choice not in _ADOPTED or "signal" not in d.evidence:
                continue
            key = (str(d.evidence.get("evidence", "")), str(d.evidence["signal"]), str(d.evidence.get("signal_detail", "")))
            if d.choice == "region_reading" and key in seen:
                continue
            seen.add(key)
            out.append(AgentOverride(target=block.id, page=block_unit(state, block) if state.format == "pdf" else None,
                                     signals=str(d.evidence["signal"]).split(","), detail=key[2], reason=d.reason,
                                     evidence=key[0]))
    return out


def _summary(block: Block | None) -> str | None:
    note = note_of(block.semantic if block is not None else None)
    return note.caption if note is not None else None


def _engines(state: DocumentState) -> dict[str, str]:
    engines = dict(state.engines)
    for block in state.blocks:
        for observation in block.observations:
            if observation.engine not in ("agent",):
                engines.setdefault(observation.engine, observation.engine_version)
    return dict(sorted(engines.items()))
