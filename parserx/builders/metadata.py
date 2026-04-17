"""MetadataBuilder — extract document-level metadata using deterministic code.

This replaces the 3-pass LLM chapter detection in legacy pipeline by analyzing
font statistics, page geometry, and numbering patterns directly from the
character-level metadata already extracted by PDFProvider.

Key insight: PDFProvider gives us font name, size, bold for every text block.
The most common font is body text; larger/bolder fonts are heading candidates.
This is deterministic — no LLM needed for 70-80% of documents.
"""

from __future__ import annotations

import re
from collections import Counter

from parserx.config.schema import MetadataBuilderConfig
from parserx.models.elements import (
    Document,
    DocumentMetadata,
    FontInfo,
    FontStatistics,
    NumberingPattern,
)


# ── Numbering detection (migrated from legacy pipeline chapter_outline_core.py L108-138) ──

_NUMBERING_PATTERNS: list[tuple[str, str, str]] = [
    # (signal_name, regex, default_heading_level)
    # Chapter: ``第一章``, ``第十编``.  ``\b`` is dropped so the pattern also
    # matches ``第一章竞争性谈判公告`` (no whitespace between 章 and the title).
    # CJK chars are all word-class in Python regex, so ``\b`` between two CJK
    # chars never fires.
    ("chapter_cn", r"^第[一二三四五六七八九十百千万0-9]+[编章节部分]", "H1"),
    # Chinese-article style: 第一条, 第二条, 第十三条 — used for contract
    # clauses and regulatory articles.  Mapped to H3 by default; document
    # hierarchy adjusts via coherence promotion.  ``\b`` is not used because
    # CJK characters are all word-chars in Python regex; the ``^`` anchor
    # already guards against inline references like ``根据第一条规定``.
    ("article_cn", r"^第[一二三四五六七八九十百千万零〇0-9]+条", "H3"),
    # Appendix / attachment marker: 附件一, 附件二 — commonly used in
    # contracts and regulatory docs to introduce a supplementary section.
    ("appendix_cn", r"^附[件录][一二三四五六七八九十百千万零〇0-9]+", "H2"),
    ("section_cn", r"^[一二三四五六七八九十百千万]+[、.．)]", "H2"),
    ("section_cn_paren", r"^[（(][一二三四五六七八九十百千万]+[）)]", "H2"),
    # Nested arabic: 1.2, 1.2.3.  Allow the title to start immediately after
    # the number (no delimiter required) for CJK text — ``1.4费用承担`` is
    # indistinguishable from ``1.4 费用承担`` in intent.
    (
        "section_arabic_nested",
        r"^\d+\.\d+(?:\.\d+)*(?:[\s、.．)]|(?=[\u4e00-\u9fff]))",
        "H3",
    ),
    ("section_arabic_paren", r"^(?:[（(]\d+[）)]|\d+[）)])", "H3"),
    # Arabic with ideographic comma: 1、评审方法, 2、谈判小组 — common in
    # Chinese regulatory/tender docs where 、 replaces . as the separator.
    ("section_arabic_ideograph", r"^\d{1,3}、[\u4e00-\u9fffA-Za-z]", "H2"),
    ("section_arabic_spaced", r"^\d{1,3}\s+[\u4e00-\u9fffA-Za-z]", "H2"),  # max 3 digits, excludes years like 2026
    # Root arabic ``N.``.  Historically required trailing whitespace; that
    # made ``4.报价有效期`` (no space) fall through even when the same
    # series had ``1. 总则`` (spaced).  Accept both forms; ambiguity with
    # ordered-list items is resolved at the heading-detection layer via
    # font/coherence signals.
    ("section_arabic_root", r"^\d+\.\s*[\u4e00-\u9fffA-Za-z]", "H2"),
]


_FULLWIDTH_NUMBER_SEP = str.maketrans({
    "\uFF0E": ".",  # ． → .
    "\uFF10": "0", "\uFF11": "1", "\uFF12": "2", "\uFF13": "3", "\uFF14": "4",
    "\uFF15": "5", "\uFF16": "6", "\uFF17": "7", "\uFF18": "8", "\uFF19": "9",
})


def detect_numbering_signal(text: str) -> tuple[str, str] | None:
    """Detect chapter/section numbering pattern in text.

    Returns (signal_name, heading_level) or None.
    Migrated from legacy pipeline chapter_outline_core.py detect_numbering_signal.

    Normalizes full-width digits and the full-width full stop ``．`` (U+FF0E)
    before matching so items like ``9．监督`` detect the same as ``9.监督``.
    TextClean applies the same normalization globally later in the pipeline
    but heading detection runs first; this local normalization keeps the
    two paths in sync without depending on processor order.
    """
    stripped = text.strip()
    # Remove markdown marks
    stripped = re.sub(r"^\s{0,3}#{1,6}\s*", "", stripped)
    stripped = stripped.replace("**", "").replace("*", "")
    stripped = stripped.translate(_FULLWIDTH_NUMBER_SEP)

    for signal, pattern, level in _NUMBERING_PATTERNS:
        if re.match(pattern, stripped):
            return signal, level
    return None


def _font_key(font: FontInfo) -> str:
    """Create a hashable key for font comparison."""
    return f"{font.name}_{font.size}_{font.bold}"


class MetadataBuilder:
    """Extract document-level metadata without LLM.

    Analyzes:
    1. Font statistics → body font vs heading candidates
    2. Page geometry → header/footer zones
    3. Numbering patterns → chapter numbering hierarchy
    4. Page types → already set by PDFProvider
    """

    def __init__(self, config: MetadataBuilderConfig | None = None):
        self._config = config or MetadataBuilderConfig()

    def build(self, doc: Document) -> Document:
        """Analyze document and populate DocumentMetadata."""
        self._build_font_statistics(doc)
        self._detect_heading_candidates(doc)
        self._detect_numbering_patterns(doc)
        self._build_page_types(doc)
        return doc

    def _build_font_statistics(self, doc: Document) -> None:
        """Count font usage across all text elements to find body font."""
        font_char_counts: Counter[str] = Counter()

        for page in doc.pages:
            for elem in page.elements:
                if elem.type != "text":
                    continue
                key = _font_key(elem.font)
                font_char_counts[key] += len(elem.content)

        if not font_char_counts:
            return

        doc.metadata.font_stats.font_counts = dict(font_char_counts)

        # Most common font = body text
        most_common_key = font_char_counts.most_common(1)[0][0]
        # Parse back the font info from the key
        for page in doc.pages:
            for elem in page.elements:
                if elem.type == "text" and _font_key(elem.font) == most_common_key:
                    doc.metadata.font_stats.body_font = elem.font.model_copy()
                    return

    def _detect_heading_candidates(self, doc: Document) -> None:
        """Find fonts that are larger or bolder than body — these are heading candidates.

        Applies a frequency filter: fonts that appear too often (by
        character count) are likely secondary body fonts (labels, nav
        links), not true headings.  A font whose char count exceeds
        ``heading_max_char_ratio`` of the body font's char count is
        excluded.
        """
        body = doc.metadata.font_stats.body_font
        if body.size == 0:
            return

        ratio = self._config.heading_font_ratio
        font_counts = doc.metadata.font_stats.font_counts or {}

        # Body font char count as denominator for frequency ratio.
        body_key = _font_key(body)
        body_chars = font_counts.get(body_key, 1)

        # Pre-compute char counts grouped by (size, bold) to catch fonts
        # that appear under different names at the same size.  Bold and
        # regular variants are counted separately because a bold variant
        # at the same size as body text is a heading signal, not body text.
        # Grouping them together would cause the large body-text count to
        # mask the small bold-heading count.
        size_bold_group_chars: dict[tuple[float, bool], int] = {}
        for key, count in font_counts.items():
            # Key format: "name_size_bold"
            parts = key.rsplit("_", 2)
            if len(parts) >= 3:
                try:
                    size = float(parts[-2])
                    bold = parts[-1] == "True"
                    rounded = round(size * 2) / 2  # round to 0.5pt
                    group_key = (rounded, bold)
                    size_bold_group_chars[group_key] = (
                        size_bold_group_chars.get(group_key, 0) + count
                    )
                except ValueError:
                    pass

        seen_keys: set[str] = set()
        candidates: list[FontInfo] = []

        for page in doc.pages:
            for elem in page.elements:
                if elem.type != "text":
                    continue
                key = _font_key(elem.font)
                if key in seen_keys:
                    continue
                seen_keys.add(key)

                is_heading = False
                # Larger font size
                if elem.font.size >= body.size * ratio:
                    is_heading = True
                # Same size but bold when body is not
                elif elem.font.bold and not body.bold and elem.font.size >= body.size:
                    is_heading = True

                if not is_heading:
                    continue

                # Frequency filter: fonts whose (size, bold) group appears
                # too often relative to body text are likely secondary body
                # fonts (labels, nav links on receipts/emails).
                rounded_size = round(elem.font.size * 2) / 2
                group_key = (rounded_size, elem.font.bold)
                group_chars = size_bold_group_chars.get(group_key, 0)
                if body_chars > 0 and group_chars > body_chars * self._config.heading_max_char_ratio:
                    continue

                candidates.append(elem.font.model_copy())

        # Sort by size descending — larger fonts = higher heading level
        candidates.sort(key=lambda f: (-f.size, not f.bold))
        doc.metadata.font_stats.heading_candidates = candidates

    def _detect_numbering_patterns(self, doc: Document) -> None:
        """Scan text elements for chapter/section numbering patterns."""
        pattern_counts: Counter[str] = Counter()

        for page in doc.pages:
            for elem in page.elements:
                if elem.type != "text":
                    continue
                # Check first line of each text block
                first_line = elem.content.split("\n")[0].strip()
                result = detect_numbering_signal(first_line)
                if result:
                    signal, _ = result
                    pattern_counts[signal] += 1

        patterns: list[NumberingPattern] = []
        for (signal, regex, level) in _NUMBERING_PATTERNS:
            count = pattern_counts.get(signal, 0)
            if count > 0:
                patterns.append(NumberingPattern(
                    signal=signal,
                    level=level,
                    count=count,
                    regex=regex,
                ))

        doc.metadata.numbering_patterns = patterns

    def _build_page_types(self, doc: Document) -> None:
        """Populate page_types dict from per-page classification."""
        doc.metadata.page_types = {
            page.number: page.page_type for page in doc.pages
        }
