"""Evaluation-side normalization (metric version 2.0).

Kept separate from ``parserx.text_utils``: v1 processors use that module for
their own thresholds, so changing it would change v1 output.

Both sides of every comparison go through the same steps:

1. HTML comments (page anchors etc.) are removed.
2. Tables — GFM or HTML — are replaced by their cell text in row-major order,
   so the table *format* never affects text scores (structure is scored by
   the table metric).
3. Image placeholders are removed and counted: ``![alt](src)``,
   ``> [图片] …`` lines, and a blockquote directly following an image line
   (the v1 description layout).  Descriptions are scored separately later
   (guide §9.3); here they must not bias text scores toward one model's
   wording.
4. For character-level comparison: NFKC (Kangxi radicals, full-width forms),
   heading markers dropped, all whitespace dropped.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass

from parserx.tables import TableGrid, find_tables

_COMMENT_RE = re.compile(r"<!--.*?-->", re.DOTALL)
_IMAGE_RE = re.compile(r"!\[[^\]]*\]\([^)]*\)")
_IMAGE_LINE_RE = re.compile(r"^\s*!\[[^\]]*\]\([^)]*\)\s*$")
_PLACEHOLDER_LINE_RE = re.compile(r"^\s*>\s*\[图片\]")
_BLOCKQUOTE_RE = re.compile(r"^\s*>")
_HEADING_MARK_RE = re.compile(r"^\s{0,3}#{1,6}\s+", re.MULTILINE)
_TAG_RE = re.compile(r"<[^>]+>")
_BR_RE = re.compile(r"<br\s*/?>", re.IGNORECASE)
_WS_RE = re.compile(r"\s+")
_NON_WORD_RE = re.compile(r"[\W_]+")


@dataclass(frozen=True)
class CanonicalDoc:
    text: str  # newlines kept, so paragraphs remain splittable on blank lines
    image_count: int


def canonicalize(markdown: str) -> CanonicalDoc:
    text = _COMMENT_RE.sub("", markdown)
    text = _flatten_tables(text)
    text, image_count = _strip_images(text)
    return CanonicalDoc(text=text, image_count=image_count)


def char_sequence(text: str) -> str:
    """Character sequence used by char_f1 / edit distance / order anchors."""
    text = unicodedata.normalize("NFKC", text)
    text = _HEADING_MARK_RE.sub("", text)
    return _WS_RE.sub("", text)


def normalize_cell(text: str) -> str:
    text = unicodedata.normalize("NFKC", _BR_RE.sub(" ", text))
    return _WS_RE.sub("", text).lower()


def normalize_label(text: str) -> str:
    """Cell text with punctuation removed, for comparing header paths ("A > B" == "A" + "B")."""
    return _NON_WORD_RE.sub("", normalize_cell(text))


def grid_text(grid: TableGrid) -> str:
    """Cell contents in row-major order: cells joined by spaces, rows by newlines."""
    rows: dict[int, list[tuple[int, str]]] = {}
    for cell in grid.cells:
        if cell.content.strip():
            rows.setdefault(cell.row, []).append((cell.col, cell.content))
    return "\n".join(
        " ".join(content for _, content in sorted(rows[r])) for r in sorted(rows)
    )


def _flatten_tables(text: str) -> str:
    parts: list[str] = []
    cursor = 0
    for span in find_tables(text):
        parts.append(text[cursor:span.start])
        raw = text[span.start:span.end]
        body = grid_text(span.grid) if span.grid is not None else _TAG_RE.sub(" ", raw)
        parts.append(f"\n\n{body}\n\n")
        cursor = span.end
    parts.append(text[cursor:])
    return "".join(parts)


def _strip_images(text: str) -> tuple[str, int]:
    kept: list[str] = []
    count = 0
    drop_quote = False  # inside a blockquote that directly follows an image line
    after_image = False  # only blank lines seen since the last image line
    for line in text.split("\n"):
        if _IMAGE_LINE_RE.match(line):
            count += 1
            after_image, drop_quote = True, False
            continue
        if _PLACEHOLDER_LINE_RE.match(line):
            count += 1
            after_image, drop_quote = False, False
            continue
        if not line.strip():
            drop_quote = False
            kept.append(line)
            continue
        if _BLOCKQUOTE_RE.match(line) and (after_image or drop_quote):
            drop_quote, after_image = True, False
            continue
        after_image = drop_quote = False
        inline = len(_IMAGE_RE.findall(line))
        if inline:
            count += inline
            line = _IMAGE_RE.sub("", line)
        kept.append(line)
    return "\n".join(kept), count
