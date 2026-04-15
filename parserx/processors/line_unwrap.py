"""Line unwrap processor for fixing visual hard line breaks.

Three-phase strategy:
1. Cross-element merging: adjacent text elements that are continuation
   lines of the same paragraph get merged into a single element.
2. Within-element unwrapping: remaining ``\\n`` characters inside
   multi-line elements get joined when they are visual line breaks.
3. LLM fallback: ambiguous break points that rules cannot resolve are
   batched and sent to an LLM for merge/keep decisions.
"""

from __future__ import annotations

import json
import logging
import re
from statistics import median
from typing import Literal

from parserx.config.schema import LineUnwrapConfig
from parserx.models.elements import Document, FontInfo, PageElement
from parserx.services.llm import LLMService

log = logging.getLogger(__name__)

MergeDecision = Literal["merge", "keep", "uncertain"]

_SENTENCE_END_RE = re.compile(r"[。！？!?；;.]$")  # Colon deliberately excluded.
_CJK_RE = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff]")

# Next-line patterns that indicate a mid-sentence hard wrap in CJK text,
# regardless of how short the current line is.  These are characters that
# cannot start a new paragraph on their own.
_CJK_CONTINUATION_RE = re.compile(
    r"^(?:"
    r"[.\uff0c\u3001\uff1b\uff1a\uff09\]\u3011\u300b\u300d\u300f\u201d\u2019\u3002]"  # orphan punct
    r"|\[[\d,\u002d\uff0c ]+\]"  # bracketed reference [1], [2,3], [11-15]
    r"|\d+[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff]"  # digit + CJK (e.g. "3种", "2中")
    r")"
)

# Common abbreviations that end with a period but are NOT sentence endings.
_ABBREV_RE = re.compile(
    r"(?:"
    r"(?:et\s+al|vs|[Ff]ig|[Tt]ab|[Ee]q|[Rr]ef|[Nn]o|[Vv]ol|[Pp]p?|[Cc]f"
    r"|[Dd]r|[Mm]r|[Mm]rs|[Mm]s|[Pp]rof|[Ss]t|[Jj]r|[Ss]r"
    r"|[Ii]nc|[Cc]orp|[Ll]td|[Ee]tc|[Aa]pprox|[Ee]sp"
    r"|[Ii]\.e|[Ee]\.g|[Ii]bid)"
    r")\.$"
)

_LLM_SYSTEM_PROMPT = """\
你是一个文档换行分析助手。给定带有标记 [?N] 的文本断点，判断每个断点是否应该合并（视觉换行）还是保留（真正的段落/句子边界）。

规则：
- "merge" 表示断点是排版引起的换行，应该合并成连续文本
- "keep" 表示断点是有意义的段落/句子分隔，应该保留
- 如果断点前后文本语义连续、句子未结束，应该 merge
- 如果断点是段落结束、列表项分隔或完整句子的结尾，应该 keep

仅返回 JSON 数组，例如: [{"idx":1,"decision":"merge"},{"idx":2,"decision":"keep"}]
不要输出任何额外解释，不要输出 Markdown。"""

_LIST_MARKER_RE = re.compile(
    r"^\s*(?:"
    r"[-*•·●○■□▪▸▹]"
    r"|(?:\d+|[A-Za-z])[.)、]"
    r"|[一二三四五六七八九十百千万]+[、.]"
    r"|第[一二三四五六七八九十百千万0-9]+[条款项目章节编]"
    r"|[（(](?:\d+|[A-Za-z一二三四五六七八九十百千万]+)[）)]"
    r"|\[\d{1,6}\]"
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


def _should_merge_lines_3way(
    current: str,
    next_line: str,
    average_line_length: float,
    *,
    last_raw_len: int | None = None,
) -> MergeDecision:
    """Three-way merge decision: ``"merge"``, ``"keep"``, or ``"uncertain"``.

    *last_raw_len* is the length of the last *original* line appended to
    *current* (before any previous merges lengthened it).  When provided,
    the CJK merge check uses this instead of ``len(current)`` so that a
    short original line (intentional break) is not masked by accumulated
    merge length.
    """
    current = current.rstrip()
    next_line = next_line.lstrip()

    if not current or not next_line:
        return "keep"

    # A bare list marker line (e.g. "[0005]") should merge into the
    # following content line — the marker opens a paragraph whose text
    # starts on the next visual line.
    if _LIST_MARKER_RE.fullmatch(current) and not _looks_like_list_item(next_line):
        return "merge"

    # A new list item starting on next_line should not merge.
    if _looks_like_list_item(next_line):
        return "keep"

    # --- Sentence-end punctuation ---
    if _SENTENCE_END_RE.search(current):
        # Check if the period is actually an abbreviation (e.g. "et al.",
        # "Fig.", "vs.") — if so, this is uncertain, not a confident keep.
        if current.endswith(".") and _ABBREV_RE.search(current):
            return "uncertain"
        # Semicolons often continue in English prose.
        if current.endswith(";") or current.endswith("\uff1b"):
            return "uncertain"
        return "keep"

    # --- Confident merge signals ---
    if current.endswith("-") and next_line[:1].islower():
        return "merge"

    if next_line[:1].islower():
        return "merge"

    # --- CJK path ---
    if _contains_cjk(current) and _contains_cjk(next_line):
        stripped_next = next_line.lstrip()
        if _CJK_CONTINUATION_RE.match(stripped_next):
            return "merge"

        if len(stripped_next) <= 2:
            return "merge"

        effective_avg = average_line_length if average_line_length > 0 else 24
        check_len = last_raw_len if last_raw_len is not None else len(current.strip())
        if check_len >= max(10, effective_avg * 0.8):
            return "merge"

        # CJK line too short for confident merge but not clearly a break.
        return "uncertain"

    # --- English uppercase start → uncertain ---
    if next_line[:1].isupper():
        return "uncertain"

    return "keep"


def _should_merge_lines(
    current: str,
    next_line: str,
    average_line_length: float,
    *,
    last_raw_len: int | None = None,
) -> bool:
    """Backward-compatible wrapper: maps ``"uncertain"`` to ``False``."""
    return _should_merge_lines_3way(
        current, next_line, average_line_length, last_raw_len=last_raw_len,
    ) == "merge"


def _compute_effective_avg(lines: list[str], average_line_length: float) -> float:
    """Compute block-local effective average for CJK merge checks.

    When this block's CJK lines are consistently narrower than the
    document-wide average (e.g. text in a narrow column), use the
    block-local column width as reference.
    """
    effective_avg = average_line_length
    cjk_lengths = sorted(
        len(l.strip()) for l in lines if l.strip() and _contains_cjk(l)
    )
    if len(cjk_lengths) >= 3:
        local_col_width = cjk_lengths[int(len(cjk_lengths) * 0.75)]
        if average_line_length > 0 and local_col_width < average_line_length * 0.8:
            effective_avg = float(local_col_width)
    return effective_avg


# Uncertain break point collected for LLM resolution.
# (paragraph_index, left_text, right_text) — paragraph_index references
# position in the result list so we can apply the merge after LLM responds.
UncertainBreak = tuple[int, str, str]


def _unwrap_text_block(
    text: str,
    average_line_length: float,
    *,
    collect_uncertain: bool = False,
) -> str | tuple[str, list[UncertainBreak]]:
    """Remove visual line breaks inside a text block while preserving paragraphs.

    When *collect_uncertain* is True, returns ``(text, uncertain_breaks)``
    where uncertain breaks are kept as line breaks but recorded for later
    LLM resolution.
    """
    if "\n" not in text:
        return (text, []) if collect_uncertain else text

    lines = text.splitlines()
    if not lines:
        return (text, []) if collect_uncertain else text

    effective_avg = _compute_effective_avg(lines, average_line_length)

    result: list[str] = []
    uncertain_breaks: list[UncertainBreak] = []
    current: str | None = None
    last_raw_len: int = 0

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

        decision = _should_merge_lines_3way(
            current, line, effective_avg, last_raw_len=last_raw_len,
        )

        if decision == "merge":
            current = _join_lines(current, line)
            if len(line) > 5:
                last_raw_len = len(line)
        elif decision == "uncertain" and collect_uncertain:
            # Keep the break for now but record it for LLM resolution.
            result.append(current)
            uncertain_breaks.append((len(result) - 1, current, line))
            current = line
            last_raw_len = len(line)
        else:
            # "keep" or "uncertain" without collection
            result.append(current)
            current = line
            last_raw_len = len(line)

    if current is not None:
        result.append(current)

    joined = "\n".join(result)
    if collect_uncertain:
        return joined, uncertain_breaks
    return joined


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
    t_content = target.content
    s_content = source.content
    new_content = _join_lines(t_content, s_content)
    target.content = new_content

    # Preserve inline formatting across the merge when the join is a
    # simple concatenation (possibly with a single-character joiner like
    # a space). When _join_lines applies non-trivial transforms (e.g. the
    # "-" hyphen-join drops a char, or _trim_join_boundary strips
    # whitespace), we can't faithfully stitch per-span text, so drop the
    # spans and let the renderer fall back to plain content.
    t_spans = target.metadata.get("inline_spans")
    s_spans = source.metadata.get("inline_spans")
    if not t_spans and not s_spans:
        return

    def _as_spans(elem: PageElement, content: str) -> list[dict]:
        spans = elem.metadata.get("inline_spans")
        if spans:
            return [dict(s) for s in spans]
        return [{"text": content, "bold": elem.font.bold, "italic": elem.font.italic}]

    # Determine joiner: new_content[len(t_content):len(t_content)+k] for
    # some k in {0, 1}. Anything else (negative length, substring
    # mismatch) means the join rewrote boundary characters.
    joiner_len = len(new_content) - len(t_content) - len(s_content)
    if (
        joiner_len < 0
        or joiner_len > 1
        or not new_content.startswith(t_content)
        or not new_content.endswith(s_content)
    ):
        target.metadata.pop("inline_spans", None)
        return

    merged = _as_spans(target, t_content)
    if joiner_len == 1:
        joiner = new_content[len(t_content)]
        merged.append({"text": joiner, "bold": False, "italic": False})
    merged.extend(_as_spans(source, s_content))
    # Coalesce adjacent same-format spans
    coalesced: list[dict] = []
    for span in merged:
        if not span["text"]:
            continue
        if coalesced and coalesced[-1]["bold"] == span["bold"] and coalesced[-1]["italic"] == span["italic"]:
            coalesced[-1]["text"] += span["text"]
        else:
            coalesced.append(span)
    target.metadata["inline_spans"] = coalesced
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

    def __init__(
        self,
        config: LineUnwrapConfig | None = None,
        llm_service: LLMService | None = None,
    ):
        self._config = config or LineUnwrapConfig()
        self._llm = llm_service

    def process(self, doc: Document) -> Document:
        if not self._config.enabled:
            return doc

        average_line_length = self._estimate_body_line_length(doc)
        use_llm = (
            self._config.llm_fallback
            and self._llm is not None
        )

        # Body font key — only body-font elements are eligible for LLM
        # fallback.  Non-body elements (titles, abstracts, table captions)
        # use different fonts and their line breaks are usually intentional.
        body_fk = _font_key(doc.metadata.font_stats.body_font)

        # Phase 1: merge adjacent text elements that are continuation lines.
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

        # Phase 2: join remaining visual line breaks within elements.
        # When LLM fallback is active, collect uncertain breaks.
        llm_candidates: list[tuple[PageElement, list[UncertainBreak]]] = []

        for page in doc.pages:
            for element in page.elements:
                if element.type != "text":
                    continue
                if element.metadata.get("heading_level"):
                    continue
                if element.metadata.get("code_block"):
                    continue

                # LLM fallback only for body-font elements — non-body
                # elements (titles, captions, etc.) stay rule-based.
                elem_eligible = use_llm and _font_key(element.font) == body_fk

                if elem_eligible:
                    text, uncertain = _unwrap_text_block(
                        element.content, average_line_length,
                        collect_uncertain=True,
                    )
                    element.content = text
                    if uncertain:
                        llm_candidates.append((element, uncertain))
                else:
                    element.content = _unwrap_text_block(
                        element.content, average_line_length,
                    )

        # Phase 3: LLM fallback for uncertain breaks.
        if llm_candidates:
            llm_merges = self._resolve_uncertain_breaks(llm_candidates)
            if llm_merges > 0:
                log.info("LLM fallback merged %d uncertain line breaks", llm_merges)
                doc.metadata.processing_stats["line_unwrap_llm_merges"] = llm_merges

        return doc

    # ── LLM fallback ─────────────────────────────────────────────────────

    def _resolve_uncertain_breaks(
        self,
        candidates: list[tuple[PageElement, list[UncertainBreak]]],
    ) -> int:
        """Send uncertain breaks to LLM in batches and apply merge decisions."""
        assert self._llm is not None
        total_merges = 0
        batch_size = self._config.llm_batch_size

        for element, breaks in candidates:
            # Process this element's uncertain breaks in batches.
            for batch_start in range(0, len(breaks), batch_size):
                batch = breaks[batch_start : batch_start + batch_size]
                decisions = self._llm_classify_breaks(element, batch)
                if decisions:
                    merged = self._apply_llm_decisions(element, batch, decisions)
                    total_merges += merged

        return total_merges

    def _llm_classify_breaks(
        self,
        element: PageElement,
        breaks: list[UncertainBreak],
    ) -> list[dict] | None:
        """Call LLM to classify a batch of uncertain breaks."""
        assert self._llm is not None

        # Build context: show the element text with [?N] markers.
        lines = element.content.splitlines()
        # Map paragraph indices in breaks to line positions.
        marked_positions: dict[int, int] = {}  # para_idx -> marker_number
        for marker_num, (para_idx, _, _) in enumerate(breaks, 1):
            marked_positions[para_idx] = marker_num

        display_lines: list[str] = []
        for i, line in enumerate(lines):
            display_lines.append(line)
            if i in marked_positions:
                display_lines.append(f"[?{marked_positions[i]}]")

        user_prompt = "\n".join(display_lines)

        try:
            response = self._llm.complete(
                _LLM_SYSTEM_PROMPT,
                user_prompt,
                temperature=0.0,
                max_tokens=max(256, len(breaks) * 32),
            )
        except Exception as exc:
            log.warning("LineUnwrap LLM fallback failed: %s", exc)
            return None

        try:
            parsed = json.loads(response)
        except json.JSONDecodeError:
            log.warning("LineUnwrap LLM returned non-JSON: %.100s", response)
            return None

        if isinstance(parsed, list):
            return [item for item in parsed if isinstance(item, dict)]
        return None

    def _apply_llm_decisions(
        self,
        element: PageElement,
        breaks: list[UncertainBreak],
        decisions: list[dict],
    ) -> int:
        """Apply LLM merge decisions to the element text."""
        # Build decision map: marker idx -> "merge" or "keep"
        merge_set: set[int] = set()
        for item in decisions:
            idx = item.get("idx")
            decision = item.get("decision", "")
            if not isinstance(idx, int) or idx < 1 or idx > len(breaks):
                continue
            if decision == "merge":
                merge_set.add(idx)

        if not merge_set:
            return 0

        # Rebuild element text, merging the marked breaks.
        lines = element.content.splitlines()
        para_indices_to_merge = {
            breaks[marker_num - 1][0]
            for marker_num in merge_set
        }

        result: list[str] = []
        current: str | None = None
        for i, line in enumerate(lines):
            if current is None:
                current = line
            elif (i - 1) in para_indices_to_merge:
                # Merge: i-1 is the paragraph index whose break should merge.
                current = _join_lines(current, line)
            else:
                result.append(current)
                current = line

        if current is not None:
            result.append(current)

        new_content = "\n".join(result)
        merged = len(merge_set)
        if new_content != element.content:
            element.content = new_content
            element.metadata["llm_line_unwrap_used"] = True
        else:
            merged = 0

        return merged

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
