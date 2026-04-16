"""Chapter/heading detection processor.

Two-stage approach:
1. Rule-based: font size/bold analysis + numbering pattern matching (covers 70-80%)
2. LLM fallback: only when rule-based confidence is low (sends heading candidates,
   not full text — one LLM call instead of three)

Numbering detection regexes migrated from legacy pipeline chapter_outline_core.py.
"""

from __future__ import annotations

import json
import logging
import re

from parserx.builders.metadata import detect_numbering_signal
from parserx.config.schema import ProcessorToggle
from parserx.models.elements import Document, FontInfo, PageElement
from parserx.services.llm import LLMService

log = logging.getLogger(__name__)

_FALLBACK_SYSTEM_PROMPT = """\
你是一个文档结构分析助手。你的任务是判断候选文本是否为标题，并给出标题层级。

返回 JSON 数组，每项格式如下：
{"idx": 1, "level": 2}

规则：
- level 只能是 0, 1, 2, 3
- 0 表示不是标题
- 只根据候选文本本身、字体信息和局部上下文判断
- 不要输出任何额外解释，不要输出 Markdown"""

_FALLBACK_MAX_CANDIDATES = 40

# ── False positive filters ──────────────────────────────────────────────

# Date patterns: "2026 年3 月", "2026-03-01", etc.
_DATE_RE = re.compile(
    r"^\d{4}\s*[年/\-\.]\s*\d{1,2}\s*[月/\-\.]"
    r"|^\d{4}\s*年"
)

# TOC entries: lines with "....." or page number references
_TOC_RE = re.compile(r"\.{3,}|…{2,}")

# Metadata fields: "标签：值" or "Label: value"
_METADATA_FIELD_RE = re.compile(
    r"^[\u4e00-\u9fffA-Za-z]{1,12}\s*[：:]\s*.+"
)

# Pure number (page number, code, etc.)
_PURE_NUMBER_RE = re.compile(r"^\d+$")
# Dotted section numbering with no trailing text — e.g. ``3.1``, ``4.5.2``.
# Used to recognise heading content where the section number landed on its
# own line and the title text follows on the next line.
_NUMBERING_ONLY_RE = re.compile(r"^\d+(?:\.\d+)*\.?$")
# Trailing function word that signals the heading text continues on the
# next line (e.g., "Controlling Data Communication and").
_DANGLING_WORD_RE = re.compile(r"\b(and|or|of|the|for|to|in|with|on)$", re.I)
_SUBTITLE_PREFIX_RE = re.compile(r"^[—–\-一]{2,}")
_HEADING_COLON_END_RE = re.compile(r"[：:]$")

# Price/currency: "$200.00", "¥1,000", "€50.00", etc.
_PRICE_RE = re.compile(r"^[$¥€£₽]\s*[\d,]+(?:\.\d+)?$")
# Navigation link: ends with "›" or trailing " >" (common in emails/receipts)
_NAV_LINK_RE = re.compile(r"[›»>]\s*$")


def _resolve_heading_text(content: str) -> str:
    """Extract heading candidate text, joining split number+title lines.

    Chinese academic papers often have the section number and title on
    separate lines within one text block (e.g. ``"5\\n算例分析"``).
    When the first line is a pure number, check if the next line contains
    short heading-like text and return the combined form (``"5 算例分析"``).
    """
    lines = content.split("\n")
    first = lines[0].strip()
    if not first or not _PURE_NUMBER_RE.match(first):
        return first
    # First line is a pure number — check next 1-2 lines for heading text
    for i in range(1, min(len(lines), 3)):
        candidate = lines[i].strip()
        if not candidate:
            continue
        if _looks_like_body_text(candidate):
            break
        combined = f"{first} {candidate}"
        if len(combined) <= 80:
            return combined
        break
    return first


def _is_false_positive(text: str) -> bool:
    """Filter out text that looks like a heading due to numbering but isn't.

    These are the main sources of false positives from iteration #2:
    1. Date lines: "2026 年3 月至..." matches section_arabic_spaced
    2. TOC entries: "第一章 公告....... 2" are not real headings
    3. Metadata fields: "采购人：中铁七局..." is a label:value pair
    4. Pure numbers
    """
    stripped = text.strip()

    if _DATE_RE.match(stripped):
        return True
    if _TOC_RE.search(stripped):
        return True
    if _PURE_NUMBER_RE.match(stripped):
        return True
    if _PRICE_RE.match(stripped):
        return True
    if _NAV_LINK_RE.search(stripped):
        return True
    # Single-character text is never a heading — catches diagram node labels
    # (e.g., "C", "b", "W", "x" from computation graphs) that happen to be
    # rendered in a large or bold font.
    if len(stripped) <= 1:
        return True
    return False


def _is_metadata_or_cover_line(text: str) -> bool:
    """Detect metadata lines that have large fonts but aren't headings.

    Only uses universally-applicable patterns to avoid overfitting to
    specific test documents. The goal is high precision — if unsure,
    don't filter, let the LLM fallback handle it later.
    """
    stripped = text.strip()

    # Metadata field pattern: "标签：值" — universal across Chinese docs
    if _METADATA_FIELD_RE.match(stripped):
        return True

    # Date line — never a heading
    if _DATE_RE.match(stripped):
        return True

    return False


# ── Heading detection helpers ───────────────────────────────────────────


def _font_key(font: FontInfo) -> str:
    return f"{font.name}_{font.size}_{font.bold}"


def _heading_level_from_font(
    font: FontInfo,
    heading_candidates: list[FontInfo],
) -> int | None:
    """Map a font to heading level based on its rank among heading candidates.

    Candidates are sorted by size descending (from MetadataBuilder).
    - First candidate → H1
    - Second candidate → H2
    - Third+ candidates → H3
    """
    key = _font_key(font)
    for idx, candidate in enumerate(heading_candidates):
        if _font_key(candidate) == key:
            if idx == 0:
                return 1
            if idx == 1:
                return 2
            return 3
    return None


def _heading_level_from_numbering(text: str) -> int | None:
    """Infer heading level from numbering pattern.

    Dotted arabic numbering uses depth: ``N`` → H2, ``N.M`` → H3,
    ``N.M.K`` → H4, etc. Non-arabic or fixed-level patterns fall back
    to the table in ``_NUMBERING_PATTERNS``.
    """
    result = detect_numbering_signal(text)
    if not result:
        return None
    signal, level_str = result
    if signal == "section_arabic_nested":
        stripped = re.sub(r"^\s{0,3}#{1,6}\s*", "", text.strip())
        stripped = stripped.replace("**", "").replace("*", "")
        m = re.match(r"^(\d+(?:\.\d+)+)", stripped)
        if m:
            depth = m.group(1).count(".") + 1  # 3.2 → 2, 3.2.1 → 3
            # Cap at H6; depth-2 → H3 (matches prior behavior for "3.2").
            return min(depth + 1, 6)
    return {"H1": 1, "H2": 2, "H3": 3}.get(level_str)


def _is_short_heading_text(text: str) -> bool:
    """Heuristic: headings are usually short (< 80 chars, single line)."""
    text = text.strip()
    if "\n" in text:
        return False
    return len(text) <= 80


def _looks_like_body_text(text: str) -> bool:
    """Heuristic: body text is usually longer and has sentence-ending punctuation."""
    text = text.strip()
    if len(text) > 100:
        return True
    # Ends with sentence punctuation (but not colon — headings can end with colon)
    if text and text[-1] in "。！？!?.;；":
        return True
    return False


def _is_subtitle_line(text: str) -> bool:
    """Check whether *text* looks like a title/subtitle fragment.

    Rejects body-text openers, metadata annotations, and long lines.
    Designed to be generic — no document-specific keywords.
    """
    s = text.strip()
    if not s or len(s) > 80:
        return False
    if _looks_like_body_text(s):
        return False
    # Parenthetical / bracketed annotations are metadata, not subtitles
    if s[0] in "(（[【":
        return False
    if _is_metadata_or_cover_line(s):
        return False
    return True


def _is_multiline_title(lines: list[str], heading_level: int) -> bool:
    """Detect multi-line title pattern worth splitting into separate headings.

    Triggers when the first line is short and the next line is also
    title-like.  Applies to H1 titles (where subtitles are common)
    and to any heading whose first line ends with colon.
    """
    if len(lines) < 2:
        return False
    first = lines[0].strip()
    if not first:
        return False
    # Two triggers: (1) document title (H1), (2) colon-ended heading
    is_h1 = heading_level == 1
    is_colon = bool(_HEADING_COLON_END_RE.search(first))
    if not is_h1 and not is_colon:
        return False
    return _is_subtitle_line(lines[1])


def _count_title_lines(lines: list[str]) -> int:
    """Count how many leading lines are title-like (max 3)."""
    count = 1  # first line always counts
    for i in range(1, min(len(lines), 3)):
        if _is_subtitle_line(lines[i]):
            count = i + 1
        else:
            break
    return count


def _ends_with_colon(text: str) -> bool:
    """Text ending with colon is likely an introductory clause, not a heading."""
    text = text.strip()
    return bool(text) and text[-1] in "：:"


def _truncate_text(text: str, limit: int = 120) -> str:
    compact = " ".join(part.strip() for part in text.splitlines() if part.strip())
    if len(compact) <= limit:
        return compact
    return compact[: limit - 1] + "…"


def _safe_int(value: object) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float) and value.is_integer():
        return int(value)
    if isinstance(value, str) and value.strip().isdigit():
        return int(value.strip())
    return None


def _bump_processing_stat(doc: Document, key: str, amount: int = 1) -> None:
    doc.metadata.processing_stats[key] = (
        doc.metadata.processing_stats.get(key, 0) + amount
    )


def _is_coherent_sequence(numbers: list[int], min_count: int = 3) -> bool:
    """Check if sorted numbers form a near-sequential series.

    Allows gaps of at most 2 between consecutive numbers, and requires
    at least 50% coverage of the spanned range.
    """
    if len(numbers) < min_count:
        return False
    max_gap = max(numbers[i + 1] - numbers[i] for i in range(len(numbers) - 1))
    if max_gap > 2:
        return False
    span = numbers[-1] - numbers[0] + 1
    return len(numbers) / span >= 0.5


def _is_right_sidebar(page_width: float, elem: PageElement) -> bool:
    if page_width <= 0:
        return False
    return elem.bbox[0] >= page_width * 0.6


def _has_heading_vertical_isolation(
    elem: PageElement,
    page_elements: list[PageElement],
    elem_idx: int,
    min_gap_ratio: float = 1.4,
) -> bool:
    """Geometric gating for bold-at-body-size heading candidates.

    A true sub-heading sits on its own line with visible whitespace above.
    Inline bold emphasis (e.g. a paragraph-opening term) is packed tight
    against the preceding paragraph.  This checks whether the nearest
    same-column preceding element leaves a gap of at least
    ``min_gap_ratio × line_height`` above the candidate.

    Returns ``True`` only if a same-column preceding element exists and
    leaves gap ≥ ``min_gap_ratio × line_height`` above the candidate.
    When no same-column predecessor is found (column/page top), the
    element is conservatively rejected — a body-sized bold line at
    column top is more likely a paragraph-continuation fragment than a
    true heading (genuine page-top headings are usually larger font and
    won't reach this gating branch).
    """
    x0, y0, x1, y1 = elem.bbox
    line_h = max(y1 - y0, 1.0)
    width = max(x1 - x0, 1.0)
    min_overlap = width * 0.5

    prev_bottom: float | None = None
    for j in range(elem_idx - 1, -1, -1):
        prev = page_elements[j]
        if prev.type != "text":
            continue
        px0, py0, px1, py1 = prev.bbox
        if py1 > y0:
            # Not strictly above — skip (could be same line or below).
            continue
        overlap = min(x1, px1) - max(x0, px0)
        if overlap < min_overlap:
            continue
        prev_bottom = py1
        break

    if prev_bottom is None:
        # No same-column predecessor.  A body-sized bold line without any
        # content above it in the same column is most often a paragraph
        # continuation across a page or column break (e.g. a bold term
        # leading the next column), not a true heading.  True page-top
        # headings are generally rendered with a larger font and won't
        # reach this branch (the gating only applies when font.size is
        # at body size).  Conservatively reject.
        return False

    gap = y0 - prev_bottom
    return gap >= min_gap_ratio * line_h


class ChapterProcessor:
    """Detect chapter/section headings and assign heading levels.

    Sets element.metadata["heading_level"] = 1/2/3 for detected headings.
    The MarkdownRenderer uses this to output # / ## / ### prefixes.
    """

    def __init__(
        self,
        config: ProcessorToggle | None = None,
        llm_service: LLMService | None = None,
    ):
        self._config = config or ProcessorToggle()
        self._llm = llm_service

    def process(self, doc: Document) -> Document:
        if not self._config.enabled:
            return doc

        heading_candidates = doc.metadata.font_stats.heading_candidates
        body_font = doc.metadata.font_stats.body_font
        numbering_patterns = doc.metadata.numbering_patterns

        detected_count = 0
        fallback_candidates: list[dict] = []

        for page in doc.pages:
            for elem_idx, elem in enumerate(page.elements):
                if elem.type != "text":
                    continue

                # Skip code blocks (detected by CodeBlockProcessor)
                if elem.metadata.get("code_block"):
                    continue

                # Skip retained page-identity elements (header/footer kept on first page)
                if elem.metadata.get("retained_page_identity"):
                    continue

                # Elements with heading_level already set (e.g. from DOCX styles, OCR layout)
                if elem.metadata.get("heading_level"):
                    original_ocr_level = elem.metadata.get("heading_level")
                    if not self._keep_existing_ocr_heading(page.width, elem):
                        # heading_level was popped; preserve OCR's original level
                        # hint so fallback can use it for level assignment
                        elem.metadata["ocr_level_hint"] = original_ocr_level
                        pass
                    elif elem.source == "ocr":
                        first_line = _resolve_heading_text(elem.content)
                        if _looks_like_body_text(first_line) or _ends_with_colon(first_line):
                            elem.metadata.pop("heading_level", None)
                            continue
                        numbering_level = _heading_level_from_numbering(first_line)
                        if numbering_level is not None and numbering_level != elem.metadata["heading_level"]:
                            elem.metadata["heading_level"] = numbering_level
                        detected_count += 1
                        continue
                    else:
                        first_line = _resolve_heading_text(elem.content)
                        numbering_level = _heading_level_from_numbering(first_line)
                        if numbering_level is not None and numbering_level != elem.metadata["heading_level"]:
                            elem.metadata["heading_level"] = numbering_level
                        detected_count += 1
                        continue

                inferred_level = self._infer_sidebar_heading_level(page.width, elem)
                if inferred_level is not None:
                    elem.metadata["heading_level"] = inferred_level
                    elem.metadata["ocr_heading_inferred"] = "sidebar_colon_label"
                    detected_count += 1
                    continue

                level = self._detect_heading(
                    elem, heading_candidates, body_font, numbering_patterns,
                    page.elements, elem_idx,
                )
                if level is not None:
                    elem.metadata["heading_level"] = level
                    detected_count += 1
                    continue

                candidate = self._build_fallback_candidate(
                    page.elements,
                    elem_idx,
                    elem,
                    heading_candidates,
                )
                if candidate is not None:
                    fallback_candidates.append(candidate)

        coherence_hits = self._promote_coherent_numbering(doc, fallback_candidates)
        detected_count += coherence_hits
        fallback_hits = self._apply_llm_fallback(doc, fallback_candidates)
        detected_count += fallback_hits
        self._normalize_ocr_title_subtitle_pair(doc)
        self._merge_cover_heading_fragments(doc)
        merged_count = self._merge_split_section_headings(doc, heading_candidates)
        detected_count += merged_count
        split_count = self._split_heading_body_elements(doc)

        log.info(
            "Detected %d headings (%d via coherence, %d via fallback, %d split)",
            detected_count,
            coherence_hits,
            fallback_hits,
            split_count,
        )
        return doc

    def _detect_heading(
        self,
        elem: PageElement,
        heading_candidates: list[FontInfo],
        body_font: FontInfo,
        numbering_patterns: list,
        page_elements: list[PageElement] | None = None,
        elem_idx: int | None = None,
    ) -> int | None:
        """Detect if an element is a heading and determine its level.

        Combines two signals:
        1. Font analysis: is the font larger/bolder than body text?
        2. Numbering pattern: does the text start with a chapter number?

        Both signals agreeing → high confidence.
        Only one signal → still accept if the signal is strong enough.

        Applies false-positive filters to catch:
        - Date lines, TOC entries, metadata fields, cover page lines
        """
        first_line = _resolve_heading_text(elem.content)

        # ── Hard filters: definitely not a heading ──
        if _looks_like_body_text(first_line):
            return None
        if _is_false_positive(first_line):
            return None

        # ── Signal 1: Font-based detection ──
        font_level = _heading_level_from_font(elem.font, heading_candidates)

        # ── Signal 2: Numbering-based detection ──
        numbering_level = _heading_level_from_numbering(first_line)

        # ── Decision logic ──
        if font_level is not None and numbering_level is not None:
            # Both signals — high confidence. Prefer numbering level.
            return numbering_level

        if numbering_level is not None:
            # Numbering alone — strong signal for Chinese docs.
            # But only if short, doesn't look like metadata, and is a strong pattern.
            # "chapter_cn" (第X章) and "section_cn" (一、二、) are strong by themselves.
            # Arabic numbering (1. 2. 3.) is weaker — too many false positives
            # from body text starting with numbers. Require font signal for these.
            result = detect_numbering_signal(first_line)
            signal = result[0] if result else ""
            strong_signals = {"chapter_cn", "section_cn", "section_cn_paren"}

            if signal in strong_signals:
                if _is_short_heading_text(first_line) and not _is_metadata_or_cover_line(first_line):
                    return numbering_level
            else:
                # Weak signal (arabic numbering) — only accept with font support
                pass  # Fall through to font_level check below

        if font_level is not None:
            # Font alone — stricter: must be short, not body, not cover/metadata.
            # Also reject numbered list items (e.g., bold "1. Build tools...")
            if (_is_short_heading_text(first_line)
                    and not _looks_like_body_text(first_line)
                    and not _is_metadata_or_cover_line(first_line)):
                # Geometric gating for bold-at-body-size candidates.
                # When a heading-candidate font shares the body font size
                # (distinguished only by bold/weight), the signal is weak —
                # body-inline emphasis (e.g. `**Variables**` in a paragraph)
                # produces the same font key.  Require clear vertical
                # isolation from preceding content before promoting.
                if (body_font.size > 0
                        and elem.font.size <= body_font.size + 0.5
                        and page_elements is not None
                        and elem_idx is not None):
                    if not _has_heading_vertical_isolation(
                        elem, page_elements, elem_idx
                    ):
                        # Block later fallback/LLM paths from re-promoting
                        # this element — geometric evidence says it's
                        # inline emphasis, not a heading.
                        elem.metadata["heading_geometric_reject"] = True
                        return None
                return font_level

        # ── DOCX fallback: bold + numbering heading detection ──
        # When font stats are unavailable (DOCX mode: heading_candidates is
        # empty, all sizes are 0), promote bold text ONLY when it also has
        # a numbering pattern.  Bold alone is too ambiguous — it matches
        # cover page text, emphasized body text, and table headers.
        if (
            not heading_candidates
            and elem.font.bold
            and numbering_level is not None
            and _is_short_heading_text(first_line)
            and not _is_metadata_or_cover_line(first_line)
            and not _ends_with_colon(first_line)
        ):
            return numbering_level

        return None

    # ── Numbering coherence pass ────────────────────────────────────────

    def _promote_coherent_numbering(
        self,
        doc: Document,
        fallback_candidates: list[dict],
    ) -> int:
        """Promote weak arabic numbering signals when document-level coherence is found.

        When elements with arabic numbering form a sequential series
        (e.g., 0, 1, 2, 3, 4, 5, 6), they are almost certainly section headings —
        even without a font-size difference from body text. This promotes undetected
        members of coherent sequences deterministically, without LLM.
        """
        root_entries: list[tuple[int, PageElement]] = []
        nested_entries: dict[int, list[tuple[int, PageElement]]] = {}

        for page in doc.pages:
            for elem in page.elements:
                if elem.type != "text":
                    continue
                first_line = _resolve_heading_text(elem.content)
                if not first_line:
                    continue
                if (_looks_like_body_text(first_line) or _is_false_positive(first_line)
                        or _ends_with_colon(first_line)):
                    continue

                result = detect_numbering_signal(first_line)
                if not result:
                    continue
                signal, _ = result

                if signal == "section_arabic_spaced":
                    m = re.match(r"^(\d{1,3})\s+", first_line)
                    if m:
                        root_entries.append((int(m.group(1)), elem))
                # Note: section_arabic_root ("N.") is NOT collected for
                # coherence promotion.  The "N." format is ambiguous —
                # it matches both section headings and ordered list items.
                # Without font differentiation, coherence alone cannot
                # distinguish them.  Heading detection for "N." format
                # requires font signal (handled in _detect_heading).
                elif signal == "section_arabic_nested":
                    m = re.match(r"^(\d+)\.(\d+)", first_line)
                    if m:
                        root = int(m.group(1))
                        sub = int(m.group(2))
                        nested_entries.setdefault(root, []).append((sub, elem))

        promoted = 0
        _COHERENCE_DENSITY_LIMIT = 8

        # Root-level coherence: ≥3 sequential numbers, but not too many (likely a numbered list)
        if 3 <= len(root_entries) <= _COHERENCE_DENSITY_LIMIT:
            numbers = sorted(set(n for n, _ in root_entries))
            if _is_coherent_sequence(numbers, min_count=3):
                for _, elem in root_entries:
                    if not elem.metadata.get("heading_level"):
                        first_line = _resolve_heading_text(elem.content)
                        if _is_short_heading_text(first_line):
                            elem.metadata["heading_level"] = 2
                            elem.metadata["numbering_coherence"] = True
                            promoted += 1

        # Nested-level coherence per root: ≥2 subsections under the same root
        for root, entries in nested_entries.items():
            if len(entries) >= 2:
                sub_numbers = sorted(set(s for s, _ in entries))
                if _is_coherent_sequence(sub_numbers, min_count=2):
                    for _, elem in entries:
                        if not elem.metadata.get("heading_level"):
                            first_line = _resolve_heading_text(elem.content)
                            if _is_short_heading_text(first_line):
                                elem.metadata["heading_level"] = 3
                                elem.metadata["numbering_coherence"] = True
                                promoted += 1

        # Remove promoted elements from fallback candidates
        if promoted:
            promoted_ids = {
                id(c["element"])
                for c in fallback_candidates
                if c["element"].metadata.get("numbering_coherence")
            }
            fallback_candidates[:] = [
                c for c in fallback_candidates if id(c["element"]) not in promoted_ids
            ]
            log.info("Numbering coherence: promoted %d heading(s)", promoted)

        return promoted

    def _normalize_ocr_title_subtitle_pair(self, doc: Document) -> None:
        """Demote OCR doc-title H1 when it is actually a peer title next to a subtitle.

        Some report covers are labeled by OCR as:
        - `doc_title` -> H1
        - immediately followed `paragraph_title` -> H2

        In practice these are often a same-level title/subtitle pair rather than
        a real document hierarchy. When the second line starts with a repeated
        dash-like prefix, demote the leading OCR H1 to H2 to better match
        downstream heading expectations.
        """
        headings = [elem for elem in doc.all_elements if elem.metadata.get("heading_level")]
        if len(headings) < 2:
            return

        first, second = headings[0], headings[1]
        if (
            first.metadata.get("heading_level") == 1
            and first.source == "ocr"
            and first.layout_type == "doc_title"
            and second.metadata.get("heading_level") == 2
            and second.source == "ocr"
            and second.layout_type == "paragraph_title"
            and _SUBTITLE_PREFIX_RE.match(second.content.strip())
        ):
            first.metadata["heading_level"] = 2
            first.metadata["ocr_heading_level_adjusted"] = "title_subtitle_pair"

    def _merge_cover_heading_fragments(self, doc: Document) -> None:
        """Merge adjacent first-page heading fragments before the first body block.

        Some cover/title pages split one long title across multiple centered
        heading lines with identical hierarchy. These are usually not separate
        sections, and merging them improves downstream heading fidelity without
        relying on document-specific patterns.
        """
        if not doc.pages:
            return

        page = doc.pages[0]
        body_seen = False
        for idx, elem in enumerate(page.elements):
            if elem.type != "text" or elem.metadata.get("skip_render"):
                continue
            if not elem.metadata.get("heading_level"):
                if _looks_like_body_text(elem.content):
                    body_seen = True
                continue
            if body_seen:
                break

            next_idx = idx + 1
            while next_idx < len(page.elements):
                next_elem = page.elements[next_idx]
                if next_elem.type != "text" or next_elem.metadata.get("skip_render"):
                    next_idx += 1
                    continue
                if not next_elem.metadata.get("heading_level"):
                    break
                if not self._should_merge_heading_fragments(elem, next_elem):
                    break
                elem.content = f"{elem.content.strip()}{next_elem.content.strip()}"
                next_elem.metadata["skip_render"] = True
                next_elem.metadata["heading_fragment_merged_into"] = idx
                elem.metadata["heading_fragments_merged"] = (
                    int(elem.metadata.get("heading_fragments_merged", 0)) + 1
                )
                next_idx += 1

    def _should_merge_heading_fragments(
        self,
        first: PageElement,
        second: PageElement,
    ) -> bool:
        if first.metadata.get("heading_level") != second.metadata.get("heading_level"):
            return False
        if first.metadata.get("heading_level") != 1:
            return False

        first_text = first.content.strip()
        second_text = second.content.strip()
        if not first_text or not second_text:
            return False
        if detect_numbering_signal(first_text) or detect_numbering_signal(second_text):
            return False
        if _looks_like_body_text(first_text) or _looks_like_body_text(second_text):
            return False
        if len(first_text) > 40 or len(second_text) > 40:
            return False

        gap = second.bbox[1] - first.bbox[3]
        if gap > max((first.bbox[3] - first.bbox[1]) * 3, 80):
            return False
        if first.bbox[0] > second.bbox[2] or second.bbox[0] > first.bbox[2]:
            return False
        return True

    def _merge_split_section_headings(
        self,
        doc: Document,
        heading_candidates: list[FontInfo],
    ) -> int:
        """Merge heading element with the next sibling holding its title text.

        Layout-based reading-order (Iter 23 PaddleOCR) sometimes splits one
        printed heading like ``"3.1 Single-Device Execution"`` into two
        adjacent PageElements: ``"3.1"`` and ``"Single-Device Execution"``,
        which share an identical bbox.  Only the numbered fragment passes
        heading detection, so the title is lost.

        Two merge cases:
        1. Heading content is numbering-only (``"3.1"``, ``"4.5"``) → join
           the next short-title element with a single space.
        2. Heading content ends with a soft hyphen (``"Imple-"``) or a
           dangling conjunction (``"and"``, ``"or"``) → join the next
           element to complete the title.

        Both cases require: same page, identical or near-identical bbox /
        same column with a tiny vertical gap, and the next element to
        carry a heading-candidate font (so we don't swallow body text).
        """
        if not heading_candidates:
            return 0

        candidate_keys = {_font_key(c) for c in heading_candidates}
        merged = 0
        for page in doc.pages:
            elems = page.elements
            for idx, elem in enumerate(elems):
                if elem.type != "text":
                    continue
                if not elem.metadata.get("heading_level"):
                    continue
                if elem.metadata.get("skip_render"):
                    continue
                head_text = elem.content.strip()
                if not head_text:
                    continue

                trailing_hyphen = head_text.endswith("-")
                numbering_only = bool(_NUMBERING_ONLY_RE.match(head_text))
                dangling_word = bool(_DANGLING_WORD_RE.search(head_text))
                if not (numbering_only or trailing_hyphen or dangling_word):
                    continue

                # Find next non-skip text element on same page.
                nxt = None
                for j in range(idx + 1, len(elems)):
                    cand = elems[j]
                    if cand.type != "text" or cand.metadata.get("skip_render"):
                        continue
                    nxt = cand
                    break
                if nxt is None:
                    continue
                if nxt.metadata.get("heading_level"):
                    continue

                next_text = nxt.content.strip()
                if not next_text or len(next_text) > 80:
                    continue
                if "\n" in nxt.content.strip():
                    continue
                if _font_key(nxt.font) not in candidate_keys:
                    continue
                if _looks_like_body_text(next_text) or _is_metadata_or_cover_line(next_text):
                    continue

                # Geometry: same column, near-identical y-range OR small gap.
                hb = elem.bbox
                nb = nxt.bbox
                same_box = (
                    abs(hb[0] - nb[0]) < 5
                    and abs(hb[1] - nb[1]) < 5
                    and abs(hb[2] - nb[2]) < 5
                    and abs(hb[3] - nb[3]) < 5
                )
                line_height = max(hb[3] - hb[1], 1.0)
                vertical_gap = nb[1] - hb[3]
                column_overlap = min(hb[2], nb[2]) - max(hb[0], nb[0])
                near_below = (
                    -line_height <= vertical_gap <= line_height * 1.2
                    and column_overlap > 0
                )
                if not (same_box or near_below):
                    continue

                if trailing_hyphen:
                    elem.content = head_text[:-1] + next_text
                else:
                    elem.content = f"{head_text} {next_text}"
                # Invalidate stale inline_spans that still reflect the
                # pre-merge (pre-rewrite) fragment text.
                elem.metadata.pop("inline_spans", None)
                nxt.metadata["skip_render"] = True
                nxt.metadata["heading_fragment_merged_into"] = idx
                merged += 1
        return merged

    @staticmethod
    def _split_heading_body_elements(doc: Document) -> int:
        """Split elements that contain both heading text and body text.

        When heading detection marks a multi-line element as a heading,
        only the first line(s) are the actual heading — the rest is body
        text that needs to flow through downstream processors (e.g.
        LineUnwrap).  This method splits such elements into a heading
        element and a body-text element.
        """
        split_count = 0
        for page in doc.pages:
            new_elements: list[PageElement] = []
            for elem in page.elements:
                if (
                    elem.type != "text"
                    or not elem.metadata.get("heading_level")
                    or "\n" not in elem.content
                ):
                    new_elements.append(elem)
                    continue

                # Determine how many lines belong to the heading.
                # _resolve_heading_text may join a pure-number first line
                # with the next line, so we need to account for that.
                lines = elem.content.split("\n")
                heading_lines = 1
                emit_separate = False
                if len(lines) > 1:
                    first = lines[0].strip()
                    is_numbering_first = bool(_NUMBERING_ONLY_RE.match(first))
                    is_continuation_first = (
                        first.endswith("-")
                        or bool(_DANGLING_WORD_RE.search(first))
                    )
                    if (not is_numbering_first
                            and not is_continuation_first
                            and _is_multiline_title(lines, elem.metadata.get("heading_level", 0))):
                        # Multi-line document title — each short,
                        # title-like line becomes its own heading element
                        # (e.g. "TensorFlow:\nLarge-Scale ML ...").
                        heading_lines = _count_title_lines(lines)
                        emit_separate = heading_lines >= 2
                    elif is_numbering_first or is_continuation_first:
                        # Greedily absorb continuation lines that complete
                        # the heading text (short, non-body).  Stop once a
                        # line no longer looks like a continuation (lacks a
                        # trailing hyphen / dangling conjunction).  Bound at
                        # 3 continuation lines so we never swallow a
                        # following paragraph.
                        for i in range(1, min(len(lines), 4)):
                            line_i = lines[i].strip()
                            if not line_i:
                                break
                            if len(line_i) > 60 or _looks_like_body_text(line_i):
                                break
                            heading_lines = i + 1
                            if not (
                                line_i.endswith("-")
                                or _DANGLING_WORD_RE.search(line_i)
                            ):
                                break

                # Check whether there is actual body text after the heading.
                body_lines = [
                    l for l in lines[heading_lines:] if l.strip()
                ]

                # ── Multi-line title: emit each heading line as a
                # separate PageElement (preserving heading_level) so
                # "Title:\nSubtitle" renders as two # lines. ──
                if emit_separate:
                    elem.content = lines[0].strip()
                    elem.metadata.pop("inline_spans", None)
                    new_elements.append(elem)
                    for i in range(1, heading_lines):
                        extra = PageElement(
                            type="text",
                            content=lines[i].strip(),
                            bbox=elem.bbox,
                            page_number=elem.page_number,
                            font=elem.font,
                            metadata={**elem.metadata},
                            confidence=elem.confidence,
                            source=elem.source,
                            layout_type=elem.layout_type,
                        )
                        new_elements.append(extra)
                    if body_lines:
                        body_text = "\n".join(lines[heading_lines:])
                        body_elem = PageElement(
                            type="text",
                            content=body_text,
                            bbox=elem.bbox,
                            page_number=elem.page_number,
                            font=elem.font,
                            metadata={
                                k: v for k, v in elem.metadata.items()
                                if k != "heading_level"
                            },
                            confidence=elem.confidence,
                            source=elem.source,
                            layout_type=elem.layout_type,
                        )
                        new_elements.append(body_elem)
                    split_count += 1
                    continue

                # Heading element keeps heading lines (combined form for
                # number-only + title on next line; hyphen-wrap joins
                # without space).
                if heading_lines >= 2:
                    parts = [l.strip() for l in lines[:heading_lines] if l.strip()]
                    combined = parts[0] if parts else ""
                    for nxt_part in parts[1:]:
                        if combined.endswith("-"):
                            combined = combined[:-1] + nxt_part
                        else:
                            combined = f"{combined} {nxt_part}"
                    heading_text = combined
                else:
                    heading_text = lines[0].strip()
                if not body_lines:
                    # No body to split off, but still rewrite to single-line
                    # form so renderer doesn't emit "## 2\nProgramming..." as
                    # heading + stray body line.
                    if elem.content != heading_text:
                        elem.content = heading_text
                        elem.metadata.pop("inline_spans", None)
                    new_elements.append(elem)
                    continue
                elem.content = heading_text
                elem.metadata.pop("inline_spans", None)

                # Body element gets remaining lines.
                body_text = "\n".join(lines[heading_lines:])
                body_elem = PageElement(
                    type="text",
                    content=body_text,
                    bbox=elem.bbox,
                    page_number=elem.page_number,
                    font=elem.font,
                    metadata={
                        k: v for k, v in elem.metadata.items()
                        if k != "heading_level"
                    },
                    confidence=elem.confidence,
                    source=elem.source,
                    layout_type=elem.layout_type,
                )

                new_elements.append(elem)
                new_elements.append(body_elem)
                split_count += 1

            page.elements = new_elements
        return split_count

    def _keep_existing_ocr_heading(self, page_width: float, elem: PageElement) -> bool:
        """Suppress OCR sidebar labels that are visually prominent but not structural headings."""
        if elem.source != "ocr" or elem.layout_type != "paragraph_title":
            return True
        if not _is_right_sidebar(page_width, elem):
            return True

        text = elem.content.split("\n")[0].strip()
        if not text:
            return False
        numbering = detect_numbering_signal(text)
        if numbering and numbering[0] != "section_arabic_spaced":
            return True
        if ":" in text or "：" in text:
            return True
        if len(text) <= 24:
            elem.metadata.pop("heading_level", None)
            elem.metadata["ocr_heading_suppressed"] = "sidebar_short_label"
            return False
        return True

    def _infer_sidebar_heading_level(self, page_width: float, elem: PageElement) -> int | None:
        """Promote short right-sidebar labels that look like real section headings."""
        if elem.source != "ocr" or elem.layout_type not in {None, "text"}:
            return None
        if not _is_right_sidebar(page_width, elem):
            return None

        text = elem.content.split("\n")[0].strip()
        if not text or len(text) > 40:
            return None
        if not _HEADING_COLON_END_RE.search(text):
            return None
        if _looks_like_body_text(text) or _is_false_positive(text):
            return None
        return 2

    def _build_fallback_candidate(
        self,
        page_elements: list[PageElement],
        elem_idx: int,
        elem: PageElement,
        heading_candidates: list[FontInfo],
    ) -> dict | None:
        if not self._config.llm_fallback or self._llm is None:
            return None

        first_line = _resolve_heading_text(elem.content)
        if not first_line or _is_false_positive(first_line):
            return None
        # Same short-text requirement as _detect_heading — long text is body,
        # not a heading.  The threshold is consistent with _is_short_heading_text.
        if not _is_short_heading_text(first_line):
            return None
        # Geometric gating rejected this element as inline emphasis —
        # don't resurrect it via fallback / LLM.
        if elem.metadata.get("heading_geometric_reject"):
            return None

        font_level = _heading_level_from_font(elem.font, heading_candidates)
        numbering = detect_numbering_signal(first_line)
        numbering_level = _heading_level_from_numbering(first_line)

        zero_signal = False
        if font_level is None and numbering_level is None:
            # When real font info is available (size > 0) and the font is
            # NOT a heading candidate, the font actively indicates body text
            # — do not promote to fallback. Zero-signal is only appropriate
            # when font info is genuinely unavailable (OCR default font).
            if elem.font.size > 0:
                return None
            # Allow short standalone text as zero-signal candidate for LLM.
            # Catches unnumbered section titles (前言, 附录, 专家评审组名单, etc.)
            if (
                len(first_line) > 30
                or _looks_like_body_text(first_line)
                or _is_metadata_or_cover_line(first_line)
                or _ends_with_colon(first_line)
            ):
                return None
            zero_signal = True

        signal_strength = 0
        if font_level is not None:
            signal_strength += 1
        if numbering_level is not None:
            signal_strength += 1

        if signal_strength >= 2:
            return None

        # "N." numbering (section_arabic_root) without font support is too
        # ambiguous — it matches both section headings and ordered list items.
        # When real font info is present and the font is NOT a heading
        # candidate, the element is body text with a numbered prefix — skip.
        # When font info is absent (OCR, size=0), allow LLM to decide.
        if signal_strength == 1 and font_level is None and numbering_level is not None:
            result = detect_numbering_signal(first_line)
            if result and result[0] == "section_arabic_root" and elem.font.size > 0:
                return None

        prev_text = self._neighbor_text(page_elements, elem_idx, direction=-1)
        next_text = self._neighbor_text(page_elements, elem_idx, direction=1)

        ocr_level_hint = elem.metadata.get("ocr_level_hint", 0)

        return {
            "element": elem,
            "page": elem.page_number,
            "text": first_line,
            "font_size": elem.font.size,
            "font_bold": elem.font.bold,
            "font_level_hint": font_level or 0,
            "numbering_signal": numbering[0] if numbering else "",
            "numbering_level_hint": numbering_level or 0,
            "prev_text": prev_text,
            "next_text": next_text,
            "zero_signal": zero_signal,
            "ocr_level_hint": ocr_level_hint,
        }

    def _neighbor_text(
        self,
        elements: list[PageElement],
        start_idx: int,
        *,
        direction: int,
    ) -> str:
        idx = start_idx + direction
        while 0 <= idx < len(elements):
            elem = elements[idx]
            if elem.type == "text" and elem.content.strip():
                return _truncate_text(elem.content, 80)
            idx += direction
        return ""

    # Maximum number of LLM-fallback-only headings at the same level
    # before we suspect they are really a numbered list, not headings.
    _FALLBACK_DENSITY_LIMIT = 8

    def _apply_llm_fallback(
        self,
        doc: Document,
        candidates: list[dict],
    ) -> int:
        if not self._config.llm_fallback or self._llm is None or not candidates:
            return 0

        # Collect all accepted predictions first, then apply density guard.
        pending: list[tuple[PageElement, int]] = []  # (element, level)

        for batch in self._iter_candidate_batches(candidates, _FALLBACK_MAX_CANDIDATES):
            predictions, attempted = self._classify_candidates(batch, doc)
            if attempted:
                _bump_processing_stat(doc, "llm_calls")
            for prediction in predictions:
                idx = _safe_int(prediction.get("idx"))
                level = _safe_int(prediction.get("level"))
                if idx is None or level is None or level not in {0, 1, 2, 3}:
                    continue
                if idx < 1 or idx > len(batch):
                    continue

                candidate = batch[idx - 1]
                elem = candidate["element"]
                if level == 0 or elem.metadata.get("heading_level"):
                    continue

                # Level calibration: the font rank among heading_candidates
                # is authoritative for depth (rank-3+ → H3). When the LLM
                # returns a shallower level than the font hint, clamp down
                # to the hint. Numbering hint, when present, takes
                # precedence over font (handled similarly). This prevents
                # the LLM's prior toward H2 from overriding clear visual
                # evidence that a bold-at-body-size candidate is H3.
                font_hint = candidate.get("font_level_hint") or 0
                numbering_hint = candidate.get("numbering_level_hint") or 0
                if numbering_hint and level < numbering_hint:
                    level = numbering_hint
                elif font_hint and level < font_hint:
                    level = font_hint

                pending.append((elem, level))

        # Density guard: if too many fallback headings appear at the same
        # level, they are likely numbered list items, not real headings.
        level_counts: dict[int, int] = {}
        for _, level in pending:
            level_counts[level] = level_counts.get(level, 0) + 1

        suppressed_levels: set[int] = set()
        for level, count in level_counts.items():
            if count > self._FALLBACK_DENSITY_LIMIT:
                suppressed_levels.add(level)

        if suppressed_levels:
            log.info(
                "Heading density guard: suppressing %d fallback heading(s) "
                "at level(s) %s (likely numbered list)",
                sum(c for lv, c in level_counts.items() if lv in suppressed_levels),
                sorted(suppressed_levels),
            )

        accepted = 0
        for elem, level in pending:
            if level in suppressed_levels:
                continue
            elem.metadata["heading_level"] = level
            elem.metadata["llm_fallback_used"] = True
            elem.metadata["llm_heading_confirmed"] = True
            accepted += 1

        return accepted

    def _iter_candidate_batches(
        self,
        candidates: list[dict],
        batch_size: int,
    ):
        for start in range(0, len(candidates), batch_size):
            yield candidates[start: start + batch_size]

    def _classify_candidates(
        self,
        batch: list[dict],
        doc: Document,
    ) -> tuple[list[dict], bool]:
        attempted = False
        body_font = doc.metadata.font_stats.body_font
        prompt_lines = [
            f"正文参考字体: name={body_font.name or 'unknown'}, size={body_font.size}, bold={body_font.bold}",
            "请判断下面候选是否为标题，并给出层级。优先保持层级连续：大的章节标题更可能是 H1，次级小标题更可能是 H2/H3。",
            "候选列表：",
        ]

        for idx, candidate in enumerate(batch, 1):
            prompt_lines.extend([
                f"{idx}. text={json.dumps(candidate['text'], ensure_ascii=False)}",
                "   "
                f"page={candidate['page']}, font_size={candidate['font_size']}, "
                f"bold={candidate['font_bold']}, font_level_hint={candidate['font_level_hint']}, "
                f"numbering_signal={candidate['numbering_signal'] or '-'}, "
                f"numbering_level_hint={candidate['numbering_level_hint']}",
                f"   prev={json.dumps(candidate['prev_text'], ensure_ascii=False)}",
                f"   next={json.dumps(candidate['next_text'], ensure_ascii=False)}",
            ])
            if candidate.get("zero_signal"):
                prompt_lines.append("   [无字体/编号信号，仅根据文本内容和上下文判断]")
            if candidate.get("ocr_level_hint"):
                prompt_lines.append(f"   [OCR布局检测建议层级: H{candidate['ocr_level_hint']}]")

        prompt_lines.append('仅返回 JSON 数组，例如: [{"idx":1,"level":2},{"idx":2,"level":0}]')
        user_prompt = "\n".join(prompt_lines)

        try:
            attempted = True
            response = self._llm.complete(
                _FALLBACK_SYSTEM_PROMPT,
                user_prompt,
                temperature=0.0,
                max_tokens=2048,
            )
        except Exception as exc:
            log.warning("Chapter LLM fallback failed: %s", exc)
            return [], True

        try:
            parsed = json.loads(response)
        except json.JSONDecodeError:
            log.warning("Chapter LLM fallback returned non-JSON response")
            return [], attempted

        if isinstance(parsed, list):
            return [item for item in parsed if isinstance(item, dict)], attempted
        return [], attempted
