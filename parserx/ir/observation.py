"""Observation: one engine's reading of one anchor (guide §4.1)."""

from __future__ import annotations

from typing import Literal

from pydantic import model_validator

from parserx.ir.anchor import SourceAnchor
from parserx.ir.base import IRModel
from parserx.ir.enums import ObservationStatus, TaskKind
from parserx.tables.grid import TableGrid


class Numbering(IRModel):
    """A numbering.xml reference. This is a list-numbering level, not a heading level (guide §6.8)."""

    num_id: str
    level: int
    text: str | None = None  # rendered number, e.g. "1.2.3"


class TextStyle(IRModel):
    """Typesetting evidence for structure decisions; only values present in the source, else None."""

    font_size: float | None = None  # pt
    bold: bool | None = None
    font: str | None = None  # the dominant font face (PDF), as named in the file
    style_name: str | None = None  # DOCX style display name, resolved along basedOn
    outline_level: int | None = None  # w:outlineLvl including style inheritance, 0-based
    numbering: Numbering | None = None
    monospace: str | None = None  # PDF: the monospaced face the block's measurable lines are set in (code, P4-6)


class Mark(IRModel):
    """Inline emphasis of a span of an observation's text (R3), or a run of sub- or superscripts (Q143 ③): the text
    itself stays plain, so numbering, titles, comparisons and the agent's edits read it unchanged; rendering finds the
    span and writes ``**…**`` or ``<u>…</u>`` around it, or the run as a script (``content/scripts.form``)."""

    kind: Literal["bold", "underline", "sub", "sup"]
    text: str  # the span as it reads in the text (spacing may differ: it is found ignoring whitespace)
    before: str = ""  # a script's: the glyph before the run, the span is found right after it


class Observation(IRModel):
    id: str
    engine: str  # "native_pdf" | "docx" | "paddleocr" | "vlm" | "layout"
    engine_version: str
    task: TaskKind
    anchor: SourceAnchor
    raw_ref: str | None = None  # response cache key (.parserx_cache/raw)
    label: str | None = None  # engine label before mapping; mapping lives only in layout/labels.py
    text: str | None = None
    cells: TableGrid | None = None
    style: TextStyle | None = None
    marks: list[Mark] = []  # inline bold and underline spans, in reading order (R3)
    det_confidence: float | None = None
    rec_confidence: float | None = None  # None means unknown, not trusted (guide §4.2)
    status: ObservationStatus
    error: str | None = None

    @model_validator(mode="after")
    def _failure_has_reason(self) -> "Observation":
        if self.status == ObservationStatus.FAILED and not self.error:
            raise ValueError("a failed observation needs an error")
        return self
