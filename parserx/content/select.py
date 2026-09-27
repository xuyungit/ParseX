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
import unicodedata
from collections import Counter
from dataclasses import dataclass
from typing import Literal

from parserx.ir import ids
from parserx.ir.anchor import AssetAnchor, PdfAnchor
from parserx.ir.base import IRModel
from parserx.ir.block import Block
from parserx.reading.compare import NEAR, normalize, pairs_share
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


def _numbers(text: str) -> list[str]:
    """The numbers of *text* in one notation: k₁ is k1, m² is m2, a full-width １２ is 12 (NFKC, as the region
    adoption of ``tools/edit.py`` compares them)."""
    return _NUMBER_RE.findall(unicodedata.normalize("NFKC", text))


def _plain(text: str) -> str:
    """A cell's text for the content-kept check: one notation (NFKC), no whitespace."""
    return "".join(unicodedata.normalize("NFKC", text).split())


class GateCheck(IRModel):
    name: Literal["image_evidence", "numeric_consistency", "structure_valid", "independent_reading"]
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
        target = best_overlap(block, visible_new)
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
    state.relations.extend(result.relations)  # pictures cut from a table or text
    state.warnings.extend(result.warnings)
    state.missing[:] = [m for m in state.missing if m.block not in dispositions]
    _page(state, n).status = PageStatus.DONE
    renumber(state, new={b.id for b in new_blocks})
    return [b.id for b in new_blocks]


def transcribed(state: DocumentState) -> set[str]:
    """Figures whose image the scan engine has read (``integrate_image``), whether or not text came out of it."""
    return {b.id for b in state.blocks
            if any(d.stage == DecisionStage.IMAGE_ROUTE and d.choice == "transcribed" for d in b.decisions)}


def integrate_image(state: DocumentState, figure: str, result) -> list[str]:
    """Q42 / guide §6.5: the text and tables read inside an embedded image follow the image in reading order
    (the image itself stays shown); ``contains`` relations tie them to it.  Returns the new block ids."""
    blocks = {b.id: b for b in state.blocks}
    image = blocks[figure]
    new = result.blocks
    for block in state.blocks:  # make room right after the image
        if block.order > image.order:
            block.order += len(new)
    for offset, block in enumerate(new, 1):
        block.order = image.order + offset
        state.blocks.append(block)
        state.relations.append(Relation(id=ids.relation_id(RelationKind.CONTAINS, figure, block.id),
                                        kind=RelationKind.CONTAINS, src=figure, dst=block.id))
    state.ledger.extend(result.ledger)
    state.warnings.extend(result.warnings)
    complete = all(b.observations[0].status.value == "ok" for b in new if b.status != BlockStatus.EXCLUDED)
    image.decisions.append(Decision(
        stage=DecisionStage.IMAGE_ROUTE, choice="transcribed", actor=ACTOR,
        reason=f"text and tables inside the image read by the scan engine: {len(new)} blocks follow the image",
        evidence={"blocks": len(new), "complete": complete}, refs=[b.id for b in new]))
    anchor = next((a for a in image.anchors if isinstance(a, AssetAnchor)), None)
    for record in state.images:
        if anchor is not None and record.id == anchor.asset:
            record.complete = complete
    return [b.id for b in new]


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
                 fill_region: set[tuple[int, int]] = frozenset(), actor: str) -> ReviewOutcome:
    """Gate a TableGrid candidate; *allowed_cells* are the cells the review was asked to check for characters,
    *fill_region* the cells a structure issue named, where content the reading missed may be filled (Q45)."""
    current = block.cells or TableGrid(n_rows=0, n_cols=0)
    native = _chosen(block) is not None and _chosen(block).engine in NATIVE_ENGINES
    grid = candidate.cells
    gate = [_image_evidence(candidate)]
    filled: set[tuple[int, int]] = set()
    if grid is None:
        gate += [GateCheck(name="numeric_consistency", passed=False, detail="candidate has no table"),
                 GateCheck(name="structure_valid", passed=False, detail="candidate has no table")]
    else:
        skip = set() if native else allowed_cells
        filled = set() if native else _filled_cells(current, grid, fill_region)
        before, after = _grid_numbers(current, skip), _grid_numbers(grid, skip | filled)
        detail = _number_diff(before, after, native)
        if before != after and not native:
            detail += _where_added(grid, after - before, skip | filled)
        elif filled:
            detail += f"; {len(filled)} cells filled from the image only (Q45)"
        gate.append(GateCheck(name="numeric_consistency", passed=before == after, detail=detail))
        lost = _lost_cells(current, grid, allowed_cells)
        gate.append(GateCheck(
            name="structure_valid", passed=grid.n_rows > 0 and grid.n_cols > 0 and not lost,
            detail="all cell content kept" if not lost else f"cells lost: {sorted(lost.elements())[:10]}"))
    outcome = _decide(block, candidate, gate, actor)
    if outcome.adopted and filled:  # Q45: shown as image-only evidence in the sidecar
        block.decisions[-1].evidence["image_only_cells"] = ",".join(f"r{r}c{c}" for r, c in sorted(filled))
    return outcome


def _filled_cells(current: TableGrid, grid: TableGrid, region: set[tuple[int, int]]) -> set[tuple[int, int]]:
    """Candidate cells in the named region (widened by the rows and columns the candidate adds) whose text the
    current reading does not have anywhere: what the reading missed.  Their numbers are new; every number of the
    current reading must still be there, so a changed number is never a fill."""
    if not region:
        return set()
    rows, cols = [r for r, _ in region], [c for _, c in region]
    r1 = max(rows) + max(0, grid.n_rows - current.n_rows)
    c1 = max(cols) + max(0, grid.n_cols - current.n_cols)
    known = {_plain(c.content) for c in current.cells if c.content.strip()}
    return {(c.row, c.col) for c in grid.cells
            if min(rows) <= c.row <= r1 and min(cols) <= c.col <= c1
            and c.content.strip() and _plain(c.content) not in known}


def _where_added(grid: TableGrid, added: Counter[str], skip: set[tuple[int, int]]) -> str:
    cells = sorted({(c.row, c.col) for c in grid.cells if (c.row, c.col) not in skip
                    and any(n in added for n in _numbers(c.content))})
    return (f" at candidate cells {cells[:12]}; numbers the reading missed may be filled only where a structure "
            "issue names the cells and the table had nothing (Q45)") if cells else ""


def review_text(block: Block, candidate: Observation, *, actor: str) -> ReviewOutcome:
    evidence = [o for o in block.observations if o.task != TaskKind.REVIEW and o.text]
    before = Counter(n for o in evidence for n in _numbers(o.text or ""))
    after = Counter(_numbers(candidate.text or ""))
    consistent = not before or before == after
    gate = [
        _image_evidence(candidate),
        GateCheck(name="numeric_consistency", passed=consistent,
                  detail=_number_diff(before, after, native=False) if before else "no numbers in the evidence"),
        GateCheck(name="structure_valid", passed=bool((candidate.text or "").strip()), detail="non-empty text"),
    ]
    return _decide(block, candidate, gate, actor)


def correct(block: Block, candidate: Observation, *, image: GateCheck, actor: str,
            seen: str | None = None) -> ReviewOutcome:
    """Gate the agent's correction of the spans or cells it named (Q30): it must have looked at the image of the
    block, and the result keeps content.  A native text layer's numbers do not change on the agent's word alone:
    only as the local reading of the block's place (*seen*, Q56) shows them — every number the correction adds is
    there, no number it removes is (edits of OCR text are confined to the named spans by construction)."""
    chosen = _chosen(block)
    native = chosen is not None and chosen.engine in NATIVE_ENGINES
    if candidate.cells is not None:
        before = _grid_numbers(block.cells or TableGrid(n_rows=0, n_cols=0), set())
        after = _grid_numbers(candidate.cells, set())
        valid = candidate.cells.n_rows > 0 and candidate.cells.n_cols > 0
    else:
        before, after = Counter(_numbers(block.text)), Counter(_numbers(candidate.text or ""))
        valid = bool((candidate.text or "").strip())
    if not native:
        numbers = GateCheck(name="numeric_consistency", passed=True, detail="edits confined to the named spans")
    elif before == after:
        numbers = GateCheck(name="numeric_consistency", passed=True, detail=_number_diff(before, after, native=True))
    elif seen is None:
        numbers = GateCheck(name="numeric_consistency", passed=False,
                            detail=_number_diff(before, after, native=True) + "; no local reading of this place")
    else:
        shown = Counter(_numbers(seen))
        backed = all(shown[k] >= after[k] for k in after - before) and all(shown[k] <= after[k] for k in before - after)
        numbers = GateCheck(name="numeric_consistency", passed=backed, detail=_number_diff(before, after, native=True) + (
            "; as the local reading of this place shows" if backed else
            f"; the local reading of this place shows {sorted(shown.elements())[:10]}"))
    gate = [image, numbers,
            GateCheck(name="structure_valid", passed=valid, detail="content kept" if valid else "empty result")]
    return _decide(block, candidate, gate, actor)


def add_gate(text: str, *, image: GateCheck, seen: str | None, holders: list[str]) -> list[GateCheck]:
    """Gate the agent's text for a place of a page no block accounts for (Q56): it looked at an image of the place,
    the local reading of the place shows the text — two independent readings agree — and no block there has it
    already.  The caller adds the block only when every check passes."""
    if seen is None:
        reading = GateCheck(name="independent_reading", passed=False, detail="no local reading of this page")
    else:
        share = pairs_share(normalize(text), normalize(seen))
        reading = GateCheck(name="independent_reading", passed=share >= NEAR,
                            detail=f"{share:.0%} of the text is in the local reading of this place")
    fresh = GateCheck(name="structure_valid", passed=bool(normalize(text)) and not holders,
                      detail=f"already in {', '.join(holders)}" if holders else
                      "new text" if normalize(text) else "empty text")
    return [image, reading, fresh]


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
    return Counter(n for c in grid.cells if (c.row, c.col) not in skip for n in _numbers(c.content))


def _cell_texts(grid: TableGrid, skip: set[tuple[int, int]]) -> Counter[str]:
    return Counter(_plain(c.content) for c in grid.cells
                   if (c.row, c.col) not in skip and c.content.strip())


def _lost_cells(current: TableGrid, grid: TableGrid, skip: set[tuple[int, int]]) -> Counter[str]:
    """Texts of the current cells (outside *skip*) the candidate does not keep whole.  Each must read on, as often
    as it occurs, as one run of the candidate's text row by row or column by column: splitting a cell into rows or
    columns, merging cells and moving rows keep content; a dropped or altered character does not."""
    missing = _cell_texts(current, skip) - _cell_texts(grid, set())
    if not missing:
        return missing
    by_rows = "".join(_plain(c.content) for c in sorted(grid.cells, key=lambda c: (c.row, c.col)))
    by_cols = "".join(_plain(c.content) for c in sorted(grid.cells, key=lambda c: (c.col, c.row)))
    wanted = _cell_texts(current, skip)
    return Counter({text: n for text, n in missing.items()
                    if max(by_rows.count(text), by_cols.count(text)) < wanted[text]})


def _number_diff(before: Counter[str], after: Counter[str], native: bool) -> str:
    if before == after:
        return "numbers unchanged" + (" (native evidence)" if native else " outside the asked cells")
    return f"numbers removed {sorted((before - after).elements())[:10]}, added {sorted((after - before).elements())[:10]}"


def best_overlap(block: Block, candidates: list[Block]) -> Block | None:
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
