"""Native text lines → paragraphs (Q80, 2026-09-26).

The PDF library's own grouping of lines into blocks is a layout heuristic that changes between versions (MuPDF 1.28
cuts a line at every change of font) and fails on common layout conventions.  Paragraphs come from the page image
instead: the local layout detector's text regions, the model that already runs on every page.  Measured against
the annotations (eval_reports/2026-09-26_pdf_library_analysis.md): join F1 0.972, against 0.908 for MuPDF 1.27's
blocks and 0.935 for geometric rules alone.

- a line belongs to the smallest detected text region its centre lies in (tables and pictures are not text regions);
- inside a region, the pieces of one visual row are one line again, and the region is one paragraph — except where
  the font size changes (a title set in the same frame as its text);
- lines outside every region (code the detector did not frame, form rows), or every line when there is no detection,
  are grouped by geometry: pieces of a row within about one em, and a row joins the one above when their spacing is
  the page's own line spacing;
- rotated lines (watermarks, vertical labels) are not prose: they keep the grouping the library gave them.

The numbers below are measurement tolerances relative to the font size, not tuned on scores.
"""

from __future__ import annotations

import statistics
from collections.abc import Sequence
from typing import Protocol

from parserx.content.order import row_order
from parserx.ir.enums import BlockKind
from parserx.layout import labels

SAME_ROW = 0.5  # vertical overlap, as a share of the lower line's height, of pieces of one visual row
ROW_GAP_EM = 1.0  # outside regions: pieces of one row are at most about one em apart (a column gap is wider)
SPACING_EM = 0.35  # outside regions: spacing beyond the page's own line spacing by more than this ends a paragraph
SIZE_PT = 0.5  # font sizes closer than this are one size (rounding of the text layer)
MAX_SPACING_EM = 1.0  # lines of one paragraph are at most double-spaced: a larger typical gap is between paragraphs

_NOT_TEXT = frozenset({BlockKind.TABLE, BlockKind.FIGURE, BlockKind.OTHER})


class LineLike(Protocol):
    bbox: tuple[float, float, float, float]
    size: float
    direction: tuple[float, float]
    block: int  # the library's block index: kept only for rotated lines


Region = tuple[str, tuple[float, float, float, float]]  # (detector label, bbox in page points)


def group_lines(lines: Sequence[LineLike], regions: Sequence[Region] | None) -> list[list[int]]:
    """Paragraphs as lists of line indices, each in visual order; paragraphs ordered by their first line."""
    text_regions = [box for label, box in regions or () if labels.LAYOUT.get(label, BlockKind.OTHER) not in _NOT_TEXT]
    groups: list[list[int]] = []
    rotated: dict[tuple, list[int]] = {}
    by_region: dict[int, list[int]] = {}
    outside: list[int] = []
    for i, line in enumerate(lines):
        if not _horizontal(line):
            rotated.setdefault((line.direction, line.block), []).append(i)
            continue
        region = _region_of(line.bbox, text_regions)
        (outside if region is None else by_region.setdefault(region, [])).append(i)
    groups.extend(rotated.values())
    for members in by_region.values():
        groups.extend(_split_at_size_changes(lines, _rows(lines, members, max_gap=None)))
    horizontal = [i for i, line in enumerate(lines) if _horizontal(line)]
    groups.extend(_by_spacing(lines, outside, _line_spacing(lines, horizontal)))
    return sorted((_visual(lines, g) for g in groups if g), key=min)


def _horizontal(line: LineLike) -> bool:
    return abs(line.direction[0] - 1.0) < 0.01 and abs(line.direction[1]) < 0.01


def _region_of(bbox, regions) -> int | None:
    cx, cy = (bbox[0] + bbox[2]) / 2, (bbox[1] + bbox[3]) / 2
    hits = [k for k, r in enumerate(regions) if r[0] <= cx <= r[2] and r[1] <= cy <= r[3]]
    return min(hits, key=lambda k: ((regions[k][2] - regions[k][0]) * (regions[k][3] - regions[k][1]), k), default=None)


def _rows(lines, members: list[int], *, max_gap: float | None) -> list[list[int]]:
    """Visual rows of *members*, top to bottom: pieces that overlap vertically (and, with *max_gap* ems, lie close)."""
    rows: list[list[int]] = []
    for i in sorted(members, key=lambda i: (lines[i].bbox[1], lines[i].bbox[0], i)):
        b = lines[i].bbox
        for row in rows:
            r = _box(lines, row)
            height = min(b[3] - b[1], r[3] - r[1])
            if height <= 0 or min(b[3], r[3]) - max(b[1], r[1]) < SAME_ROW * height:
                continue
            gap = max(b[0] - r[2], r[0] - b[2])
            if max_gap is None or gap <= max_gap * max(lines[i].size, _size(lines, row), 1.0):
                row.append(i)
                break
        else:
            rows.append([i])
    return sorted(rows, key=lambda r: (_box(lines, r)[1], _box(lines, r)[0]))


def _split_at_size_changes(lines, rows: list[list[int]]) -> list[list[int]]:
    parts: list[list[int]] = []
    for row in rows:
        if parts and abs(_size(lines, row) - _size(lines, parts[-1])) <= SIZE_PT:
            parts[-1].extend(row)
        else:
            parts.append(list(row))
    return parts


def _line_spacing(lines, members: list[int]) -> float:
    """The page's own line spacing in ems: the median gap between a row and the next row under it, same size."""
    rows = _rows(lines, members, max_gap=ROW_GAP_EM)
    boxes = [_box(lines, r) for r in rows]
    gaps = [(boxes[j][1] - boxes[k][3]) / max(_size(lines, rows[k]), 1.0) for k in range(len(rows))
            if (j := _next_below(boxes, k)) is not None and abs(_size(lines, rows[k]) - _size(lines, rows[j])) <= SIZE_PT]
    return min(statistics.median(gaps), MAX_SPACING_EM) if gaps else 0.5


def _by_spacing(lines, members: list[int], spacing: float) -> list[list[int]]:
    rows = _rows(lines, members, max_gap=ROW_GAP_EM)
    boxes = [_box(lines, r) for r in rows]
    parent = list(range(len(rows)))

    def find(k):
        while parent[k] != k:
            parent[k] = parent[parent[k]]
            k = parent[k]
        return k

    for k in range(len(rows)):
        j = _next_below(boxes, k)
        if j is None or abs(_size(lines, rows[k]) - _size(lines, rows[j])) > SIZE_PT:
            continue
        if (boxes[j][1] - boxes[k][3]) / max(_size(lines, rows[k]), 1.0) <= spacing + SPACING_EM:
            parent[find(k)] = find(j)
    out: dict[int, list[int]] = {}
    for k, row in enumerate(rows):
        out.setdefault(find(k), []).extend(row)
    return list(out.values())


def _next_below(boxes, k) -> int | None:
    """The nearest row under row *k* that overlaps it horizontally."""
    b = boxes[k]
    under = [j for j, c in enumerate(boxes) if j != k and c[1] >= b[1] + 0.5 * (b[3] - b[1])
             and min(b[2], c[2]) - max(b[0], c[0]) > 0]
    return min(under, key=lambda j: (boxes[j][1], j), default=None)


def _box(lines, members):
    bs = [lines[i].bbox for i in members]
    return (min(b[0] for b in bs), min(b[1] for b in bs), max(b[2] for b in bs), max(b[3] for b in bs))


def _size(lines, members) -> float:
    return max(lines[i].size for i in members)


def _visual(lines, members: list[int]) -> list[int]:
    return [members[k] for k in row_order([lines[i].bbox for i in members])]
