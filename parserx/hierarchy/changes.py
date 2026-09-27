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
from parserx.ir.enums import RelationKind

# table / figure / formula / scan hold content in other forms; they never change role.
StructuralKind = Literal["title", "text", "list", "caption", "footnote", "header", "footer", "page_number", "other"]
Grounds = dict[str, float | int | str | bool]  # what a change rests on: named facts, or an evidence id (Q85)
EvidenceRef = Grounds | str


class SetRole(IRModel):
    op: Literal["set_role"]
    block: str
    kind: StructuralKind
    level: int | None = Field(None, ge=1, le=6)  # with kind "title": its level in the same change (P2-5)
    reason: str
    evidence: EvidenceRef = {}


class SetLevel(IRModel):
    op: Literal["set_level"]
    block: str
    level: int | None = Field(ge=1, le=6)
    reason: str
    evidence: EvidenceRef = {}


class Move(IRModel):
    """Put a block right after another in reading order."""

    op: Literal["move"]
    block: str
    after: str | None  # None: to the front of the document
    reason: str
    evidence: EvidenceRef = {}


class Link(IRModel):
    """Relate two blocks: ``continues`` (one paragraph or table broken in two), ``captions``, ``footnotes`` …"""

    op: Literal["link"]
    kind: RelationKind
    src: str
    dst: str
    reason: str = ""
    evidence: EvidenceRef = {}
    confidence: float | None = None


class Unlink(IRModel):
    """Remove the relation ``link`` made (named the same way)."""

    op: Literal["unlink"]
    kind: RelationKind
    src: str
    dst: str
    reason: str = ""


class MergeTables(IRModel):
    """``second`` continues ``first`` on the next page (guide §6.9): its rows are appended to ``first``."""

    op: Literal["merge_tables"]
    first: str
    second: str
    drop_rows: int = Field(0, ge=0)  # leading rows of ``second`` repeating the header of ``first``
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
    """Undo an exclusion (page furniture, a decorative image, an earlier ``exclude``); text deleted by a revision
    stays deleted (Q26)."""

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
    SetRole | SetLevel | Move | Link | Unlink | MarkPending | MergeTables | Exclude | Include | Split,
    Field(discriminator="op"),
]


class LegalityRule(StrEnum):
    UNKNOWN_BLOCK = "unknown_block"
    KIND_NOT_STRUCTURAL = "kind_not_structural"
    LEVEL_ON_NON_TITLE = "level_on_non_title"
    LEVEL_SKIP = "level_skip"
    NUMBERING_LEVEL_INCONSISTENT = "numbering_level_inconsistent"
    ORDER_CYCLE = "order_cycle"
    DUPLICATE_RELATION = "duplicate_relation"
    NOT_MERGE_CANDIDATE = "not_merge_candidate"
    ROWS_NOT_DUPLICATE = "rows_not_duplicate"
    REASON_REQUIRED = "reason_required"  # exclude: content leaves the output only with a reason
    NOT_VISIBLE = "not_visible"  # exclude: the block is not in the output
    NOT_EXCLUDED = "not_excluded"  # restore: the block is not excluded
    NOT_RESTORABLE = "not_restorable"  # restore: text deleted by a revision (Q26)
    UNKNOWN_RELATION = "unknown_relation"  # unlink: no such relation
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
