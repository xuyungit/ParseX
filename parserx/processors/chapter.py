"""Chapter/heading detection processor.

Two-stage approach:
1. Rule-based: font size/bold analysis + numbering pattern matching (covers 70-80%)
2. LLM fallback: only when rule-based confidence is low (sends heading candidates,
   not full text — one LLM call instead of three)

Numbering detection regexes migrated from doc-refine chapter_outline_core.py.
"""

from __future__ import annotations

import logging
import re

from parserx.builders.metadata import detect_numbering_signal
from parserx.config.schema import ProcessorToggle
from parserx.models.elements import Document, FontInfo, PageElement

log = logging.getLogger(__name__)


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
    # Ends with sentence punctuation
    if text and text[-1] in "。！？!?.;；，,":
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
        """
        first_line = elem.content.split("\n")[0].strip()

        # Skip obviously non-heading content
        if _looks_like_body_text(first_line):
            return None

        # Signal 1: Font-based detection
        font_level = _heading_level_from_font(elem.font, heading_candidates)

        # Signal 2: Numbering-based detection
        numbering_level = _heading_level_from_numbering(first_line)

        # Decision logic
        if font_level is not None and numbering_level is not None:
            # Both signals agree — high confidence, prefer numbering level
            # because it's more semantically precise
            return numbering_level

        if numbering_level is not None:
            # Numbering alone is a strong signal for Chinese docs
            if _is_short_heading_text(first_line):
                return numbering_level

        if font_level is not None:
            # Font alone — only accept if text is short and doesn't look like body
            if _is_short_heading_text(first_line) and not _looks_like_body_text(first_line):
                return font_level

        return None
