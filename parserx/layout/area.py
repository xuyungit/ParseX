"""Area statistics for image routing (guide §6.5 step 3).

Both fractions are over the whole image.  ``t`` is the union of text-like
boxes (text, titles, lists, tables, captions, notes, furniture, formulas);
``f`` the union of figure-like boxes (images, charts, seals).  A text box
lying inside a figure box is a label of that figure and counts only for ``f``.
White space counts for neither.
"""

from __future__ import annotations

import numpy as np

from parserx.ir.enums import BlockKind
from parserx.layout import labels
from parserx.layout.detector import Region

TEXT_KINDS = frozenset({BlockKind.TEXT, BlockKind.TITLE, BlockKind.LIST, BlockKind.TABLE, BlockKind.CAPTION,
                        BlockKind.FOOTNOTE, BlockKind.FORMULA, BlockKind.HEADER, BlockKind.FOOTER,
                        BlockKind.PAGE_NUMBER})
FIGURE_KINDS = frozenset({BlockKind.FIGURE})
_INSIDE = 0.9  # share of a text box's area inside a figure box for it to be the figure's label
_GRID = 400  # union areas are measured on a grid of at most this many cells per side


def coverage(regions: list[Region], *, width: int, height: int, source: str) -> tuple[float, float]:
    if width <= 0 or height <= 0:
        return 0.0, 0.0
    figures = [r for r in regions if labels.to_kind(source, r.label) in FIGURE_KINDS]
    texts = [r for r in regions if labels.to_kind(source, r.label) in TEXT_KINDS
             and not any(_inside(r.bbox, f.bbox) for f in figures)]
    scale = min(1.0, _GRID / max(width, height))
    shape = (max(1, round(height * scale)), max(1, round(width * scale)))
    return _union(texts, shape, scale), _union(figures, shape, scale)


def _union(regions: list[Region], shape: tuple[int, int], scale: float) -> float:
    if not regions:
        return 0.0
    mask = np.zeros(shape, dtype=bool)
    for r in regions:
        x0, y0, x1, y1 = (int(round(v * scale)) for v in r.bbox)
        mask[max(0, y0):max(0, y1), max(0, x0):max(0, x1)] = True
    return round(float(mask.mean()), 4)


def _inside(inner, outer) -> bool:
    area = max(0.0, inner[2] - inner[0]) * max(0.0, inner[3] - inner[1])
    if area <= 0:
        return False
    w = max(0.0, min(inner[2], outer[2]) - max(inner[0], outer[0]))
    h = max(0.0, min(inner[3], outer[3]) - max(inner[1], outer[1]))
    return w * h / area >= _INSIDE
