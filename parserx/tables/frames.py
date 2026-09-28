"""Several tables in one frame (tables T3, guide §6.9).

Forms draw one outer frame around several tables: a row across the whole frame — "（一）项目经费来源",
"单台50万元以下设备费", "合计：…" — separates parts whose columns differ (two columns, then four, then five).  Read as
one grid, the parts share columns they do not have, and the table cannot be read.

A full-width row is a cell spanning every column.  The frame is cut at a full-width row where the parts above and
below it use different column lines, or where the part below starts again with the head of the one above (a cell of
its first row repeats a label of that table's first row: "设备名称 | 与研究任务的相关性" after "加工或测试内容 |
与研究任务的相关性").  Otherwise the row is a group row of one table ("Restriction enzyme sites" between primer
rows) or a closing row of it ("增值税税率为 %"), and stays in it.  Once a frame is cut, its full-width rows between or around the parts become paragraphs (whether one is a title
is the title step's and the agent's to judge, Q89), and each part keeps only the column lines it uses.

A table that is not cut may still open with full-width rows: a title drawn inside the frame ("（二）考核指标、考核
方式/方法", "一、基本信息").  They become paragraphs before it; full-width rows at its end stay (a closing row).

Like ``split`` of a text block, the table block keeps the first part and its ledger (the content is only divided,
never rewritten); the other parts become new blocks right after it, on the same anchors.
"""

from __future__ import annotations

import re

from parserx.ir.block import Block
from parserx.ir.decision import Decision
from parserx.ir.enums import BlockKind, DecisionStage, ObservationStatus, TaskKind
from parserx.ir.observation import Observation
from parserx.ir.state import DocumentState, share_containers
from parserx.tables.grid import Cell, TableGrid
from parserx.workspace.queries import HIDDEN, ordered

ACTOR = "program:tables.frames"


def split_frames(state: DocumentState) -> list[str]:
    """Cut every visible table whose frame holds several tables; returns the ids of the tables cut."""
    cut = []
    for block in [b for b in ordered(state) if b.kind == BlockKind.TABLE and b.status not in HIDDEN]:
        parts = frame_parts(block.cells) if block.cells is not None else None
        if parts is not None:
            _cut(state, block, parts)
            cut.append(block.id)
    return cut


def frame_parts(grid: TableGrid) -> list[TableGrid | str] | None:
    """The tables and paragraphs a frame holds, in order, or None when it is one table."""
    full = {c.row for c in grid.cells if c.colspan == grid.n_cols and c.rowspan == 1 and grid.n_cols > 1}
    if not full:
        return None
    bands: list[list[int]] = []  # runs of rows between full-width rows
    current: list[int] = []
    for row in range(grid.n_rows):
        if row in full:
            if current:
                bands.append(current)
            current = []
        else:
            current.append(row)
    if current:
        bands.append(current)
    if any(not _inside(grid, band) for band in bands):
        return None  # a cell reaches across a full-width row: not a frame of parts
    if not bands:
        return None
    lines = [_lines(grid, band) for band in bands]
    tables: list[list[int]] = [list(bands[0])]  # parts: bands of one table, with the rows between them
    for previous, band, before, after in zip(bands, bands[1:], lines, lines[1:]):
        if before == after and not _restarts(grid, tables[-1][0], band[0]):
            tables[-1] += list(range(previous[-1] + 1, band[0])) + band
        else:
            tables.append(list(band))
    if len(tables) == 1:  # one table: its full-width rows are group or closing rows, a title above it is not
        if bands[0][0] == 0:
            return None
        tables[0] += list(range(bands[-1][-1] + 1, grid.n_rows))
    inside = {r for rows in tables for r in rows}
    out: list[TableGrid | str] = []
    row = 0
    for rows in tables:
        for r in range(row, rows[0]):
            if r not in inside:
                out.append(_text(grid, r))
        out.append(_part(grid, rows))
        row = rows[-1] + 1
    out += [_text(grid, r) for r in range(row, grid.n_rows)]
    return [p for p in out if not (isinstance(p, str) and not p.strip())]


def _restarts(grid: TableGrid, head: int, row: int) -> bool:
    """Row *row* repeats a label of row *head*: a new table under the same kind of head."""
    def labels(r: int) -> set[str]:
        return {"".join(c.content.split()) for c in grid.cells if c.row == r
                and len("".join(c.content.split())) >= 2 and not _number(c.content)}

    return bool(labels(head) & labels(row))


def _number(text: str) -> bool:
    return bool(re.fullmatch(r"[\d\s.,%+\-−–()（）]*", text))


def _inside(grid: TableGrid, band: list[int]) -> bool:
    rows = set(band)
    return all(set(range(c.row, c.row + c.rowspan)) <= rows for c in grid.cells if c.row in rows)


def _lines(grid: TableGrid, band: list[int]) -> frozenset[int]:
    """The column lines a band's cells use."""
    rows = set(band)
    return frozenset(x for c in grid.cells if c.row in rows for x in (c.col, c.col + c.colspan))


def _part(grid: TableGrid, rows: list[int]) -> TableGrid:
    """The rows as a table of their own, on the column lines they use."""
    keep = set(rows)
    cells = [c for c in grid.cells if c.row in keep]
    lines = sorted({x for c in cells for x in (c.col, c.col + c.colspan)} | {0, grid.n_cols})
    col = {x: i for i, x in enumerate(lines)}
    row = {r: i for i, r in enumerate(sorted(keep))}
    moved = [Cell(row=row[c.row], col=col[c.col], rowspan=c.rowspan, colspan=col[c.col + c.colspan] - col[c.col],
                  content=c.content) for c in cells]
    return TableGrid(n_rows=len(keep), n_cols=len(lines) - 1, cells=moved)


def _text(grid: TableGrid, row: int) -> str:
    return " ".join(c.content.strip() for c in sorted((c for c in grid.cells if c.row == row), key=lambda c: c.col)
                    if c.content.strip())


def _cut(state: DocumentState, block: Block, parts: list[TableGrid | str]) -> None:
    first = next(i for i, p in enumerate(parts) if isinstance(p, TableGrid))
    new: list[Block] = []
    for k, part in enumerate(parts):
        if k == first:
            continue
        part_id = f"{block.id}-f{len(new) + 1}"
        table = isinstance(part, TableGrid)
        obs = Observation(id=f"o-{part_id}-frame-1", engine="frame", engine_version=ACTOR, task=TaskKind.SPLIT,
                          anchor=block.anchors[0], text=None if table else part, cells=part if table else None,
                          status=ObservationStatus.OK)
        new.append(Block(id=part_id, kind=BlockKind.TABLE if table else BlockKind.TEXT, order=block.order,
                         anchors=list(block.anchors), observations=[obs], chosen_observation=obs.id,
                         text="" if table else part, cells=part if table else None,
                         decisions=[Decision(stage=DecisionStage.STRUCTURE, choice="split_frame", actor=ACTOR,
                                             reason="a part of a frame holding several tables", evidence={},
                                             refs=[block.id])]))
    block.cells = parts[first]
    block.decisions.append(Decision(
        stage=DecisionStage.STRUCTURE, choice="split_frame", actor=ACTOR, refs=[b.id for b in new],
        reason="one frame, several tables: full-width rows separate parts with different columns",
        evidence={"parts": len(parts), "tables": sum(isinstance(p, TableGrid) for p in parts)}))
    before, after = new[:first], new[first:]
    sequence = ordered(state)
    at = sequence.index(block)
    sequence[at:at + 1] = [*before, block, *after]
    state.blocks.extend(new)
    share_containers(state, block.id, [b.id for b in new])
    for order, item in enumerate(sequence):
        item.order = order
