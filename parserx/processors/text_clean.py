"""Text cleaning processor.

Handles CJK space removal, encoding fixes, and text normalization.
Migrated from doc-refine: pdf_extract.py _fix_chinese_spaces (L49-57).
"""

from __future__ import annotations

import re

from parserx.config.schema import TextCleanConfig
from parserx.models.elements import Document

# ── CJK space fix (migrated from doc-refine pdf_extract.py L34-57) ──────

_CJK = (
    r"\u4e00-\u9fff\u3400-\u4dbf\uf900-\ufaff"
    r"\u3000-\u303f\uff00-\uffef"
)
_CJK_CHAR = f"[{_CJK}]"
_CJK_PUNCT = (
    r"[\u3001\u3002\uff0c\uff0e\uff1a\uff1b\uff01\uff1f"
    r"\u300a\u300b\u3008\u3009\u300c\u300d\u2018\u2019\u201c\u201d\uff08\uff09]"
)
_RE_CJK_SPACE = re.compile(rf"({_CJK_CHAR})[^\S\n]+({_CJK_CHAR})")
_RE_CJK_PUNCT_SPACE = re.compile(rf"({_CJK_CHAR})[^\S\n]+({_CJK_PUNCT})")
_RE_PUNCT_CJK_SPACE = re.compile(rf"({_CJK_PUNCT})[^\S\n]+({_CJK_CHAR})")


def fix_chinese_spaces(text: str) -> str:
    """Remove spurious spaces between CJK characters.

    PDF extractors often insert spaces between CJK characters that
    don't exist in the original document.
    """
    prev = None
    while prev != text:
        prev = text
        text = _RE_CJK_SPACE.sub(r"\1\2", text)
        text = _RE_CJK_PUNCT_SPACE.sub(r"\1\2", text)
        text = _RE_PUNCT_CJK_SPACE.sub(r"\1\2", text)
    return text


# ── Windows-1252 C1 range recovery (inspired by LiteParse) ─────────────

_WINDOWS_1252_MAP = {
    0x80: "\u20AC", 0x82: "\u201A", 0x83: "\u0192", 0x84: "\u201E",
    0x85: "\u2026", 0x86: "\u2020", 0x87: "\u2021", 0x88: "\u02C6",
    0x89: "\u2030", 0x8A: "\u0160", 0x8B: "\u2039", 0x8C: "\u0152",
    0x8E: "\u017D", 0x91: "\u2018", 0x92: "\u2019", 0x93: "\u201C",
    0x94: "\u201D", 0x95: "\u2022", 0x96: "\u2013", 0x97: "\u2014",
    0x98: "\u02DC", 0x99: "\u2122", 0x9A: "\u0161", 0x9B: "\u203A",
    0x9C: "\u0153", 0x9E: "\u017E", 0x9F: "\u0178",
}


def fix_c1_encoding(text: str) -> str:
    """Map C1 control characters (0x80-0x9F) to proper Unicode."""
    result = []
    for ch in text:
        code = ord(ch)
        if 0x80 <= code <= 0x9F and code in _WINDOWS_1252_MAP:
            result.append(_WINDOWS_1252_MAP[code])
        else:
            result.append(ch)
    return "".join(result)


def clean_control_chars(text: str) -> str:
    """Remove control characters except common whitespace."""
    return re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]", "", text)


def normalize_whitespace(text: str) -> str:
    """Normalize multiple spaces to single space (preserve newlines)."""
    return re.sub(r"[^\S\n]+", " ", text)


class TextCleanProcessor:
    """Clean text artifacts from extracted content."""

    def __init__(self, config: TextCleanConfig | None = None):
        self._config = config or TextCleanConfig()

    def process(self, doc: Document) -> Document:
        for page in doc.pages:
            for element in page.elements:
                if element.type in ("text", "header", "footer"):
                    element.content = self._clean(element.content)
        return doc

    def _clean(self, text: str) -> str:
        text = clean_control_chars(text)
        if self._config.fix_encoding:
            text = fix_c1_encoding(text)
        if self._config.fix_cjk_spaces:
            text = fix_chinese_spaces(text)
        text = normalize_whitespace(text)
        return text.strip()
