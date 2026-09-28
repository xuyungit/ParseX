"""``edit_draft``: change the draft (Q85) — the only way it changes.

A call is a list of operations, applied in order in one transaction; each is accepted or refused on its own, with
the rule that refused it (``atomic``: any refusal and none takes effect).  Every operation says why (``reason``);
one that changes content cites the evidence it rests on (``evidence``, from ``view_source``):

- content: ``replace_text`` (a span that occurs exactly once in the block), ``insert_text`` (text a page shows where
  no block has it), ``set_cells`` (table cells), ``adopt`` (a reading of the source as it was read: a page's or a
  figure's text, a table read again, a figure's description, a region of a page whose blocks it replaces — Q87),
  ``unadopt`` (a region's blocks back);
- structure: ``set_role``, ``move``, ``join`` / ``unjoin``, ``split``, ``exclude`` / ``include``, ``mark_pending`` —
  never the text;
- the worklist: ``dismiss`` an issue the evidence shows needs no change;
- the understanding: ``note`` what the document is and which conventions hold where (Q87), revised, never removed.

The program checks each operation: content must rest on evidence of its place, a native text layer's numbers change
only as the local reading shows them, a table read again passes the acceptance gate, structure stays legal (no
skipped level, one level per numbering pattern), and a proposal of the pipeline never overrides the agent.
"""

from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from collections import Counter
from typing import Annotated, Literal

from pydantic import Field, model_validator

from parserx.content import scan
from parserx.content.select import NATIVE_ENGINES, add_gate, best_overlap, integrate_image, transcribed
from parserx.content.select import correct as correct_gate
from parserx.content.select import review_table as table_gate
from parserx.hierarchy import apply_batch
from parserx.hierarchy.changes import (
    BLOCK,
    EVIDENCE,
    REASON,
    Exclude,
    Include,
    Join,
    MarkPending,
    Move,
    SetRole,
    Split,
    Unjoin,
    agent_doc,
)
from parserx.ir import ids
from parserx.ir.anchor import AssetAnchor, PdfAnchor
from parserx.ir.base import BBox, IRModel
from parserx.ir.block import Block
from parserx.ir.decision import Decision
from parserx.ir.enums import BlockKind, BlockStatus, DecisionStage, ObservationStatus, RelationKind, TaskKind
from parserx.ir.observation import Observation
from parserx.ir.relation import Relation
from parserx.ir.state import ClosedItem, DocumentState, LedgerEntry, Note
from parserx.reading.compare import READING_ACTOR, holders_of, missed_lines, text_at, text_near
from parserx.tables.grid import Cell, TableGrid
from parserx.tools.context import ToolContext, ToolOutput, output
from parserx.tools.describe_figure import Described
from parserx.tools.describe_figure import apply as apply_description
from parserx.tools.envelope import ToolFailure, Unresolved, UnresolvedKind
from parserx.tools.evidence import image_evidence, image_evidence_at
from parserx.tools.recognize import _next_block_seq, _next_item, integrate_page, scan_engine_pages
from parserx.tools.views import unresolved_items
from parserx.workspace.queries import HIDDEN, block_unit, current_notes, ordered

ACTOR = "agent"
_TEXT_KINDS = frozenset({BlockKind.TEXT, BlockKind.TITLE, BlockKind.LIST, BlockKind.CAPTION, BlockKind.FOOTNOTE,
                         BlockKind.FORMULA, BlockKind.OTHER})
# Failures are resolved by processing, not dismissed: a pending page, a failed block, a skipped budget, a lost asset.
_NOT_DISMISSED = frozenset({UnresolvedKind.PAGE_PENDING, UnresolvedKind.BLOCK_FAILED, UnresolvedKind.BUDGET_SKIPPED,
                            UnresolvedKind.ASSET_MISSING})


DESCRIPTION = ("改初稿：初稿只能这样改。ops 是一组操作，按顺序在一个事务里执行，每条单独被接受或拒绝："
               "结果 outcomes 里每条有 accepted，被拒绝的给出规则名 rule 与原因 detail，据此修正后再提交。"
               "每条操作写明理由 reason；改内容的（replace_text、set_cells、insert_text、adopt）和 dismiss 必须引用证据 "
               "evidence——view_source 给出的编号，证据要看得到被改之处（这一块、它所在的页或区域、或跨页接缝）。"
               "结构操作从不改文字。块号形如 b-p003-0012（第 3 页第 12 块）；表格的行、列从 0 起。"
               "结果的 issues_opened / issues_closed 是这次修改新开和关掉的待办。")


class CellEdit(IRModel):
    row: int = Field(description="行（从 0 起）")
    col: int = Field(description="列（从 0 起）")
    content: str = Field(description="原件上的写法")


class ReplaceText(IRModel):
    model_config = agent_doc("改正文：把块中恰好出现一次的片段 find 换成原件上的写法 replace；找不到或不止一处会被拒绝，"
                             "给更长的片段再试。同一个错字在块内重复出现时用 all: true 一并替换。原生文字层中的数字，"
                             "只有页面的本地读数（程序自己读的，与你看原件无关）在该处显示为新值时才采用。")

    op: Literal["replace_text"]
    block: str = Field(description=BLOCK)
    find: str = Field(description="块中恰好出现一次的片段")
    replace: str = Field(description="原件上的写法")
    all: bool = Field(False, description="块内每一处 find 都换（同一个错字重复出现时）")
    reason: str = Field(description=REASON)
    evidence: str = Field(description=EVIDENCE)


class InsertText(IRModel):
    model_config = agent_doc("补入原件上有、初稿里没有的文字（待办 text_unaccounted）：程序只在证据看得到该处、"
                             "且本地读数在该处也有这段文字时才补入。结果的 block 是新块。")

    op: Literal["insert_text"]
    page: int = Field(description="页")
    bbox: BBox = Field(description="文字在页面上的位置 [x0, y0, x1, y1]（页面点；用待办项给出的位置）")
    text: str = Field(description="原件上的文字")
    after: str | None = Field(None, description="排在哪一块之后；不给则按位置排进阅读顺序")
    reason: str = Field(description=REASON)
    evidence: str = Field(description=EVIDENCE)


class SetCells(IRModel):
    model_config = agent_doc("改表格的单元格；网格内的空位也可以补填。")

    op: Literal["set_cells"]
    block: str = Field(description=BLOCK)
    cells: list[CellEdit] = Field(min_length=1, description="[{row, col, content}, …]")  # empty places too (Q45)
    reason: str = Field(description=REASON)
    evidence: str = Field(description=EVIDENCE)


class Adopt(IRModel):
    model_config = agent_doc("采用 view_source 读出的内容，采用的正是当时读到的：重读的表格（as=table）、图片描述（description）、"
                             "图片里的文字或一页的识别结果（text）。给出被读的 block 或 page 之一。"
                             "页面区域的读数（as=text 加 bbox）替换区域里的块，用于初稿把块分错的地方（表格被拆成文字、"
                             "标题和正文连成一块等）：程序检查内容守恒（旧块的文字在读数里找得到、原生文字层的数字不变），"
                             "区域里的图片不动，旧块保留，可用 unadopt 撤回。")

    op: Literal["adopt"]
    block: str | None = Field(None, description="被读的块：表格、图片")
    page: int | None = Field(None, description="被读的页：尚未识别或识别失败的页；或区域读数所在的页")
    evidence: str = Field(description="读数的证据编号")
    reason: str = Field(description=REASON)

    @model_validator(mode="after")
    def _one_target(self) -> "Adopt":
        if (self.block is None) == (self.page is None):
            raise ValueError("adopt: give the block or the page the reading is of")
        return self


class Unadopt(IRModel):
    model_config = agent_doc("撤回一次区域采用：被替换的块恢复，读数的块不再输出。")

    op: Literal["unadopt"]
    evidence: str = Field(description="当时采用的读数的证据编号")
    reason: str = Field(description=REASON)


class Dismiss(IRModel):
    model_config = agent_doc("关闭一项待办：看过证据、确认不需要改。只有提示类待办能关闭，待识别页、失败块等要处理；"
                             "关闭后该处内容若变化，待办会重新出现。")

    op: Literal["dismiss"]
    issue: str = Field(description="待办编号（w-…，read_draft 的 issues 视图）")
    reason: str = Field(description=REASON)
    evidence: str = Field(description="看过的证据")
    occluded: bool = Field(False, description="text_not_seen：文字确实在，只是被别的元素盖住（保留原文）")  # Q71


class WriteNote(IRModel):
    model_config = agent_doc("记下对文档的理解：由哪几部分组成、某一部分的标题惯例、某处为什么这样判断，写明适用范围与证据。"
                             "记录不改初稿，只能修订（replaces 指向旧记录）、不能删除；上下文被清理后仍在（read_draft 的 "
                             "notes 视图）。没有 reason：结论本身就是。结果的 target 是记录的编号。")

    op: Literal["note"]
    text: str = Field(description="结论")
    scope: str = Field(description="适用范围，如“全文”“第 20–35 页”“附件一”")
    evidence: list[str] = Field([], description="证据编号（可省）")
    replaces: str | None = Field(None, description="修订哪一条记录（n-…）")


EditOp = Annotated[
    ReplaceText | SetCells | InsertText | Adopt | Unadopt | SetRole | Move | Join | Unjoin | Split | Exclude
    | Include | MarkPending | Dismiss | WriteNote,
    Field(discriminator="op"),
]


class EditDraftRequest(IRModel):
    ops: list[EditOp] = Field(min_length=1, description="操作列表，按顺序执行")
    atomic: bool = Field(False, description="任一条被拒绝则全部不生效")


class OpOutcome(IRModel):
    index: int
    op: str
    accepted: bool
    rule: str | None = None  # what refused it
    detail: str | None = None
    block: str | None = None  # a block the operation made (insert_text, split)
    target: str | None = None  # dismiss: what the issue was about (a block, a page "p3"); note: the note's id


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
    try:
        with ctx.ws.txn("tool:edit_draft:agent") as state:
            issues = _Issues(state)
            batch: list[tuple[int, object]] = []  # consecutive structure changes: judged by the outline they produce
            for index, op in enumerate(req.ops):
                if isinstance(op, _STRUCTURE):
                    batch.append((index, op))
                    continue
                outcomes += _structure(state, batch, issues)
                batch = []
                outcomes.append(_apply(ctx, state, index, op, issues))
            outcomes += _structure(state, batch, issues)
            accepted = [o.accepted for o in outcomes]
            if not any(accepted) or (req.atomic and not all(accepted)):
                raise _Rollback
    except _Rollback:
        if req.atomic:
            outcomes = [o.model_copy(update={"accepted": False, "rule": "atomic",
                                             "detail": "another operation of this call was refused"})
                        if o.accepted else o for o in outcomes]
        return output(EditDraftResult(outcomes=outcomes, issues_opened=[], issues_closed=[]))
    open_after = {u.id: u for u in unresolved_items(ctx.ws.load())}
    return output(EditDraftResult(outcomes=outcomes,
                                  issues_opened=[u for k, u in open_after.items() if k not in open_before],
                                  issues_closed=[k for k in open_before if k not in open_after]))


def _apply(ctx: ToolContext, state: DocumentState, index: int, op, issues: "_Issues") -> OpOutcome:
    made, detail, target = None, None, None
    try:
        if isinstance(op, ReplaceText):
            detail = _replace_text(state, op)
        elif isinstance(op, SetCells):
            detail = _set_cells(state, op)
        elif isinstance(op, InsertText):
            made, detail = _insert_text(state, op)
        elif isinstance(op, Adopt):
            detail = _adopt(ctx, state, op)
        elif isinstance(op, Unadopt):
            detail = _unadopt(state, op)
        elif isinstance(op, Dismiss):
            target = _dismiss(state, op, issues)
        elif isinstance(op, WriteNote):
            target = _note(state, op)
    except _Refused as exc:
        return OpOutcome(index=index, op=op.op, accepted=False, rule=exc.rule, detail=exc.detail)
    except ToolFailure as exc:
        return OpOutcome(index=index, op=op.op, accepted=False, rule=exc.failure.code.value,
                         detail=exc.failure.message)
    issues.changed()
    return OpOutcome(index=index, op=op.op, accepted=True, block=made, detail=detail, target=target)


_STRUCTURE = (SetRole, Move, Join, Unjoin, Split, Exclude, Include, MarkPending)


def _structure(state: DocumentState, batch: list[tuple[int, object]], issues: "_Issues") -> list[OpOutcome]:
    """A run of structure changes, applied together (``apply_batch``): the outline is judged as it comes out, so a
    whole numbering pattern can move a level at once."""
    if not batch:
        return []
    known = {b.id for b in state.blocks}
    outcome = apply_batch(state, [op for _, op in batch], actor=ACTOR)
    made = [b.id for b in state.blocks if b.id not in known]
    refused = {r.index: r for r in outcome.rejected}
    out = []
    for j, (index, op) in enumerate(batch):
        if j in refused:
            out.append(OpOutcome(index=index, op=op.op, accepted=False, rule=refused[j].rule.value,
                                 detail=refused[j].detail))
        else:
            block = next((b for b in made if isinstance(op, Split) and b.startswith(op.block + "-")), None)
            out.append(OpOutcome(index=index, op=op.op, accepted=True, block=block))
    if outcome.accepted:
        issues.changed()
    return out


# ── content ─────────────────────────────────────────────────────────────


def _block(state: DocumentState, block_id: str) -> Block:
    block = next((b for b in state.blocks if b.id == block_id), None)
    if block is None:
        raise _Refused("unknown_block", f"no block {block_id}")
    if block.status == BlockStatus.MERGED:  # its content lives on in another block: a change here would not show
        into = next((r.src for r in state.relations if r.kind == RelationKind.CONTINUES and r.dst == block.id), None)
        raise _Refused("merged", f"{block_id} is merged into {into or 'another block'}: change that block")
    return block


def _replace_text(state: DocumentState, op: ReplaceText) -> str:
    block = _block(state, op.block)
    if block.kind not in _TEXT_KINDS:
        raise _Refused("not_text", f"{op.block} is a {block.kind.value}: " + (
            "use set_cells" if block.kind == BlockKind.TABLE else "only text blocks have text to replace"))
    count = block.text.count(op.find) if op.find else 0
    if count == 0 or (count > 1 and not op.all):
        raise _Refused("find", f"'find' must occur exactly once in {op.block}'s text (found {count} times): "
                               f"{op.find[:40]!r}; give a longer span" + (", or all: true for every one"
                                                                           if count > 1 else ""))
    return _correct(state, block, op, text=block.text.replace(op.find, op.replace), grid=None)


def _set_cells(state: DocumentState, op: SetCells) -> str:
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
    return _correct(state, block, op, text=None, grid=edited)


def _correct(state: DocumentState, block: Block, op, *, text: str | None, grid: TableGrid | None) -> str:
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
    return _gated(outcome.gate)


def _gated(gate) -> str:
    """The checks' details; refused with the first failed check's name when any failed."""
    failed = [g for g in gate if not g.passed]
    if failed:
        raise _Refused(failed[0].name, "; ".join(f"{g.name}: {g.detail}" for g in failed))
    return "; ".join(f"{g.name}: {g.detail}" for g in gate)


def _insert_text(state: DocumentState, op: InsertText) -> tuple[str, str]:
    if state.format != "pdf" or op.page not in {p.n for p in state.pages}:
        raise _Refused("page", f"no PDF page {op.page}")
    if op.after is not None and op.after not in {b.id for b in state.blocks}:
        raise _Refused("unknown_block", f"no block {op.after}")
    gate = add_gate(op.text, image=image_evidence_at(state, op.page, op.bbox, op.evidence),
                    seen=text_at(state, op.page, op.bbox), holders=holders_of(state, op.page, op.bbox, op.text))
    detail = _gated(gate)
    decision = Decision(stage=DecisionStage.REVIEW_ACCEPT, choice="added", actor=ACTOR,
                        reason=f"text the page shows where no block had it; {op.reason}",
                        evidence={"evidence": op.evidence, **{g.name: g.detail for g in gate}})
    block_id = _add_text_block(state, op.page, op.bbox, op.text, engine="agent", engine_version=ACTOR,
                               task=TaskKind.CORRECT, decision=decision, unit="agent_text", after=op.after)
    return block_id, detail


def add_missed_text(state: DocumentState) -> list[str]:
    """The lines the local page reading sees where the output has nothing (``reading.compare.missed_lines``, Q133),
    added as text blocks at their places: the fixed pipeline does not lose them; the worklist lists each for review
    (``text_added``).  The new block ids."""
    engines = {r.n: r.engine for r in state.readings}
    made = []
    for n, lines in missed_lines(state).items():
        for line in sorted(lines, key=lambda ln: (ln.bbox[1], ln.bbox[0])):
            decision = Decision(stage=DecisionStage.CONTENT_SOURCE, choice="added", actor=READING_ACTOR,
                                reason="text the local page reading sees where the output had nothing (Q133)",
                                evidence={"reading": engines[n], "score": round(line.score, 3)})
            made.append(_add_text_block(state, n, line.bbox, line.text, engine="page_reading",
                                        engine_version=engines[n], task=TaskKind.RECOGNIZE, decision=decision,
                                        unit="read_text"))
    return made


def _add_text_block(state: DocumentState, page: int, bbox, text: str, *, engine: str, engine_version: str,
                    task: TaskKind, decision: Decision, unit, after: str | None = None) -> str:
    """A text block at *bbox* on *page*, in reading order (after *after*, else by position), with its ledger item."""
    block_id = ids.block_id_pdf(page, _next_block_seq(state, page))
    anchor = PdfAnchor(page=page, bbox=bbox, coord_space="page_pt")
    observation = Observation(id=ids.observation_id(block_id, engine, 1), engine=engine,
                              engine_version=engine_version, task=task, anchor=anchor, text=text,
                              status=ObservationStatus.OK)
    block = Block(id=block_id, kind=BlockKind.TEXT, order=0, anchors=[anchor], observations=[observation],
                  chosen_observation=observation.id, text=text, decisions=[decision])
    sequence = ordered(state)
    at = (next(i for i, b in enumerate(sequence) if b.id == after) + 1 if after
          else _place(state, sequence, page, bbox))
    sequence.insert(at, block)
    for order, item in enumerate(sequence):
        item.order = order
    state.blocks.append(block)
    state.ledger.append(LedgerEntry(item=ids.ledger_item_pdf(page, _next_item(state, page)), unit=unit,
                                    source=anchor, chars=len("".join(text.split())), disposition="output",
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


def _adopt(ctx: ToolContext, state: DocumentState, op: Adopt) -> str | None:
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
        return _adopt_table(state, op, evidence)
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
        return _adopt_text(ctx, state, op, evidence)


def _adopt_table(state: DocumentState, op: Adopt, evidence) -> str:
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
    return _gated(outcome.gate)


def _adopt_text(ctx: ToolContext, state: DocumentState, op: Adopt, evidence) -> str | None:
    path = ctx.ws.root / (evidence.reading or "")
    data = path.read_bytes() if evidence.reading and path.is_file() else b""
    if hashlib.sha256(data).hexdigest() != evidence.reading_sha256:
        raise _Refused("evidence", f"the reading of {op.evidence} is missing or was changed")
    page = json.loads(data)
    if op.page is not None and evidence.bbox is not None:
        return _adopt_region(state, op, evidence, page)
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


# ── a region read again (Q87) ───────────────────────────────────────────

RECALL = 0.9  # of the replaced blocks' characters, found again in the reading
_NUMBER = re.compile(r"\d+(?:[.,]\d+)*")


def _adopt_region(state: DocumentState, op: Adopt, evidence, page_json: dict) -> str:
    """Replace the blocks in a region of a page by the scan engine's reading of that region: the reading's
    structure (paragraphs, titles, tables) takes the place of the draft's.  The content must be conserved — the
    replaced blocks' characters found again, a native text layer's numbers unchanged — and the replaced blocks stay,
    as duplicates of the new ones, so ``unadopt`` can bring them back.  Figures in the region are left as they are."""
    crop = next((a for a in state.assets if a.id == evidence.image), None)
    if crop is None or not isinstance(crop.source, PdfAnchor):
        raise _Refused("evidence", f"the image of {op.evidence} is gone")
    region = crop.source.bbox
    n = op.page
    old = [b for b in ordered(state) if b.status not in HIDDEN and block_unit(state, b) == n
           and b.kind not in (BlockKind.FIGURE, BlockKind.SCAN) and _inside(b, n, region)]
    size = (region[2] - region[0], region[3] - region[1])
    read = scan.page_blocks(scan.PageScan(page=n, raw=page_json, raw_ref=evidence.raw_ref or "",
                                          engine_version=evidence.engine or scan.ENGINE),
                            page_size=size, origin=(region[0], region[1]), first_seq=_next_block_seq(state, n),
                            first_item=_next_item(state, n))
    new = [b for b in read.blocks if b.kind not in (BlockKind.FIGURE, BlockKind.SCAN)]
    kept = {b.id for b in new}
    shown = [b for b in new if b.status not in HIDDEN]
    before, after = "".join(_content(b) for b in old), "".join(_content(b) for b in shown)
    native = [b for b in old if (c := _chosen_of(b)) is not None and c.engine in NATIVE_ENGINES]
    lost = _missing_numbers("".join(_content(b) for b in native), after)
    if lost:
        raise _Refused("native_numbers", f"the reading does not keep the native text layer's numbers {lost[:8]}: "
                                         "the reading misread them; keep the draft here")
    recall = _recall(before, after)
    if old and recall < RECALL:
        raise _Refused("content_lost", f"the reading has {recall:.0%} of the {len(before)} characters of "
                                       f"{', '.join(b.id for b in old)} (needs {RECALL:.0%}): read a region that "
                                       "covers them, or keep the draft")
    for block in old:
        block.status = BlockStatus.DUPLICATE
        target = best_overlap(block, shown)
        if target is not None:
            state.relations.append(Relation(id=ids.relation_id(RelationKind.DUPLICATE_OF, block.id, target.id),
                                            kind=RelationKind.DUPLICATE_OF, src=block.id, dst=target.id))
        block.decisions.append(Decision(stage=DecisionStage.CONTENT_SOURCE, choice="superseded_by_reading",
                                        actor=ACTOR, reason=op.reason, evidence={"evidence": op.evidence},
                                        refs=[b.id for b in shown[:20]]))
    replaced = {b.id for b in old}
    for entry in state.ledger:
        if entry.block in replaced and entry.disposition in ("output", "merged"):
            entry.disposition = "duplicate"
    for block in new:
        block.decisions.append(Decision(stage=DecisionStage.CONTENT_SOURCE, choice="region_reading", actor=ACTOR,
                                        reason=op.reason, evidence={"evidence": op.evidence},
                                        refs=[b.id for b in old[:20]]))
    sequence = ordered(state)
    at = sequence.index(old[0]) if old else _place(state, sequence, n, region)
    sequence[at:at] = new
    for order, item in enumerate(sequence):
        item.order = order
    state.blocks.extend(new)
    state.ledger.extend(e for e in read.ledger if e.block in kept)
    return (f"{len(old)} blocks replaced by {len(shown)} ({', '.join(b.kind.value for b in shown)}); "
            f"characters kept {recall:.0%}")


def _unadopt(state: DocumentState, op: Unadopt) -> str:
    """Undo a region's adoption: the replaced blocks come back, the reading's blocks become their duplicates."""
    made = [b for b in state.blocks if b.status not in HIDDEN and any(
        d.choice == "region_reading" and d.evidence.get("evidence") == op.evidence for d in b.decisions)]
    replaced = [b for b in state.blocks if b.status == BlockStatus.DUPLICATE and any(
        d.choice == "superseded_by_reading" and d.evidence.get("evidence") == op.evidence for d in b.decisions)]
    if not made and not replaced:
        raise _Refused("not_adopted", f"no region adopted from {op.evidence} is in the draft")
    back = {b.id for b in replaced}
    state.relations[:] = [r for r in state.relations
                          if not (r.kind == RelationKind.DUPLICATE_OF and r.src in back)]
    for block in replaced:
        block.status = BlockStatus.OK
        block.decisions.append(Decision(stage=DecisionStage.CONTENT_SOURCE, choice="restored", actor=ACTOR,
                                        reason=op.reason, evidence={"evidence": op.evidence}))
    gone = {b.id for b in made}
    for block in made:
        block.status = BlockStatus.DUPLICATE
        target = best_overlap(block, replaced)
        if target is not None:
            state.relations.append(Relation(id=ids.relation_id(RelationKind.DUPLICATE_OF, block.id, target.id),
                                            kind=RelationKind.DUPLICATE_OF, src=block.id, dst=target.id))
        block.decisions.append(Decision(stage=DecisionStage.CONTENT_SOURCE, choice="unadopted", actor=ACTOR,
                                        reason=op.reason, evidence={"evidence": op.evidence}))
    for entry in state.ledger:
        if entry.block in back and entry.disposition == "duplicate":
            entry.disposition = "output"
        elif entry.block in gone and entry.disposition == "output":
            entry.disposition = "duplicate"
    return f"{len(replaced)} blocks back, {len(made)} from the reading set aside"


def _inside(block: Block, n: int, region: BBox) -> bool:
    """The block's box on page *n* has its centre in *region*."""
    for anchor in block.anchors:
        if isinstance(anchor, PdfAnchor) and anchor.page == n and anchor.coord_space == "page_pt":
            x, y = (anchor.bbox[0] + anchor.bbox[2]) / 2, (anchor.bbox[1] + anchor.bbox[3]) / 2
            return region[0] <= x <= region[2] and region[1] <= y <= region[3]
    return False


def _content(block: Block) -> str:
    text = block.text or ""
    if block.cells is not None:
        text += "".join(c.content for c in block.cells.cells)
    return "".join(text.split())


def _chosen_of(block: Block):
    return next((o for o in block.observations if o.id == block.chosen_observation), None)


def _recall(before: str, after: str) -> float:
    if not before:
        return 1.0
    have = Counter(unicodedata.normalize("NFKC", after))
    need = Counter(unicodedata.normalize("NFKC", before))
    return sum(min(c, have[ch]) for ch, c in need.items()) / sum(need.values())


def _missing_numbers(before: str, after: str) -> list[str]:
    have = Counter(_NUMBER.findall(unicodedata.normalize("NFKC", after)))
    missing = Counter(_NUMBER.findall(unicodedata.normalize("NFKC", before))) - have
    return sorted(missing.elements())


# ── the understanding ───────────────────────────────────────────────────


def _note(state: DocumentState, op: WriteNote) -> str:
    if not op.text.strip() or not op.scope.strip():
        raise _Refused("note", "a note says what holds (text) and where (scope)")
    if op.replaces is not None and op.replaces not in {n.id for n in current_notes(state)}:
        raise _Refused("unknown_note", f"no current note {op.replaces} (read_draft view=notes)")
    known = {e.id for e in state.evidence}
    unknown = [e for e in op.evidence if e not in known]
    if unknown:
        raise _Refused("evidence", f"no evidence {unknown} (view_source gives it)")
    note = Note(id=f"n-{len(state.notes) + 1:03d}", text=op.text.strip(), scope=op.scope.strip(),
                evidence=op.evidence, replaces=op.replaces, actor=ACTOR)
    state.notes.append(note)
    return note.id


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


def _dismiss(state: DocumentState, op: Dismiss, issues: _Issues) -> str:
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
    return item.target
