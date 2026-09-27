"""Evidence: what was looked at in the source and what was seen there (Q85).

Every look at the source (``view_source``) leaves one: the image looked at, the question and the service VLM's
answer, or a reading of the source by an engine (text, a table, a figure's description).  Evidence never changes the
draft; a change that rests on the source names its evidence, and the program checks the change against it — an
edit must rest on an image of its place, an adopted reading is exactly what was read.  Evidence lives in the
state (its digest guards it) and travels to the sidecar: why a change was made stays traceable.
"""

from __future__ import annotations

import hashlib
import json
from typing import Literal

from parserx.ir.base import BBox, IRModel
from parserx.ir.semantic import FigureSemantic
from parserx.tables.grid import TableGrid

How = Literal["image", "answer", "text", "table", "description"]


class Evidence(IRModel):
    id: str  # "e-" + digest of how it was looked at and what was seen
    how: How
    block: str | None = None  # the block looked at (its crop or its image)
    page: int | None = None  # the whole page, or the region bbox of it
    bbox: BBox | None = None  # a region of the page, in page points
    seam: int | None = None  # page N's bottom half above page N + 1's top half
    rows: tuple[int, int] | None = None  # a band of a table's rows
    image: str | None = None  # the image looked at (asset id)
    question: str | None = None
    answer: str | None = None  # the VLM's answer; for a text reading, the text read
    cells: TableGrid | None = None  # a table reading
    undetermined: list[tuple[int, int]] = []  # a table reading: cells the reader could not determine
    semantic: FigureSemantic | None = None  # a figure's description
    reading: str | None = None  # a text reading's engine response, stored in the workspace (path)
    reading_sha256: str | None = None
    engine: str | None = None  # the reader of a text, table or description reading, with its version
    raw_ref: str | None = None  # the engine request's cache key


def evidence_id(how: str, target: dict, seen: object) -> str:
    """Stable: the same look with the same result is the same evidence."""
    key = json.dumps({"how": how, "target": target, "seen": seen}, sort_keys=True, ensure_ascii=False, default=str)
    return "e-" + hashlib.sha256(key.encode("utf-8")).hexdigest()[:12]
