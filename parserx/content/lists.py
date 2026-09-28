"""Bulleted list items (R1): paragraphs the page marks with a bullet become list items (``- …`` in the Markdown).

A paragraph is a bulleted item when the page shows a bullet before its first line:

- its text starts with a bullet character (the closed set ``BULLETS``: a correctness mapping of the Unicode
  bullet and square characters and the symbol fonts' private-use bullets, not a list of words) — the text layer
  or the scan engine read the mark;
- on a native page, a small filled shape is drawn left of its first line — no wider than ``MARK_EM`` of the
  text's size, about as wide as high, vertically centred on the line, at most ``GAP_EM`` before the first
  glyph (a printed web page draws its bullets as vector dots, with nothing in the text layer);
- on a scanned page, the first ink left of the first line is a solid mark — nearly square, filled, about the
  height of a stroke, followed by a clear gap before the words (the scan engine may miss a ■ it read elsewhere
  on the same page).

Only shown text paragraphs: titles, tables, code and captions keep their roles.  Numbered items ("1.", "（1）")
are left as written; the output keeps their numbers.  The text is not changed: the bullet stays in the text when
the page's reading has it, and rendering writes ``- `` in its place.
"""

from __future__ import annotations

import re
from pathlib import Path

from parserx.ir.anchor import PdfAnchor
from parserx.ir.block import Block
from parserx.ir.decision import Decision
from parserx.ir.enums import BlockKind, DecisionStage
from parserx.ir.rotation import shown
from parserx.ir.state import DocumentState
from parserx.reading.compare import _centre, _inside
from parserx.workspace.queries import HIDDEN

ACTOR = "program:content.lists"
BULLETS = frozenset("•●○◦■□▪▫◆◇►▶➢‣⁃✓✔◾◼⦁")
MARK_EM = 0.6  # a drawn bullet is at most this share of the text size wide and high
GAP_EM = 2.0  # and ends at most this many text sizes before the first glyph
SCAN_DPI = 200
SOLID = 0.85  # share of a scanned mark's box that is ink
_NATIVE, _SCAN = "native_pdf", "paddleocr"
_WORDS = re.compile(r"[^\W\d_]{2}")


def mark_list_items(state: DocumentState, source: Path | None) -> list[str]:
    """Set the bulleted paragraphs of a PDF as list items; the ids of the blocks set."""
    if state.format != "pdf":
        return []
    marked: list[str] = []
    drawings: dict[int, list] = {}
    images: dict[int, tuple] = {}
    readings = {r.n: r for r in state.readings if r.lines}
    pages = {p.n: p for p in state.pages}
    doc = None
    try:
        for block in state.blocks:
            anchor = block.anchors[0] if block.anchors else None
            if block.kind != BlockKind.TEXT or block.status in HIDDEN or not _WORDS.search(strip_bullet(block.text or "")) \
                    or not isinstance(anchor, PdfAnchor) or anchor.coord_space != "page_pt" or _code(block):
                continue  # an item says something: a diagram's "..." beside a dot is no list
            how = "character" if block.text.lstrip()[:1] in BULLETS else None
            engine = _engine(block)
            if how is None and source is not None and engine in (_NATIVE, _SCAN):
                if doc is None:
                    import pymupdf

                    doc = pymupdf.open(source)
                page = doc[anchor.page - 1]
                if engine == _NATIVE:
                    if anchor.page not in drawings:
                        drawings[anchor.page] = [d["rect"] for d in page.get_drawings() if d.get("fill") is not None]
                    how = "drawn" if _drawn_mark(drawings[anchor.page], anchor.bbox, _size(block)) else None
                elif anchor.page in readings:  # compared as the page is shown, like its render
                    if anchor.page not in images:
                        images[anchor.page] = _render(page)
                    state_page = pages.get(anchor.page)
                    box = shown(state_page, anchor.bbox)
                    height = _first_line_height([shown(state_page, ln.bbox) for ln in readings[anchor.page].lines], box)
                    how = "image" if height and _scanned_mark(images[anchor.page], box, height) else None
            if how is not None:
                block.kind = BlockKind.LIST
                block.decisions.append(Decision(stage=DecisionStage.STRUCTURE, choice="list", actor=ACTOR,
                                                reason=f"a bullet before the first line ({how})",
                                                evidence={"bullet": how}))
                marked.append(block.id)
    finally:
        if doc is not None:
            doc.close()
    return marked


def bulleted(block: Block) -> bool:
    """A list item the page marks with a bullet (rendered ``- ``), as opposed to a numbered or a Word list item."""
    return block.kind == BlockKind.LIST and ((block.text or "").lstrip()[:1] in BULLETS
                                              or any(d.actor == ACTOR and d.choice == "list" for d in block.decisions))


def strip_bullet(text: str) -> str:
    stripped = text.lstrip()
    return stripped[1:].lstrip() if stripped[:1] in BULLETS else text


def _drawn_mark(marks: list, box, size: float) -> bool:
    """A small filled shape left of the block's first line, vertically centred on it."""
    x0, y0 = box[0], box[1]
    centre = y0 + 0.6 * size  # the middle of the first line
    for mark in marks:
        w, h = mark.x1 - mark.x0, mark.y1 - mark.y0
        if 0 < w <= MARK_EM * size and 0 < h <= MARK_EM * size and 0.6 <= w / h <= 1.6 \
                and mark.x1 <= x0 + 0.1 * size and x0 - mark.x1 <= GAP_EM * size \
                and abs((mark.y0 + mark.y1) / 2 - centre) <= 0.35 * size:
            return True
    return False


def _render(page):
    import numpy as np
    import pymupdf

    pix = page.get_pixmap(dpi=SCAN_DPI, colorspace=pymupdf.csGRAY)
    return np.frombuffer(pix.samples, np.uint8).reshape(pix.height, pix.width), SCAN_DPI / 72.0


def _scanned_mark(image, box, line_height: float) -> bool:
    """The first ink of the block's first line is a solid, nearly square mark followed by a clear gap."""
    import numpy as np

    pixels, k = image
    lh = line_height * k
    x0, y0 = int(max(0, box[0] * k - 0.3 * lh)), int(max(0, box[1] * k))
    band = pixels[y0:int(y0 + 1.2 * lh), x0:int(x0 + 3.5 * lh)] < 128
    cols = np.where(band.any(axis=0))[0]
    if cols.size == 0:
        return False
    runs = np.split(cols, np.where(np.diff(cols) > max(2, int(0.15 * lh)))[0] + 1)
    first = runs[0]
    rows = np.where(band[:, first[0]:first[-1] + 1].any(axis=1))[0]
    w, h = first[-1] - first[0] + 1, rows[-1] - rows[0] + 1
    fill = band[rows[0]:rows[-1] + 1, first[0]:first[-1] + 1].mean()
    gap = runs[1][0] - first[-1] if len(runs) > 1 else 0
    return (0.25 * lh <= h <= 0.9 * lh and abs(w - h) <= 0.3 * max(w, h) and fill >= SOLID
            and gap >= 0.5 * w)


def _first_line_height(lines, box) -> float | None:
    inside = sorted((ln for ln in lines if _inside(_centre(ln), box)), key=lambda ln: ln[1])
    return inside[0][3] - inside[0][1] if inside else None


def _size(block: Block) -> float:
    chosen = next((o for o in block.observations if o.id == block.chosen_observation), None)
    return (chosen.style.font_size if chosen is not None and chosen.style and chosen.style.font_size else 10.0)


def _engine(block: Block) -> str | None:
    engines = {o.engine for o in block.observations}
    return _NATIVE if _NATIVE in engines else _SCAN if _SCAN in engines else None


def _code(block: Block) -> bool:
    chosen = next((o for o in block.observations if o.id == block.chosen_observation), None)
    return chosen is not None and chosen.style is not None and bool(chosen.style.monospace)
