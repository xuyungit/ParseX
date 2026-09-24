"""Response schemas and parsers of the tool-internal VLM tasks (guide §6.6–§6.7).

Parsing follows guide §8.2 rule 3: a response that is not JSON is
``UnparseableResponse`` (requested once more, replayably); a response that is
JSON but does not fit the task is final and reported as a failure.
"""

from __future__ import annotations

import json
import re
from typing import Any

from parserx.ir.enums import EvidenceLevel
from parserx.ir.semantic import ChartSemantic, DiagramSemantic, Edge, Evidenced, FigureSemantic, GenericSemantic, Series
from parserx.scheduling import UnparseableResponse
from parserx.tables.grid import TableGrid

_LEVELS = [level.value for level in EvidenceLevel]
_EV = {"type": "object", "additionalProperties": False, "required": ["value", "level"],
       "properties": {"value": {"type": ["string", "null"]}, "level": {"type": "string", "enum": _LEVELS}}}
_CHART = {"type": "object", "additionalProperties": False,
          "required": ["chart_type", "title", "x_axis", "y_axis", "unit", "series"],
          "properties": {"chart_type": _EV, "title": _EV, "x_axis": _EV, "y_axis": _EV, "unit": _EV,
                         "series": {"type": "array", "items": {
                             "type": "object", "additionalProperties": False, "required": ["name", "values"],
                             "properties": {"name": _EV, "values": {"type": "array", "items": _EV}}}}}}
_DIAGRAM = {"type": "object", "additionalProperties": False, "required": ["diagram_type", "nodes", "edges"],
            "properties": {"diagram_type": _EV, "nodes": {"type": "array", "items": _EV},
                           "edges": {"type": "array", "items": {
                               "type": "object", "additionalProperties": False,
                               "required": ["src", "dst", "label", "direction"],
                               "properties": {"src": {"type": "string"}, "dst": {"type": "string"}, "label": _EV,
                                              "direction": {"type": "string",
                                                            "enum": ["forward", "backward", "both", "unknown"]}}}}}}
DESCRIBE_SCHEMA: dict[str, Any] = {
    "type": "object", "additionalProperties": False,
    "required": ["type", "summary", "visible_text", "chart", "diagram"],
    "properties": {
        "type": {"type": "string", "enum": ["chart", "diagram", "photo", "seal", "other"]},
        "summary": _EV,
        "visible_text": {"type": "array", "items": _EV},
        "chart": {"anyOf": [{"type": "null"}, _CHART]},
        "diagram": {"anyOf": [{"type": "null"}, _DIAGRAM]},
    },
}
REVIEW_SCHEMA: dict[str, Any] = {
    "type": "object", "additionalProperties": False, "required": ["table_html", "undetermined"],
    "properties": {
        "table_html": {"type": "string"},
        "undetermined": {"type": "array", "items": {"type": "array", "items": {"type": "integer"}}},
    },
}

_FENCE_RE = re.compile(r"^```[a-zA-Z]*\s*|\s*```$")


def _json(text: str) -> Any:
    """The first complete JSON value; text after it (a second object, a remark) is ignored."""
    body = _FENCE_RE.sub("", text.strip())
    try:
        value, _end = json.JSONDecoder().raw_decode(body)
        return value
    except json.JSONDecodeError as exc:
        raise UnparseableResponse(f"not JSON: {exc}") from exc


def parse_describe(text: str) -> FigureSemantic | str:
    """The semantic, or a string saying why a parsed answer does not fit (final, not retried)."""
    data = _json(text)
    try:
        kind = data["type"]
        summary = Evidenced(**data["summary"])
        if kind == "chart" and data.get("chart"):
            c = data["chart"]
            return ChartSemantic(
                chart_type=Evidenced(**c["chart_type"]), title=_opt(c.get("title")), x_axis=_opt(c.get("x_axis")),
                y_axis=_opt(c.get("y_axis")), unit=_opt(c.get("unit")),
                series=[Series(name=Evidenced(**s["name"]), values=[Evidenced(**v) for v in s["values"]])
                        for s in c.get("series", [])])
        if kind == "diagram" and data.get("diagram"):
            d = data["diagram"]
            return DiagramSemantic(
                diagram_type=Evidenced(**d["diagram_type"]), nodes=[Evidenced(**n) for n in d.get("nodes", [])],
                edges=[Edge(src=e["src"], dst=e["dst"], label=_opt(e.get("label")), direction=e["direction"])
                       for e in d.get("edges", [])])
        return GenericSemantic(type=kind if kind in ("photo", "seal", "other") else "other", summary=summary,
                               visible_text=[Evidenced(**t) for t in data.get("visible_text", [])])
    except (KeyError, TypeError, ValueError) as exc:
        return f"answer does not fit the description schema: {type(exc).__name__}: {exc}"


def _opt(item: dict | None) -> Evidenced | None:
    return Evidenced(**item) if item and item.get("value") is not None else None


def parse_review(text: str) -> tuple[TableGrid | None, list[tuple[int, int]], str | None]:
    """(grid, undetermined cells, problem); a parsed answer is final even when its table is unusable."""
    data = _json(text)
    try:
        html = data["table_html"]
        undetermined = [(int(r), int(c)) for r, c in data.get("undetermined", [])]
    except (KeyError, TypeError, ValueError) as exc:
        return None, [], f"answer does not fit the review schema: {exc}"
    try:
        return TableGrid.from_html(html), undetermined, None
    except ValueError as exc:
        return None, undetermined, f"candidate table is not a valid grid: {exc}"
