"""Selection step (guide §6.3, §3.3 acceptance gate; Q20).

Model output is a candidate; this program step decides what the document
uses and records why:

- ``integrate_scan_page``: a page whose native layer failed takes the scan
  engine's blocks; the native blocks become ``duplicate`` (with a
  ``duplicate_of`` Relation) and the page's scan images ``merged``.  Nothing
  is deleted: rejected readings stay as evidence.
- ``mark_scan_failed``: without a scan result the fallbacks stay visible (the
  native text as ``degraded``, the scan image as is) and the loss is listed.
- ``review_table`` / ``review_text``: a review candidate replaces the adopted
  reading only when it passes the acceptance gate — image evidence, numbers
  consistent with the evidence, valid structure without lost content.

Numbers (guide §11.5, first requirement): native text is exact, so a
candidate may never change a number the native layer holds; OCR can be
wrong, so a number may change only in the cells the review was asked to
check.  A text candidate may not change the numbers of the evidence at all.
"""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass
from typing import Literal

from parserx.ir import ids
from parserx.ir.anchor import AssetAnchor, PdfAnchor
from parserx.ir.base import IRModel
from parserx.ir.block import Block
from parserx.ir.decision import Decision
from parserx.ir.enums import BlockKind, BlockStatus, DecisionStage, PageStatus, RelationKind, TaskKind
from parserx.ir.observation import Observation
from parserx.ir.relation import Relation
from parserx.ir.state import DocumentState, Missing
from parserx.tables.grid import TableGrid
from parserx.workspace.queries import HIDDEN, block_unit

ACTOR = "program:content.select"
NATIVE_ENGINES = frozenset({"native_pdf", "docx"})
_NUMBER_RE = re.compile(r"\d+(?:[.,]\d+)*")


class GateCheck(IRModel):
    name: Literal["image_evidence", "numeric_consistency", "structure_valid"]
    passed: bool
    detail: str


@dataclass
class ReviewOutcome:
    adopted: bool
    gate: list[GateCheck]


# ── Scan pages ──────────────────────────────────────────────────────────


def integrate_scan_page(state: DocumentState, n: int, result) -> list[str]:
    """Adopt a scan result (``content.scan.PageScanResult``) for page *n*; returns the new block ids."""
    new_blocks = list(result.blocks)
    visible_new = [b for b in new_blocks if b.status not in HIDDEN and b.kind != BlockKind.FIGURE]
    dispositions: dict[str, str] = {}
    for block in state.blocks:
        if block_unit(state, block) != n or block.status in HIDDEN:
            continue
        if block.kind == BlockKind.SCAN:
            block.status = BlockStatus.MERGED
            dispositions[block.id] = "merged"
            block.decisions.append(Decision(
                stage=DecisionStage.CONTENT_SOURCE, choice="scan_engine", actor=ACTOR,
                reason="page image recognised by the scan engine; its content is in the page's new blocks",
                evidence={"new_blocks": len(new_blocks)}, refs=[b.id for b in new_blocks[:20]]))
            continue
        block.status = BlockStatus.DUPLICATE
        dispositions[block.id] = "duplicate"
        target = _best_overlap(block, visible_new)
        refs = [target.id] if target else []
        if target is not None:
            state.relations.append(Relation(id=ids.relation_id(RelationKind.DUPLICATE_OF, block.id, target.id),
                                            kind=RelationKind.DUPLICATE_OF, src=block.id, dst=target.id))
        block.decisions.append(Decision(
            stage=DecisionStage.CONTENT_SOURCE, choice="scan_engine", actor=ACTOR, refs=refs,
            reason="superseded by the scan engine: the native layer failed its quality check", evidence={}))
    for entry in state.ledger:
        if entry.block in dispositions:
            entry.disposition = dispositions[entry.block]
    known_assets = {a.id for a in state.assets}
    state.assets.extend(a for a in result.assets if a.id not in known_assets)
    state.blocks.extend(new_blocks)
    state.ledger.extend(result.ledger)
    state.warnings.extend(result.warnings)
    state.missing[:] = [m for m in state.missing if m.block not in dispositions]
    _page(state, n).status = PageStatus.DONE
    renumber(state, new={b.id for b in new_blocks})
    return [b.id for b in new_blocks]


def mark_scan_failed(state: DocumentState, n: int, reason: str, *, skipped: bool) -> None:
    """No scan result for page *n*: keep the fallbacks visible and list what is missing."""
    _page(state, n).status = PageStatus.SKIPPED if skipped else PageStatus.FAILED
    on_page = [b for b in state.blocks if block_unit(state, b) == n and b.status not in HIDDEN]
    scans = [b for b in on_page if b.kind == BlockKind.SCAN]
    for block in on_page:
        if block.kind != BlockKind.SCAN and block.status != BlockStatus.DEGRADED:
            block.status = BlockStatus.DEGRADED
            block.decisions.append(Decision(
                stage=DecisionStage.BUDGET if skipped else DecisionStage.CONTENT_SOURCE, choice="native_fallback",
                reason=f"scan engine gave no result ({reason}); the failed native layer is kept",
                evidence={}, actor=ACTOR))
    lost = {b.id for b in (scans or on_page)}
    state.missing[:] = [m for m in state.missing if m.block not in lost]
    state.missing.extend(Missing(block=b, reason=f"scan engine: {reason}") for b in sorted(lost))


def renumber(state: DocumentState, *, new: set[str] = frozenset()) -> None:
    """Contiguous ``order`` by (page / segment, existing before new, previous order)."""
    def key(block: Block):
        unit = block_unit(state, block)
        return (unit if unit is not None else 0, block.id in new, block.order, block.id)

    for order, block in enumerate(sorted(state.blocks, key=key)):
        block.order = order


# ── Acceptance gate ─────────────────────────────────────────────────────


def review_table(block: Block, candidate: Observation, *, allowed_cells: set[tuple[int, int]],
                 actor: str) -> ReviewOutcome:
    """Gate a TableGrid candidate; *allowed_cells* are the cells the review was asked to check for characters."""
    current = block.cells or TableGrid(n_rows=0, n_cols=0)
    native = _chosen(block) is not None and _chosen(block).engine in NATIVE_ENGINES
    grid = candidate.cells
    gate = [_image_evidence(candidate)]
    if grid is None:
        gate += [GateCheck(name="numeric_consistency", passed=False, detail="candidate has no table"),
                 GateCheck(name="structure_valid", passed=False, detail="candidate has no table")]
    else:
        skip = set() if native else allowed_cells
        before, after = _grid_numbers(current, skip), _grid_numbers(grid, skip)
        gate.append(GateCheck(name="numeric_consistency", passed=before == after,
                              detail=_number_diff(before, after, native)))
        lost = _cell_texts(current, allowed_cells) - _cell_texts(grid, set())
        gate.append(GateCheck(
            name="structure_valid", passed=grid.n_rows > 0 and grid.n_cols > 0 and not lost,
            detail="all cell content kept" if not lost else f"cells lost: {sorted(lost.elements())[:10]}"))
    return _decide(block, candidate, gate, actor)


def review_text(block: Block, candidate: Observation, *, actor: str) -> ReviewOutcome:
    evidence = [o for o in block.observations if o.task != TaskKind.REVIEW and o.text]
    before = Counter(n for o in evidence for n in _NUMBER_RE.findall(o.text or ""))
    after = Counter(_NUMBER_RE.findall(candidate.text or ""))
    consistent = not before or before == after
    gate = [
        _image_evidence(candidate),
        GateCheck(name="numeric_consistency", passed=consistent,
                  detail=_number_diff(before, after, native=False) if before else "no numbers in the evidence"),
        GateCheck(name="structure_valid", passed=bool((candidate.text or "").strip()), detail="non-empty text"),
    ]
    return _decide(block, candidate, gate, actor)


def _decide(block: Block, candidate: Observation, gate: list[GateCheck], actor: str) -> ReviewOutcome:
    previous = block.chosen_observation
    block.observations.append(candidate)  # adopted or not, the candidate stays as evidence
    adopted = all(check.passed for check in gate)
    if adopted:
        if candidate.cells is not None:
            block.cells = candidate.cells
        else:
            block.text = candidate.text or ""
        block.chosen_observation = candidate.id
    failed = [c.name for c in gate if not c.passed]
    block.decisions.append(Decision(
        stage=DecisionStage.REVIEW_ACCEPT, choice="adopted" if adopted else "rejected",
        reason="all acceptance checks passed" if adopted else f"failed: {', '.join(failed)}",
        evidence={c.name: c.passed for c in gate}, actor=actor,
        refs=[candidate.id] + ([previous] if previous else [])))
    return ReviewOutcome(adopted=adopted, gate=gate)


def _image_evidence(candidate: Observation) -> GateCheck:
    anchor = candidate.anchor
    looked = isinstance(anchor, AssetAnchor) or (isinstance(anchor, PdfAnchor) and anchor.coord_space == "image_px")
    passed = candidate.task == TaskKind.REVIEW and candidate.raw_ref is not None and looked
    return GateCheck(name="image_evidence", passed=passed,
                     detail="candidate read from the region image" if passed
                     else "candidate was not produced from the region image")


# ── Helpers ─────────────────────────────────────────────────────────────


def _chosen(block: Block) -> Observation | None:
    return next((o for o in block.observations if o.id == block.chosen_observation), None)


def _grid_numbers(grid: TableGrid, skip: set[tuple[int, int]]) -> Counter[str]:
    return Counter(n for c in grid.cells if (c.row, c.col) not in skip for n in _NUMBER_RE.findall(c.content))


def _cell_texts(grid: TableGrid, skip: set[tuple[int, int]]) -> Counter[str]:
    return Counter("".join(c.content.split()) for c in grid.cells
                   if (c.row, c.col) not in skip and c.content.strip())


def _number_diff(before: Counter[str], after: Counter[str], native: bool) -> str:
    if before == after:
        return "numbers unchanged" + (" (native evidence)" if native else " outside the asked cells")
    return f"numbers removed {sorted((before - after).elements())[:10]}, added {sorted((after - before).elements())[:10]}"


def _best_overlap(block: Block, candidates: list[Block]) -> Block | None:
    box = block.anchors[0].bbox if isinstance(block.anchors[0], PdfAnchor) else None
    best, best_area = None, 0.0
    for other in candidates:
        o = other.anchors[0].bbox if isinstance(other.anchors[0], PdfAnchor) else None
        if box is None or o is None:
            continue
        area = max(0.0, min(box[2], o[2]) - max(box[0], o[0])) * max(0.0, min(box[3], o[3]) - max(box[1], o[1]))
        if area > best_area:
            best, best_area = other, area
    return best


def _page(state: DocumentState, n: int):
    return next(p for p in state.pages if p.n == n)
