"""What tools show of the workspace: compact views, document text always in DocText."""

from __future__ import annotations

from collections import Counter
from typing import Literal

from parserx.ir.anchor import SourceAnchor
from parserx.ir.base import Affine, IRModel
from parserx.ir.block import Block
from parserx.ir.enums import BlockKind, BlockStatus, DocumentStatus, ObservationStatus, TaskKind
from parserx.ir.observation import Observation
from parserx.ir.state import DocumentState
from parserx.tables.grid import TableGrid
from parserx.tables.arithmetic import arithmetic_issues
from parserx.tables.merge import merge_candidates
from parserx.tools.envelope import DocText, Unresolved, UnresolvedKind
from parserx.workspace.queries import HIDDEN, block_unit, ordered, outline

NATIVE_ENGINES = frozenset({"native_pdf", "docx"})  # exact numbers: nothing to re-read on the image

PREVIEW = 80  # characters of document text in outline previews


class DocInfo(IRModel):
    id: str
    source: str
    format: Literal["pdf", "docx"]
    pages: int
    status: DocumentStatus
    version: int


class CellView(IRModel):
    row: int
    col: int
    rowspan: int
    colspan: int
    is_header: bool
    content: DocText


class TableView(IRModel):
    n_rows: int
    n_cols: int
    header_rows: int
    cells: list[CellView]


class BlockView(IRModel):
    id: str
    kind: BlockKind
    order: int
    status: BlockStatus
    level: int | None
    page: int | None  # PDF page or DOCX segment
    text: DocText | None
    table: TableView | None
    anchors: list[SourceAnchor] | None  # only when geometry was asked for
    chosen_observation: str | None


class ObservationView(IRModel):
    id: str
    block: str
    engine: str
    engine_version: str
    task: TaskKind
    status: ObservationStatus
    label: str | None
    text: DocText | None
    table: TableView | None
    det_confidence: float | None
    rec_confidence: float | None
    raw_ref: str | None
    error: str | None
    anchor: SourceAnchor | None  # only when geometry was asked for


class OutlineNode(IRModel):
    block: str
    level: int | None
    text: DocText  # preview


class ImageRef(IRModel):
    asset: str
    path: str  # absolute path of the image file
    width: int
    height: int
    coord_space: Literal["image_px"] = "image_px"
    transform: Affine | None = None  # image px → page pt (PDF); None when there is no page geometry


def table_view(grid: TableGrid | None) -> TableView | None:
    if grid is None:
        return None
    return TableView(n_rows=grid.n_rows, n_cols=grid.n_cols, header_rows=grid.header_rows, cells=[
        CellView(row=c.row, col=c.col, rowspan=c.rowspan, colspan=c.colspan, is_header=c.is_header,
                 content=DocText(doc_text=c.content)) for c in grid.cells])


def block_view(state: DocumentState, block: Block, *, geometry: bool = True) -> BlockView:
    return BlockView(
        id=block.id, kind=block.kind, order=block.order, status=block.status, level=block.level,
        page=block_unit(state, block), text=DocText(doc_text=block.text) if block.text else None,
        table=table_view(block.cells), anchors=block.anchors if geometry else None,
        chosen_observation=block.chosen_observation,
    )


def observation_view(block: Block, obs: Observation, *, geometry: bool = True) -> ObservationView:
    return ObservationView(
        id=obs.id, block=block.id, engine=obs.engine, engine_version=obs.engine_version, task=obs.task,
        status=obs.status, label=obs.label, text=DocText(doc_text=obs.text) if obs.text else None,
        table=table_view(obs.cells), det_confidence=obs.det_confidence, rec_confidence=obs.rec_confidence,
        raw_ref=obs.raw_ref, error=obs.error, anchor=obs.anchor if geometry else None,
    )


def outline_nodes(state: DocumentState) -> list[OutlineNode]:
    return [OutlineNode(block=b.id, level=b.level, text=DocText(doc_text=b.text[:PREVIEW])) for b in outline(state)]


def doc_info(state: DocumentState) -> DocInfo:
    return DocInfo(id=state.id, source=state.source, format=state.format, pages=len(state.pages),
                   status=state.status, version=state.version)


def unresolved_items(state: DocumentState) -> list[Unresolved]:
    """Open work in the document, in page / block order."""
    items: list[Unresolved] = []
    for page in state.pages:
        if page.status.value == "pending":
            items.append(Unresolved(target=f"p{page.n}", kind=UnresolvedKind.PAGE_PENDING,
                                    detail="content not acquired yet"))
    skipped = {m.block for m in state.missing if "budget" in m.reason}
    for missing in state.missing:
        kind = UnresolvedKind.BUDGET_SKIPPED if missing.block in skipped else UnresolvedKind.BLOCK_FAILED
        items.append(Unresolved(target=missing.block, kind=kind, detail=missing.reason))
    listed = {m.block for m in state.missing}
    for block in state.blocks:
        if block.status == BlockStatus.FAILED and block.id not in listed:
            items.append(Unresolved(target=block.id, kind=UnresolvedKind.BLOCK_FAILED,
                                    detail=block.decisions[-1].reason if block.decisions else "failed"))
        elif block.kind == BlockKind.TITLE and block.level is None and block.status not in HIDDEN:
            items.append(Unresolved(target=block.id, kind=UnresolvedKind.STRUCTURE_PENDING,
                                    detail="title level not decided"))
        elif block.status == BlockStatus.DEGRADED and any(d.choice == "pending" for d in block.decisions):
            items.append(Unresolved(target=block.id, kind=UnresolvedKind.STRUCTURE_PENDING,
                                    detail="structure left pending"))
    for block in ordered(state):  # recognized tables whose own arithmetic points at a misread digit
        if block.kind == BlockKind.TABLE and block.cells is not None and block.status not in HIDDEN \
                and _chosen_engine(block) not in NATIVE_ENGINES:
            items += [Unresolved(target=block.id, kind=UnresolvedKind.TABLE_ARITHMETIC, detail=issue)
                      for issue in arithmetic_issues(block.cells)]
    for candidate in merge_candidates(state):
        items.append(Unresolved(target=candidate.second, kind=UnresolvedKind.TABLE_MERGE_CANDIDATE,
                                detail=f"may continue {candidate.first}: "
                                       + ", ".join(f"{k}={v}" for k, v in candidate.evidence.items())))
    return items


def _chosen_engine(block: Block) -> str | None:
    chosen = next((o for o in block.observations if o.id == block.chosen_observation), None)
    return chosen.engine if chosen is not None else None


def unresolved_counts(state: DocumentState) -> dict[UnresolvedKind, int]:
    return dict(sorted(Counter(u.kind for u in unresolved_items(state)).items()))
