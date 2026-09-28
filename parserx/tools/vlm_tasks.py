"""Response schemas and parsers of the tool-internal VLM tasks (guide §6.6–§6.7).

Parsing follows guide §8.2 rule 3: a response that is not JSON is
``UnparseableResponse`` (requested once more, replayably); a response that is
JSON but does not fit the task is final and reported as a failure.
"""

from __future__ import annotations

import copy
import json
import re
from typing import Any

from parserx.ir.enums import EvidenceLevel
from parserx.ir.semantic import FIGURE_TYPES, FigureNote
from parserx.scheduling import UnparseableResponse
from parserx.tables.grid import TableGrid

_LEVELS = [level.value for level in EvidenceLevel]
DESCRIBE_SCHEMA: dict[str, Any] = {
    "type": "object", "additionalProperties": False, "required": ["type", "caption"],
    "properties": {"type": {"type": "string", "enum": list(FIGURE_TYPES)}, "caption": {"type": "string"}},
}


def describe_schema(figure_type: str | None = None) -> dict[str, Any]:
    """The description schema (a type and a caption, Q121); a caller-named type is the only type the model may answer."""
    if figure_type is None:
        return DESCRIBE_SCHEMA
    schema = copy.deepcopy(DESCRIBE_SCHEMA)
    schema["properties"]["type"]["enum"] = [figure_type]
    return schema


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


def parse_describe(text: str) -> FigureNote | str:
    """The figure's note, or a string saying why a parsed answer does not fit (final, not retried)."""
    data = _json(text)
    try:
        kind, caption = data["type"], " ".join(str(data["caption"]).split())
        if kind not in FIGURE_TYPES:
            kind = "other"
        if not caption:
            return "answer does not fit the description schema: an empty caption"
        return FigureNote(type=kind, caption=caption)
    except (KeyError, TypeError, ValueError) as exc:
        return f"answer does not fit the description schema: {type(exc).__name__}: {exc}"


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
