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
Evidence = dict[str, float | int | str | bool]


class SetRole(IRModel):
    op: Literal["set_role"]
    block: str
    kind: StructuralKind
    reason: str
    evidence: Evidence = {}


class SetLevel(IRModel):
    op: Literal["set_level"]
    block: str
    level: int | None = Field(ge=1, le=6)
    reason: str
    evidence: Evidence = {}


class MoveAfter(IRModel):
    op: Literal["move_after"]
    block: str
    after: str | None  # None: to the front of the document
    reason: str


class AddRelation(IRModel):
    op: Literal["add_relation"]
    kind: RelationKind
    src: str
    dst: str
    confidence: float | None = None


class RemoveRelation(IRModel):
    op: Literal["remove_relation"]
    relation: str


class MergeTables(IRModel):
    """``second`` continues ``first`` on the next page (guide §6.9): its rows are appended to ``first``."""

    op: Literal["merge_tables"]
    first: str
    second: str
    drop_rows: int = Field(0, ge=0)  # leading rows of ``second`` repeating the header of ``first``
    reason: str
    evidence: Evidence = {}


class MarkPending(IRModel):
    op: Literal["mark_pending"]
    block: str
    reason: str


StructureChange = Annotated[
    SetRole | SetLevel | MoveAfter | AddRelation | RemoveRelation | MarkPending | MergeTables,
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


class Rejection(IRModel):
    index: int
    rule: LegalityRule
    detail: str


@dataclass
class ApplyOutcome:
    accepted: list[int] = field(default_factory=list)
    rejected: list[Rejection] = field(default_factory=list)
