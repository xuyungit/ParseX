"""Code block processor — detect monospace-font code regions and mark them.

Detection strategy:
- Identify monospace fonts by name (Monaco, Menlo, Courier, etc.)
- Only mark code blocks when the document uses a MIX of monospace and
  proportional fonts (if everything is monospace, skip — it's just the
  document's body font)
- Merge consecutive code-block elements on the same page into a single
  element with newline-separated content
- Downstream processors (ChapterProcessor, LineUnwrapProcessor) skip
  elements tagged with ``code_block`` metadata
"""

from __future__ import annotations

import logging
import re

from collections import Counter

from parserx.config.schema import CodeBlockConfig
from parserx.models.elements import Document, FontInfo, PageElement

log = logging.getLogger(__name__)

# Known monospace font family patterns (case-insensitive substring match).
# Content patterns that indicate "text with inline code", not a code block.
# If element content starts with these, the element should NOT be tagged as
# code_block even when the dominant font is monospace.
_NON_CODE_START_RE = re.compile(
    r"^\s*(?:"
    # Numbered list: "1. ", "12) ", "i. ", "ii. ", "iii. "
    r"(?:\d+|[ivxIVX]+)[.)、]\s"
    # CJK characters (body text, not code)
    r"|[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff]"
    # Roman numeral sub-items
    r"|[a-zA-Z][.)]\s"
    r")"
)

_MONO_PATTERNS: tuple[re.Pattern[str], ...] = tuple(
    re.compile(p, re.IGNORECASE)
    for p in (
        r"monaco",
        r"menlo",
        r"courier",
        r"consolas",
        r"monospace",
        r"source\s*code\s*pro",
        r"fira\s*code",
        r"fira\s*mono",
        r"jetbrains\s*mono",
        r"roboto\s*mono",
        r"sf\s*mono",
        r"ubuntu\s*mono",
        r"droid\s*sans\s*mono",
        r"dejavu\s*sans\s*mono",
        r"liberation\s*mono",
        r"lucida\s*console",
        r"andale\s*mono",
        r"noto\s*sans\s*mono",
        r"ibm\s*plex\s*mono",
        r"hack(?!ney)",  # Hack font, but not Hackney
        r"inconsolata",
    )
)


def is_monospace_font(font_name: str) -> bool:
    """Return True if *font_name* matches a known monospace font family."""
    if not font_name:
        return False
    return any(p.search(font_name) for p in _MONO_PATTERNS)


def _has_bbox(elem: PageElement) -> bool:
    return elem.bbox != (0.0, 0.0, 0.0, 0.0)


class CodeBlockProcessor:
    """Detect and tag monospace code regions in mixed-font documents."""

    def __init__(self, config: CodeBlockConfig | None = None):
        self._config = config or CodeBlockConfig()

    def process(self, doc: Document) -> Document:
        if not self._config.enabled:
            return doc

        # Classify all text elements by font type.
        has_mono = False
        has_proportional = False

        for page in doc.pages:
            for elem in page.elements:
                if elem.type != "text":
                    continue
                if elem.metadata.get("skip_render"):
                    continue
                if is_monospace_font(elem.font.name):
                    has_mono = True
                else:
                    has_proportional = True
                if has_mono and has_proportional:
                    break
            if has_mono and has_proportional:
                break

        if not has_mono or not has_proportional:
            # All-mono or all-proportional: no code blocks to detect.
            return doc

        # Tag monospace text elements as code blocks.
        tagged = 0
        for page in doc.pages:
            for elem in page.elements:
                if elem.type != "text":
                    continue
                if elem.metadata.get("skip_render"):
                    continue
                if not is_monospace_font(elem.font.name):
                    continue
                # Skip elements that start with non-code patterns (e.g.,
                # numbered list items with inline code like
                # "2. 停止osd容器，docker stop ceph_osd_6").
                if _NON_CODE_START_RE.match(elem.content):
                    continue
                elem.metadata["code_block"] = True
                tagged += 1

        if tagged == 0:
            return doc

        # Merge consecutive code-block elements on the same page.
        merged = 0
        for page in doc.pages:
            page.elements, count = self._merge_consecutive_code(page.elements)
            merged += count

        total = tagged - merged
        log.info("Detected %d code block region(s) (%d elements merged)", total, merged)

        # Recalculate body font excluding code_block elements.
        # When code has more characters than prose, the body font gets set to
        # the monospace font, skewing all downstream font-based decisions.
        self._recalculate_body_font(doc)

        return doc

    @staticmethod
    def _recalculate_body_font(doc: Document) -> None:
        """Recompute body font and font counts excluding code_block elements."""
        font_char_counts: Counter[str, int] = Counter()
        font_samples: dict[str, FontInfo] = {}

        for page in doc.pages:
            for elem in page.elements:
                if elem.type != "text":
                    continue
                if elem.metadata.get("code_block"):
                    continue
                key = f"{elem.font.name}_{elem.font.size}_{elem.font.bold}"
                font_char_counts[key] += len(elem.content)
                if key not in font_samples:
                    font_samples[key] = elem.font

        if not font_char_counts:
            return

        most_common_key = font_char_counts.most_common(1)[0][0]
        new_body = font_samples[most_common_key]

        old_body = doc.metadata.font_stats.body_font
        if new_body.name != old_body.name or new_body.size != old_body.size:
            doc.metadata.font_stats.body_font = new_body.model_copy()
            doc.metadata.font_stats.font_counts = dict(font_char_counts)
            log.info(
                "Recalculated body font: %s %.1fpt (was %s %.1fpt)",
                new_body.name, new_body.size, old_body.name, old_body.size,
            )

    @staticmethod
    def _merge_consecutive_code(
        elements: list[PageElement],
    ) -> tuple[list[PageElement], int]:
        """Merge adjacent code_block elements into single elements."""
        if not elements:
            return elements, 0

        result: list[PageElement] = []
        current: PageElement | None = None
        merge_count = 0

        for elem in elements:
            if not elem.metadata.get("code_block"):
                if current is not None:
                    result.append(current)
                    current = None
                result.append(elem)
                continue

            if current is None:
                current = elem
                continue

            # Merge into current: join content with newline.
            current.content = current.content.rstrip() + "\n" + elem.content.lstrip("\n")
            # Expand bbox.
            if _has_bbox(current) and _has_bbox(elem):
                current.bbox = (
                    min(current.bbox[0], elem.bbox[0]),
                    min(current.bbox[1], elem.bbox[1]),
                    max(current.bbox[2], elem.bbox[2]),
                    max(current.bbox[3], elem.bbox[3]),
                )
            merge_count += 1

        if current is not None:
            result.append(current)

        return result, merge_count
