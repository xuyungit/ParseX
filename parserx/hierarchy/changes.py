"""Structure change requests (docs/v2_phase1_interfaces.md §5.7).

No change type has a text field: "structure changes never rewrite content" is
guaranteed by the schema, not checked at run time.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Annotated, Literal

from pydantic import ConfigDict, Field

from parserx.ir.base import IRModel

# A block's role, as the draft shows it and as it is set (Q86): a title by its level, or a kind of body text.
# table / figure / formula / scan hold content in other forms and never change role; page furniture (header, footer,
# page number, watermark) is the program's label for what it leaves out, not a role to set.
Role = Literal["H1", "H2", "H3", "H4", "H5", "H6", "text", "list", "caption", "footnote", "other"]
Grounds = dict[str, float | int | str | bool]  # what a change rests on: named facts, or an evidence id (Q85)
EvidenceRef = Grounds | str

# What the agent reads about a change (Q86: the schema is the contract; its words are the task's language).  The
# fields every operation shares are described once, in the tool's description; the rest here.
BLOCK = "块号"
REASON = "理由"
EVIDENCE = "证据编号（e-…，view_source 给出）"
EVIDENCE_OPTIONAL = "证据编号（可省）"
OVERRIDE = "凭证据突破一条文档经验：原件表明这里是例外时设为 true，必须带 evidence，在 reason 里写明为什么是例外"


def agent_doc(text: str) -> ConfigDict:
    """A model config whose JSON Schema description is *text*: what an agent reads (the docstring is for code)."""
    return ConfigDict(json_schema_extra={"description": text})


class SetRole(IRModel):
    model_config = agent_doc("设块的角色，与 read_draft 显示的角色相同。H1–H6 是标题及其层级：层级不能跳（H1 之后不能直接 H3），"
                             "同一部分里同一编号模式同级；一次调用中连续的结构操作按结果判定，整组编号可以一起调层级。"
                             "表格、图片、公式、扫描图不能改角色。被程序当作页眉页脚隐去的块，设了角色就重新输出。")

    op: Literal["set_role"]
    block: str = Field(description=BLOCK)
    role: Role = Field(description="H1–H6：标题；text：正文；list：列表项；caption：图表题；footnote：脚注；other：其他")
    reason: str = Field(description=REASON)
    evidence: EvidenceRef = Field({}, description=EVIDENCE_OPTIONAL)
    override: bool = Field(False, description=OVERRIDE + "（同一部分里同一编号模式同级）")

    @property
    def kind(self) -> str:
        return "title" if self.role.startswith("H") else self.role

    @property
    def level(self) -> int | None:
        return int(self.role[1]) if self.role.startswith("H") else None


class Move(IRModel):
    """Put a block right after another in reading order."""

    model_config = agent_doc("调整阅读顺序：把块移到 after 之后。")

    op: Literal["move"]
    block: str = Field(description=BLOCK)
    after: str | None = Field(description="它之前的块；null 表示移到最前")
    reason: str = Field(description=REASON)
    evidence: EvidenceRef = Field({}, description=EVIDENCE_OPTIONAL)


class Join(IRModel):
    """``second`` continues ``first`` (guide §6.9): two paragraphs broken by a page or a column are output as one;
    two tables become one, ``second``'s rows appended to ``first`` (a table continued on the next page)."""

    model_config = agent_doc("续接：second 接着 first。两段文字（被分页或分栏拆开的一段）在输出中合成一段，块的原文不变；"
                             "两张表（下一页的续表）合成一张。")

    op: Literal["join"]
    first: str = Field(description="前一块")
    second: str = Field(description="后一块")
    drop_rows: int = Field(0, ge=0, description="表格：去掉 second 开头逐字重复 first 表头的几行")
    reason: str = Field(description=REASON)
    evidence: EvidenceRef = Field({}, description=EVIDENCE_OPTIONAL)
    override: bool = Field(False, description=OVERRIDE + "（表格：续表在下一页，中间只有页眉页脚；列数仍须相同）")


class Unjoin(IRModel):
    """Undo a join: two paragraphs output apart again; two joined tables separated, each with its rows."""

    model_config = agent_doc("撤销续接：两段文字重新分开；合成的表拆回两张，各自的行（含合并后改过的单元格）回到各自的表。"
                             "一张表接了几张时从最后接上的开始拆。")

    op: Literal["unjoin"]
    first: str = Field(description="前一块")
    second: str = Field(description="后一块")
    reason: str = Field(description=REASON)
    evidence: EvidenceRef = Field({}, description=EVIDENCE_OPTIONAL)


class MarkPending(IRModel):
    model_config = agent_doc("拿不准这一块是不是标题：保留正文，结构待定（留下一项待办，写进最终报告）。")

    op: Literal["mark_pending"]
    block: str = Field(description=BLOCK)
    reason: str = Field(description=REASON)
    evidence: EvidenceRef = Field({}, description=EVIDENCE_OPTIONAL)


class Exclude(IRModel):
    """Leave a block out of the output (an icon read as a character, interface text …); its text stays in the
    sidecar, the ledger counts it as excluded and the Decision says why (guide §2.3, Q27; P2-5)."""

    model_config = agent_doc("不输出这一块（界面文字、图标被识成的字符、扫描软件字样、装饰图等），必须写明理由；文字留在 sidecar。")

    op: Literal["exclude"]
    block: str = Field(description=BLOCK)
    reason: str = Field(description=REASON)
    evidence: EvidenceRef = Field({}, description=EVIDENCE_OPTIONAL)


class Include(IRModel):
    """Undo an exclusion (page furniture, a decorative image, an earlier ``exclude``): page furniture comes back as
    text; text deleted by a revision stays deleted (Q26)."""

    model_config = agent_doc("恢复不输出的块（程序判为页眉页脚、装饰图的，或 exclude 的）；页眉页脚类恢复后是 text。"
                             "修订中删除的文字不能恢复。")

    op: Literal["include"]
    block: str = Field(description=BLOCK)
    reason: str = Field(description=REASON)
    evidence: EvidenceRef = Field({}, description=EVIDENCE_OPTIONAL)


class Split(IRModel):
    """Divide a text block at one of its line breaks (a soft line break that joined a title and the next item):
    the text after the break becomes a new text block right after it.  No text field — the break is named by its
    number, so the content is only divided, never rewritten (2026-09-25)."""

    model_config = agent_doc("在块内第 at_break 个换行处拆成两块（例如标题和下一条被软换行连在一块里）；只拆开，不改文字。"
                             "结果的 block 是拆出的新块。")

    op: Literal["split"]
    block: str = Field(description=BLOCK)
    at_break: int = Field(1, ge=1, description="第几个换行（从 1 起）")
    reason: str = Field(description=REASON)
    evidence: EvidenceRef = Field({}, description=EVIDENCE_OPTIONAL)


StructureChange = Annotated[
    SetRole | Move | Join | Unjoin | Split | Exclude | Include | MarkPending,
    Field(discriminator="op"),
]


class LegalityRule(StrEnum):
    UNKNOWN_BLOCK = "unknown_block"
    KIND_NOT_STRUCTURAL = "kind_not_structural"
    LEVEL_SKIP = "level_skip"
    NUMBERING_LEVEL_INCONSISTENT = "numbering_level_inconsistent"
    ORDER_CYCLE = "order_cycle"
    ALREADY_JOINED = "already_joined"  # join: second continues first already
    NOT_JOINABLE = "not_joinable"  # join: two paragraphs or two tables, both in the output
    NOT_MERGE_CANDIDATE = "not_merge_candidate"  # join of tables: not a continuation of the same table
    NOT_ADJACENT = "not_adjacent"  # join of tables: not on the next page, or more than page furniture between
    ROWS_NOT_DUPLICATE = "rows_not_duplicate"
    OVERRIDE_WITHOUT_EVIDENCE = "override_without_evidence"  # an exception rests on evidence that exists
    REASON_REQUIRED = "reason_required"  # exclude: content leaves the output only with a reason
    NOT_VISIBLE = "not_visible"  # exclude: the block is not in the output
    NOT_EXCLUDED = "not_excluded"  # restore: the block is not excluded
    NOT_RESTORABLE = "not_restorable"  # restore: text deleted by a revision (Q26)
    NOT_JOINED = "not_joined"  # unjoin: second does not continue first
    TABLES_MERGED = "tables_merged"  # unjoin: joined tables that cannot be separated again
    NO_LINE_BREAK = "no_line_break"  # split: the block has no such line break with text on both sides
    DECIDED_BY_AGENT = "decided_by_agent"  # a program proposal on a block whose structure the agent decided


class Rejection(IRModel):
    index: int
    rule: LegalityRule
    detail: str


@dataclass
class ApplyOutcome:
    accepted: list[int] = field(default_factory=list)
    rejected: list[Rejection] = field(default_factory=list)
