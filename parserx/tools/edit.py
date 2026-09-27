"""``edit_draft``: change the draft (Q85) — the only way it changes.

A call is a list of operations, applied in order in one transaction; each is accepted or refused on its own, with
the rule that refused it (``atomic``: any refusal and none takes effect).  Every operation says why (``reason``);
one that changes content cites the evidence it rests on (``evidence``, from ``view_source``):

- content: ``replace_text`` (a span that occurs exactly once in the block), ``insert_text`` (text a page shows where
  no block has it), ``set_cells`` (table cells), ``adopt`` (a reading of the source as it was read: a page's or a
  figure's text, a table read again, a figure's description);
- structure: ``set_role``, ``set_level``, ``move``, ``link`` / ``unlink``, ``merge_tables``, ``split``, ``exclude`` /
  ``include``, ``mark_pending`` — never the text;
- the worklist: ``dismiss`` an issue the evidence shows needs no change.

The program checks each operation: content must rest on evidence of its place, a native text layer's numbers change
only as the local reading shows them, a table read again passes the acceptance gate, structure stays legal (no
skipped level, one level per numbering pattern), and a proposal of the pipeline never overrides the agent.
"""

from __future__ import annotations

import hashlib
import json
from typing import Annotated, Literal

from pydantic import Field, model_validator

from parserx.content import scan
from parserx.content.select import add_gate, integrate_image, transcribed
from parserx.content.select import correct as correct_gate
from parserx.content.select import review_table as table_gate
from parserx.hierarchy import apply_changes
from parserx.hierarchy.changes import (
    Exclude,
    Include,
    Link,
    MarkPending,
    MergeTables,
    Move,
    SetLevel,
    SetRole,
    Split,
    Unlink,
)
from parserx.ir import ids
from parserx.ir.anchor import AssetAnchor, PdfAnchor
from parserx.ir.base import BBox, IRModel
from parserx.ir.block import Block
from parserx.ir.decision import Decision
from parserx.ir.enums import BlockKind, BlockStatus, DecisionStage, ObservationStatus, RelationKind, TaskKind
from parserx.ir.observation import Observation
from parserx.ir.state import ClosedItem, DocumentState, LedgerEntry
from parserx.reading.compare import holders_of, text_at, text_near
from parserx.tables.grid import Cell, TableGrid
from parserx.tools.context import ToolContext, ToolOutput, output
from parserx.tools.describe_figure import Described
from parserx.tools.describe_figure import apply as apply_description
from parserx.tools.envelope import Change, ToolFailure, Unresolved, UnresolvedKind
from parserx.tools.evidence import image_evidence, image_evidence_at
from parserx.tools.recognize import _next_block_seq, _next_item, integrate_page, scan_engine_pages
from parserx.tools.views import unresolved_items
from parserx.workspace.queries import block_unit, ordered

ACTOR = "agent"
_TEXT_KINDS = frozenset({BlockKind.TEXT, BlockKind.TITLE, BlockKind.LIST, BlockKind.CAPTION, BlockKind.FOOTNOTE,
                         BlockKind.FORMULA, BlockKind.OTHER})
# Failures are resolved by processing, not dismissed: a pending page, a failed block, a skipped budget, a lost asset.
_NOT_DISMISSED = frozenset({UnresolvedKind.PAGE_PENDING, UnresolvedKind.BLOCK_FAILED, UnresolvedKind.BUDGET_SKIPPED,
                            UnresolvedKind.ASSET_MISSING})


class CellEdit(IRModel):
    row: int
    col: int
    content: str


class ReplaceText(IRModel):
    op: Literal["replace_text"]
    block: str
    find: str  # must occur exactly once in the block's current text
    replace: str  # what the source shows
    reason: str
    evidence: str


class InsertText(IRModel):
    op: Literal["insert_text"]
    page: int
    bbox: BBox  # page points: where the page shows the text
    text: str
    after: str | None = None  # the block it follows in reading order; default: by its place on the page
    reason: str
    evidence: str


class SetCells(IRModel):
    op: Literal["set_cells"]
    block: str
    cells: list[CellEdit] = Field(min_length=1)  # an empty place inside the grid may be filled (Q45)
    reason: str
    evidence: str


class Adopt(IRModel):
    op: Literal["adopt"]
    block: str | None = None  # a table read again, a figure's description or text
    page: int | None = None  # a page's text
    evidence: str
    reason: str

    @model_validator(mode="after")
    def _one_target(self) -> "Adopt":
        if (self.block is None) == (self.page is None):
            raise ValueError("adopt: give the block or the page the reading is of")
        return self


class Dismiss(IRModel):
    op: Literal["dismiss"]
    issue: str  # the issue's id (read_draft view=issues)
    reason: str
    evidence: str  # what showed that nothing needs to change
    occluded: bool = False  # text_not_seen: the text is there, drawn under another element (Q71)


EditOp = Annotated[
    ReplaceText | InsertText | SetCells | Adopt | Dismiss | SetRole | SetLevel | Move | Link | Unlink | MergeTables
    | MarkPending | Exclude | Include | Split,
    Field(discriminator="op"),
]


class EditDraftRequest(IRModel):
    ops: list[EditOp] = Field(min_length=1)
    atomic: bool = False  # any refusal → nothing applied


class OpOutcome(IRModel):
    index: int
    op: str
    accepted: bool
    rule: str | None = None  # what refused it
    detail: str | None = None
    block: str | None = None  # a block the operation made (insert_text, split)


class EditDraftResult(IRModel):
    outcomes: list[OpOutcome]  # one per operation, in order
    issues_opened: list[Unresolved]  # what the accepted changes opened
    issues_closed: list[str]  # ids of issues they resolved or dismissed


class _Refused(Exception):
    def __init__(self, rule: str, detail: str):
        super().__init__(detail)
        self.rule, self.detail = rule, detail


class _Rollback(Exception):
    pass


def run(ctx: ToolContext, req: EditDraftRequest) -> ToolOutput[EditDraftResult]:
    open_before = {u.id: u for u in unresolved_items(ctx.ws.load())}
    outcomes: list[OpOutcome] = []
    diff: list[Change] = []
    try:
        with ctx.ws.txn("tool:edit_draft:agent") as state:
            before = _snapshot(state)
            issues = _Issues(state)
            for index, op in enumerate(req.ops):
                outcomes.append(_apply(ctx, state, index, op, issues))
            accepted = [o.accepted for o in outcomes]
            if not any(accepted) or (req.atomic and not all(accepted)):
                raise _Rollback
            diff = _diff(before, state)
    except _Rollback:
        if req.atomic:
            outcomes = [o.model_copy(update={"accepted": False, "rule": "atomic",
                                             "detail": "another operation of this call was refused"})
                        if o.accepted else o for o in outcomes]
        return output(EditDraftResult(outcomes=outcomes, issues_opened=[], issues_closed=[]))
    open_after = {u.id: u for u in unresolved_items(ctx.ws.load())}
    return output(EditDraftResult(outcomes=outcomes,
                                  issues_opened=[u for k, u in open_after.items() if k not in open_before],
                                  issues_closed=[k for k in open_before if k not in open_after]), diff=diff)


def _apply(ctx: ToolContext, state: DocumentState, index: int, op, issues: "_Issues") -> OpOutcome:
    made = None
    try:
        if isinstance(op, ReplaceText):
            _replace_text(state, op)
        elif isinstance(op, SetCells):
            _set_cells(state, op)
        elif isinstance(op, InsertText):
            made = _insert_text(state, op)
        elif isinstance(op, Adopt):
            _adopt(ctx, state, op)
        elif isinstance(op, Dismiss):
            _dismiss(state, op, issues)
        else:
            known = {b.id for b in state.blocks}
            outcome = apply_changes(state, [op], actor=ACTOR)
            if outcome.rejected:
                rejection = outcome.rejected[0]
                raise _Refused(rejection.rule.value, rejection.detail)
            made = next((b.id for b in state.blocks if b.id not in known), None)
    except _Refused as exc:
        return OpOutcome(index=index, op=op.op, accepted=False, rule=exc.rule, detail=exc.detail)
    except ToolFailure as exc:
        return OpOutcome(index=index, op=op.op, accepted=False, rule=exc.failure.code.value,
                         detail=exc.failure.message)
    issues.changed()
    return OpOutcome(index=index, op=op.op, accepted=True, block=made)


# ── content ─────────────────────────────────────────────────────────────


def _block(state: DocumentState, block_id: str) -> Block:
    block = next((b for b in state.blocks if b.id == block_id), None)
    if block is None:
        raise _Refused("unknown_block", f"no block {block_id}")
    if block.status == BlockStatus.MERGED:  # its content lives on in another block: a change here would not show
        into = next((r.src for r in state.relations if r.kind == RelationKind.CONTINUES and r.dst == block.id), None)
        raise _Refused("merged", f"{block_id} is merged into {into or 'another block'}: change that block")
    return block


def _replace_text(state: DocumentState, op: ReplaceText) -> None:
    block = _block(state, op.block)
    if block.kind not in _TEXT_KINDS:
        raise _Refused("not_text", f"{op.block} is a {block.kind.value}: " + (
            "use set_cells" if block.kind == BlockKind.TABLE else "only text blocks have text to replace"))
    count = block.text.count(op.find) if op.find else 0
    if count != 1:
        raise _Refused("find", f"'find' must occur exactly once in {op.block}'s text (found {count} times): "
                               f"{op.find[:40]!r}; give a longer span")
    _correct(state, block, op, text=block.text.replace(op.find, op.replace, 1), grid=None)


def _set_cells(state: DocumentState, op: SetCells) -> None:
    block = _block(state, op.block)
    if block.kind != BlockKind.TABLE or block.cells is None:
        raise _Refused("not_table", f"{op.block} is a {block.kind.value}, not a table")
    grid = block.cells
    cells = {(c.row, c.col): c for c in grid.cells}
    for edit in op.cells:
        if not (0 <= edit.row < grid.n_rows and 0 <= edit.col < grid.n_cols):
            raise _Refused("cell", f"cell ({edit.row}, {edit.col}) is outside the {grid.n_rows}×{grid.n_cols} table")
        slot = grid.slot(edit.row, edit.col)
        if slot is None:
            cells[(edit.row, edit.col)] = Cell(row=edit.row, col=edit.col, content=edit.content)
        else:
            cells[(slot.row, slot.col)] = slot.model_copy(update={"content": edit.content})
    edited = TableGrid(n_rows=grid.n_rows, n_cols=grid.n_cols, header_rows=grid.header_rows,
                       cells=sorted(cells.values(), key=lambda c: (c.row, c.col)))
    _correct(state, block, op, text=None, grid=edited)


def _correct(state: DocumentState, block: Block, op, *, text: str | None, grid: TableGrid | None) -> None:
    """The agent's own reading of spans or cells becomes a candidate; the gate adopts it or not (content/select.py)."""
    image = image_evidence(state, block, op.evidence)
    n = sum(1 for o in block.observations if o.task == TaskKind.CORRECT) + 1
    candidate = Observation(id=ids.observation_id(block.id, "agent", n), engine="agent", engine_version=ACTOR,
                            task=TaskKind.CORRECT, anchor=block.anchors[0], text=text, cells=grid,
                            status=ObservationStatus.OK)
    if not image.passed:
        raise _Refused(image.name, image.detail)
    outcome = correct_gate(block, candidate, image=image, actor=ACTOR, seen=text_near(state, block))
    block.decisions[-1].evidence["evidence"] = op.evidence
    block.decisions[-1].reason += f"; {op.reason}"
    if not outcome.adopted:
        failed = next(g for g in outcome.gate if not g.passed)
        raise _Refused(failed.name, failed.detail)


def _insert_text(state: DocumentState, op: InsertText) -> str:
    if state.format != "pdf" or op.page not in {p.n for p in state.pages}:
        raise _Refused("page", f"no PDF page {op.page}")
    if op.after is not None and op.after not in {b.id for b in state.blocks}:
        raise _Refused("unknown_block", f"no block {op.after}")
    gate = add_gate(op.text, image=image_evidence_at(state, op.page, op.bbox, op.evidence),
                    seen=text_at(state, op.page, op.bbox), holders=holders_of(state, op.page, op.bbox, op.text))
    failed = next((g for g in gate if not g.passed), None)
    if failed is not None:
        raise _Refused(failed.name, failed.detail)
    block_id = ids.block_id_pdf(op.page, _next_block_seq(state, op.page))
    anchor = PdfAnchor(page=op.page, bbox=op.bbox, coord_space="page_pt")
    observation = Observation(id=ids.observation_id(block_id, "agent", 1), engine="agent", engine_version=ACTOR,
                              task=TaskKind.CORRECT, anchor=anchor, text=op.text, status=ObservationStatus.OK)
    block = Block(id=block_id, kind=BlockKind.TEXT, order=0, anchors=[anchor], observations=[observation],
                  chosen_observation=observation.id, text=op.text, decisions=[Decision(
                      stage=DecisionStage.REVIEW_ACCEPT, choice="added", actor=ACTOR,
                      reason=f"text the page shows where no block had it; {op.reason}",
                      evidence={"evidence": op.evidence, **{g.name: g.detail for g in gate}})])
    sequence = ordered(state)
    at = (next(i for i, b in enumerate(sequence) if b.id == op.after) + 1 if op.after
          else _place(state, sequence, op.page, op.bbox))
    sequence.insert(at, block)
    for order, item in enumerate(sequence):
        item.order = order
    state.blocks.append(block)
    state.ledger.append(LedgerEntry(item=ids.ledger_item_pdf(op.page, _next_item(state, op.page)), unit="agent_text",
                                    source=anchor, chars=len("".join(op.text.split())), disposition="output",
                                    block=block_id))
    return block_id


def _place(state: DocumentState, sequence: list, page: int, bbox) -> int:
    """Reading-order position of new text on *page*: after the last block of the page that starts above it."""
    on_page = [i for i, b in enumerate(sequence) if block_unit(state, b) == page]
    above = [i for i in on_page if isinstance(sequence[i].anchors[0], PdfAnchor)
             and sequence[i].anchors[0].bbox[1] <= bbox[1]]
    if above:
        return above[-1] + 1
    if on_page:
        return on_page[0]
    return sum(1 for b in sequence if (block_unit(state, b) or 0) < page)


# ── adopting a reading ──────────────────────────────────────────────────


def _adopt(ctx: ToolContext, state: DocumentState, op: Adopt) -> None:
    evidence = next((e for e in state.evidence if e.id == op.evidence), None)
    if evidence is None:
        raise _Refused("evidence", f"no evidence {op.evidence} (view_source gives it)")
    if evidence.how in ("image", "answer"):
        raise _Refused("evidence", f"{op.evidence} is an {evidence.how}: adopt takes a reading (as text, table or "
                                   "description); an edit cites an image or an answer")
    if (evidence.block, evidence.page) != (op.block, op.page):
        target = evidence.block or f"page {evidence.page}"
        raise _Refused("evidence_target", f"{op.evidence} is a reading of {target}, not of "
                                          f"{op.block or f'page {op.page}'}")
    if evidence.how == "table":
        _adopt_table(state, op, evidence)
    elif evidence.how == "description":
        block = _block(state, op.block)
        anchor = next((a for a in block.anchors if isinstance(a, AssetAnchor) and a.asset == evidence.image), None)
        if anchor is None:
            raise _Refused("evidence_target", f"{op.evidence} is not of {op.block}'s image")
        apply_description(state, Described(op.block, anchor, evidence.semantic, None, evidence.raw_ref or ""),
                          evidence.engine or "vlm")
        block.decisions.append(Decision(stage=DecisionStage.REVIEW_ACCEPT, choice="described", actor=ACTOR,
                                        reason=op.reason, evidence={"evidence": op.evidence}))
    else:
        _adopt_text(ctx, state, op, evidence)


def _adopt_table(state: DocumentState, op: Adopt, evidence) -> None:
    block = _block(state, op.block)
    if block.kind != BlockKind.TABLE or block.cells is None:
        raise _Refused("not_table", f"{op.block} is a {block.kind.value}, not a table")
    crop = next((a for a in state.assets if a.id == evidence.image), None)
    if crop is None:
        raise _Refused("evidence", f"the image of {op.evidence} is gone")
    asked = json.loads(evidence.question or "{}").get("issues", [])
    allowed = {tuple(c) for i in asked if i["kind"] == "char" for c in (i.get("cells") or [])}
    region = {tuple(c) for i in asked if i["kind"] == "structure" for c in (i.get("cells") or [])}
    n = sum(1 for o in block.observations if o.engine == "vlm") + 1
    candidate = Observation(
        id=ids.observation_id(block.id, "vlm", n), engine="vlm", engine_version=evidence.engine or "vlm",
        task=TaskKind.REVIEW, raw_ref=evidence.raw_ref, cells=evidence.cells, status=ObservationStatus.OK,
        anchor=AssetAnchor(asset=crop.id, bbox=(0, 0, crop.width, crop.height), image_size=(crop.width, crop.height),
                           transform=crop.transform))
    outcome = table_gate(block, candidate, allowed_cells=allowed, fill_region=region, actor=ACTOR)
    block.decisions[-1].evidence["evidence"] = op.evidence
    block.decisions[-1].reason += f"; {op.reason}"
    if not outcome.adopted:
        failed = next(g for g in outcome.gate if not g.passed)
        raise _Refused(failed.name, failed.detail)


def _adopt_text(ctx: ToolContext, state: DocumentState, op: Adopt, evidence) -> None:
    path = ctx.ws.root / (evidence.reading or "")
    data = path.read_bytes() if evidence.reading and path.is_file() else b""
    if hashlib.sha256(data).hexdigest() != evidence.reading_sha256:
        raise _Refused("evidence", f"the reading of {op.evidence} is missing or was changed")
    page = json.loads(data)
    if op.page is not None:
        if op.page not in scan_engine_pages(state):
            raise _Refused("native_page", f"page {op.page}'s native text layer passed its check: correct its text "
                                          "(replace_text) instead of replacing the page")
        integrate_page(ctx, state, op.page, page, evidence.raw_ref or "", evidence.engine or scan.ENGINE)
        return
    block = _block(state, op.block)
    if block.id in transcribed(state):
        raise _Refused("transcribed", f"the text in {op.block}'s image is read already")
    asset = next(a for a in state.assets if a.id == evidence.image)
    read = scan.image_blocks(scan.PageScan(page=0, raw=page, raw_ref=evidence.raw_ref or "",
                                           engine_version=evidence.engine or scan.ENGINE), asset, figure=block.id)
    integrate_image(state, block.id, read)


# ── the worklist ────────────────────────────────────────────────────────


class _Issues:
    """The open issues of the state being edited, computed when needed."""

    def __init__(self, state: DocumentState):
        self.state, self._items = state, None

    def changed(self) -> None:
        self._items = None

    def get(self, issue: str) -> Unresolved | None:
        if self._items is None:
            self._items = {u.id: u for u in unresolved_items(self.state)}
        return self._items.get(issue)


def _dismiss(state: DocumentState, op: Dismiss, issues: _Issues) -> None:
    item = issues.get(op.issue)
    if item is None:
        raise _Refused("unknown_issue", f"no open issue {op.issue} (read_draft view=issues)")
    if item.kind in _NOT_DISMISSED:
        raise _Refused("not_dismissable", f"{item.kind.value} is resolved by processing, not dismissed")
    if op.occluded and item.kind != UnresolvedKind.TEXT_NOT_SEEN:
        raise _Refused("occluded", "only text the page does not show (text_not_seen) is occluded")
    if item.target.startswith("p") and item.target[1:].isdigit():
        n = int(item.target[1:])
        page = next((p for p in state.pages if p.n == n), None)
        box = (0.0, 0.0, *page.size_pt) if page is not None and page.size_pt else (0.0, 0.0, 1e6, 1e6)
        seen = image_evidence_at(state, n, box, op.evidence)
    else:
        seen = image_evidence(state, _block(state, item.target), op.evidence)
    if not seen.passed:
        raise _Refused(seen.name, seen.detail)
    state.closed.append(ClosedItem(target=item.target, kind=item.kind.value, quotes=[q.doc_text for q in item.quotes],
                                   reason=op.reason, actor=ACTOR, image=op.evidence, occluded=op.occluded))


# ── what changed ────────────────────────────────────────────────────────


def _snapshot(state: DocumentState) -> dict[str, dict]:
    return {b.id: _fields(b) for b in state.blocks}


def _fields(block: Block) -> dict:
    return {"kind": block.kind.value, "level": block.level, "order": block.order, "status": block.status.value,
            "text": block.text, "rows": block.cells.n_rows if block.cells is not None else None,
            "cells": block.cells.model_dump_json() if block.cells is not None else None,
            "semantic": block.semantic.type if block.semantic is not None else None}


def _diff(before: dict[str, dict], state: DocumentState) -> list[Change]:
    changes = []
    for block in state.blocks:
        old, new = before.get(block.id, {}), _fields(block)
        for field, value in new.items():
            if old.get(field) == value or (not old and value is None):
                continue
            if field == "text":  # document text travels only as DocText (guide §3.3)
                changes.append(Change(target=block.id, field="text",
                                      before={"doc_text": old.get("text")} if old.get("text") else None,
                                      after={"doc_text": value} if value else None))
            elif field == "cells":
                changes.append(Change(target=block.id, field="cells", before=None, after="changed"))
            else:
                changes.append(Change(target=block.id, field=field, before=old.get(field), after=value))
    return changes
