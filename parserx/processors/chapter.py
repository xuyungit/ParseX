"""Chapter/heading detection processor.

Two-stage approach:
1. Rule-based: font size/bold analysis + numbering pattern matching (covers 70-80%)
2. LLM fallback: only when rule-based confidence is low (sends heading candidates,
   not full text — one LLM call instead of three)

Numbering detection regexes migrated from legacy pipeline chapter_outline_core.py.
"""

from __future__ import annotations

import logging
import re

from parserx.builders.metadata import detect_numbering_signal
from parserx.config.schema import ProcessorToggle
from parserx.models.elements import Document, FontInfo, PageElement

log = logging.getLogger(__name__)

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


class ChapterProcessor:
    """Detect chapter/section headings and assign heading levels.

    Sets element.metadata["heading_level"] = 1/2/3 for detected headings.
    The MarkdownRenderer uses this to output # / ## / ### prefixes.
    """

    def __init__(self, config: ProcessorToggle | None = None):
        self._config = config or ProcessorToggle()

    def process(self, doc: Document) -> Document:
        if not self._config.enabled:
            return doc

        heading_candidates = doc.metadata.font_stats.heading_candidates
        body_font = doc.metadata.font_stats.body_font
        numbering_patterns = doc.metadata.numbering_patterns

        detected_count = 0

        for page in doc.pages:
            for elem in page.elements:
                if elem.type != "text":
                    continue

                # Skip elements with heading_level already set (e.g. from DOCX styles)
                if elem.metadata.get("heading_level"):
                    detected_count += 1
                    continue

                level = self._detect_heading(
                    elem, heading_candidates, body_font, numbering_patterns
                )
                if level is not None:
                    elem.metadata["heading_level"] = level
                    detected_count += 1

        log.info("Detected %d headings", detected_count)
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
