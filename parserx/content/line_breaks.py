"""Line breaks of scanned text that the scan engine read as one run (P4).

The scan engine may return a passage whose lines were separate on the page as one run of text: the dated steps of
a schedule, the items "（1）…（2）…（3）…", the paragraphs of an answer in a form.  The local page reading (guide §9.5)
has each line with its place.  Where a line ends well before the right edge of its region — the region's width
``SHORT`` or more left over — and the next line starts again at the left edge — and the line closes a sentence (``。`` ``；`` ``.`` …) or the next
starts an item (a number, a date) — the line was ended on purpose (a paragraph, an item); a line that runs to the
edge wraps, unless it closes an item and the next line starts the way it did (the same numbering, a date): the next
item of a list.  Text that flows narrow beside a picture or a table ends short without closing anything: it wraps.  The break is put back in the text at the start of the next
line, found by its first characters (compared on letters and digits, as the two readings are compared).

Only where the region's right edge is known: a text block (its box) and a table cell spanning every column (the
table's box).  Boxes are compared as the page is shown: on a page turned by its /Rotate, the region and the reading's
lines (in the unrotated page, like every box) are turned to the shown page first (``ir/rotation.py``).  A text block
is split at the breaks into paragraphs (the output joins the lines of a paragraph); a cell keeps them as line
breaks.  Text is never rewritten: only line breaks are added, recorded as an observation of
the block.  Lines the two readings do not both have, and lines whose start cannot be found, add no break.
"""

from __future__ import annotations

import re

from rapidfuzz import fuzz

from parserx.hierarchy.changes import Split
from parserx.hierarchy.legality import apply_changes, numbering_signature
from parserx.ir import ids
from parserx.ir.anchor import PdfAnchor
from parserx.ir.block import Block
from parserx.ir.decision import Decision
from parserx.ir.enums import BlockKind, DecisionStage, ObservationStatus, TaskKind
from parserx.ir.observation import Observation
from parserx.ir.rotation import shown
from parserx.ir.state import DocumentState, ReadLine
from parserx.reading.compare import _centre, _inside, normalize
from parserx.tables.grid import TableGrid
from parserx.workspace.queries import HIDDEN

ACTOR = "program:content.line_breaks"
SHORT = 0.15  # share of the region's width a line leaves before the right edge when it was ended on purpose
LEFT = 0.08  # share of the region's width within which the next line starts again at the left edge
KEY = 4  # letters and digits of a line's start looked for in the text
SLACK = 3  # letters by which the two readings may differ where one line meets the next
MATCH = 80  # rapidfuzz ratio of a line against the text where it is found: the same line, not a look-alike
_CLOSES = re.compile(r"[；。;！？!?：:.]\s*$")
_DATE = re.compile(r"\d{4}\s*[.．年/\-]\s*\d{1,2}")
_OPENING = "（([【《〈“‘\"'"
_SCAN_ENGINES = frozenset({"paddleocr"})


def restore_line_breaks(state: DocumentState) -> list[str]:
    """Put back the line breaks of scanned text blocks and full-width cells; the ids of the blocks changed."""
    readings = {r.n: r for r in state.readings if r.lines}
    pages = {p.n: p for p in state.pages}
    changed: list[str] = []
    for block in list(state.blocks):
        anchors = [a for a in block.anchors if isinstance(a, PdfAnchor) and a.coord_space == "page_pt"]
        if block.status in HIDDEN or not anchors or len(anchors) != len(block.anchors) \
                or not any(o.engine in _SCAN_ENGINES for o in block.observations):
            continue
        # each line of the local reading with the region it lies in (a table continued on the next page: two)
        lines = []
        for a in anchors:
            page = pages.get(a.page)
            region = shown(page, a.bbox)
            turned = [ln.model_copy(update={"bbox": shown(page, ln.bbox)})
                      for ln in (readings[a.page].lines if a.page in readings else [])]
            lines += [(ln, region) for ln in _rows([ln for ln in turned if _inside(_centre(ln.bbox), region)])]
        if block.kind == BlockKind.TEXT and block.text and "\n" not in block.text:
            text = _with_breaks(block.text, lines)
            if text != block.text:
                _record(block, text=text, grid=None, breaks=text.count("\n"))
                for at in range(text.count("\n"), 0, -1):  # from the last: the parts stay in order
                    apply_changes(state, [Split(op="split", block=block.id, at_break=at,
                                                reason="a line the page ends before the right edge (P4)")],
                                  actor=ACTOR)
                changed.append(block.id)
        elif block.kind == BlockKind.TABLE and block.cells is not None:
            grid = _cells_with_breaks(block.cells, lines)
            if grid is not None:
                _record(block, text=None, grid=grid, breaks=sum(c.content.count("\n") for c in grid.cells)
                        - sum(c.content.count("\n") for c in block.cells.cells))
                changed.append(block.id)
    return changed


def _with_breaks(text: str, lines: list[tuple[ReadLine, tuple]]) -> str:
    """*text* with a line break before each line that follows a line ended on purpose (lines with their regions, in
    reading order).  The lines of *text* are those that follow one another in it: the first where the text starts,
    each next one where the one before ends (within ``SLACK`` letters: the two readings differ a little); a line of
    another cell or block that happens to share words does not follow."""
    index = [i for i, ch in enumerate(text) if normalize(ch)]
    letters = "".join(normalize(text[i]) for i in index)
    chain: list[tuple[ReadLine, tuple, int]] = []  # line, region, where its letters start in *letters*
    for line, box in lines:
        own = normalize(line.text)
        if len(own) < 2:
            continue
        if chain:
            expected = chain[-1][2] + len(normalize(chain[-1][0].text))
            found = letters.find(own[:KEY], max(0, expected - SLACK))
            if found >= 0 and abs(found - expected) <= SLACK and _reads(letters, own, found):
                chain.append((line, box, found))
                continue
        if len(chain) < 2 and _reads(letters, own, 0):  # the text's first line (another cell's may look alike)
            chain = [(line, box, 0)]
    breaks: list[int] = []
    for (line, box, start), (following, next_box, found) in zip(chain, chain[1:]):
        if found <= 0:
            continue
        at = index[found]
        while at > 0 and text[at - 1] in _OPENING:  # "（1）" starts at its bracket
            at -= 1
        # judged on the engine's text around the break: the local reader may drop a space or a mark
        this, rest = text[index[start] if start else 0:at], text[at:]
        short = box[2] - line.bbox[2] >= SHORT * (box[2] - box[0])
        ended = (short and (_CLOSES.search(this) is not None or _item_start(rest) is not None)) \
            or _next_item(this, rest)
        again = following.bbox[0] - next_box[0] <= LEFT * (next_box[2] - next_box[0])
        if ended and again and at > (breaks[-1] if breaks else 0):
            breaks.append(at)
    if not breaks:
        return text
    parts, last = [], 0
    for at in breaks:
        parts.append(text[last:at].rstrip())
        last = at
    parts.append(text[last:].lstrip())
    return "\n".join(part for part in parts if part)


def _rows(lines: list[ReadLine]) -> list[ReadLine]:
    """The lines of a region as rows, top to bottom: the local reader may cut one row at a wide gap (justified text,
    a form's spacing) — pieces side by side are one row, which ends where its last piece ends."""
    rows: list[list[ReadLine]] = []
    for line in sorted(lines, key=lambda ln: (ln.bbox[1], ln.bbox[0])):
        row = rows[-1] if rows else None
        if row is not None and _same_row(row[-1].bbox, line.bbox):
            row.append(line)
        else:
            rows.append([line])
    out = []
    for row in rows:
        row.sort(key=lambda ln: ln.bbox[0])
        box = (min(ln.bbox[0] for ln in row), min(ln.bbox[1] for ln in row),
               max(ln.bbox[2] for ln in row), max(ln.bbox[3] for ln in row))
        out.append(ReadLine(bbox=box, text=" ".join(ln.text for ln in row), score=min(ln.score for ln in row)))
    return out


def _same_row(a, b) -> bool:
    overlap = min(a[3], b[3]) - max(a[1], b[1])
    return overlap > 0.5 * min(a[3] - a[1], b[3] - b[1])


def _next_item(line: str, following: str) -> bool:
    """A line that runs to the edge but closes an item, followed by a line that starts the way it did — the same
    numbering, or a date: the next item of a list (a schedule's dated steps)."""
    if not _CLOSES.search(line):
        return False
    start, next_start = _item_start(line), _item_start(following)
    return start is not None and start == next_start


def _item_start(text: str) -> str | None:
    text = text.strip()
    return numbering_signature(text) or ("date" if _DATE.match(text) else None)


def _reads(letters: str, own: str, at: int) -> bool:
    """The text has the line's letters at *at*, give or take a misread letter."""
    return fuzz.ratio(letters[at:at + len(own)], own) >= MATCH


def _cells_with_breaks(grid: TableGrid, lines: list[tuple[ReadLine, tuple]]) -> TableGrid | None:
    """The grid with line breaks put back in its full-width cells, or None when none changes."""
    cells, changed = [], False
    for cell in grid.cells:
        if cell.colspan == grid.n_cols and grid.n_cols > 1 and "\n" not in cell.content:
            content = _with_breaks(cell.content, lines)
            if content != cell.content:
                cell, changed = cell.model_copy(update={"content": content}), True
        cells.append(cell)
    return grid.model_copy(update={"cells": cells}) if changed else None


def _record(block: Block, *, text: str | None, grid: TableGrid | None, breaks: int) -> None:
    n = sum(1 for o in block.observations if o.engine == "line_breaks") + 1
    observation = Observation(id=ids.observation_id(block.id, "line_breaks", n), engine="line_breaks",
                              engine_version=ACTOR, task=TaskKind.SPLIT, anchor=block.anchors[0],
                              text=text if text is not None else None, cells=grid, status=ObservationStatus.OK)
    block.observations.append(observation)
    block.chosen_observation = observation.id
    if text is not None:
        block.text = text
    if grid is not None:
        block.cells = grid
    block.decisions.append(Decision(stage=DecisionStage.CONTENT_SOURCE, choice="line_breaks", actor=ACTOR,
                                    reason="lines the page ends before the right edge, found by the local reading",
                                    evidence={"breaks": breaks}))
