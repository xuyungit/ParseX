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
from difflib import SequenceMatcher
from typing import Literal

from rapidfuzz import fuzz

from parserx.ir import ids
from parserx.ir.anchor import AssetAnchor, PdfAnchor
from parserx.ir.base import IRModel
from parserx.ir.block import Block
from parserx.content.latex import placed
from parserx.content.text import normalize_fullwidth_ascii
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
    name: Literal["image_evidence", "numeric_consistency", "text_consistency", "structure_valid",
                  "independent_reading", "as_printed"]
    passed: bool
    detail: str
    # a check that does not stop the agent but is recorded and listed in the summary (execution plan §3.4): the
    # program's comparison is evidence, the agent's reading of the image decides
    signal: str | None = None


def signals(gate: list[GateCheck]) -> dict[str, str]:
    """The signals of a gate as a decision's evidence: ``signal`` (their kinds) and ``signal_detail``."""
    raised = [g for g in gate if g.signal]
    if not raised:
        return {}
    return {"signal": ",".join(g.signal for g in raised), "signal_detail": "; ".join(g.detail for g in raised)}


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
            detail="all cell content kept" if not lost else _lost_detail(lost, current, grid)))
    outcome = _decide(block, candidate, gate, actor)
    if outcome.adopted and filled:  # Q45: shown as image-only evidence in the sidecar
        block.decisions[-1].evidence["image_only_cells"] = ",".join(f"r{r}c{c}" for r, c in sorted(filled))
    return outcome


def _filled_cells(current: TableGrid, grid: TableGrid, region: set[tuple[int, int]]) -> set[tuple[int, int]]:
    """Candidate cells in the named region (widened by the rows and columns the candidate adds) whose text the
    current reading does not have anywhere — not as a cell, nor as a few consecutive cells joined (a head split
    into a row of k and a row of 1, merged back as k₁): what the reading missed.  Their numbers are
    new; every number of the current reading must still be there, so a changed number is never a fill."""
    if not region:
        return set()
    rows, cols = [r for r, _ in region], [c for _, c in region]
    r1 = max(rows) + max(0, grid.n_rows - current.n_rows)
    c1 = max(cols) + max(0, grid.n_cols - current.n_cols)
    known = {_plain(c.content) for c in current.cells if c.content.strip()} | _merges(current)
    return {(c.row, c.col) for c in grid.cells
            if min(rows) <= c.row <= r1 and min(cols) <= c.col <= c1 and (text := _plain(c.content))
            and text not in known}


def _merges(grid: TableGrid, most: int = 4) -> set[str]:
    """Texts of 2 to *most* consecutive non-empty cells joined, row by row and column by column: cells a reading
    may merge into one."""
    out: set[str] = set()
    for key in ((lambda c: (c.row, c.col)), (lambda c: (c.col, c.row))):
        texts = [x for c in sorted(grid.cells, key=key) if (x := _plain(c.content))]
        out |= {"".join(texts[i:i + n]) for n in range(2, most + 1) for i in range(len(texts) - n + 1)}
    return out


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
    block, and the result keeps content.  A native text layer's numbers change on the agent's reading of the image
    (execution plan §3.4): the change is a signal — recorded with whether the local reading of the block's place
    (*seen*, Q56) shows it — and the summary lists it (edits of OCR text are confined to the named spans by
    construction).  So is a changed letter of a native text layer that maps every glyph (the layer is what the page
    prints: a typo of the original corrected by meaning is what this catches; user 2026-09-30, keep the original as
    printed); notation — spacing, width, case, markup — is not a change."""
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
    else:
        if seen is None:
            reading = "no local reading of this place"
        else:
            shown = Counter(_numbers(seen))
            backed = (all(shown[k] >= after[k] for k in after - before)
                      and all(shown[k] <= after[k] for k in before - after))
            reading = ("as the local reading shows" if backed else
                       f"the local reading of this place does not show it (it shows {sorted(shown.elements())[:10]})")
        numbers = GateCheck(name="numeric_consistency", passed=True, signal="native_numbers_changed",
                            detail=f"native numbers changed: {_number_diff(before, after, native=True)}; {reading}")
    gate = [image, numbers]
    before_text = _run(block.cells, False) if candidate.cells is not None and block.cells is not None else block.text
    after_text = _run(candidate.cells, False) if candidate.cells is not None else candidate.text or ""
    changed = _letters_changed(before_text, after_text) if native and not _unmapped(before_text) else None
    if changed:
        lost, added = changed
        shown = Counter(normalize(seen)) if seen is not None else None
        reading = ("no local reading of this place" if shown is None else "as the local reading shows"
                   if not Counter(added) - shown else "the local reading of this place does not show it")
        gate.append(GateCheck(name="text_consistency", passed=True, signal="native_text_changed",
                              detail=f"native letters changed: lost '{lost[:40]}', added '{added[:40]}'; {reading}"))
    printed = as_printed(block, before_text, after_text, seen)
    if printed is not None:
        gate.append(printed)
    gate.append(GateCheck(name="structure_valid", passed=valid, detail="content kept" if valid else "empty result"))
    return _decide(block, candidate, gate, actor)


AGREEING = 2  # independent readings that, agreeing on what the page prints, outweigh a correction no reading shows
# Readers that read by meaning, as the agent does — the service model, given the draft's reading of the place as it
# reads it — are no evidence of what the page prints against the agent: the text layer and the recognition engines are.
_BY_MEANING = frozenset({"agent", "vlm"})
# Readings of a block's image alone, by readers shown neither the draft nor a correction (tools/second_reading.py):
# the second reading of scanned mathematics, and the readings the edit tool asks for a correction of characters.
UNPROMPTED = frozenset({"second_reading", "recheck"})
_SCRIPT = {"sub": "_", "sup": "^", "": ""}
# a correction refused because the readings of the place show the draft: the agent and the readings disagree — the
# program records both versions for a person to check (tools/edit.py)
PRINTED_AS_DRAFT = "printed_as_draft"


def substitutes(before: str, after: str, math: bool = False) -> bool:
    """Whether *after* writes other characters in place of some of *before* — letters or digits, or one moved into or
    out of a script — not only adds or removes some (characters compared as printed, ``_placed``; *math*: a formula
    block's LaTeX)."""
    old, new = Counter(_placed(before, math)), Counter(_placed(after, math))
    return bool(old - new) and bool(new - old)


def as_printed(block: Block, before: str, after: str, seen: str | None) -> GateCheck | None:
    """A correction that writes other characters in place of some (``substitutes``) is held against what the page
    prints.  Where the block has readings of its image alone (``UNPROMPTED``: the readers saw neither the draft nor
    the correction), they decide (``_read_alone``): it stands where some shows it at its place and none shows the
    draft, and a text layer mapping every glyph does not print the draft's characters.  Where none of them reads
    the place, or there are none, the independent readings of the place are the block's own readings by the text
    layer and recognition engines and the local reading of its place (*seen*): where at least ``AGREEING`` of them
    agree with the draft and none shows the correction, the page prints what they read, and a mistake of the
    original is kept as printed (round-1 review: ``No`` corrected to ``N_0`` by meaning) — refused; recorded
    otherwise.  These readings are compared by
    their letters and digits (``_printed``), scripts not told apart (a text layer and most recognition engines write
    none).  A reading shows a side when it holds that side's changed characters as often as that side does and the
    other side's not: a reading of part of the place (a formula's local reading) shows neither."""
    alone = {o.engine_version: o.text for o in block.observations if o.label in UNPROMPTED and o.text}
    if alone:
        check = _read_alone(block, alone, before, after)
        if check is not None:
            return check
    old, new = Counter(_printed(before)), Counter(_printed(after))
    lost, added = old - new, new - old
    if not lost or not added:
        return None
    readings = {o.engine: _run(o.cells, False) if o.cells is not None else o.text or ""
                for o in block.observations
                if o.task in (TaskKind.EXTRACT, TaskKind.RECOGNIZE, TaskKind.REVIEW) and o.engine not in _BY_MEANING}
    if seen is not None:
        readings["page_reading"] = seen
    draft, change = [], []
    for engine, text in readings.items():
        shown = Counter(_printed(text))
        has_draft = all(shown[ch] >= old[ch] for ch in lost)
        has_change = all(shown[ch] >= new[ch] for ch in added)
        if has_draft and not has_change:
            draft.append(engine)
        elif has_change and not has_draft:
            change.append(engine)
    what = f"'{''.join(sorted(lost.elements()))[:20]}' written as '{''.join(sorted(added.elements()))[:20]}'"
    if len(draft) >= AGREEING and not change:
        return GateCheck(name="as_printed", passed=False, signal=PRINTED_AS_DRAFT, detail=(
            f"{what}: the readings of this place agree with the draft ({', '.join(draft)}) and none shows the "
            "change — the page prints it so; write it as printed, even where it looks like a mistake of the "
            "original, and the program records both versions as a disagreement for a person to check"))
    return GateCheck(name="as_printed", passed=True, detail=(
        f"{what}: readings showing the change: {', '.join(change) or 'none'}; showing the draft: "
        f"{', '.join(draft) or 'none'}"))


def _read_alone(block: Block, readings: dict[str, str], before: str, after: str) -> GateCheck | None:
    """A substitution judged by readings of the block's image alone (*readings*, by reader), or None where none of
    them reads any place it changes (an image that misses them).  It stands where every reading shows it at every
    changed place the reading reads (``_sides``) — models "correct" what is printed by meaning as the agent does, but
    none of these was told what to see; a reading showing the draft at a place, or the place read a third way, keeps
    the draft.  A text layer that maps every glyph is what the page prints, whatever the readers make of it: where it
    shows the draft's characters, the draft stays (it writes no scripts, so a character's place is the readers' to
    tell)."""
    math = block.kind == BlockKind.FORMULA
    old, new = _placed(before, math), _placed(after, math)
    lost, added = Counter(old) - Counter(new), Counter(new) - Counter(old)
    if not lost or not added:
        return None
    what = f"'{' '.join(sorted(lost.elements()))[:40]}' written as '{' '.join(sorted(added.elements()))[:40]}'"
    layer = next((_run(o.cells, False) if o.cells is not None else o.text or "" for o in block.observations
                  if o.engine in NATIVE_ENGINES and o.task == TaskKind.EXTRACT), None)
    if layer is not None and not _unmapped(layer):
        flat_old, flat_new, flat_layer = ([t[-1] for t in tokens] for tokens in (old, new, _placed(layer)))
        if (Counter(flat_old) - Counter(flat_new) and Counter(flat_new) - Counter(flat_old)
                and _shows(flat_new, flat_old, flat_layer) and not _shows(flat_old, flat_new, flat_layer)):
            return GateCheck(name="as_printed", passed=False, signal=PRINTED_AS_DRAFT, detail=(
                f"{what}: the text layer, which maps every glyph of this place, prints the draft's characters — the "
                "page prints them so; write it as printed, even where it looks like a mistake of the original, and "
                "the program records it as a doubt about the original"))
    places = [op[1:] for op in SequenceMatcher(None, old, new, autojunk=False).get_opcodes() if op[0] != "equal"]
    verdicts = {}
    for reader, text in sorted(readings.items()):
        read = [side for side in _sides(old, new, places, _placed(text, math)) if side is not None]
        if read:
            verdicts[reader.split(":", 1)[-1]] = ("change" if all(side == "change" for side in read) else
                                                  "draft" if "draft" in read else "otherwise")
    if not verdicts:
        return None
    otherwise = {name: v for name, v in verdicts.items() if v != "change"}
    if otherwise:
        confirmed = PRINTED_AS_DRAFT if "draft" in otherwise.values() else None
        return GateCheck(name="as_printed", passed=False, signal=confirmed, detail=(
            f"{what}: read again on the image alone, without the draft, "
            + "; ".join(f"{name} reads {'the draft' if v == 'draft' else 'it otherwise'}"
                        for name, v in otherwise.items())
            + " at this place — the draft stays; write what is printed, even where it looks like a mistake of the "
              "original" + (", and the program records both versions as a disagreement for a person to check" if confirmed else
                            "; a mistake of the original you are sure of: record it with doubt")))
    return GateCheck(name="as_printed", passed=True, detail=(
        f"{what}: every reading of the image alone shows it ({', '.join(verdicts)})"))


def _sides(draft: list[str], change: list[str], places: list[tuple[int, int, int, int]],
           reading: list[str]) -> list[str | None]:
    """What *reading* shows at each changed place (draft[i1:i2] written as change[j1:j2]): "change", "draft",
    "otherwise" (read a third way), or None where it does not read the place — neither side's characters nor the
    characters beside the place line up with it (an image that misses it, a reading of part of the block)."""
    on_change, on_draft = _aligned(change, reading), _aligned(draft, reading)
    out: list[str | None] = []
    for i1, i2, j1, j2 in places:
        writes = all(j in on_change for j in range(j1, j2))
        keeps = all(i in on_draft for i in range(i1, i2))
        near = bool({j1 - 1, j2} & on_change or {i1 - 1, i2} & on_draft)
        has_change = writes and (j2 > j1 or (near and not (i2 > i1 and keeps)))
        has_draft = keeps and (i2 > i1 or (near and not (j2 > j1 and writes)))
        out.append("change" if has_change and not has_draft else "draft" if has_draft and not has_change else
                   "otherwise" if near or has_change or has_draft else None)
    return out


def _aligned(text: list[str], reading: list[str]) -> set[int]:
    """The positions of *text* that line up with the same character of *reading*."""
    return {a + k for a, _, n in SequenceMatcher(None, text, reading, autojunk=False).get_matching_blocks()
            for k in range(n)}


def _shows(draft: list[str], change: list[str], reading: list[str]) -> bool:
    """Whether *reading* holds a correction at its place: every character the correction writes (*change* aligned
    with *draft*) is aligned with the same character of the reading (a reading's slip elsewhere in the block, or a
    reading of part of it, is no matter)."""
    written = {j for tag, _, _, j1, j2 in SequenceMatcher(None, draft, change, autojunk=False).get_opcodes()
               if tag in ("replace", "insert") for j in range(j1, j2)}
    held = {a + k for a, _, n in SequenceMatcher(None, change, reading, autojunk=False).get_matching_blocks()
            for k in range(n)}
    return written <= held


def _placed(text: str, math: bool = False) -> list[str]:
    """The letters and digits of *text* as printed, each with its script (``N`` ``_o``): one notation (NFKC, full
    width, accents dropped — a text layer may hold an accent as a glyph of its own, ``Fr´ed´eric``, ``Mart´ın``:
    ``_ACCENT`` — LaTeX commands as their characters, HTML tags dropped), case kept (``J_K`` is not ``J_k``)."""
    out = []
    text = _UNDER_ACCENT.sub(lambda m: _DOTTED[m.group(0)], normalize_fullwidth_ascii(text or ""))
    for ch, kind in placed(text, math):
        for c in unicodedata.normalize("NFKD", unicodedata.normalize("NFKC", ch)):
            if c.isalnum() and not _modifier(c):
                out.append(_SCRIPT[kind] + c)
    return out


# An accent a text layer holds as a glyph of its own: a spacing modifier (ˆ is a "letter" to Unicode) or a spacing
# accent (´ ¨ ` ¯ ¸); TeX sets an accented i or j on the dotless ı or ȷ beside it (Mart´ın is Martín).
_ACCENT = "\u00b4\u0060\u00a8\u00af\u00b8\u02b0-\u02ff\u0300-\u036f"
_UNDER_ACCENT = re.compile(f"(?<=[{_ACCENT}])[ıȷ]|[ıȷ](?=[{_ACCENT}])")
_DOTTED = {"ı": "i", "ȷ": "j"}


def _modifier(ch: str) -> bool:
    return 0x02B0 <= ord(ch) <= 0x02FF


def _printed(text: str) -> str:
    """The letters and digits of *text* as printed (``_placed``), scripts not told apart."""
    return "".join(t[-1] for t in _placed(text))


def _letters_changed(before: str, after: str) -> tuple[str, str] | None:
    """The letters a correction lost and added (letters only: the numbers check has the digits), counted — text
    moved within the block or table is no change — in one notation (``reading.compare.normalize``: NFKC, width,
    case and markup folded); None when there are none."""
    a = Counter(ch for ch in normalize(before) if not ch.isdigit())
    b = Counter(ch for ch in normalize(after) if not ch.isdigit())
    if a == b:
        return None
    return "".join(sorted((a - b).elements())), "".join(sorted((b - a).elements()))


def _unmapped(text: str) -> bool:
    """A glyph the text layer does not map to a character (private use, U+FFFD): writing it is a correction's job."""
    return any(0xE000 <= ord(ch) <= 0xF8FF or ord(ch) >= 0xF0000 or ch == "\ufffd" for ch in text or "")


def add_gate(text: str, *, image: GateCheck, seen: str | None, holders: list[str]) -> list[GateCheck]:
    """Gate the agent's text for a place of a page no block accounts for (Q56): it looked at an image of the place
    and no block there has it already.  Whether the local reading of the place shows the text is a signal
    (execution plan §3.4), recorded with the block.  The caller adds the block only when every check passes."""
    if seen is None:
        reading = GateCheck(name="independent_reading", passed=True, detail="no local reading of this page",
                            signal="text_not_in_reading")
    else:
        share = pairs_share(normalize(text), normalize(seen))
        reading = GateCheck(name="independent_reading", passed=True,
                            detail=f"{share:.0%} of the text is in the local reading of this place",
                            signal=None if share >= NEAR else "text_not_in_reading")  # §3.4: evidence, not a gate
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
        evidence={**{c.name: c.passed for c in gate}, **signals(gate)}, actor=actor,
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
    by_rows, by_cols = _run(grid, False), _run(grid, True)
    wanted = _cell_texts(current, skip)
    return Counter({text: n for text, n in missing.items()
                    if max(by_rows.count(text), by_cols.count(text)) < wanted[text]})


def _lost_detail(lost: Counter[str], current: TableGrid, grid: TableGrid) -> str:
    """Each lost text with the nearest of the reading's cells the current table does not have: the reading may
    write the same content differently (word order, notation), which the image decides."""
    extra = _cell_texts(grid, set()) - _cell_texts(current, set())
    cells = [(c, _plain(c.content)) for c in grid.cells if c.content.strip()]
    cells = [x for x in cells if x[1] in extra] or cells
    parts = []
    for text in sorted(lost)[:10]:
        near = max(cells, key=lambda x: fuzz.ratio(text, x[1]), default=None)
        where = f" (nearest in the reading: '{near[0].content.strip()}' at r{near[0].row}c{near[0].col})" if near else ""
        parts.append(f"'{text}'" + (f" ×{lost[text]}" if lost[text] > 1 else "") + where)
    return "cells lost: " + "; ".join(parts)


def _run(grid: TableGrid, by_column: bool) -> str:
    """The grid's text read on cell after cell, row by row or column by column (one notation, no whitespace)."""
    key = (lambda c: (c.col, c.row)) if by_column else (lambda c: (c.row, c.col))
    return "".join(_plain(c.content) for c in sorted(grid.cells, key=key))


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
