"""Evaluation-side normalization (metric version 2.0).

Kept separate from ``parserx.eval.text`` (formerly ``parserx.text_utils``): v1
processors use that module for their own thresholds, so changing it would change v1 output.

Both sides of every comparison go through the same steps:

0. Text read inside an image (``<!-- parserx:image-text … -->`` … ``<!-- /parserx:image-text -->``, IO6-5) is
   text: its label line is dropped, its quote marks and the bold of a title read in it are removed.
1. HTML comments (page anchors etc.) are removed.
2. Tables — GFM or HTML — are replaced by their cell text in row-major order,
   so the table *format* never affects text scores (structure is scored by
   the table metric).  A cell repeating the one directly above or to its left
   is written once (2.1): a merged value counts once whether it is written as
   a span, repeated in every row, or written once with blanks around it.
3. Image placeholders are removed and counted: ``![alt](src)``,
   ``> [图片] …`` lines, notes on missing content (``> 〔未识别〕…``, Q117), and a blockquote directly following an image line
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
_IMAGE_TEXT_RE = re.compile(r"<!-- parserx:image-text[^\n]*?-->\n(.*?)\n<!-- /parserx:image-text -->", re.DOTALL)
_IMAGE_TEXT_LABEL_RE = re.compile(r"^>\s*\*\*(〔图片识别〕|\[Text from image\])\*\*")
_QUOTE_MARK_RE = re.compile(r"^> ?", re.MULTILINE)
_BOLD_LINE_RE = re.compile(r"^\*\*(.+)\*\*$", re.MULTILINE)
_IMAGE_RE = re.compile(r"!\[[^\]]*\]\([^)]*\)")
_IMAGE_LINE_RE = re.compile(r"^\s*!\[[^\]]*\]\([^)]*\)\s*$")
_PLACEHOLDER_LINE_RE = re.compile(r"^\s*>\s*(\[图片\]|〔未识别〕|〔Not recognised〕)")  # + notes on missing content (Q117)
_BLOCKQUOTE_RE = re.compile(r"^\s*>")
_HEADING_MARK_RE = re.compile(r"^\s{0,3}#{1,6}\s+", re.MULTILINE)
_TAG_RE = re.compile(r"<[^>]+>")
_BR_RE = re.compile(r"<br\s*/?>", re.IGNORECASE)
_WS_RE = re.compile(r"\s+")
_TILDE_ESCAPE_RE = re.compile(r"\\~")  # the renderer writes a tilde that could pair into strikethrough as \~
# inline emphasis is formatting, not text (R3, metric 2.4): scored apart (``eval/formatting.py``)
_EMPHASIS_RE = re.compile(r"\*\*|</?(?:u|b|strong|em|i|ins)>", re.IGNORECASE)
_STAR_ESCAPE_RE = re.compile(r"\\\*")
_NON_WORD_RE = re.compile(r"[\W_]+")


@dataclass(frozen=True)
class CanonicalDoc:
    text: str  # newlines kept, so paragraphs remain splittable on blank lines
    image_count: int


def canonicalize(markdown: str) -> CanonicalDoc:
    text = _IMAGE_TEXT_RE.sub(_unquote_image_text, _TILDE_ESCAPE_RE.sub("~", markdown))
    text = _STAR_ESCAPE_RE.sub("*", _EMPHASIS_RE.sub("", text))
    text = _COMMENT_RE.sub("", text)
    text = _flatten_tables(text)
    text, image_count = _strip_images(text)
    return CanonicalDoc(text=text, image_count=image_count)


def _unquote_image_text(match: re.Match) -> str:
    lines = match.group(1).split("\n")
    if lines and _IMAGE_TEXT_LABEL_RE.match(lines[0]):
        lines = lines[1:]
    body = _BOLD_LINE_RE.sub(r"\1", _QUOTE_MARK_RE.sub("", "\n".join(lines)))
    return f"\n\n{body}\n\n"


def char_sequence(text: str) -> str:
    """Character sequence used by char_f1 / edit distance / order anchors."""
    text = unicodedata.normalize("NFKC", text)
    text = _HEADING_MARK_RE.sub("", text)
    return _WS_RE.sub("", text)


def normalize_cell(text: str) -> str:
    text = _STAR_ESCAPE_RE.sub("*", _EMPHASIS_RE.sub("", _TILDE_ESCAPE_RE.sub("~", text)))
    text = unicodedata.normalize("NFKC", _BR_RE.sub(" ", text))
    return _WS_RE.sub("", text).lower()


def normalize_label(text: str) -> str:
    """Cell text with punctuation removed, for comparing header paths ("A > B" == "A" + "B")."""
    return _NON_WORD_RE.sub("", normalize_cell(text))


def grid_text(grid: TableGrid) -> str:
    """Cell contents in row-major order: cells joined by spaces, rows by newlines.

    Positions repeating the content directly above or to the left are skipped,
    so merged cells count once in every notation.
    """
    matrix = grid.slot_matrix()
    lines: list[str] = []
    for r, row in enumerate(matrix):
        parts: list[str] = []
        for c, cell in enumerate(row):
            if cell is None or not cell.content.strip():
                continue
            left = row[c - 1] if c else None
            above = matrix[r - 1][c] if r else None
            if (left is not None and left.content == cell.content) or (
                above is not None and above.content == cell.content
            ):
                continue
            parts.append(cell.content)
        if parts:
            lines.append(" ".join(parts))
    return "\n".join(lines)


_SCRIPT_FORMS: dict[str, dict[str, str]] = {"sup": {}, "sub": {}}  # base character → its super- / subscript form
for _cp in range(0x80, 0x10000):
    _decomposition = unicodedata.decomposition(chr(_cp))
    for _kind, _mark in (("sup", "<super>"), ("sub", "<sub>")):
        if _decomposition.startswith(_mark):
            _SCRIPT_FORMS[_kind].setdefault(unicodedata.normalize("NFKC", chr(_cp)), chr(_cp))
_HTML_TABLE_RE = re.compile(r"<table\b.*?</table>", re.IGNORECASE | re.DOTALL)
_HTML_SCRIPT_RE = re.compile(r"<(sup|sub)>(.*?)</\1>", re.IGNORECASE | re.DOTALL)


def _cell_scripts(table: str) -> str:
    """HTML super- and subscripts of a table written so the cell text keeps them (the grid keeps text, not tags):
    their Unicode forms where every character has one (NFKC folds them back for the text metrics), else LaTeX."""
    def written(match: re.Match) -> str:
        kind, body = match.group(1).lower(), _TAG_RE.sub("", match.group(2))
        forms = _SCRIPT_FORMS[kind]
        if body and all(ch in forms for ch in body):
            return "".join(forms[ch] for ch in body)
        return f"${'^' if kind == 'sup' else '_'}{{{body}}}$"

    return _HTML_SCRIPT_RE.sub(written, table)


def _flatten_tables(text: str) -> str:
    text = _HTML_TABLE_RE.sub(lambda m: _cell_scripts(m.group(0)), text)
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
