"""What a figure shows (guide §6.7, Q121): a type and a caption of one or two sentences (``FigureNote``).  The
structured shapes with evidence levels before it (chart series, diagram nodes, visible text) are still read, so older
states open; nothing writes them any more."""

from __future__ import annotations

from typing import Literal

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


# content: the words on the image are what it conveys (an invoice, a certificate, a page, a table, a formula) — its
# text is transcribed; the others are pictures, shown with their note (IO6)
FIGURE_TYPES = ("content", "screenshot", "chart", "diagram", "photo", "seal", "other")


class FigureNote(IRModel):
    """A figure in one or two sentences, typically at most 50 characters, at most 100 (Q121)."""

    type: Literal["content", "screenshot", "chart", "diagram", "photo", "seal", "other"]
    caption: str


# A note, or one of the older shapes: each rejects the others' fields, so the union needs no discriminator.
FigureSemantic = FigureNote | ChartSemantic | DiagramSemantic | GenericSemantic


def note_of(semantic) -> FigureNote | None:
    """The note of a figure's semantic, older shapes included."""
    if semantic is None or isinstance(semantic, FigureNote):
        return semantic
    if isinstance(semantic, GenericSemantic):
        return FigureNote(type=semantic.type, caption=str(semantic.summary.value or ""))
    if isinstance(semantic, ChartSemantic):
        parts = [semantic.chart_type.value, semantic.title.value if semantic.title else None]
        return FigureNote(type="chart", caption="：".join(str(p) for p in parts if p))
    return FigureNote(type="diagram", caption=str(semantic.diagram_type.value or ""))
