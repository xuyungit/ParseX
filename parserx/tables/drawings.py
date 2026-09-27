"""Tables read in a drawing are its labels (tables T5).

The scan engine reads the text of a figure (Q42) and sometimes lays a drawing's labels out as a table — a
flowchart's boxes ("坡度 0.1 | 系数 9.8 | CC#4 → | 开始"), an engineering drawing's callouts ("非偏载侧防撞墙 | 第二跨 |
① | ②"). When the service VLM, looking at the whole image, describes it as a diagram or a chart, the two readings
disagree and the whole-image one decides: what was read in it is text — the labels in reading order — not a table.
A table photographed or scanned is not described (its route reads it as text), so it is left as it is.
"""

from __future__ import annotations

from parserx.ir.decision import Decision
from parserx.ir.enums import BlockKind, DecisionStage, RelationKind
from parserx.ir.state import DocumentState
from parserx.workspace.queries import HIDDEN

ACTOR = "program:tables.drawings"
DRAWINGS = frozenset({"diagram", "chart"})


def tables_in_drawings(state: DocumentState) -> list[str]:
    """Turn the tables read inside figures described as diagrams or charts into text; returns their ids."""
    blocks = {b.id: b for b in state.blocks}
    drawings = {b.id for b in state.blocks if b.kind == BlockKind.FIGURE and b.semantic is not None
                and b.semantic.type in DRAWINGS}
    done = []
    for relation in state.relations:
        block = blocks.get(relation.dst)
        if (relation.kind != RelationKind.CONTAINS or relation.src not in drawings or block is None
                or block.kind != BlockKind.TABLE or block.status in HIDDEN or block.cells is None):
            continue
        cells = sorted(block.cells.cells, key=lambda c: (c.row, c.col))
        labels = " ".join(" ".join(c.content.split()) for c in cells if c.content.strip())
        block.cells, block.kind, block.text = None, BlockKind.TEXT, labels
        block.decisions.append(Decision(
            stage=DecisionStage.STRUCTURE, choice="labels_of_a_drawing", actor=ACTOR, refs=[relation.src],
            reason=f"read as a table in a figure described as a {blocks[relation.src].semantic.type}: its labels",
            evidence={"figure": relation.src}))
        done.append(block.id)
    return done
