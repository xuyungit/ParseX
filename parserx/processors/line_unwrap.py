"""Line unwrap processor for fixing visual hard line breaks."""

from __future__ import annotations

import re
from statistics import median

from parserx.config.schema import LineUnwrapConfig
from parserx.models.elements import Document, FontInfo, PageElement

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
        return current + next_line

    return current + " " + next_line


def _should_merge_lines(current: str, next_line: str, average_line_length: float) -> bool:
    """Decide whether a visual line break should be removed."""
    current = current.rstrip()
    next_line = next_line.lstrip()

    if not current or not next_line:
        return False

    if _looks_like_list_item(current) or _looks_like_list_item(next_line):
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
        return len(current.strip()) >= max(10, effective_avg * 0.8)

    return False


def _unwrap_text_block(text: str, average_line_length: float) -> str:
    """Remove visual line breaks inside a text block while preserving paragraphs."""
    if "\n" not in text:
        return text

    lines = text.splitlines()
    if not lines:
        return text

    result: list[str] = []
    current: str | None = None

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
            continue

        if _should_merge_lines(current, line, average_line_length):
            current = _join_lines(current, line)
        else:
            result.append(current)
            current = line

    if current is not None:
        result.append(current)

    return "\n".join(result)


class LineUnwrapProcessor:
    """Fix paragraph-internal hard line breaks introduced by PDF extraction."""

    def __init__(self, config: LineUnwrapConfig | None = None):
        self._config = config or LineUnwrapConfig()

    def process(self, doc: Document) -> Document:
        if not self._config.enabled:
            return doc

        average_line_length = self._estimate_body_line_length(doc)

        for page in doc.pages:
            for element in page.elements:
                if element.type != "text":
                    continue
                if element.metadata.get("heading_level"):
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
