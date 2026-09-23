"""Reading order of regions on one page, by position (Phase 1).

Regions are sorted top to bottom; regions whose vertical extents overlap by
more than half of the shorter one form a row and are read left to right.  This
matches v1's provider order and fixes content streams written out of visual
order.  It does not detect columns; Phase 4 takes reading order from the
layout detector.
"""

from __future__ import annotations

from parserx.ir.base import BBox


def _vertical_overlap(a: BBox, b: BBox) -> float:
    shorter = min(a[3] - a[1], b[3] - b[1])
    if shorter <= 0:
        return 0.0
    return max(0.0, min(a[3], b[3]) - max(a[1], b[1])) / shorter


def reading_order(boxes: list[BBox]) -> list[int]:
    """Indices of *boxes* in reading order (deterministic for equal positions)."""
    by_top = sorted(range(len(boxes)), key=lambda i: (boxes[i][1], boxes[i][0], i))
    rows: list[list[int]] = []
    for i in by_top:
        if rows and _vertical_overlap(boxes[rows[-1][-1]], boxes[i]) > 0.5:
            rows[-1].append(i)
        else:
            rows.append([i])
    return [i for row in rows for i in sorted(row, key=lambda j: (boxes[j][0], boxes[j][1], j))]
