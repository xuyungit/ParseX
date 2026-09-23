"""Decision: who decided what, on which evidence (guide §4.4)."""

from __future__ import annotations

from parserx.ir.base import IRModel
from parserx.ir.enums import DecisionStage


class Decision(IRModel):
    stage: DecisionStage
    choice: str
    reason: str
    evidence: dict[str, float | int | str | bool]  # flat scalars only
    actor: str  # "program:<module>" | "pipeline" | "agent" | "tool:<name>" | "adapter:v1"
    refs: list[str] = []  # Observation / Relation / Block ids used as evidence
