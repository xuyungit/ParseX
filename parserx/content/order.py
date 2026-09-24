"""Reading order of regions on one page, by position (guide §6.3, Q33).

``reading_order`` is a recursive XY cut that looks for columns first:

1. **Gutter.** In a region, find a vertical strip that no column-sized box
   crosses (boxes wider than 60 % of the region are full width and do not
   count; neither do slivers such as a page number sitting in the gutter).
2. **Sections.** Boxes that overlap the gutter are full-width breaks: they cut
   the region into sections, read top to bottom with the breaks in between.
   Inside a section the left side is read before the right side, each side
   ordered recursively (a side may hold columns of its own).
   When no gutter is clean, the boxes over the least-covered position in the
   middle of the region (centred titles, equations) may be set aside as
   breaks, if they are few.
3. **Guards.** A gutter only counts as a column split when both sides are
   substantial (each at least 20 % of the region's width and covering at
   least a quarter of the height the sides span) and side by side (their
   vertical extents overlap by at least half of the shorter one).  When the two sides pair up
   line by line, as in forms and unruled tables, the split also needs both
   sides to look like column text: at least three boxes each, sides of
   similar width, and most boxes spanning at least half of their side.
4. **Bands.** A region without a column split is cut at its horizontal gaps
   into bands, read top to bottom; neighbouring bands are joined again while
   together they still split into columns (a figure above two columns stays
   out of them; a gap that happens to run across both columns cuts nothing).
5. What is left is read by ``row_order``: regions top to bottom, regions
   whose vertical extents overlap by more than half of the shorter one form a
   row read left to right.  This matches v1's provider order and fixes content
   streams written out of visual order.

Scan-engine regions keep the engine's own order (``content/scan.py``).
"""

from __future__ import annotations

from parserx.ir.base import BBox

FULL_WIDTH = 0.6  # a box wider than this share of its region is full width
SLIVER = 0.05  # a box narrower than this share of the region does not block a gutter
MIN_GUTTER = 0.015  # a gutter is at least this share of the region's width
CENTRAL = (0.2, 0.8)  # where a gutter crossed by a few boxes may lie, as shares of the region's width
MAX_CROSSING = 0.2  # crossing boxes: at most this share of the column-sized boxes' total height
MIN_SIDE_WIDTH = 0.2
MIN_SIDE_COVER = 0.25
MIN_SIDE_BY_SIDE = 0.5  # the sides' vertical extents overlap by at least this share of the shorter one
ROW_PAIRED = 0.5  # paired sides: more than this share of the larger side pairs with a box on the same line
MIN_PAIRED_LINES = 3  # paired sides: column text needs at least this many boxes a side,
MIN_BALANCE = 0.6  # sides of similar width (narrower / wider),
MIN_LONG = 0.6  # and this share of each side's boxes at least half as wide as the side


def reading_order(boxes: list[BBox]) -> list[int]:
    """Indices of *boxes* in reading order (deterministic for equal positions)."""
    return _order(list(range(len(boxes))), boxes)


def row_order(boxes: list[BBox], indices: list[int] | None = None) -> list[int]:
    """Top to bottom by rows, left to right within a row."""
    indices = list(range(len(boxes))) if indices is None else indices
    by_top = sorted(indices, key=lambda i: (boxes[i][1], boxes[i][0], i))
    rows: list[list[int]] = []
    for i in by_top:
        if rows and _vertical_overlap(boxes[rows[-1][-1]], boxes[i]) > 0.5:
            rows[-1].append(i)
        else:
            rows.append([i])
    return [i for row in rows for i in sorted(row, key=lambda j: (boxes[j][0], boxes[j][1], j))]


def _order(indices: list[int], boxes: list[BBox]) -> list[int]:
    split = _column_split(indices, boxes)
    if split is not None:
        return _by_columns(indices, boxes, split)
    bands = _bands(indices, boxes)
    if len(bands) < 2:
        return row_order(boxes, indices)
    # Neighbouring bands join while together they still split into columns: a gap that happens to run
    # across both columns must not cut them into pieces read side by side.
    groups: list[list[int]] = [bands[0]]
    for band in bands[1:]:
        if _column_split(groups[-1] + band, boxes) is not None:
            groups[-1] = groups[-1] + band
        else:
            groups.append(band)
    return [i for group in groups for i in _order(group, boxes)]


def _by_columns(indices: list[int], boxes: list[BBox], split: tuple[float, float]) -> list[int]:
    lo, hi = split
    middle = (lo + hi) / 2
    breaks = sorted((i for i in indices if boxes[i][0] < hi and boxes[i][2] > lo),
                    key=lambda i: (boxes[i][1], boxes[i][0], i))
    sections: list[list[int]] = [[] for _ in range(len(breaks) + 1)]
    for i in indices:
        if i in breaks:
            continue
        centre = (boxes[i][1] + boxes[i][3]) / 2
        sections[sum(1 for b in breaks if (boxes[b][1] + boxes[b][3]) / 2 < centre)].append(i)
    result: list[int] = []
    for k, section in enumerate(sections):
        left = [i for i in section if (boxes[i][0] + boxes[i][2]) / 2 < middle]
        right = [i for i in section if (boxes[i][0] + boxes[i][2]) / 2 >= middle]
        result += _order(left, boxes) + _order(right, boxes)
        if k < len(breaks):
            result.append(breaks[k])
    return result


def _bands(indices: list[int], boxes: list[BBox]) -> list[list[int]]:
    """The region cut at every horizontal gap no box crosses, top to bottom."""
    bands: list[list[int]] = []
    reach = None
    for i in sorted(indices, key=lambda k: (boxes[k][1], boxes[k][0], k)):
        if reach is None or boxes[i][1] > reach:
            bands.append([i])
            reach = boxes[i][3]
        else:
            bands[-1].append(i)
            reach = max(reach, boxes[i][3])
    return bands


def _column_split(indices: list[int], boxes: list[BBox]) -> tuple[float, float] | None:
    """The gutter of the region that passes the guards, as (left edge, right edge), or None."""
    if len(indices) < 2:
        return None
    x0, x1 = min(boxes[i][0] for i in indices), max(boxes[i][2] for i in indices)
    width = x1 - x0
    if width <= 0:
        return None
    column_sized = [i for i in indices if SLIVER * width <= _width(boxes[i]) <= FULL_WIDTH * width]
    # First a clean gutter; then one that a few centred boxes (titles, equations) cross, those boxes set aside.
    for blockers in ((), _crossers(column_sized, boxes, x0, width)):
        free = [i for i in column_sized if i not in blockers]
        for lo, hi in _gaps(free, boxes, MIN_GUTTER * width):
            left = [i for i in indices if boxes[i][2] <= lo]
            right = [i for i in indices if boxes[i][0] >= hi]
            if _is_column_split(left, right, boxes, width):
                return lo, hi
    return None


def _crossers(column_sized: list[int], boxes: list[BBox], x0: float, width: float) -> tuple[int, ...]:
    """The boxes over the least-covered position in the middle of the region, when they are few."""
    edges = sorted({boxes[i][0] for i in column_sized} | {boxes[i][2] for i in column_sized})
    best: tuple[float, float, tuple[int, ...]] | None = None
    for a, b in zip(edges, edges[1:]):
        middle = (a + b) / 2
        if not CENTRAL[0] * width <= middle - x0 <= CENTRAL[1] * width:
            continue
        over = tuple(i for i in column_sized if boxes[i][0] < b and boxes[i][2] > a)
        height = sum(boxes[i][3] - boxes[i][1] for i in over)
        if best is None or height < best[0]:
            best = (height, middle, over)
    total = sum(boxes[i][3] - boxes[i][1] for i in column_sized)
    if best is None or not best[2] or best[0] > MAX_CROSSING * total:
        return ()
    return best[2]


def _gaps(indices: list[int], boxes: list[BBox], min_width: float) -> list[tuple[float, float]]:
    """Vertical strips no box of *indices* crosses, widest first."""
    spans = sorted((boxes[i][0], boxes[i][2]) for i in indices)
    gaps: list[tuple[float, float]] = []
    reach = spans[0][1] if spans else 0.0
    for start, end in spans[1:]:
        if start - reach >= min_width:
            gaps.append((reach, start))
        reach = max(reach, end)
    return sorted(gaps, key=lambda g: (-(g[1] - g[0]), g[0]))


def _is_column_split(left: list[int], right: list[int], boxes: list[BBox], width: float) -> bool:
    if not left or not right:
        return False
    sides = [_extent(left, boxes), _extent(right, boxes)]
    if any(s[2] - s[0] < MIN_SIDE_WIDTH * width for s in sides):
        return False
    top = min(s[1] for s in sides)
    height = max(s[3] for s in sides) - top
    if height <= 0 or any(_cover(side, boxes) < MIN_SIDE_COVER * height for side in (left, right)):
        return False
    if _vertical_overlap(*sides) < MIN_SIDE_BY_SIDE:  # columns run side by side, not one after the other
        return False
    if _paired(left, right, boxes) <= ROW_PAIRED:
        return True
    # The sides pair up line by line, as in forms and unruled tables, but also in two text columns set on the
    # same line grid.  Column text: several lines a side, sides of similar width, most lines spanning their side.
    widths = [s[2] - s[0] for s in sides]
    return (min(len(left), len(right)) >= MIN_PAIRED_LINES and min(widths) >= MIN_BALANCE * max(widths)
            and all(_long_share(side, w, boxes) >= MIN_LONG for side, w in zip((left, right), widths)))


def _paired(left: list[int], right: list[int], boxes: list[BBox]) -> float:
    """Share of the larger side's boxes that pair one to one with a box of the other side starting on the same line."""
    free = sorted(right, key=lambda j: (boxes[j][1], j))
    pairs = 0
    for i in sorted(left, key=lambda k: (boxes[k][1], k)):
        a = boxes[i]
        match = next((j for j in free if abs(a[1] - boxes[j][1]) <= 0.3 * max(1e-6, min(a[3] - a[1], boxes[j][3] - boxes[j][1]))), None)
        if match is not None:
            free.remove(match)
            pairs += 1
    return pairs / max(len(left), len(right))


def _long_share(side: list[int], side_width: float, boxes: list[BBox]) -> float:
    """Share of the side's boxes at least half as wide as the side."""
    return sum(1 for i in side if _width(boxes[i]) >= 0.5 * side_width) / len(side)


def _cover(side: list[int], boxes: list[BBox]) -> float:
    """Length of the union of the side's vertical extents."""
    total, reach = 0.0, None
    for top, bottom in sorted((boxes[i][1], boxes[i][3]) for i in side):
        if reach is None or top > reach:
            total += bottom - top
            reach = bottom
        elif bottom > reach:
            total += bottom - reach
            reach = bottom
    return total


def _extent(side: list[int], boxes: list[BBox]) -> BBox:
    return (min(boxes[i][0] for i in side), min(boxes[i][1] for i in side),
            max(boxes[i][2] for i in side), max(boxes[i][3] for i in side))


def _width(box: BBox) -> float:
    return box[2] - box[0]


def _vertical_overlap(a: BBox, b: BBox) -> float:
    shorter = min(a[3] - a[1], b[3] - b[1])
    if shorter <= 0:
        return 0.0
    return max(0.0, min(a[3], b[3]) - max(a[1], b[1])) / shorter
