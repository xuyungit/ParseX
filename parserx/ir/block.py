"""Block: a logical piece of content (guide §4.1).

Validators check one block's own structure. Cross-block rules (level skips,
numbering consistency, dangling references) belong to ``hierarchy/`` and
``accounting/``, which report them instead of refusing to load the state.
"""

from __future__ import annotations

from pydantic import Field, model_validator

from parserx.ir.anchor import SourceAnchor
from parserx.ir.base import IRModel
from parserx.ir.decision import Decision
from parserx.ir.enums import BlockKind, BlockStatus
from parserx.ir.observation import Observation
from parserx.ir.semantic import FigureSemantic
from parserx.tables.grid import TableGrid


class Block(IRModel):
    id: str
    kind: BlockKind
    order: int
    status: BlockStatus = BlockStatus.OK
    anchors: list[SourceAnchor] = Field(min_length=1)  # merged blocks keep every source (§11.5)
    observations: list[Observation] = []
    chosen_observation: str | None = None
    text: str = ""  # derived from chosen_observation; empty for tables
    cells: TableGrid | None = None  # tables only
    level: int | None = Field(None, ge=1, le=6)  # titles only; None while the level is pending
    semantic: FigureSemantic | None = None  # figures only
    decisions: list[Decision] = []

    @model_validator(mode="after")
    def _check_structure(self) -> "Block":
        if self.level is not None and self.kind != BlockKind.TITLE:
            raise ValueError(f"level on a {self.kind} block")
        if self.cells is not None and self.kind != BlockKind.TABLE:
            raise ValueError(f"cells on a {self.kind} block")
        if self.kind == BlockKind.TABLE and self.text:
            raise ValueError("table content lives in cells, not text")
        if self.semantic is not None and self.kind != BlockKind.FIGURE:
            raise ValueError(f"semantic on a {self.kind} block")
        if self.chosen_observation is not None and all(
            o.id != self.chosen_observation for o in self.observations
        ):
            raise ValueError(f"chosen_observation {self.chosen_observation} is not among the observations")
        return self
