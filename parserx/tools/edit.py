"""``edit_draft``: change the draft (Q85) — the only way it changes.

A call is a list of operations, applied in order in one transaction; each is accepted or refused on its own, with
the rule that refused it (``atomic``: any refusal and none takes effect).  Every operation says why (``reason``);
one that changes content cites the evidence it rests on (``evidence``, from ``view_source``):

- content: ``replace_text`` (a span that occurs exactly once in the block), ``insert_text`` (text a page shows where
  no block has it), ``set_cells`` (table cells), ``set_table`` (a table's rows, columns and merged cells, its
  characters kept), ``adopt`` (a reading of the source as it was read: a page's or a
  figure's text, a table read again, a figure's description, a region of a page whose blocks it replaces — Q87),
  ``unadopt`` (a region's blocks back);
- structure: ``set_role``, ``move``, ``join`` / ``unjoin``, ``split``, ``exclude`` / ``include``, ``mark_pending`` —
  never the text;
- the worklist: ``dismiss`` an issue the evidence shows needs no change;
- the understanding: ``note`` what the document is and which conventions hold where (Q87), revised, never removed.

The program checks each operation: content must rest on evidence of its place, a table read again passes the
acceptance gate, structure stays legal (no skipped level, one level per numbering pattern), and a proposal of the
pipeline never overrides the agent.  Where the agent goes against a program's comparison — a native text layer's
number changed, a region reading that drops characters or numbers, text the local reading does not show — the
change stands on the image's evidence and the comparison is a signal, recorded and listed in the summary
(execution plan §3.4).  A correction that writes other characters in place of some — letters, digits, or one moved
into or out of a script — is no signal but a question for the page: before the operations apply, the configured
readers read each such block again on its image alone, all at once, and the correction stands only where every
reading shows it (``tools/second_reading.recheck``, ``content/select.as_printed``).
"""

from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from collections import Counter
from typing import Annotated, Literal

from pydantic import Field, TypeAdapter, ValidationError, model_validator
from pydantic.json_schema import SkipJsonSchema

from parserx.content import scan
from parserx.content.latex import characters, problems
from parserx.content.select import NATIVE_ENGINES, add_gate, best_overlap, integrate_image, renumber, signals
from parserx.content.select import numbers_seen, transcribed
from parserx.content.select import PRINTED_AS_DRAFT, _letters_changed, _unmapped, substitutes
from parserx.content.select import correct as correct_gate, structure_only
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
from parserx.ir import ids, rotation
from parserx.ir.anchor import AssetAnchor, PdfAnchor
from parserx.ir.base import BBox, IRModel
from parserx.ir.block import Block
from parserx.ir.decision import Decision
from parserx.ir.enums import BlockKind, BlockStatus, DecisionStage, ObservationStatus, RelationKind, TaskKind
from parserx.layout import labels
from parserx.ir.observation import Observation
from parserx.ir.relation import Relation
from parserx.ir.state import ClosedItem, DocumentState, Doubt, LedgerEntry, Note
from parserx.reading.compare import READING_ACTOR, holders_of, missed_lines, normalize, text_at, text_near
from parserx.tables.grid import Cell, TableGrid
from parserx.tools.context import ToolContext, ToolOutput, output
from parserx.tools import second_reading
from parserx.tools.describe_figure import Described
from parserx.tools.describe_figure import apply as apply_description
from parserx.tools.envelope import ToolFailure, Unresolved, UnresolvedKind
from parserx.tools.evidence import image_evidence, image_evidence_at, image_evidence_whole
from parserx.tools.formulas import _adopt as adopt_passage, _edited_block, _lost as formula_lost
from parserx.tools.formulas import _union as formula_union, disagreement, listed, passage_of, pending_candidates
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
               "每条操作写明理由 reason；改内容的（replace_text、set_cells、insert_text、adopt、transcribe_passage）、"
               "改表格结构的 set_table 和 dismiss 必须引用证据 "
               "evidence——view_source 给出的编号，证据要看得到被改之处（这一块、它所在的页或区域、或跨页接缝）。"
               "存疑（doubt）和记录（note）不改初稿。结构操作从不改文字。块号形如 b-p003-0012（第 3 页第 12 块）；表格的行、列从 0 起。"
               "结果的 issues_opened / issues_closed 是这次修改新开和关掉的待办。")


class CellEdit(IRModel):
    row: int = Field(description="行（从 0 起）")
    col: int = Field(description="列（从 0 起）")
    content: str = Field(description="原件上的写法")


class ReplaceText(IRModel):
    model_config = agent_doc("改正文：把块中恰好出现一次的片段 find 换成原件上的写法 replace；找不到或不止一处会被拒绝，"
                             "给更长的片段再试。同一个错字在块内重复出现时用 all: true 一并替换。原生文字层中的数字"
                             "看图确认原件不同才改：程序记下原值、新值、证据与理由，并说明页面的本地读数（程序自己读的）"
                             "是否显示新值，摘要里单列。")

    op: Literal["replace_text"]
    block: str = Field(description=BLOCK)
    find: str = Field(description="块中恰好出现一次的片段")
    replace: str = Field(description="原件上的写法")
    all: bool = Field(False, description="块内每一处 find 都换（同一个错字重复出现时）")
    reason: str = Field(description=REASON)
    evidence: str = Field(description=EVIDENCE)


class InsertText(IRModel):
    model_config = agent_doc("补入原件上有、初稿里没有的文字（待办 text_unaccounted）：证据要看得到该处，已有块含这段文字时"
                             "拒绝；本地读数在该处没有这段文字时照样补入，记为信号、摘要里单列。结果的 block 是新块。")

    op: Literal["insert_text"]
    page: int | None = Field(None, description="PDF 的页（与 figure 二选一）")
    figure: str | None = Field(None, description="图片块：补进这张图里读出的文字（图里漏读的一行；Word 文档只能这样补，"
                                                 "它没有页）")
    bbox: BBox | None = Field(None, description="文字的位置 [x0, y0, x1, y1]：page 时为页面点（用待办项给出的位置，"
                                                "必填）；figure 时为图片像素（可省）")
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


class SetTable(IRModel):
    model_config = agent_doc("改表格的结构：行、列、合并单元格，例如把被分页切成两半的一行合回一行、删去读数多出的空行、"
                             "把读成一格的两格分开。给出整张表，写法与 read_draft 的 blocks 视图里的表格相同（rows 按行、spans 列合并格）。"
                             "只改结构不改字：表里的字一个不多、一个不少，几格的文字可以接成一格、一格的文字可以分到几格，"
                             "每格原有的文字仍连在一起。改字、删字用 set_cells。输出总把第一行当表头，只为标出表头不必用它。")

    op: Literal["set_table"]
    block: str = Field(description=BLOCK)
    rows: list[list[str | None]] = Field(min_length=1, description="每行一个数组，每格一个字符串（空格写 \"\"）；"
                                                                    "合并的格写在它的第一行第一列，被它盖住的位置写 null")
    spans: list[list[int]] = Field([], description="合并的格：[行, 列, 跨几行, 跨几列]（从 0 起）")
    header_rows: int = Field(0, ge=0, description="表头有几行（不止一行时才要写）")
    reason: str = Field(description=REASON)
    evidence: str = Field(description=EVIDENCE)


class Adopt(IRModel):
    model_config = agent_doc("采用 view_source 读出的内容，采用的正是当时读到的：重读的表格（as=table）、图片描述（description）、"
                             "图片里的文字或一页的识别结果（text）。给出被读的 block 或 page 之一。"
                             "页面区域的读数（as=text 加 bbox）替换区域里的块，用于初稿把块分错的地方（表格被拆成文字、"
                             "标题和正文连成一块等）：程序比对内容（旧块的文字在读数里找不全、原生文字层的数字变了，"
                             "照样采用，记为信号、摘要里单列，所以只在看图确认读数对时采用），区域里的图片不动，"
                             "旧块保留，可用 unadopt 撤回。")

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


class TranscribePassage(IRModel):
    model_config = agent_doc("整段写出公式待定的一段（待办 formula_candidate）：看过整段原件（看这一块即看到整段）后，"
                             "把这一段的正文、公式和编号照图完整写出，公式用 LaTeX（行内 $…$，独立成行 $$…$$）。"
                             "用于公式结构要改、或文字层把一段切成了几块的地方；个别字符用 replace_text。"
                             "程序核对：文字层的每个字母和数字都要写出（一个不少），多写的字母或数字要有别的读数在这里看得到，"
                             "LaTeX 要能排版。照原件写，原件的错字也照写。旧块保留，可用 unadopt 撤回。")

    op: Literal["transcribe_passage"]
    block: str = Field(description="待办 formula_candidate 的块")
    text: str = Field(description="整段的写法：正文照写，公式用 LaTeX")
    reason: str = Field(description=REASON)
    evidence: str = Field(description="看过整段原件的证据编号（看这一块、它所在的整页，或包住整段的区域）")


class Unadopt(IRModel):
    model_config = agent_doc("撤回一次区域采用或整段写出：被替换的块恢复，读数或写出的块不再输出。")

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


class RecordDoubt(IRModel):
    model_config = agent_doc("存疑：原件本身看来印错了（错字、漏字、编号或数值前后矛盾），正文照印的写、不改，"
                             "在这里记下印的是什么、疑为什么、为什么这样认为，交付的报告单列，供校对原件。不改初稿。"
                             "结果的 target 是记录的编号。")

    op: Literal["doubt"]
    block: str = Field(description=BLOCK)
    printed: str = Field(description="原件印的写法：这一块里的片段（表格为某格里的片段）")
    suggested: str = Field("", description="疑为什么（可省）")
    reason: str = Field(description=REASON)
    evidence: str = Field(description="看过原件这一处的证据编号")


class WriteNote(IRModel):
    model_config = agent_doc("记下对文档的理解：由哪几部分组成、某一部分的标题惯例、某处为什么这样判断，写明适用范围与证据。"
                             "记录不改初稿，只能修订（replaces 指向旧记录）、不能删除；上下文被清理后仍在（read_draft 的 "
                             "notes 视图）。没有 reason：结论本身就是。结果的 target 是记录的编号。")

    op: Literal["note"]
    text: str = Field(description="结论")
    scope: str = Field(description="适用范围，如“全文”“第 20–35 页”“附件一”")
    evidence: str = Field("", description="证据编号（可省；几个用逗号隔开）")
    replaces: str | None = Field(None, description="修订哪一条记录（n-…）")


_OPS = (ReplaceText | SetCells | SetTable | InsertText | Adopt | TranscribePassage | Unadopt | SetRole | Move | Join
        | Unjoin | Split | Exclude | Include | MarkPending | Dismiss | RecordDoubt | WriteNote)
EditOp = Annotated[_OPS, Field(discriminator="op")]
_ONE_OP = TypeAdapter(EditOp)


class InvalidOp(IRModel):
    """An operation that does not fit its form: refused alone, with its problem, while the others of the call go on
    (speed plan Q164: one malformed operation used to void the whole call).  Not part of the request's schema."""

    op: Literal["invalid"] = "invalid"
    given: str  # the operation's name as given
    problem: str


class EditDraftRequest(IRModel):
    ops: list[Annotated[_OPS | SkipJsonSchema[InvalidOp], Field(discriminator="op")]] = Field(
        min_length=1, description="操作列表，按顺序执行")
    atomic: bool = Field(False, description="任一条被拒绝则全部不生效")

    @model_validator(mode="before")
    @classmethod
    def _each_op(cls, data):
        """Each operation checked on its own: one out of form becomes an ``InvalidOp`` at its place."""
        if not isinstance(data, dict) or not isinstance(data.get("ops"), list):
            return data
        ops = []
        for raw in data["ops"]:
            if isinstance(raw, dict) and raw.get("op") == "invalid":
                raw = {k: v for k, v in raw.items() if k != "op"}  # an agent cannot ask for the placeholder
            try:
                _ONE_OP.validate_python(raw)
                ops.append(raw)
            except ValidationError as exc:
                given = str(raw.get("op")) if isinstance(raw, dict) else type(raw).__name__
                ops.append({"op": "invalid", "given": given, "problem": "; ".join(
                    f"{'.'.join(map(str, e['loc'][1:] or e['loc']))}: {e['msg']}" for e in exc.errors()[:4])})
        return {**data, "ops": ops}


class OpOutcome(IRModel):
    index: int
    op: str
    accepted: bool
    rule: str | None = None  # what refused it
    detail: str | None = None
    block: str | None = None  # a block the operation made (insert_text, split)
    target: str | None = None  # dismiss: what the issue was about (a block, a page "p3"); note: the note's id


class TitleCounts(IRModel):
    """The document's titles by level (H1 … H6, "no level"), before and after the call (Q165)."""

    before: dict[str, int]
    after: dict[str, int]


class EditDraftResult(IRModel):
    outcomes: list[OpOutcome]  # one per operation, in order
    issues_opened: list[Unresolved]  # what the accepted changes opened
    issues_closed: list[str]  # ids of issues they resolved or dismissed
    titles: TitleCounts | None = None  # when the call changed the outline: its titles before and after, by level


class _Refused(Exception):
    def __init__(self, rule: str, detail: str):
        super().__init__(detail)
        self.rule, self.detail = rule, detail


class _Rollback(Exception):
    pass


def run(ctx: ToolContext, req: EditDraftRequest) -> ToolOutput[EditDraftResult]:
    open_before = {u.id: u for u in unresolved_items(ctx.ws.load())}
    titles_before = _titles(ctx.ws.load())
    outcomes: list[OpOutcome] = []
    failures = second_reading.recheck(ctx, _substituting(ctx.ws.load(), req.ops))
    refused: list[Doubt] = []  # doubts from refused corrections: kept even where the call takes no effect
    try:
        with ctx.ws.txn("tool:edit_draft:agent") as state:
            known = len(state.doubts)
            issues = _Issues(state, open_before)
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
                refused = [d for d in state.doubts[known:] if d.refused]
                raise _Rollback
    except _Rollback:
        if refused:
            with ctx.ws.txn("tool:edit_draft:doubts") as state:
                for d in refused:
                    _record_doubt(state, d.block, d.printed, d.suggested, reason=d.reason, evidence=d.evidence,
                                  refused=True, readings=d.readings)
        if req.atomic:
            outcomes = [o.model_copy(update={"accepted": False, "rule": "atomic",
                                             "detail": "another operation of this call was refused"})
                        if o.accepted else o for o in outcomes]
        return output(EditDraftResult(outcomes=outcomes, issues_opened=[], issues_closed=[]), failures=failures)
    after = ctx.ws.load()
    open_after = {u.id: u for u in unresolved_items(after)}
    titles_after = _titles(after)
    return output(EditDraftResult(outcomes=outcomes,
                                  issues_opened=[u for k, u in open_after.items() if k not in open_before],
                                  issues_closed=[k for k in open_before if k not in open_after],
                                  titles=TitleCounts(before=titles_before, after=titles_after)
                                  if titles_after != titles_before else None), failures=failures)


def _titles(state: DocumentState) -> dict[str, int]:
    """The outline's titles by level, as the outline view shows them (Q165: a batch of role changes that turns twelve
    section titles into captions is seen in its result)."""
    from parserx.tools.draft import outline_blocks

    counts = Counter(f"H{b.level}" if b.level else "no level" for b in outline_blocks(state) if b.kind == BlockKind.TITLE)
    return dict(sorted(counts.items()))


def _substituting(state: DocumentState, ops) -> list[str]:
    """The blocks a ``replace_text`` or ``set_cells`` of *ops* writes other characters in place of some in
    (``substitutes``), as the block stands before the call."""
    blocks = {b.id: b for b in state.blocks}
    out: list[str] = []
    for op in ops:
        block = blocks.get(getattr(op, "block", None) or "")
        if block is None or block.id in out:
            continue
        if isinstance(op, ReplaceText) and op.find and op.find in (block.text or ""):
            changed = substitutes(block.text, block.text.replace(op.find, op.replace),
                                  math=block.kind == BlockKind.FORMULA)
        elif isinstance(op, SetCells) and block.cells is not None:
            slots = [block.cells.slot(e.row, e.col) for e in op.cells]
            changed = any(substitutes(s.content if s else "", e.content) for s, e in zip(slots, op.cells))
        else:
            continue
        if changed:
            out.append(block.id)
    return out


def _apply(ctx: ToolContext, state: DocumentState, index: int, op, issues: "_Issues") -> OpOutcome:
    made, detail, target = None, None, None
    if isinstance(op, InvalidOp):
        return OpOutcome(index=index, op=op.given, accepted=False, rule="invalid_request",
                         detail=f"{op.problem} (this operation only; the others of the call go on)")
    try:
        if isinstance(op, ReplaceText):
            detail = _replace_text(state, op)
        elif isinstance(op, SetCells):
            detail = _set_cells(state, op)
        elif isinstance(op, SetTable):
            detail = _set_table(state, op)
        elif isinstance(op, InsertText):
            made, detail = _insert_text(state, op)
        elif isinstance(op, Adopt):
            detail = _adopt(ctx, state, op)
        elif isinstance(op, TranscribePassage):
            made, detail = _transcribe_passage(state, op)
        elif isinstance(op, Unadopt):
            detail = _unadopt(state, op)
        elif isinstance(op, Dismiss):
            target, detail = _dismiss(state, op, issues)
        elif isinstance(op, RecordDoubt):
            target = _doubt(state, op)
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


def _set_table(state: DocumentState, op: SetTable) -> str:
    """The table's structure written anew on the agent's look at it: the same characters, no other and no fewer
    (``content/select.structure_only``), then the correction's gate (evidence of its place)."""
    block = _block(state, op.block)
    if block.kind != BlockKind.TABLE or block.cells is None:
        raise _Refused("not_table", f"{op.block} is a {block.kind.value}, not a table")
    try:
        grid = TableGrid.from_rows(op.rows, op.spans, header_rows=op.header_rows)
    except ValidationError as exc:
        raise _Refused("cell", "; ".join(e["msg"].removeprefix("Value error, ") for e in exc.errors())) from None
    except ValueError as exc:
        raise _Refused("cell", str(exc)) from None
    check = structure_only(block.cells, grid)
    if not check.passed:
        raise _Refused(check.name, check.detail)
    return _correct(state, block, op, text=None, grid=grid)


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
    refusal = next((g for g in outcome.gate if not g.passed and g.signal == PRINTED_AS_DRAFT), None)
    if refusal is not None:  # the agent and the readings disagree: both versions, for a person to check
        for printed, suggested in _spans(block, op):
            _record_doubt(state, block.id, printed, suggested, reason=op.reason, evidence=[op.evidence],
                          refused=True, readings=refusal.detail)
    return _gated(outcome.gate)


def _spans(block: Block, op) -> list[tuple[str, str]]:
    """What a correction writes in place of what: its find and replace, or each cell's content before and after."""
    if isinstance(op, ReplaceText):
        return [(op.find, op.replace)]
    if isinstance(op, SetTable):  # the table's own characters: nothing written in place of others
        return []
    before = [block.cells.slot(e.row, e.col) if block.cells is not None else None for e in op.cells]
    return [(b.content if b else "", e.content) for b, e in zip(before, op.cells)
            if substitutes(b.content if b else "", e.content)]


def _gated(gate) -> str:
    """The checks' details (a signal marked as one); refused with the first failed check's name when any failed."""
    failed = [g for g in gate if not g.passed]
    if failed:
        raise _Refused(failed[0].name, "; ".join(f"{g.name}: {g.detail}" for g in failed))
    return "; ".join(f"{g.name}: {'signal ' + g.signal + ' — ' if g.signal else ''}{g.detail}" for g in gate)


def _insert_text(state: DocumentState, op: InsertText) -> tuple[str, str]:
    if (op.page is None) == (op.figure is None):
        raise _Refused("place", "give the place: a PDF page (page, bbox) or a figure whose image shows the text")
    if op.figure is not None:
        return _insert_in_image(state, op)
    if state.format != "pdf" or op.page not in {p.n for p in state.pages}:
        where = "a Word document has no pages: text missing inside an image goes in with figure" \
            if state.format == "docx" else "this page is not in the document"
        raise _Refused("page", f"no PDF page {op.page} ({where})")
    if op.bbox is None:
        raise _Refused("place", "a page's text needs its place on the page (bbox, page points)")
    if op.after is not None and op.after not in {b.id for b in state.blocks}:
        raise _Refused("unknown_block", f"no block {op.after}")
    gate = add_gate(op.text, image=image_evidence_at(state, op.page, op.bbox, op.evidence),
                    seen=text_at(state, op.page, op.bbox), holders=holders_of(state, op.page, op.bbox, op.text))
    detail = _gated(gate)
    decision = Decision(stage=DecisionStage.REVIEW_ACCEPT, choice="added", actor=ACTOR,
                        reason=f"text the page shows where no block had it; {op.reason}",
                        evidence={"evidence": op.evidence, **{g.name: g.detail for g in gate}, **signals(gate)})
    block_id = _add_text_block(state, op.page, op.bbox, op.text, engine="agent", engine_version=ACTOR,
                               task=TaskKind.CORRECT, decision=decision, unit="agent_text", after=op.after)
    return block_id, detail


def _insert_in_image(state: DocumentState, op: InsertText) -> tuple[str, str]:
    """Text the image shows that its transcription lacks, added to the text read inside it (a block of the image,
    ``contains``), after *after* or after the image's last block (Q164: a Word document has no page to put it on)."""
    figure = _block(state, op.figure)
    anchor = next((a for a in figure.anchors if isinstance(a, AssetAnchor)), None)
    assets = {a.id: a for a in state.assets}
    if figure.kind != BlockKind.FIGURE or anchor is None or anchor.asset not in assets:
        raise _Refused("not_figure", f"{op.figure} is not a figure with an image")
    inside = [b for b in ordered(state) if any(r.kind == RelationKind.CONTAINS and r.src == figure.id
                                                and r.dst == b.id for r in state.relations)]
    if op.after is not None and op.after not in {figure.id, *(b.id for b in inside)}:
        raise _Refused("unknown_block", f"{op.after} is not {op.figure} or a block read inside it")
    wanted = normalize(op.text)  # already there: the whole line in one of the image's blocks (a share of
    # character pairs refused short lines that only share a date's digits with a block)
    holders = [b.id for b in inside if b.status in (BlockStatus.OK, BlockStatus.DEGRADED, BlockStatus.EXCLUDED,
                                                     BlockStatus.MERGED)
               and wanted and wanted in normalize(_plain(b))]
    asset = assets[anchor.asset]
    record = next((r for r in state.images if r.id == asset.id), None)
    box = op.bbox or (0.0, 0.0, float(asset.width), float(asset.height))
    seen = None if record is None or record.reading is None else " ".join(
        ln.text for ln in record.reading if box[0] <= (ln.bbox[0] + ln.bbox[2]) / 2 <= box[2]
        and box[1] <= (ln.bbox[1] + ln.bbox[3]) / 2 <= box[3])
    gate = add_gate(op.text, image=image_evidence(state, figure, op.evidence), seen=seen, holders=holders)
    detail = _gated(gate)
    taken = [int(m.group(1)) for b in state.blocks if (m := re.fullmatch(re.escape(figure.id) + r"-r(\d+)", b.id))]
    n = max(taken, default=0) + 1
    block_id = f"{figure.id}-r{n:03d}"
    new_anchor = AssetAnchor(asset=asset.id, bbox=tuple(box), image_size=(asset.width, asset.height))
    observation = Observation(id=ids.observation_id(block_id, "agent", 1), engine="agent", engine_version=ACTOR,
                              task=TaskKind.CORRECT, anchor=new_anchor, text=op.text, status=ObservationStatus.OK)
    block = Block(id=block_id, kind=BlockKind.TEXT, status=BlockStatus.OK, order=0, anchors=[new_anchor],
                  observations=[observation], chosen_observation=observation.id, text=op.text,
                  decisions=[Decision(stage=DecisionStage.REVIEW_ACCEPT, choice="added", actor=ACTOR,
                                      reason=f"text the image shows where its transcription had none; {op.reason}",
                                      evidence={"evidence": op.evidence, **{g.name: g.detail for g in gate},
                                                **signals(gate)})])
    sequence = ordered(state)
    after = op.after or (inside[-1].id if inside else figure.id)
    sequence.insert(next(i for i, b in enumerate(sequence) if b.id == after) + 1, block)
    for order, item in enumerate(sequence):
        item.order = order
    state.blocks.append(block)
    state.relations.append(Relation(id=ids.relation_id(RelationKind.CONTAINS, figure.id, block_id),
                                    kind=RelationKind.CONTAINS, src=figure.id, dst=block_id))
    state.ledger.append(LedgerEntry(item=f"i-{block_id}", unit="agent_text", source=new_anchor,
                                    chars=len("".join(op.text.split())), disposition="output", block=block_id))
    return block_id, detail


def _plain(block) -> str:
    return block.text or (" ".join(c.content for c in block.cells.cells) if block.cells is not None else "")


def add_missed_text(state: DocumentState) -> list[str]:
    """The lines the local page reading sees where the output has nothing (``reading.compare.missed_lines``, Q133),
    added as blocks at their places: the fixed pipeline does not lose them.  A line's role is the layout detector's
    label at its place (``PageReading.roles``): in a header, footer or page-number region it is page furniture,
    excluded like the scan engine's furniture (a scan engine may leave such regions out altogether, GLM-OCR); in a
    footnote region a footnote; elsewhere text.  The worklist lists each shown one for review (``text_added``).
    The new block ids."""
    readings = {r.n: r for r in state.readings}
    pages = {p.n: p for p in state.pages}
    made = []
    for n, lines in missed_lines(state).items():
        for line in sorted(lines, key=lambda ln: _top_left(rotation.shown(pages.get(n), ln.bbox))):
            engine = readings[n].engine
            decisions = [Decision(stage=DecisionStage.CONTENT_SOURCE, choice="added", actor=READING_ACTOR,
                                  reason="text the local page reading sees where the output had nothing (Q133)",
                                  evidence={"reading": engine, "score": round(line.score, 3)})]
            label = _role_at(readings[n], line.bbox)
            kind = labels.to_kind("layout", label) if label else BlockKind.TEXT
            status = BlockStatus.OK
            if kind in labels.FURNITURE:
                status = BlockStatus.EXCLUDED
                decisions.append(Decision(stage=DecisionStage.EXCLUDE, choice=kind.value, actor=READING_ACTOR,
                                          reason=f"page furniture: the layout detector's label {label!r} at its place",
                                          evidence={"label": label}))
            made.append(_add_text_block(state, n, line.bbox, line.text, engine="page_reading", engine_version=engine,
                                        task=TaskKind.RECOGNIZE, decision=decisions, unit="read_text", kind=kind,
                                        status=status))
    return made


def _role_at(reading, bbox) -> str | None:
    """The label of the reading's role region holding the centre of *bbox*, if any."""
    x, y = (bbox[0] + bbox[2]) / 2, (bbox[1] + bbox[3]) / 2
    return next((r.label for r in reading.roles if r.bbox[0] <= x <= r.bbox[2] and r.bbox[1] <= y <= r.bbox[3]), None)


def _add_text_block(state: DocumentState, page: int, bbox, text: str, *, engine: str, engine_version: str,
                    task: TaskKind, decision: Decision | list[Decision], unit, after: str | None = None,
                    kind: BlockKind = BlockKind.TEXT, status: BlockStatus = BlockStatus.OK) -> str:
    """A text block at *bbox* on *page*, in reading order (after *after*, else by position), with its ledger item."""
    block_id = ids.block_id_pdf(page, _next_block_seq(state, page))
    anchor = PdfAnchor(page=page, bbox=bbox, coord_space="page_pt")
    observation = Observation(id=ids.observation_id(block_id, engine, 1), engine=engine,
                              engine_version=engine_version, task=task, anchor=anchor, text=text,
                              status=ObservationStatus.OK)
    block = Block(id=block_id, kind=kind, status=status, order=0, anchors=[anchor], observations=[observation],
                  chosen_observation=observation.id, text=text,
                  decisions=decision if isinstance(decision, list) else [decision])
    sequence = ordered(state)
    at = (next(i for i, b in enumerate(sequence) if b.id == after) + 1 if after
          else _place(state, sequence, page, bbox))
    sequence.insert(at, block)
    for order, item in enumerate(sequence):
        item.order = order
    state.blocks.append(block)
    state.ledger.append(LedgerEntry(item=ids.ledger_item_pdf(page, _next_item(state, page)), unit=unit,
                                    source=anchor, chars=len("".join(text.split())),
                                    disposition="excluded" if status == BlockStatus.EXCLUDED else "output",
                                    block=block_id))
    return block_id


def _top_left(box) -> tuple[float, float]:
    return box[1], box[0]


def _place(state: DocumentState, sequence: list, page: int, bbox) -> int:
    """Reading-order position of new text on *page*: after the last block of the page that starts above it (on the
    page as shown)."""
    on_page = [i for i, b in enumerate(sequence) if block_unit(state, b) == page]
    pages = {p.n: p for p in state.pages}
    top = rotation.shown(pages.get(page), bbox)[1]
    above = [i for i in on_page if isinstance(sequence[i].anchors[0], PdfAnchor)
             and rotation.shown(pages.get(sequence[i].anchors[0].page), sequence[i].anchors[0].bbox)[1] <= top]
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
    outcome = table_gate(block, candidate, allowed_cells=allowed, fill_region=region, actor=ACTOR,
                         seen=numbers_seen(_reading_at(state, block)))
    block.decisions[-1].evidence["evidence"] = op.evidence
    block.decisions[-1].reason += f"; {op.reason}"
    return _gated(outcome.gate)


def _reading_at(state: DocumentState, block) -> str | None:
    """What the local reading shows where *block* sits: on its page (a PDF block), or inside the image it was read
    from (lines whose centre is in its box); None when the place was not read."""
    anchor = block.anchors[0]
    if not isinstance(anchor, AssetAnchor):
        return text_near(state, block)
    record = next((r for r in state.images if r.id == anchor.asset), None)
    if record is None or record.reading is None:
        return None
    x0, y0, x1, y1 = anchor.bbox
    return " ".join(ln.text for ln in record.reading
                    if x0 <= (ln.bbox[0] + ln.bbox[2]) / 2 <= x1 and y0 <= (ln.bbox[1] + ln.bbox[3]) / 2 <= y1)


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
    page_state = next((p for p in state.pages if p.n == n), None)
    seen = rotation.shown(page_state, region)  # the crop the engine read is of the page as shown
    size = (seen[2] - seen[0], seen[3] - seen[1])
    read = scan.page_blocks(scan.PageScan(page=n, raw=page_json, raw_ref=evidence.raw_ref or "",
                                          engine_version=evidence.engine or scan.ENGINE),
                            page_size=size, origin=(seen[0], seen[1]), first_seq=_next_block_seq(state, n),
                            first_item=_next_item(state, n), page=page_state)
    new = [b for b in read.blocks if b.kind not in (BlockKind.FIGURE, BlockKind.SCAN)]
    kept = {b.id for b in new}
    shown = [b for b in new if b.status not in HIDDEN]
    before, after = "".join(_content(b) for b in old), "".join(_content(b) for b in shown)
    native = [b for b in old if (c := _chosen_of(b)) is not None and c.engine in NATIVE_ENGINES]
    lost = _missing_numbers("".join(_content(b) for b in native), after)
    recall = _recall(before, after)
    # execution plan §3.4: the agent decides on the image; what the reading drops is a signal, recorded and listed
    raised = {}
    if lost:
        raised["region_numbers_changed"] = (f"the reading does not keep the native text layer's numbers {lost[:8]} "
                                            "(native numbers changed)")
    if old and recall < RECALL:
        raised["region_characters_lost"] = (f"the reading has {recall:.0%} of the {len(before)} characters of "
                                            f"{', '.join(b.id for b in old)} (below {RECALL:.0%})")
    native_text = "".join(_content(b) for b in native)
    changed = _letters_changed(native_text, after) if native and not _unmapped(native_text) else None
    if changed:  # the native text layer is what the page prints (user 2026-09-30: keep the original as printed)
        raised["native_text_changed"] = (f"the reading changes the native text layer's letters: lost '{changed[0][:40]}', "
                                         f"added '{changed[1][:40]}'")
    recorded = ({"signal": ",".join(raised), "signal_detail": "; ".join(raised.values())} if raised else {})
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
                                        reason=op.reason, evidence={"evidence": op.evidence, **recorded},
                                        refs=[b.id for b in old[:20]]))
    sequence = ordered(state)
    at = sequence.index(old[0]) if old else _place(state, sequence, n, region)
    sequence[at:at] = new
    for order, item in enumerate(sequence):
        item.order = order
    state.blocks.extend(new)
    state.ledger.extend(e for e in read.ledger if e.block in kept)
    return (f"{len(old)} blocks replaced by {len(shown)} ({', '.join(b.kind.value for b in shown)}); "
            f"characters kept {recall:.0%}" + "".join(f"; signal {k} — {v}" for k, v in raised.items()))


TRANSCRIBED, SUPERSEDED = "passage_transcription", "superseded_by_transcription"
_ADOPTED = frozenset({"region_reading", TRANSCRIBED})
_SUPERSEDED = frozenset({"superseded_by_reading", SUPERSEDED})


def _transcribe_passage(state: DocumentState, op: TranscribePassage) -> tuple[str, str]:
    """The agent writes a kept formula passage whole (a ``formula_candidate`` item), on a look at all of it: held to
    the rule the page reading and its editor are held to (``tools/formulas``: every letter and digit of the text
    layer, a mis-mapped glyph aside), each letter or digit it adds seen by another reading of the place (the page
    reading, the local reading), and its LaTeX renders.  It replaces the passage's blocks as their readings do; they
    stay, and ``unadopt`` brings them back."""
    owner = _block(state, op.block)
    candidate = dict(pending_candidates(state)).get(owner.id)
    if candidate is None:
        raise _Refused("not_candidate", f"{op.block} stands for no kept formula passage (formula_candidate); "
                                        "change a block's characters with replace_text")
    members = passage_of(state, owner.id) or [owner]
    page = next((a.page for a in owner.anchors if isinstance(a, PdfAnchor)), None)
    members = [b for b in members if any(isinstance(a, PdfAnchor) and a.page == page for a in b.anchors)]
    box = formula_union([b.anchors[0].bbox for b in members])
    shown = image_evidence_whole(state, owner, page, box, op.evidence)
    if not shown.passed:
        raise _Refused(shown.name, shown.detail)
    text = op.text.strip()
    wrong = problems(text)
    if not text or wrong:
        raise _Refused("latex", "; ".join(wrong) or "an empty passage")
    native = "\n".join(b.text or "" for b in members)
    blocks = {b.id: b for b in state.blocks}
    if formula_lost(state, page, [b.id for b in members], blocks, native, text):
        lacks, _ = disagreement(native, text)
        raise _Refused("conservation", f"the text layer here has {listed(lacks, ', ')} the transcription lacks: "
                                       "write every letter and digit the passage prints, prose and numbers included")
    written = Counter(normalize(characters(text)))
    layer, others = Counter(normalize(native)), Counter(normalize(characters(candidate)))
    others |= Counter(normalize(text_at(state, page, box) or ""))
    unseen = +Counter({ch: k - max(layer[ch], others[ch]) for ch, k in written.items()})
    if unseen:
        raise _Refused("not_seen", f"{listed(unseen, ', ')} written here is in no reading of this place (the text "
                                   "layer, the page reading, the local reading): write what the page prints")
    reading = _edited_block(page, members, [], text)
    reading.observations[0] = reading.observations[0].model_copy(
        update={"engine": "agent", "engine_version": ACTOR, "label": None})
    known = {b.id for b in state.blocks}
    adopt_passage(state, page, members, [reading], how="agent")
    made = [b for b in state.blocks if b.id not in known]
    for block in made:
        block.decisions.append(Decision(stage=DecisionStage.CONTENT_SOURCE, choice=TRANSCRIBED, actor=ACTOR,
                                        reason=op.reason, evidence={"evidence": op.evidence},
                                        refs=[b.id for b in members]))
    for block in members:
        block.decisions.append(Decision(stage=DecisionStage.CONTENT_SOURCE, choice=SUPERSEDED, actor=ACTOR,
                                        reason=op.reason, evidence={"evidence": op.evidence},
                                        refs=[b.id for b in made]))
    renumber(state)
    return made[0].id, (f"the passage of {len(members)} block(s) written whole as {made[0].id}; "
                        "its blocks stay (unadopt brings them back)")


def _unadopt(state: DocumentState, op: Unadopt) -> str:
    """Undo a region's adoption or a passage's transcription: the replaced blocks come back, the reading's (or the
    transcription's) blocks become their duplicates."""
    made = [b for b in state.blocks if b.status not in HIDDEN and any(
        d.choice in _ADOPTED and d.evidence.get("evidence") == op.evidence for d in b.decisions)]
    replaced = [b for b in state.blocks if b.status == BlockStatus.DUPLICATE and any(
        d.choice in _SUPERSEDED and d.evidence.get("evidence") == op.evidence for d in b.decisions)]
    if not made and not replaced:
        raise _Refused("not_adopted", f"no region adopted or passage written on {op.evidence} is in the draft")
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


def _doubt(state: DocumentState, op: RecordDoubt) -> str:
    """The agent's doubt about the original at a place it looked at; the draft stays."""
    block = _block(state, op.block)
    printed = op.printed.strip()
    held = block.text if block.cells is None else "\n".join(c.content for c in block.cells.cells)
    if not printed or printed not in (held or ""):
        raise _Refused("find", f"'printed' must be a piece of {op.block} as the draft has it: {printed[:40]!r}")
    image = image_evidence(state, block, op.evidence)
    if not image.passed:
        raise _Refused(image.name, image.detail)
    return _record_doubt(state, block.id, printed, op.suggested.strip(), reason=op.reason.strip(),
                         evidence=[op.evidence]).id


def _record_doubt(state: DocumentState, block: str, printed: str, suggested: str, *, reason: str,
                  evidence: list[str], refused: bool = False, readings: str = "") -> Doubt:
    """A place the original itself may be wrong (``Doubt``); one already raised there (same block, printed and
    suggested) is kept, not repeated."""
    for doubt in state.doubts:
        if (doubt.block, doubt.printed, doubt.suggested) == (block, printed, suggested):
            return doubt
    doubt = Doubt(id=f"q-{len(state.doubts) + 1:03d}", block=block, printed=printed, suggested=suggested,
                  reason=reason, by=ACTOR, evidence=evidence, refused=refused, readings=readings)
    state.doubts.append(doubt)
    return doubt


def _note(state: DocumentState, op: WriteNote) -> str:
    if not op.text.strip() or not op.scope.strip():
        raise _Refused("note", "a note says what holds (text) and where (scope)")
    if op.replaces is not None and op.replaces not in {n.id for n in current_notes(state)}:
        raise _Refused("unknown_note", f"no current note {op.replaces} (read_draft view=notes)")
    known = {e.id for e in state.evidence}
    cited = [e for e in re.split(r"[\s,，;；]+", op.evidence) if e]
    unknown = [e for e in cited if e not in known]
    if unknown:
        raise _Refused("evidence", f"no evidence {unknown} (view_source gives it)")
    note = Note(id=f"n-{len(state.notes) + 1:03d}", text=op.text.strip(), scope=op.scope.strip(),
                evidence=cited, replaces=op.replaces, actor=ACTOR)
    state.notes.append(note)
    return note.id


# ── the worklist ────────────────────────────────────────────────────────


class _Issues:
    """The open issues of the state being edited, computed when needed, and those open when the call began."""

    def __init__(self, state: DocumentState, before: dict[str, Unresolved]):
        self.state, self.before, self._items = state, before, None

    def changed(self) -> None:
        self._items = None

    def get(self, issue: str) -> Unresolved | None:
        if self._items is None:
            self._items = {u.id: u for u in unresolved_items(self.state)}
        return self._items.get(issue)


def _dismiss(state: DocumentState, op: Dismiss, issues: _Issues) -> tuple[str, str | None]:
    item = issues.get(op.issue)
    if item is None and op.issue in issues.before:  # a change earlier in this call resolved it: closing is done
        return issues.before[op.issue].target, f"{op.issue} was resolved by an earlier change of this call"
    if item is None:
        raise _Refused("unknown_issue", f"no open issue {op.issue} (read_draft view=issues)")
    if item.kind in _NOT_DISMISSED:
        raise _Refused("not_dismissable", f"{item.kind.value} is resolved by processing, not dismissed")
    if op.occluded and item.kind != UnresolvedKind.TEXT_NOT_SEEN:
        raise _Refused("occluded", "only text the page does not show (text_not_seen) is occluded")
    if item.target.startswith("p") and item.target[1:].isdigit():
        n = int(item.target[1:])
        page = next((p for p in state.pages if p.n == n), None)
        box = rotation.whole(page) if page is not None and page.size_pt else (0.0, 0.0, 1e6, 1e6)
        seen = image_evidence_at(state, n, box, op.evidence)
    else:
        seen = image_evidence(state, _block(state, item.target), op.evidence)
    if not seen.passed:
        raise _Refused(seen.name, seen.detail)
    state.closed.append(ClosedItem(target=item.target, kind=item.kind.value, quotes=[q.doc_text for q in item.quotes],
                                   detail="" if item.quotes else item.detail, reason=op.reason, actor=ACTOR,
                                   image=op.evidence, occluded=op.occluded))
    return item.target, None
