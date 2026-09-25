"""The local page reading step of ``process`` (guide §9.5, Q56).

Every PDF page render is read by the local recognizer (``reading/local.py``) and stored as a PageReading with
the regions the layout detector calls picture or formula content.  The reading is evidence for the two-way
comparison with the output (``reading/compare.py``, listed by ``unresolved_items``); it never becomes output.
Positions are stored in page points of the unrotated page, like every PdfAnchor.  Readings and detections
are local and kept in the derived cache, so a replay does not load the models.  DOCX has no page renders yet
(Q50): nothing is read.
"""

from __future__ import annotations

import fitz

from parserx.content.scan import render_page_at
from parserx.ir.state import DocumentState, PageReading, ReadLine
from parserx.layout.detector import detect_cached
from parserx.layout.labels import NOT_PROSE
from parserx.reading.local import read_cached
from parserx.runtimes.events import Step
from parserx.tools.context import ToolContext


def reading_todo(state: DocumentState) -> list[int]:
    """PDF pages without a local reading."""
    if state.format != "pdf":
        return []
    done = {r.n for r in state.readings}
    return [p.n for p in state.pages if p.n not in done]


def read_pages(ctx: ToolContext, pages: list[int]) -> int:
    """Read *pages* locally and store the readings; the number of pages read."""
    reader, detector, cache = ctx.reader(), ctx.detector(), ctx.cache
    dpi, layout_dpi = ctx.config.tools.reading_dpi, ctx.config.layout.page_dpi
    readings = []
    with fitz.open(ctx.ws.source_path) as doc:
        for n in pages:
            back = doc[n - 1].derotation_matrix
            png, _, _ = render_page_at(doc, n, dpi)
            lines = [ReadLine(bbox=_page_box(box, dpi, back), text=text, score=score)
                     for box, text, score in read_cached(reader, png, cache)]
            layout_png, _, _ = render_page_at(doc, n, layout_dpi)
            not_prose = [_page_box(r.bbox, layout_dpi, back) for r in detect_cached(detector, layout_png, cache)
                         if r.label in NOT_PROSE]
            readings.append(PageReading(n=n, engine=reader.version, dpi=dpi, lines=lines, not_prose=not_prose))
            ctx.report(Step("process", "reading", done=len(readings), total=len(pages)))
    with ctx.ws.txn("tool:process:reading") as state:
        done = {r.n for r in state.readings}
        state.readings = sorted([*state.readings, *(r for r in readings if r.n not in done)], key=lambda r: r.n)
        state.engines["reading"] = reader.version
    return len(readings)


def _page_box(box, dpi: int, back: fitz.Matrix) -> tuple[float, float, float, float]:
    """A box in render pixels as page points of the unrotated page."""
    k = 72.0 / dpi
    rect = fitz.Rect(box[0] * k, box[1] * k, box[2] * k, box[3] * k) * back
    return tuple(round(v, 1) for v in (rect.x0, rect.y0, rect.x1, rect.y1))
