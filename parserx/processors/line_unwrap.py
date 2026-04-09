"""Line unwrap processor for fixing visual hard line breaks.

Two-pass strategy:
1. Cross-element merging: adjacent text elements that are continuation
   lines of the same paragraph get merged into a single element.
2. Within-element unwrapping: remaining ``\\n`` characters inside
   multi-line elements get joined when they are visual line breaks.
"""

from __future__ import annotations

import logging
import re
from statistics import median

from parserx.config.schema import LineUnwrapConfig
from parserx.models.elements import Document, FontInfo, PageElement

log = logging.getLogger(__name__)

_SENTENCE_END_RE = re.compile(r"[。！？!?；;.]$")  # Colon deliberately excluded.
_CJK_RE = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff]")
_LIST_MARKER_RE = re.compile(
    r"^\s*(?:"
    r"[-*•·●○■□▪▸▹]"
    r"|(?:\d+|[A-Za-z])[.)、]"
    r"|[一二三四五六七八九十百千万]+[、.]"
    r"|第[一二三四五六七八九十百千万0-9]+[条款项目章节编]"
    r"|[（(](?:\d+|[A-Za-z一二三四五六七八九十百千万]+)[）)]"
    r")\s*"
)


def _font_key(font: FontInfo) -> str:
    return f"{font.name}_{font.size}_{font.bold}"


def _contains_cjk(text: str) -> bool:
    return bool(_CJK_RE.search(text))


def _looks_like_list_item(text: str) -> bool:
    return bool(_LIST_MARKER_RE.match(text))


def _trim_join_boundary(left: str, right: str) -> tuple[str, str]:
    return left.rstrip(), right.lstrip()


def _join_lines(current: str, next_line: str) -> str:
    """Join two lines using language-aware spacing."""
    current, next_line = _trim_join_boundary(current, next_line)

    if current.endswith("-") and next_line[:1].islower():
        return current[:-1] + next_line

    if not current or not next_line:
        return current + next_line

    if _contains_cjk(current) or _contains_cjk(next_line):
        # Add space at CJK↔Latin boundary (e.g. "研究方法" + "The method")
        left_is_cjk = bool(_CJK_RE.search(current[-1])) if current else False
        right_is_cjk = bool(_CJK_RE.search(next_line[0])) if next_line else False
        if left_is_cjk != right_is_cjk:
            return current + " " + next_line
        return current + next_line

    return current + " " + next_line


def _should_merge_lines(
    current: str,
    next_line: str,
    average_line_length: float,
    *,
    last_raw_len: int | None = None,
) -> bool:
    """Decide whether a visual line break should be removed.

    *last_raw_len* is the length of the last *original* line appended to
    *current* (before any previous merges lengthened it).  When provided,
    the CJK merge check uses this instead of ``len(current)`` so that a
    short original line (intentional break) is not masked by accumulated
    merge length.
    """
    current = current.rstrip()
    next_line = next_line.lstrip()

    if not current or not next_line:
        return False

    # A new list item starting on next_line should not merge.
    # But a list item on current CAN have continuation lines.
    if _looks_like_list_item(next_line):
        return False

    if _SENTENCE_END_RE.search(current):
        return False

    if current.endswith("-") and next_line[:1].islower():
        return True

    if next_line[:1].islower():
        # Intentional: lowercase-start continuation is such a strong signal
        # for English/mixed-language prose that we merge before the CJK path.
        return True

    if _contains_cjk(current) and _contains_cjk(next_line):
        # Fallback 24 approximates a typical short Chinese body line when we
        # cannot infer a body-font median from document metadata.
        effective_avg = average_line_length if average_line_length > 0 else 24
        check_len = last_raw_len if last_raw_len is not None else len(current.strip())
        return check_len >= max(10, effective_avg * 0.8)

    return False


def _unwrap_text_block(text: str, average_line_length: float) -> str:
    """Remove visual line breaks inside a text block while preserving paragraphs."""
    if "\n" not in text:
        return text

    lines = text.splitlines()
    if not lines:
        return text

    # Adaptive average: when this block's CJK lines are consistently
    # narrower than the document-wide average (e.g. text in a narrow
    # column while the rest of the document is single-column), use the
    # block-local column width as reference so that wrapped lines are
    # still detected.  Only CJK lines are considered because ASCII
    # lines do not participate in CJK merge logic and would skew the
    # estimate.  We use the 75th percentile rather than the max because
    # lines mixing CJK and ASCII characters have inflated character
    # counts relative to their physical width.
    effective_avg = average_line_length
    cjk_lengths = sorted(
        len(l.strip()) for l in lines if l.strip() and _contains_cjk(l)
    )
    if len(cjk_lengths) >= 3:
        local_col_width = cjk_lengths[int(len(cjk_lengths) * 0.75)]
        if average_line_length > 0 and local_col_width < average_line_length * 0.8:
            effective_avg = float(local_col_width)

    result: list[str] = []
    current: str | None = None
    last_raw_len: int = 0  # length of the last original line in *current*

    for raw_line in lines:
        line = raw_line.strip()

        if not line:
            if current is not None:
                result.append(current)
                current = None
            if result and result[-1] != "":
                result.append("")
            continue

        if current is None:
            current = line
            last_raw_len = len(line)
            continue

        if _should_merge_lines(current, line, effective_avg, last_raw_len=last_raw_len):
            current = _join_lines(current, line)
            last_raw_len = len(line)
        else:
            result.append(current)
            current = line
            last_raw_len = len(line)

    if current is not None:
        result.append(current)

    return "\n".join(result)


# ── Cross-element merging helpers ──────────────────────────────────────


def _has_bbox(elem: PageElement) -> bool:
    return elem.bbox != (0.0, 0.0, 0.0, 0.0)


def _vertical_gap(a: PageElement, b: PageElement) -> float | None:
    """Vertical distance from bottom of *a* to top of *b*.

    Returns ``None`` when either element lacks bbox data.
    """
    if not _has_bbox(a) or not _has_bbox(b):
        return None
    return b.bbox[1] - a.bbox[3]  # b.y0 - a.y1


def _estimate_interline_gap(elements: list[PageElement]) -> float | None:
    """Median vertical gap between consecutive text elements on a page."""
    gaps: list[float] = []
    prev: PageElement | None = None
    for elem in elements:
        if elem.type != "text":
            prev = None
            continue
        if prev is not None:
            gap = _vertical_gap(prev, elem)
            if gap is not None and gap >= 0:
                gaps.append(gap)
        prev = elem
    if len(gaps) < 2:
        return None
    return float(median(gaps))


def _should_merge_elements(
    a: PageElement,
    b: PageElement,
    average_line_length: float,
    typical_gap: float | None,
    page_right_margin: float | None = None,
) -> bool:
    """Decide whether adjacent text element *b* is a continuation of *a*."""
    if a.type != "text" or b.type != "text":
        return False
    if a.page_number != b.page_number:
        return False
    if a.metadata.get("heading_level") or b.metadata.get("heading_level"):
        return False
    if a.metadata.get("skip_render") or b.metadata.get("skip_render"):
        return False
    if a.metadata.get("code_block") or b.metadata.get("code_block"):
        return False
    # Never merge elements from different columns.
    col_a = a.metadata.get("column")
    col_b = b.metadata.get("column")
    if col_a and col_b and col_a != col_b:
        return False
    if _font_key(a.font) != _font_key(b.font):
        return False

    # Vertical gap: large gap → paragraph break.
    if typical_gap is not None and typical_gap > 0:
        gap = _vertical_gap(a, b)
        if gap is not None and gap > typical_gap * 2.0:
            return False

    # Width guard: if element *a* ends well before the right margin,
    # the line break is intentional (not a soft wrap).
    # Prefer per-column right margin over global page right margin.
    effective_right = a.metadata.get("column_right_margin") or page_right_margin
    if effective_right is not None and effective_right > 0 and _has_bbox(a):
        if a.bbox[2] < effective_right * 0.85:
            return False

    return _should_merge_lines(a.content, b.content, average_line_length)


def _merge_element_into(target: PageElement, source: PageElement) -> None:
    """Merge *source* content into *target* in-place."""
    target.content = _join_lines(target.content, source.content)
    # Expand bbox to encompass both elements.
    if _has_bbox(target) and _has_bbox(source):
        target.bbox = (
            min(target.bbox[0], source.bbox[0]),
            min(target.bbox[1], source.bbox[1]),
            max(target.bbox[2], source.bbox[2]),
            max(target.bbox[3], source.bbox[3]),
        )


def _estimate_page_right_margin(elements: list[PageElement]) -> float | None:
    """Estimate the right margin of the page from text element bboxes."""
    right_edges: list[float] = []
    for elem in elements:
        if elem.type == "text" and _has_bbox(elem):
            right_edges.append(elem.bbox[2])
    if len(right_edges) < 2:
        return None
    # Use the maximum right edge as the page right margin estimate.
    return max(right_edges)


def _merge_adjacent_elements(
    elements: list[PageElement],
    average_line_length: float,
    typical_gap: float | None,
) -> list[PageElement]:
    """Merge consecutive continuation-line elements into single elements."""
    if not elements:
        return elements

    page_right_margin = _estimate_page_right_margin(elements)

    result: list[PageElement] = []
    current = elements[0]

    for i in range(1, len(elements)):
        nxt = elements[i]
        if _should_merge_elements(
            current, nxt, average_line_length, typical_gap, page_right_margin,
        ):
            _merge_element_into(current, nxt)
        else:
            result.append(current)
            current = nxt

    result.append(current)
    return result


class LineUnwrapProcessor:
    """Fix paragraph-internal hard line breaks introduced by PDF extraction."""

    def __init__(self, config: LineUnwrapConfig | None = None):
        self._config = config or LineUnwrapConfig()

    def process(self, doc: Document) -> Document:
        if not self._config.enabled:
            return doc

        average_line_length = self._estimate_body_line_length(doc)

        # Pass 1: merge adjacent text elements that are continuation lines.
        merged_count = 0
        for page in doc.pages:
            before = len(page.elements)
            typical_gap = _estimate_interline_gap(page.elements)
            page.elements = _merge_adjacent_elements(
                page.elements, average_line_length, typical_gap,
            )
            merged_count += before - len(page.elements)

        if merged_count > 0:
            log.info("Merged %d continuation-line elements across pages", merged_count)

        # Pass 2: join remaining visual line breaks within elements.
        for page in doc.pages:
            for element in page.elements:
                if element.type != "text":
                    continue
                if element.metadata.get("heading_level"):
                    continue
                if element.metadata.get("code_block"):
                    continue
                element.content = _unwrap_text_block(element.content, average_line_length)

        return doc

    def _estimate_body_line_length(self, doc: Document) -> float:
        """Estimate the typical body-text line length for CJK merge heuristics."""
        body_font_key = _font_key(doc.metadata.font_stats.body_font)
        candidates: list[int] = []

        for page in doc.pages:
            for element in page.elements:
                if element.type != "text":
                    continue
                if element.metadata.get("heading_level"):
                    continue
                if body_font_key and _font_key(element.font) != body_font_key:
                    continue

                for line in element.content.splitlines():
                    stripped = line.strip()
                    if len(stripped) >= 8 and not _looks_like_list_item(stripped):
                        candidates.append(len(stripped))

        if not candidates:
            return 0.0

        return float(median(candidates))
