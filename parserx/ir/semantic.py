"""Figure semantics with evidence levels (guide §6.7). Phase 1 fixes the shapes only."""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import Field

from parserx.ir.base import IRModel
from parserx.ir.enums import EvidenceLevel


class Evidenced(IRModel):
    value: str | float | None
    level: EvidenceLevel


class Series(IRModel):
    name: Evidenced
    values: list[Evidenced] = []


class Edge(IRModel):
    src: str  # node name as it appears in nodes
    dst: str
    label: Evidenced | None = None
    direction: Literal["forward", "backward", "both", "unknown"] = "unknown"


class ChartSemantic(IRModel):
    type: Literal["chart"] = "chart"
    chart_type: Evidenced
    title: Evidenced | None = None
    x_axis: Evidenced | None = None
    y_axis: Evidenced | None = None
    series: list[Series] = []
    unit: Evidenced | None = None
    axis_scale: Evidenced | None = None


class DiagramSemantic(IRModel):
    type: Literal["diagram"] = "diagram"
    diagram_type: Evidenced
    nodes: list[Evidenced] = []
    edges: list[Edge] = []


class GenericSemantic(IRModel):
    type: Literal["photo", "seal", "other"]
    summary: Evidenced
    visible_text: list[Evidenced] = []


FigureSemantic = Annotated[ChartSemantic | DiagramSemantic | GenericSemantic, Field(discriminator="type")]
