"""Cross-page table continuation (guide §6.9, Q33).

A **candidate** is what makes a merge possible at all: two visible tables with
the same number of columns, the second on the page right after the first
table's last page, with nothing between them in reading order except page
furniture (running headers, footers, page numbers).  Native pages have no
furniture labels yet, so a short line in the top or bottom margin band that is a
bare page number, or whose text (digits aside) recurs in the same band on
another page, also counts as furniture here.  It is only skipped when judging
adjacency, never hidden.

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

MARGIN_BAND = 0.12  # share of the page height at the top and bottom where furniture sits
MARGIN_MAX_HEIGHT = 0.03  # a furniture line is at most this share of the page height
MAX_X_OFFSET = 0.05  # confirmed: both table edges within this share of the page width
_PAGE_NUMBER_RE = re.compile(r"[\s\-–—·•|()（）\[\]]*(?:\d{1,4}|[ivxlcdm]{1,6})[\s\-–—·•|()（）\[\]]*", re.IGNORECASE)

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
    recurring = _recurring(state, sequence)
    out: list[MergeCandidate] = []
    for i, block in enumerate(sequence):
        if block.kind != BlockKind.TABLE:
            continue
        following = next((b for b in sequence[i + 1:] if not _is_furniture(state, b, recurring)), None)
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
    offset = _x_offset(state, first, second, first_page, second_page)
    evidence: Evidence = {
        "columns": first.cells.n_cols, "first_page": first_page, "second_page": second_page,
        "x_offset": -1.0 if offset is None else round(offset, 3),
        "header_repeated": repeated, "second_has_own_header": own_header,
    }
    confirmed = offset is not None and offset <= MAX_X_OFFSET and not own_header
    return MergeCandidate(first=first.id, second=second.id, drop_rows=header if repeated else 0,
                          confirmed=confirmed, evidence=evidence)


def propose_merges(state: DocumentState) -> list[dict]:
    """``merge_tables`` changes for the confirmed candidates; a table over several pages merges into its first part."""
    work = state.model_copy(deep=True)
    changes: list[dict] = []
    while True:
        candidate = next((c for c in merge_candidates(work) if c.confirmed), None)
        if candidate is None:
            return changes
        reason = "cross-page table continuation: same columns, aligned edges, consecutive pages"
        merge_tables(work, candidate.first, candidate.second, candidate.drop_rows, actor="propose", reason=reason,
                     evidence=candidate.evidence)
        changes.append({"op": "merge_tables", "first": candidate.first, "second": candidate.second,
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
    first.cells = merge_grids(first.cells, second.cells, drop_rows)
    first.anchors = [*first.anchors, *second.anchors]
    second.status = BlockStatus.MERGED
    first.decisions.append(Decision(stage=DecisionStage.STRUCTURE, choice="merge_table", reason=reason,
                                    evidence={**evidence, "drop_rows": drop_rows}, actor=actor, refs=[second.id]))
    second.decisions.append(Decision(stage=DecisionStage.STRUCTURE, choice="merged_into", reason=reason,
                                     evidence={}, actor=actor, refs=[first.id]))
    state.relations.append(Relation(id=ids.relation_id(RelationKind.CONTINUES, first.id, second.id),
                                    kind=RelationKind.CONTINUES, src=first.id, dst=second.id))
    for entry in state.ledger:
        if entry.block == second.id and entry.disposition == "output":
            entry.disposition, entry.block = "merged", first.id


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


def _is_furniture(state: DocumentState, block: Block, recurring: set[tuple[str, str]]) -> bool:
    if block.kind in FURNITURE:
        return True
    band = _band(state, block)
    if band is None:
        return False
    return bool(_PAGE_NUMBER_RE.fullmatch(block.text)) or (band, _shape(block.text)) in recurring


def _band(state: DocumentState, block: Block) -> str | None:
    """"top" / "bottom" for a short text line inside a margin band of its page, else None."""
    if block.kind not in (BlockKind.TEXT, BlockKind.OTHER) or not block.text.strip():
        return None
    page = _first_page(block)
    size = _page_size(state, page) if page is not None else None
    box = _box_on(block, page) if page is not None else None
    if not size or box is None or box[3] - box[1] > MARGIN_MAX_HEIGHT * size[1]:
        return None
    if box[3] <= MARGIN_BAND * size[1]:
        return "top"
    if box[1] >= (1 - MARGIN_BAND) * size[1]:
        return "bottom"
    return None


def _recurring(state: DocumentState, sequence: list[Block]) -> set[tuple[str, str]]:
    """(band, text shape) pairs found in the same margin band on at least two pages."""
    pages: dict[tuple[str, str], set[int]] = {}
    for block in sequence:
        band = _band(state, block)
        if band is not None:
            pages.setdefault((band, _shape(block.text)), set()).add(_first_page(block))
    return {key for key, found in pages.items() if len(found) > 1}


def _shape(text: str) -> str:
    return re.sub(r"\d+", "#", re.sub(r"\s+", "", text))


def _adjacent(state: DocumentState, first: Block, second: Block) -> bool:
    """Only furniture (or hidden blocks) between *first* and *second* in reading order."""
    sequence = [b for b in ordered(state) if b.status not in HIDDEN]
    ids_ = [b.id for b in sequence]
    i, j = ids_.index(first.id), ids_.index(second.id)
    recurring = _recurring(state, sequence)
    return i < j and all(_is_furniture(state, b, recurring) for b in sequence[i + 1:j])


def _x_offset(state: DocumentState, first: Block, second: Block, first_page: int, second_page: int) -> float | None:
    a, b = _box_on(first, first_page), _box_on(second, second_page)
    sa, sb = _page_size(state, first_page), _page_size(state, second_page)
    if a is None or b is None or not sa or not sb or not sa[0] or not sb[0]:
        return None
    return max(abs(a[0] / sa[0] - b[0] / sb[0]), abs(a[2] / sa[0] - b[2] / sb[0]))


def _norm(cell: Cell | None) -> str:
    return re.sub(r"\s+", "", cell.content) if cell is not None else ""
