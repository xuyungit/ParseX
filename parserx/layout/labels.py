"""Engine labels → BlockKind (guide §6.2). The only place label sets may differ.

Every label a source is known to emit has an entry (``tests/test_layout_labels.py``
checks the tables are complete).  A label a service starts emitting later maps
to OTHER — its content is kept and rendered as text — and callers record a
warning (``is_known``).  Labels are evidence about a region's role, not
verdicts on whether its content exists: header / footer / page-number kinds
are excluded from the Markdown but stay in the sidecar with a Decision.
"""

from __future__ import annotations

from parserx.ir.enums import BlockKind

# PaddleOCR-VL layout labels (the 25-class PP-DocLayout set).
PADDLEOCR: dict[str, BlockKind] = {
    "text": BlockKind.TEXT,
    "abstract": BlockKind.TEXT,
    "content": BlockKind.TEXT,            # table-of-contents entries
    "reference": BlockKind.TEXT,
    "reference_content": BlockKind.TEXT,
    "aside_text": BlockKind.TEXT,
    "vertical_text": BlockKind.TEXT,
    "algorithm": BlockKind.TEXT,
    "formula_number": BlockKind.TEXT,     # "(3.1)" beside a formula: kept in the text flow
    "paragraph_title": BlockKind.TITLE,
    "doc_title": BlockKind.TITLE,
    "table": BlockKind.TABLE,
    "image": BlockKind.FIGURE,
    "chart": BlockKind.FIGURE,
    "seal": BlockKind.FIGURE,
    "display_formula": BlockKind.FORMULA,
    "inline_formula": BlockKind.FORMULA,
    "figure_title": BlockKind.CAPTION,
    "footnote": BlockKind.FOOTNOTE,
    "vision_footnote": BlockKind.FOOTNOTE,
    "header": BlockKind.HEADER,
    "header_image": BlockKind.HEADER,
    "footer": BlockKind.FOOTER,
    "footer_image": BlockKind.FOOTER,
    "number": BlockKind.PAGE_NUMBER,
}

SOURCES: dict[str, dict[str, BlockKind]] = {"paddleocr": PADDLEOCR}

# Kinds that are page furniture: not rendered, recorded as excluded (guide §6.3).
FURNITURE = frozenset({BlockKind.HEADER, BlockKind.FOOTER, BlockKind.PAGE_NUMBER})


def is_known(source: str, label: str) -> bool:
    return label in SOURCES[source]


def to_kind(source: str, label: str) -> BlockKind:
    return SOURCES[source].get(label, BlockKind.OTHER)
