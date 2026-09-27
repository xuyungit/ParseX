"""Cross-page table continuation (guide §6.9, Q33).

A **candidate** is what makes a merge possible at all: two visible tables with
the same number of columns, the second on the page right after the first
table's last page, with nothing between them in reading order except page
furniture (running headers, footers, page numbers: excluded by the scan
engine's labels or by ``content/furniture.py`` on native pages).

**Confirmation** needs more evidence: the left and right table edges line up
and the second table has no header of its own that differs from the first
table's (a repeated header is dropped when merging).  The fixed runtime merges
confirmed candidates; the others stay open for a runtime that can look at the
page images (``unresolved`` item ``table_merge_candidate``).

Merging appends the second table's rows to the first, keeps both tables'
anchors and marks the second ``merged``; its ledger items become ``merged``
into the first.  No cell text changes: only exact repetitions of the first
table's header rows are dropped.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from parserx.ir import ids
from parserx.ir.anchor import PdfAnchor
from parserx.ir.base import BBox
from parserx.ir.block import Block
from parserx.ir.decision import Decision
from parserx.ir.enums import BlockKind, BlockStatus, DecisionStage, RelationKind
from parserx.ir.relation import Relation
from parserx.ir.state import DocumentState
from parserx.layout.labels import FURNITURE
from parserx.tables.grid import Cell, TableGrid
from parserx.workspace.queries import HIDDEN, ordered

MAX_X_OFFSET = 0.05  # confirmed: the table edges within this share of the page width
SCAN_ENGINE = "paddleocr"  # its table boxes are as wide as their content: only the left edge is the table's

Evidence = dict[str, float | int | str | bool]


@dataclass(frozen=True)
class MergeCandidate:
    first: str
    second: str
    drop_rows: int  # leading rows of ``second`` that repeat the header of ``first``
    confirmed: bool
    evidence: Evidence


def merge_candidates(state: DocumentState) -> list[MergeCandidate]:
    """Candidates between each visible table and the next non-furniture block, in reading order."""
    sequence = [b for b in ordered(state) if b.status not in HIDDEN]
    out: list[MergeCandidate] = []
    for i, block in enumerate(sequence):
        if block.kind != BlockKind.TABLE:
            continue
        following = next((b for b in sequence[i + 1:] if not _is_furniture(b)), None)
        if following is not None:
            candidate = merge_candidate(state, block, following)
            if candidate is not None:
                out.append(candidate)
    return out


def merge_candidate(state: DocumentState, first: Block, second: Block) -> MergeCandidate | None:
    """The candidate for *second* continuing *first*, or None when the two cannot be one table."""
    if not (_is_table(first) and _is_table(second)) or first.cells.n_cols != second.cells.n_cols:
        return None
    first_page, second_page = _last_page(first), _first_page(second)
    if first_page is None or second_page != first_page + 1 or not _adjacent(state, first, second):
        return None
    header = first.cells.header_rows or 1
    repeated = second.cells.n_rows > header and repeats_header(first.cells, second.cells, header)
    own_header = second.cells.header_rows > 0 and not repeated
    left_only = _scanned(first) or _scanned(second)
    offset = _x_offset(state, first, second, first_page, second_page, left_only=left_only)
    evidence: Evidence = {
        "columns": first.cells.n_cols, "first_page": first_page, "second_page": second_page,
        "x_offset": -1.0 if offset is None else round(offset, 3), "edges": "left" if left_only else "both",
        "header_repeated": repeated, "second_has_own_header": own_header,
    }
    confirmed = offset is not None and offset <= MAX_X_OFFSET and not own_header
    return MergeCandidate(first=first.id, second=second.id, drop_rows=header if repeated else 0,
                          confirmed=confirmed, evidence=evidence)


def propose_merges(state: DocumentState) -> list[dict]:
    """``join`` changes for the confirmed candidates; a table over several pages merges into its first part."""
    work = state.model_copy(deep=True)
    changes: list[dict] = []
    while True:
        candidate = next((c for c in merge_candidates(work) if c.confirmed), None)
        if candidate is None:
            return changes
        reason = "cross-page table continuation: same columns, aligned edges, consecutive pages"
        merge_tables(work, candidate.first, candidate.second, candidate.drop_rows, actor="propose", reason=reason,
                     evidence=candidate.evidence)
        changes.append({"op": "join", "first": candidate.first, "second": candidate.second,
                        "drop_rows": candidate.drop_rows, "reason": reason, "evidence": candidate.evidence})


def repeats_header(first: TableGrid, second: TableGrid, rows: int) -> bool:
    """The first *rows* rows of *second* repeat those of *first* exactly (whitespace aside), no span crossing them."""
    if rows < 1 or rows >= second.n_rows or rows > first.n_rows:
        return False
    if any(c.row < rows < c.row + c.rowspan for c in second.cells):
        return False
    a, b = first.slot_matrix(), second.slot_matrix()
    return all([_norm(c) for c in a[r]] == [_norm(c) for c in b[r]] for r in range(rows))


def merge_grids(first: TableGrid, second: TableGrid, drop_rows: int) -> TableGrid:
    shift = first.n_rows - drop_rows
    moved = [cell.model_copy(update={"row": cell.row + shift}) for cell in second.cells if cell.row >= drop_rows]
    return TableGrid(n_rows=first.n_rows + second.n_rows - drop_rows, n_cols=first.n_cols,
                     cells=[*first.cells, *moved], header_rows=first.header_rows)


def merge_tables(state: DocumentState, first_id: str, second_id: str, drop_rows: int, *, actor: str, reason: str,
                 evidence: Evidence) -> None:
    """Append *second*'s rows to *first* (legality is checked by the caller)."""
    blocks = {b.id: b for b in state.blocks}
    first, second = blocks[first_id], blocks[second_id]
    moved = [e for e in state.ledger if e.block == second.id and e.disposition == "output"]
    rows_before = first.cells.n_rows
    first.cells = merge_grids(first.cells, second.cells, drop_rows)
    first.anchors = [*first.anchors, *second.anchors]
    second.status = BlockStatus.MERGED
    # what separating them again needs (unjoin, Q87): where the seam is, which ledger items moved
    first.decisions.append(Decision(stage=DecisionStage.STRUCTURE, choice="merge_table", reason=reason,
                                    evidence={**evidence, "drop_rows": drop_rows, "rows_before": rows_before,
                                              "moved_items": ",".join(e.item for e in moved)},
                                    actor=actor, refs=[second.id]))
    second.decisions.append(Decision(stage=DecisionStage.STRUCTURE, choice="merged_into", reason=reason,
                                     evidence={}, actor=actor, refs=[first.id]))
    state.relations.append(Relation(id=ids.relation_id(RelationKind.CONTINUES, first.id, second.id),
                                    kind=RelationKind.CONTINUES, src=first.id, dst=second.id))
    for entry in moved:
        entry.disposition, entry.block = "merged", first.id


def split_problem(state: DocumentState, first: Block, second: Block) -> str | None:
    """Why the table *second* merged into *first* cannot be separated again, or None."""
    merges = _joined(state, first)
    if not merges or merges[-1].refs != [second.id]:
        return f"{second.id} is not the last table joined to {first.id}: unjoin the later ones first"
    seam = _seam(first, second, merges[-1])
    if first.cells is None or second.cells is None or first.cells.n_rows != seam + second.cells.n_rows - int(
            merges[-1].evidence.get("drop_rows", 0)):
        return f"{first.id} was changed since the join (its rows): it stays one table"
    if any(c.row < seam < c.row + c.rowspan for c in first.cells.cells):
        return f"a cell of {first.id} spans the seam at row {seam}"
    return None


def split_tables(state: DocumentState, first_id: str, second_id: str, *, actor: str, reason: str,
                 evidence: Evidence) -> None:
    """Undo ``merge_tables``: *first* keeps its rows up to the seam, *second* gets the rest back (with the header rows
    the join dropped) — edits made to the joined table stay with the rows they were made on."""
    blocks = {b.id: b for b in state.blocks}
    first, second = blocks[first_id], blocks[second_id]
    merge = _joined(state, first)[-1]
    seam, drop = _seam(first, second, merge), int(merge.evidence.get("drop_rows", 0))
    grid = first.cells
    head = [c for c in second.cells.cells if c.row < drop]
    tail = [c.model_copy(update={"row": c.row - seam + drop}) for c in grid.cells if c.row >= seam]
    second.cells = TableGrid(n_rows=grid.n_rows - seam + drop, n_cols=grid.n_cols, cells=[*head, *tail],
                             header_rows=second.cells.header_rows)
    first.cells = TableGrid(n_rows=seam, n_cols=grid.n_cols, cells=[c for c in grid.cells if c.row < seam],
                            header_rows=grid.header_rows)
    if first.anchors[-len(second.anchors):] == second.anchors:
        first.anchors = first.anchors[:-len(second.anchors)]
    second.status = BlockStatus.OK
    items = set(str(merge.evidence.get("moved_items", "")).split(","))
    for entry in state.ledger:
        if entry.item in items and entry.disposition == "merged" and entry.block == first.id:
            entry.disposition, entry.block = "output", second.id
    state.relations[:] = [r for r in state.relations
                          if (r.kind, r.src, r.dst) != (RelationKind.CONTINUES, first.id, second.id)]
    for block, other in ((first, second), (second, first)):
        block.decisions.append(Decision(stage=DecisionStage.STRUCTURE, choice="unjoin_table", reason=reason,
                                        evidence=evidence, actor=actor, refs=[other.id]))


def _joined(state: DocumentState, first: Block) -> list:
    """The joins of tables into *first* still in place (a separated one's block is no longer merged), in order."""
    blocks = {b.id: b for b in state.blocks}
    return [d for d in first.decisions if d.choice == "merge_table" and d.refs
            and blocks.get(d.refs[0]) is not None and blocks[d.refs[0]].status == BlockStatus.MERGED]


def _seam(first: Block, second: Block, merge) -> int:
    """The first row of *second*'s part in the joined table."""
    if "rows_before" in merge.evidence:
        return int(merge.evidence["rows_before"])
    return first.cells.n_rows - second.cells.n_rows + int(merge.evidence.get("drop_rows", 0))


# ── Helpers ─────────────────────────────────────────────────────────────


def _is_table(block: Block) -> bool:
    return block.kind == BlockKind.TABLE and block.cells is not None and block.status not in HIDDEN


def _page_anchors(block: Block) -> list[PdfAnchor]:
    return [a for a in block.anchors if isinstance(a, PdfAnchor) and a.coord_space == "page_pt"]


def _first_page(block: Block) -> int | None:
    anchors = _page_anchors(block)
    return min(a.page for a in anchors) if anchors else None


def _last_page(block: Block) -> int | None:
    anchors = _page_anchors(block)
    return max(a.page for a in anchors) if anchors else None


def _box_on(block: Block, page: int) -> BBox | None:
    boxes = [a.bbox for a in _page_anchors(block) if a.page == page]
    if not boxes:
        return None
    return (min(b[0] for b in boxes), min(b[1] for b in boxes), max(b[2] for b in boxes), max(b[3] for b in boxes))


def _page_size(state: DocumentState, n: int) -> tuple[float, float] | None:
    page = next((p for p in state.pages if p.n == n), None)
    return page.size_pt if page is not None else None


def _is_furniture(block: Block) -> bool:
    return block.kind in FURNITURE


def _adjacent(state: DocumentState, first: Block, second: Block) -> bool:
    """Only furniture (or hidden blocks) between *first* and *second* in reading order."""
    sequence = [b for b in ordered(state) if b.status not in HIDDEN]
    ids_ = [b.id for b in sequence]
    i, j = ids_.index(first.id), ids_.index(second.id)
    return i < j and all(_is_furniture(b) for b in sequence[i + 1:j])


def _x_offset(state: DocumentState, first: Block, second: Block, first_page: int, second_page: int, *,
              left_only: bool = False) -> float | None:
    """How far apart the two tables' edges are, as a share of the page width: the left edge only for a table the
    scan engine read (its box is as wide as its content, so the right edge moves with the text: ocr01)."""
    a, b = _box_on(first, first_page), _box_on(second, second_page)
    sa, sb = _page_size(state, first_page), _page_size(state, second_page)
    if a is None or b is None or not sa or not sb or not sa[0] or not sb[0]:
        return None
    left = abs(a[0] / sa[0] - b[0] / sb[0])
    return left if left_only else max(left, abs(a[2] / sa[0] - b[2] / sb[0]))


def _scanned(block: Block) -> bool:
    chosen = next((o for o in block.observations if o.id == block.chosen_observation), None)
    return chosen is not None and chosen.engine == SCAN_ENGINE


def _norm(cell: Cell | None) -> str:
    return re.sub(r"\s+", "", cell.content) if cell is not None else ""
