"""Structure change requests (docs/v2_phase1_interfaces.md §5.7).

No change type has a text field: "structure changes never rewrite content" is
guaranteed by the schema, not checked at run time.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Annotated, Literal

from pydantic import Field

from parserx.ir.base import IRModel

# A block's role, as the draft shows it and as it is set (Q86): a title by its level, or a kind of body text.
# table / figure / formula / scan hold content in other forms and never change role; page furniture (header, footer,
# page number, watermark) is the program's label for what it leaves out, not a role to set.
Role = Literal["H1", "H2", "H3", "H4", "H5", "H6", "text", "list", "caption", "footnote", "other"]
Grounds = dict[str, float | int | str | bool]  # what a change rests on: named facts, or an evidence id (Q85)
EvidenceRef = Grounds | str


class SetRole(IRModel):
    op: Literal["set_role"]
    block: str
    role: Role
    reason: str
    evidence: EvidenceRef = {}

    @property
    def kind(self) -> str:
        return "title" if self.role.startswith("H") else self.role

    @property
    def level(self) -> int | None:
        return int(self.role[1]) if self.role.startswith("H") else None


class Move(IRModel):
    """Put a block right after another in reading order."""

    op: Literal["move"]
    block: str
    after: str | None  # None: to the front of the document
    reason: str
    evidence: EvidenceRef = {}


class Join(IRModel):
    """``second`` continues ``first`` (guide §6.9): two paragraphs broken by a page or a column are output as one;
    two tables become one, ``second``'s rows appended to ``first`` (a table continued on the next page)."""

    op: Literal["join"]
    first: str
    second: str
    drop_rows: int = Field(0, ge=0)  # tables: leading rows of ``second`` repeating the header of ``first``
    reason: str
    evidence: EvidenceRef = {}


class Unjoin(IRModel):
    """Undo the joining of two paragraphs; joined tables are one table and stay so."""

    op: Literal["unjoin"]
    first: str
    second: str
    reason: str
    evidence: EvidenceRef = {}


class MarkPending(IRModel):
    op: Literal["mark_pending"]
    block: str
    reason: str
    evidence: EvidenceRef = {}


class Exclude(IRModel):
    """Leave a block out of the output (an icon read as a character, interface text …); its text stays in the
    sidecar, the ledger counts it as excluded and the Decision says why (guide §2.3, Q27; P2-5)."""

    op: Literal["exclude"]
    block: str
    reason: str
    evidence: EvidenceRef = {}


class Include(IRModel):
    """Undo an exclusion (page furniture, a decorative image, an earlier ``exclude``): page furniture comes back as
    text; text deleted by a revision stays deleted (Q26)."""

    op: Literal["include"]
    block: str
    reason: str
    evidence: EvidenceRef = {}


class Split(IRModel):
    """Divide a text block at one of its line breaks (a soft line break that joined a title and the next item):
    the text after the break becomes a new text block right after it.  No text field — the break is named by its
    number, so the content is only divided, never rewritten (2026-09-25)."""

    op: Literal["split"]
    block: str
    at_break: int = Field(1, ge=1)  # the n-th line break of the block's text
    reason: str
    evidence: EvidenceRef = {}


StructureChange = Annotated[
    SetRole | Move | Join | Unjoin | MarkPending | Exclude | Include | Split,
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
    ROWS_NOT_DUPLICATE = "rows_not_duplicate"
    REASON_REQUIRED = "reason_required"  # exclude: content leaves the output only with a reason
    NOT_VISIBLE = "not_visible"  # exclude: the block is not in the output
    NOT_EXCLUDED = "not_excluded"  # restore: the block is not excluded
    NOT_RESTORABLE = "not_restorable"  # restore: text deleted by a revision (Q26)
    NOT_JOINED = "not_joined"  # unjoin: second does not continue first
    TABLES_MERGED = "tables_merged"  # unjoin: joined tables are one table
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
