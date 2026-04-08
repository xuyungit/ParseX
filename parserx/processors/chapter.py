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
_SUBTITLE_PREFIX_RE = re.compile(r"^[—–\-一]{2,}")
_HEADING_COLON_END_RE = re.compile(r"[：:]$")

# Price/currency: "$200.00", "¥1,000", "€50.00", etc.
_PRICE_RE = re.compile(r"^[$¥€£₽]\s*[\d,]+(?:\.\d+)?$")
# Navigation link: ends with "›" or trailing " >" (common in emails/receipts)
_NAV_LINK_RE = re.compile(r"[›»>]\s*$")


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
    """Infer heading level from numbering pattern."""
    result = detect_numbering_signal(text)
    if not result:
        return None
    _, level_str = result
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


def _is_right_sidebar(page_width: float, elem: PageElement) -> bool:
    if page_width <= 0:
        return False
    return elem.bbox[0] >= page_width * 0.6


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

                # Skip elements with heading_level already set (e.g. from DOCX styles)
                if elem.metadata.get("heading_level"):
                    if not self._keep_existing_ocr_heading(page.width, elem):
                        continue
                    detected_count += 1
                    continue

                inferred_level = self._infer_sidebar_heading_level(page.width, elem)
                if inferred_level is not None:
                    elem.metadata["heading_level"] = inferred_level
                    elem.metadata["ocr_heading_inferred"] = "sidebar_colon_label"
                    detected_count += 1
                    continue

                level = self._detect_heading(
                    elem, heading_candidates, body_font, numbering_patterns
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

        fallback_hits = self._apply_llm_fallback(doc, fallback_candidates)
        detected_count += fallback_hits
        self._normalize_ocr_title_subtitle_pair(doc)
        self._merge_cover_heading_fragments(doc)

        log.info(
            "Detected %d headings (%d via fallback)",
            detected_count,
            fallback_hits,
        )
        return doc

    def _detect_heading(
        self,
        elem: PageElement,
        heading_candidates: list[FontInfo],
        body_font: FontInfo,
        numbering_patterns: list,
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
        first_line = elem.content.split("\n")[0].strip()

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
            if (_is_short_heading_text(first_line)
                    and not _looks_like_body_text(first_line)
                    and not _is_metadata_or_cover_line(first_line)):
                return font_level

        return None

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

        first_line = elem.content.split("\n")[0].strip()
        if not first_line or _is_false_positive(first_line):
            return None
        if len(first_line) > 120:
            return None

        font_level = _heading_level_from_font(elem.font, heading_candidates)
        numbering = detect_numbering_signal(first_line)
        numbering_level = _heading_level_from_numbering(first_line)

        if font_level is None and numbering_level is None:
            return None

        signal_strength = 0
        if font_level is not None:
            signal_strength += 1
        if numbering_level is not None:
            signal_strength += 1

        if signal_strength >= 2:
            return None

        prev_text = self._neighbor_text(page_elements, elem_idx, direction=-1)
        next_text = self._neighbor_text(page_elements, elem_idx, direction=1)

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

                elem = batch[idx - 1]["element"]
                if level == 0 or elem.metadata.get("heading_level"):
                    continue

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
