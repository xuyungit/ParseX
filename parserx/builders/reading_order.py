"""ReadingOrderBuilder — detect multi-column layout and reorder elements.

Geometric column detection: analyze text element bboxes to find a vertical
gutter (column gap).  When detected with sufficient confidence, reorder
elements so that left-column content comes before right-column content
within each vertical zone (delimited by full-width elements like titles).

Stores per-element metadata:
- ``column``: "left" | "right" | "full_width"
- ``column_right_margin``: right edge of the element's column (float)
"""

from __future__ import annotations

import logging
import statistics
from dataclasses import dataclass

from parserx.config.schema import ReadingOrderConfig
from parserx.models.elements import Document, Page, PageElement

log = logging.getLogger(__name__)

# ── Column detection parameters ───────────────────────────────────────

_MIN_ELEMENTS = 6          # Minimum text elements to attempt detection
_FULL_WIDTH_RATIO = 0.60   # Element wider than this × page_width = full-width
_GUTTER_POSITION_LO = 0.30 # Gutter must be between 30%–70% of page width
_GUTTER_POSITION_HI = 0.70
_MIN_GUTTER_WIDTH = 5.0    # Minimum gap width in points
_MIN_SIDE_ELEMENTS = 3     # Each side of gutter needs at least this many
_CONFIDENCE_THRESHOLD = 0.6


@dataclass
class ColumnLayout:
    """Detected column structure for a single page."""

    column_count: int
    gutter_x: float           # Center x of the gutter
    gutter_width: float       # Width of the gutter gap
    left_col_right: float     # Max right edge of left-column elements
    right_col_right: float    # Max right edge of right-column elements
    confidence: float


def _has_bbox(elem: PageElement) -> bool:
    return elem.bbox != (0.0, 0.0, 0.0, 0.0)


def detect_columns(
    elements: list[PageElement],
    page_width: float,
) -> ColumnLayout | None:
    """Detect two-column layout from element bboxes.

    Uses a "physical gutter" approach: scan the central region of the
    page for a vertical band where few element bboxes overlap.  This is
    more robust than midpoint-gap analysis when small elements (figure
    labels, footnote markers) cluster near the column boundary.

    Returns ``None`` for single-column pages or when confidence is below
    the threshold.
    """
    if page_width <= 0:
        return None

    # Collect text elements with valid bboxes (all types, not just narrow).
    text_elems: list[PageElement] = []
    for elem in elements:
        if elem.type != "text" or not _has_bbox(elem):
            continue
        text_elems.append(elem)

    if len(text_elems) < _MIN_ELEMENTS:
        return None

    # Scan the central band of the page for a vertical gap.
    # We look for an x-position where the fewest element bboxes overlap.
    scan_lo = page_width * _GUTTER_POSITION_LO
    scan_hi = page_width * _GUTTER_POSITION_HI
    step = max(1.0, page_width / 200)  # ~200 sample points

    # Build a list of "column-body" elements: text blocks that are
    # clearly part of a column (not full-width, not tiny fragments).
    col_body: list[PageElement] = []
    for elem in text_elems:
        width = elem.bbox[2] - elem.bbox[0]
        if width > page_width * _FULL_WIDTH_RATIO:
            continue  # Skip full-width elements
        if width < page_width * 0.15:
            continue  # Skip tiny fragments (page numbers, figure labels)
        col_body.append(elem)

    if len(col_body) < _MIN_ELEMENTS:
        return None

    # For each candidate x, count how many column-body elements span it.
    best_x = 0.0
    best_count = len(col_body) + 1

    x = scan_lo
    while x <= scan_hi:
        count = sum(1 for e in col_body if e.bbox[0] <= x <= e.bbox[2])
        if count < best_count:
            best_count = count
            best_x = x
        x += step

    # If the minimum overlap count is too high, no gutter exists.
    # Allow up to 2 overlapping elements (figure captions, etc.).
    if best_count > 2:
        return None

    # Refine gutter boundaries: find the widest gap around best_x.
    gutter_left = best_x
    gutter_right = best_x
    for elem in text_elems:
        width = elem.bbox[2] - elem.bbox[0]
        if width > page_width * _FULL_WIDTH_RATIO:
            continue
        # Elements ending just left of gutter define its left edge.
        if elem.bbox[2] <= best_x and elem.bbox[2] > gutter_left - page_width * 0.3:
            gutter_left = max(gutter_left, elem.bbox[2])
        # Elements starting just right of gutter define its right edge.
        if elem.bbox[0] >= best_x and elem.bbox[0] < gutter_right + page_width * 0.3:
            gutter_right = min(gutter_right, elem.bbox[0]) if gutter_right != best_x else elem.bbox[0]

    # Re-scan to find precise gutter edges using column-body elements.
    left_edges: list[float] = []
    right_edges: list[float] = []
    for elem in col_body:
        xcenter = (elem.bbox[0] + elem.bbox[2]) / 2
        if xcenter < best_x:
            left_edges.append(elem.bbox[2])
        else:
            right_edges.append(elem.bbox[0])

    if not left_edges or not right_edges:
        return None

    gutter_left = max(left_edges)
    gutter_right = min(right_edges)
    gutter_width = gutter_right - gutter_left
    gutter_center = (gutter_left + gutter_right) / 2

    # Count elements on each side.
    left_count = len(left_edges)
    right_count = len(right_edges)

    if left_count < _MIN_SIDE_ELEMENTS or right_count < _MIN_SIDE_ELEMENTS:
        return None

    # Compute per-column right margins.
    left_col_right = max(left_edges)  # = gutter_left
    right_col_rights = [
        e.bbox[2] for e in col_body
        if (e.bbox[0] + e.bbox[2]) / 2 > best_x
    ]
    right_col_right = max(right_col_rights) if right_col_rights else gutter_right

    # Confidence scoring.
    confidence = 1.0

    # Gutter near center?
    center_ratio = gutter_center / page_width
    center_deviation = abs(center_ratio - 0.5)
    if center_deviation > 0.15:
        confidence *= 0.7

    # Clear physical gutter?
    if gutter_width < _MIN_GUTTER_WIDTH:
        confidence *= 0.5

    # Zero-overlap gutter is strong evidence.
    if best_count == 0:
        confidence *= 1.0
    else:
        confidence *= 0.7

    if confidence < _CONFIDENCE_THRESHOLD:
        return None

    return ColumnLayout(
        column_count=2,
        gutter_x=gutter_center,
        gutter_width=max(gutter_width, 0),
        left_col_right=left_col_right,
        right_col_right=right_col_right,
        confidence=confidence,
    )


def detect_columns_with_hint(
    elements: list[PageElement],
    page_width: float,
    hint_gutter_x: float,
) -> ColumnLayout | None:
    """Relaxed column detection using a known gutter position as a hint.

    Used for document-level propagation: when a majority of pages have a
    detected gutter, apply that gutter to pages where normal detection fails
    (too few elements, side imbalance, or gutter obstruction from figures).

    Relaxed thresholds: MIN_ELEMENTS=2, MIN_SIDE_ELEMENTS=1.
    Skips gutter scanning — uses ``hint_gutter_x`` directly.
    """
    if page_width <= 0:
        return None

    text_elems = [
        e for e in elements
        if e.type == "text" and _has_bbox(e)
    ]
    if len(text_elems) < 2:
        return None

    # Guard: skip pages dominated by tiny fragments (figure labels).
    # Reordering such pages causes more harm than good.
    col_sized = 0
    tiny_count = 0
    for e in text_elems:
        w = e.bbox[2] - e.bbox[0]
        if page_width * 0.15 <= w <= page_width * _FULL_WIDTH_RATIO:
            col_sized += 1
        elif w < page_width * 0.15:
            tiny_count += 1
    if col_sized < 4 or tiny_count > col_sized * 1.5:
        return None

    # Classify elements relative to the hint gutter.
    left_edges: list[float] = []
    right_edges: list[float] = []
    for elem in text_elems:
        width = elem.bbox[2] - elem.bbox[0]
        if width > page_width * _FULL_WIDTH_RATIO:
            continue
        if width < page_width * 0.10:
            continue  # Even more relaxed tiny-fragment filter
        xcenter = (elem.bbox[0] + elem.bbox[2]) / 2
        # Element that spans across gutter is full-width, skip
        if elem.bbox[0] < hint_gutter_x and elem.bbox[2] > hint_gutter_x:
            continue
        if xcenter < hint_gutter_x:
            left_edges.append(elem.bbox[2])
        else:
            right_edges.append(elem.bbox[0])

    if len(left_edges) < 2 or len(right_edges) < 2:
        return None

    left_col_right = max(left_edges)
    right_col_rights = [
        e.bbox[2] for e in text_elems
        if _has_bbox(e)
        and (e.bbox[2] - e.bbox[0]) <= page_width * _FULL_WIDTH_RATIO
        and (e.bbox[0] + e.bbox[2]) / 2 > hint_gutter_x
    ]
    right_col_right = max(right_col_rights) if right_col_rights else hint_gutter_x + 50

    gutter_width = min(right_edges) - left_col_right
    confidence = 0.5  # Lower confidence for hint-based detection

    return ColumnLayout(
        column_count=2,
        gutter_x=hint_gutter_x,
        gutter_width=max(gutter_width, 0),
        left_col_right=left_col_right,
        right_col_right=right_col_right,
        confidence=confidence,
    )


def classify_element(
    elem: PageElement,
    layout: ColumnLayout,
    page_width: float,
) -> str:
    """Classify an element as 'left', 'right', or 'full_width'."""
    if not _has_bbox(elem):
        return "left"  # Default

    width = elem.bbox[2] - elem.bbox[0]
    if width > page_width * _FULL_WIDTH_RATIO:
        return "full_width"

    # Check if the element spans across the gutter.
    if elem.bbox[0] < layout.gutter_x and elem.bbox[2] > layout.gutter_x:
        return "full_width"

    xcenter = (elem.bbox[0] + elem.bbox[2]) / 2
    return "left" if xcenter < layout.gutter_x else "right"


def reorder_elements(
    elements: list[PageElement],
    layout: ColumnLayout,
    page_width: float,
) -> list[PageElement]:
    """Reorder elements respecting column structure.

    Full-width elements act as zone boundaries. Within each column zone,
    left-column elements come first (sorted by y), then right-column.
    """
    # Classify all elements.
    classified: list[tuple[PageElement, str]] = []
    for elem in elements:
        col = classify_element(elem, layout, page_width)
        classified.append((elem, col))

    # Store column metadata.
    for elem, col in classified:
        elem.metadata["column"] = col
        if col == "left":
            elem.metadata["column_right_margin"] = layout.left_col_right
        elif col == "right":
            elem.metadata["column_right_margin"] = layout.right_col_right
        else:
            elem.metadata["column_right_margin"] = max(
                layout.left_col_right, layout.right_col_right,
            )

    # Build zones separated by full-width elements.
    result: list[PageElement] = []
    zone_left: list[PageElement] = []
    zone_right: list[PageElement] = []

    def _flush_zone() -> None:
        """Emit accumulated column elements: left first, then right."""
        zone_left.sort(key=lambda e: (e.bbox[1], e.bbox[0]))
        zone_right.sort(key=lambda e: (e.bbox[1], e.bbox[0]))
        result.extend(zone_left)
        result.extend(zone_right)
        zone_left.clear()
        zone_right.clear()

    for elem, col in classified:
        if col == "full_width":
            _flush_zone()
            result.append(elem)
        elif col == "left":
            zone_left.append(elem)
        else:
            zone_right.append(elem)

    _flush_zone()
    return result


class ReadingOrderBuilder:
    """Detect multi-column layout and reorder page elements."""

    def __init__(self, config: ReadingOrderConfig | None = None):
        self._config = config or ReadingOrderConfig()

    # Minimum fraction of pages that must be detected as two-column
    # before propagation kicks in.
    _PROPAGATION_THRESHOLD = 0.4

    def build(self, doc: Document) -> Document:
        if not self._config.enabled:
            return doc

        # ── Pass 1: per-page independent detection ──
        layouts: dict[int, ColumnLayout | None] = {}
        for page in doc.pages:
            layouts[page.number] = detect_columns(page.elements, page.width)

        # ── Pass 2: document-level propagation ──
        detected = [l for l in layouts.values() if l is not None]
        propagated = 0
        if len(detected) >= len(doc.pages) * self._PROPAGATION_THRESHOLD:
            median_gutter = statistics.median(l.gutter_x for l in detected)
            for page in doc.pages:
                if layouts[page.number] is not None:
                    continue
                hint_layout = detect_columns_with_hint(
                    page.elements, page.width, median_gutter,
                )
                if hint_layout is not None:
                    layouts[page.number] = hint_layout
                    propagated += 1

        # ── Apply layouts ──
        reordered_pages = 0
        for page in doc.pages:
            layout = layouts[page.number]
            if layout is None:
                continue
            page.elements = reorder_elements(page.elements, layout, page.width)
            reordered_pages += 1

        if reordered_pages > 0:
            msg = "Reading order: %d/%d pages reordered (multi-column detected)"
            if propagated:
                msg += f", {propagated} via document-level propagation"
            log.info(msg, reordered_pages, len(doc.pages))

        return doc
