"""``correct``: the agent corrects what it read from the image itself (plan P2-5, guide §14 Q30).

The main agent sees images: once it has looked at a block (``read --image crop`` or the page image) and read the
text differently, it names the spans (text) or cells (table) to change and what they say — no second VLM reading.
The program still decides (``content/select.py::correct``): the image must be one ``read`` produced for this block
or its page in this workspace, a native text layer's numbers never change, and the result keeps content.  The
correction is a new Observation (``task=correct``, ``engine=agent``); the earlier readings stay as evidence.

Text: each ``find`` must occur exactly once in the block's current text; ``replace`` is what the image shows.
Table: each cell is ``row``, ``col`` and the new content; an empty slot inside the grid may be filled (Q45).
A native text layer's numbers change only as the local page reading of the block's place shows them (Q56).
Add (Q56): text a page image shows where no block has it (a ``text_unaccounted`` item) — ``page``, ``bbox`` in page
points, the ``text``.  It becomes a new text block only when the agent looked at an image of that place and the
local reading of the place shows the text: two independent readings agree.
DOCX text comes from the document's XML and has no page image, so it cannot be corrected this way.
"""

from __future__ import annotations

from parserx.content.select import GateCheck
from parserx.content.select import add_gate
from parserx.content.select import correct as correct_gate
from parserx.ir import ids
from parserx.ir.base import IRModel
from parserx.ir.anchor import PdfAnchor
from parserx.ir.block import Block
from parserx.ir.decision import Decision
from parserx.ir.enums import BlockKind, BlockStatus, DecisionStage, ObservationStatus, RelationKind, TaskKind
from parserx.ir.observation import Observation
from parserx.ir.state import LedgerEntry
from parserx.reading.compare import holders_of, text_at, text_near
from parserx.tables.grid import Cell, TableGrid
from parserx.tools.context import ToolContext, ToolOutput, output
from parserx.tools.envelope import Change, DocText, FailureCode, ToolFailure, Unresolved, UnresolvedKind
from parserx.tools.evidence import image_evidence, image_evidence_at
from parserx.tools.recognize import _next_block_seq, _next_item
from parserx.workspace.queries import block_unit, ordered

_TEXT_KINDS = frozenset({BlockKind.TEXT, BlockKind.TITLE, BlockKind.LIST, BlockKind.CAPTION, BlockKind.FOOTNOTE,
                         BlockKind.FORMULA, BlockKind.OTHER})


class TextEdit(IRModel):
    find: str  # must occur exactly once in the block's current text
    replace: str


class CellEdit(IRModel):
    row: int
    col: int
    content: str


class AddText(IRModel):
    page: int
    bbox: tuple[float, float, float, float]  # page points: where the page image shows the text
    text: str
    after: str | None = None  # the block it follows in reading order; default: by its place on the page


class CorrectRequest(IRModel):
    block: str | None = None  # the block to correct; none when adding
    image: str  # the evidence the correction rests on (an evidence id, or the image a look returned)
    reason: str
    edits: list[TextEdit] = []
    cells: list[CellEdit] = []
    add: AddText | None = None  # text in no block yet (Q56)
    actor: str = "agent"


class CorrectResult(IRModel):
    candidate: str  # the new Observation
    adopted: bool
    gate: list[GateCheck]
    text: DocText | None = None  # the block's text after the call (text blocks)


def run(ctx: ToolContext, req: CorrectRequest) -> ToolOutput[CorrectResult]:
    if (req.add is None) == (req.block is None):
        raise ToolFailure(FailureCode.INVALID_REQUEST, "give a block to correct, or add")
    if req.add is not None:
        return _add(ctx, req)
    state = ctx.ws.load()
    block = next((b for b in state.blocks if b.id == req.block), None)
    if block is None:
        raise ToolFailure(FailureCode.NOT_FOUND, f"no block {req.block}", targets=[req.block])
    if bool(req.edits) == bool(req.cells):
        raise ToolFailure(FailureCode.INVALID_REQUEST, "give edits (text) or cells (table)", targets=[req.block])
    if block.status == BlockStatus.MERGED:  # its content lives on in another block: a correction here would not show
        into = next((r.src for r in state.relations if r.kind == RelationKind.CONTINUES and r.dst == block.id), None)
        raise ToolFailure(FailureCode.INVALID_REQUEST, f"{req.block} is merged into {into or 'another block'}: "
                                                       "correct that block (it holds this page's content too)",
                          targets=[req.block])
    if block.kind == BlockKind.TABLE:
        if not req.cells or block.cells is None:
            raise ToolFailure(FailureCode.INVALID_REQUEST, f"{req.block} is a table: give cells", targets=[req.block])
        text, grid = None, _edited_grid(block.cells, req.cells, req.block)
    elif block.kind in _TEXT_KINDS:
        if not req.edits:
            raise ToolFailure(FailureCode.INVALID_REQUEST, f"{req.block} is text: give edits", targets=[req.block])
        text, grid = _edited_text(block.text, req.edits, req.block), None
    else:
        raise ToolFailure(FailureCode.INVALID_REQUEST, f"{req.block} is a {block.kind}; only text and table blocks "
                                                       "can be corrected", targets=[req.block])
    image = image_evidence(state, block, req.image)
    with ctx.ws.txn(f"tool:correct:{req.actor}") as state:
        block = next(b for b in state.blocks if b.id == req.block)
        n = sum(1 for o in block.observations if o.task == TaskKind.CORRECT) + 1
        candidate = Observation(id=ids.observation_id(block.id, "agent", n), engine="agent", engine_version=req.actor,
                                task=TaskKind.CORRECT, anchor=block.anchors[0], text=text, cells=grid,
                                status=ObservationStatus.OK)
        before = block.text if grid is None else None
        outcome = correct_gate(block, candidate, image=image, actor=req.actor, seen=text_near(state, block))
        block.decisions[-1].evidence["image"] = req.image
        block.decisions[-1].reason += f"; {req.reason}"
        after_text = block.text
    diff = []
    if outcome.adopted:
        if grid is not None:
            diff.append(Change(target=req.block, field="cells", before=None, after=len(req.cells)))
        else:  # document text travels only as DocText (guide §3.3)
            diff.append(Change(target=req.block, field="text", before={"doc_text": before},
                               after={"doc_text": after_text}))
    unresolved = [] if outcome.adopted else [Unresolved(
        target=req.block, kind=UnresolvedKind.EVIDENCE_CONFLICT,
        detail="correction not adopted: " + "; ".join(f"{g.name}: {g.detail}" for g in outcome.gate if not g.passed))]
    return output(CorrectResult(candidate=candidate.id, adopted=outcome.adopted, gate=outcome.gate,
                                text=DocText(doc_text=after_text) if grid is None else None),
                  diff=diff, unresolved=unresolved)


def _add(ctx: ToolContext, req: CorrectRequest) -> ToolOutput[CorrectResult]:
    add = req.add
    state = ctx.ws.load()
    if state.format != "pdf" or add.page not in {p.n for p in state.pages}:
        raise ToolFailure(FailureCode.INVALID_REQUEST, f"no PDF page {add.page}", targets=[f"p{add.page}"])
    if add.after is not None and add.after not in {b.id for b in state.blocks}:
        raise ToolFailure(FailureCode.NOT_FOUND, f"no block {add.after}", targets=[add.after])
    gate = add_gate(add.text, image=image_evidence_at(state, add.page, add.bbox, req.image),
                    seen=text_at(state, add.page, add.bbox), holders=holders_of(state, add.page, add.bbox, add.text))
    if not all(g.passed for g in gate):
        return output(CorrectResult(candidate="", adopted=False, gate=gate), unresolved=[Unresolved(
            target=f"p{add.page}", kind=UnresolvedKind.EVIDENCE_CONFLICT,
            detail="text not added: " + "; ".join(f"{g.name}: {g.detail}" for g in gate if not g.passed))])
    with ctx.ws.txn(f"tool:correct:{req.actor}") as state:
        block_id = ids.block_id_pdf(add.page, _next_block_seq(state, add.page))
        anchor = PdfAnchor(page=add.page, bbox=add.bbox, coord_space="page_pt")
        observation = Observation(id=ids.observation_id(block_id, "agent", 1), engine="agent", engine_version=req.actor,
                                  task=TaskKind.CORRECT, anchor=anchor, text=add.text, status=ObservationStatus.OK)
        block = Block(id=block_id, kind=BlockKind.TEXT, order=0, anchors=[anchor], observations=[observation],
                      chosen_observation=observation.id, text=add.text, decisions=[Decision(
                          stage=DecisionStage.REVIEW_ACCEPT, choice="added", actor=req.actor,
                          reason=f"text the page image shows where no block had it; {req.reason}",
                          evidence={"image": req.image, **{g.name: g.detail for g in gate}})])
        sequence = ordered(state)
        at = (next(i for i, b in enumerate(sequence) if b.id == add.after) + 1 if add.after
              else _place(state, sequence, add.page, add.bbox))
        sequence.insert(at, block)
        for order, item in enumerate(sequence):
            item.order = order
        state.blocks.append(block)
        state.ledger.append(LedgerEntry(item=ids.ledger_item_pdf(add.page, _next_item(state, add.page)),
                                        unit="agent_text", source=anchor, chars=len("".join(add.text.split())),
                                        disposition="output", block=block_id))
    return output(CorrectResult(candidate=block_id, adopted=True, gate=gate, text=DocText(doc_text=add.text)),
                  diff=[Change(target=block_id, field="text", before=None, after={"doc_text": add.text})])


def _place(state, sequence: list, page: int, bbox) -> int:
    """Reading-order position of new text on *page*: after the last block of the page that starts above it."""
    on_page = [i for i, b in enumerate(sequence) if block_unit(state, b) == page]
    above = [i for i in on_page if isinstance(sequence[i].anchors[0], PdfAnchor)
             and sequence[i].anchors[0].bbox[1] <= bbox[1]]
    if above:
        return above[-1] + 1
    if on_page:
        return on_page[0]
    return sum(1 for b in sequence if (block_unit(state, b) or 0) < page)


def _edited_text(text: str, edits: list[TextEdit], block: str) -> str:
    for edit in edits:
        count = text.count(edit.find) if edit.find else 0
        if count != 1:
            raise ToolFailure(FailureCode.INVALID_REQUEST, f"'find' must occur exactly once in {block}'s text "
                                                           f"(found {count} times): {edit.find[:40]!r}",
                              targets=[block])
        text = text.replace(edit.find, edit.replace, 1)
    return text


def _edited_grid(grid: TableGrid, edits: list[CellEdit], block: str) -> TableGrid:
    cells = {(c.row, c.col): c for c in grid.cells}
    for edit in edits:
        if not (0 <= edit.row < grid.n_rows and 0 <= edit.col < grid.n_cols):
            raise ToolFailure(FailureCode.INVALID_REQUEST, f"cell ({edit.row}, {edit.col}) is outside the "
                                                           f"{grid.n_rows}×{grid.n_cols} table", targets=[block])
        slot = grid.slot(edit.row, edit.col)
        if slot is None:  # an empty position inside the grid: filled (Q45)
            cells[(edit.row, edit.col)] = Cell(row=edit.row, col=edit.col, content=edit.content)
        else:
            cells[(slot.row, slot.col)] = slot.model_copy(update={"content": edit.content})
    return TableGrid(n_rows=grid.n_rows, n_cols=grid.n_cols, header_rows=grid.header_rows,
                     cells=sorted(cells.values(), key=lambda c: (c.row, c.col)))


