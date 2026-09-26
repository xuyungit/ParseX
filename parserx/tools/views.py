"""What tools show of the workspace: compact views, document text always in DocText."""

from __future__ import annotations

from collections import Counter
from typing import Literal

from parserx.ir.anchor import SourceAnchor
from parserx.ir.base import Affine, IRModel
from parserx.ir.block import Block
from parserx.ir.enums import BlockKind, BlockStatus, DocumentStatus, ObservationStatus, RelationKind, TaskKind
from parserx.ir.observation import Observation
from parserx.ir.state import DocumentState
from parserx.tables.grid import TableGrid
from parserx.content.text_audit import suspicious_characters
from parserx.tables.arithmetic import arithmetic_issues
from parserx.tables.merge import merge_candidates
from parserx.tools.envelope import DocText, Unresolved, UnresolvedKind
from parserx.workspace.queries import HIDDEN, block_unit, ordered, outline
from parserx.hierarchy.layout_titles import layout_titles
from parserx.hierarchy.numbering_gaps import numbering_gaps
from parserx.reading.compare import unaccounted_lines, unseen_segments

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
    """Open work in the document, in page / block order; signals the agent checked and closed are left out."""
    closed = {(c.target, c.kind, tuple(c.quotes)) for c in state.closed}
    return [u for u in _all_items(state) if (u.target, u.kind.value, tuple(q.doc_text for q in u.quotes)) not in closed]


def _all_items(state: DocumentState) -> list[Unresolved]:
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
    shown = [b for b in ordered(state) if b.status not in HIDDEN]
    texts = {b.id: _all_text(b) for b in shown}
    document = list(texts.values())
    for block in shown:  # characters no reader can use: font encodings without Unicode, stray scripts
        found = suspicious_characters(texts[block.id], document) if texts[block.id] else None
        if found:
            items.append(Unresolved(target=block.id, kind=UnresolvedKind.TEXT_SUSPICIOUS,
                                    detail=found + "; compare with the image"))
    for block in ordered(state):  # recognized tables whose own arithmetic points at a misread digit
        if block.kind == BlockKind.TABLE and block.cells is not None and block.status not in HIDDEN \
                and _chosen_engine(block) not in NATIVE_ENGINES:
            items += [Unresolved(target=block.id, kind=UnresolvedKind.TABLE_ARITHMETIC, detail=issue)
                      for issue in arithmetic_issues(block.cells)]
    for n, lines in unaccounted_lines(state).items():  # the local page reading sees text no block has (Q56)
        places = ", ".join("(" + ", ".join(f"{v:.0f}" for v in ln.bbox) + ")" for ln in lines[:_QUOTES])
        items.append(Unresolved(
            target=f"p{n}", kind=UnresolvedKind.TEXT_UNACCOUNTED, quotes=_quotes([ln.text for ln in lines]),
            detail=f"{len(lines)} line(s) seen on the page image are in no block, at {places} (page points); "
                   "the quotes are the local reading; look at the page"))
    for block_id, segments in unseen_segments(state).items():
        items.append(Unresolved(
            target=block_id, kind=UnresolvedKind.TEXT_NOT_SEEN, quotes=_quotes(segments),
            detail=f"{len(segments)} segment(s) of this block are not seen on the page image where the block sits; "
                   "compare with the image"))
    from parserx.tools.formulas import pending_candidates

    for block_id, candidate in pending_candidates(state):  # the page reading has this passage with formulas (Q70)
        items.append(Unresolved(
            target=block_id, kind=UnresolvedKind.FORMULA_CANDIDATE, quotes=_quotes([candidate]),
            detail="the page reading writes this passage with its formulas as LaTeX, but it (and the editor's "
                   "version) lacks characters the text layer has; look at the image: if the reading is right, correct "
                   "the block with it (keep every character the image shows), else close the item"))
    for block_id in figures_without_content(state):  # an image shown with nothing a reader who cannot see it gets
        items.append(Unresolved(
            target=block_id, kind=UnresolvedKind.FIGURE_WITHOUT_CONTENT,
            detail="this image is shown without a description and without transcribed text after it; look at it: "
                   "describe it if it carries information, or close the item with the reason (a code, a logo …)"))
    for block_id, text, level, evidence in layout_titles(state):  # the page image shows a title the outline lacks
        items.append(Unresolved(
            target=block_id, kind=UnresolvedKind.TITLE_CANDIDATE, quotes=_quotes([text]),
            detail=f"the layout detector marks this line a section title and it is set apart from the body text "
                   f"({evidence['set_apart']}); the outline does not have it — look at the page and, if it is a "
                   f"title, set its role and level (proposed level {level})"))
    listed = {u.target for u in items if u.kind == UnresolvedKind.TITLE_CANDIDATE}
    for block_id, text, level, evidence in numbering_gaps(state):  # a numbered title sequence misses this number
        if block_id not in listed:
            items.append(Unresolved(
                target=block_id, kind=UnresolvedKind.TITLE_CANDIDATE, quotes=_quotes([text]),
                detail=f"the numbered titles around it miss the number this paragraph starts with "
                       f"({evidence['numbering']}); if it is a title, set its role and level (proposed level {level})"))
    for candidate in merge_candidates(state):
        items.append(Unresolved(target=candidate.second, kind=UnresolvedKind.TABLE_MERGE_CANDIDATE,
                                detail=f"may continue {candidate.first}: "
                                       + ", ".join(f"{k}={v}" for k, v in candidate.evidence.items())))
    return items


def figures_without_content(state: DocumentState) -> list[str]:
    """Shown figures whose content does not reach a reader who cannot see them (Q66, conservation): processing gave
    them content — a description was attempted, or text was transcribed from them — and none of it is in the output
    (the description failed, the transcription was removed).  Figures nothing was tried on yet are not listed."""
    shown = {b.id: b for b in state.blocks if b.status not in HIDDEN}
    contained: dict[str, list[str]] = {}
    for r in state.relations:
        if r.kind == RelationKind.CONTAINS:
            contained.setdefault(r.src, []).append(r.dst)
    out = []
    for block in ordered(state):
        if block.id not in shown or block.kind != BlockKind.FIGURE or block.semantic is not None:
            continue
        described = any(o.task == TaskKind.DESCRIBE for o in block.observations)
        transcribed = contained.get(block.id, [])
        if any(d in shown and _all_text(shown[d]).strip() for d in transcribed):
            continue
        if described or transcribed:
            out.append(block.id)
    return out


_QUOTES = 5  # quoted texts per item; the count says how many there are


def _quotes(texts: list[str]) -> list[DocText]:
    return [DocText(doc_text=t if len(t) <= 80 else t[:79] + "…") for t in texts[:_QUOTES]]


def _all_text(block: Block) -> str:
    cells = " ".join(c.content for c in block.cells.cells) if block.cells is not None else ""
    return (block.text or "") + (" " + cells if cells else "")


def _chosen_engine(block: Block) -> str | None:
    chosen = next((o for o in block.observations if o.id == block.chosen_observation), None)
    return chosen.engine if chosen is not None else None


def unresolved_counts(state: DocumentState) -> dict[UnresolvedKind, int]:
    return dict(sorted(Counter(u.kind for u in unresolved_items(state)).items()))
