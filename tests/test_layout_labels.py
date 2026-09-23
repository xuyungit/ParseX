"""Label mapping (guide §6.2): every known engine label has a destination; unknown labels keep content."""

from parserx.ir.enums import BlockKind
from parserx.layout import labels

# PaddleOCR-VL's PP-DocLayout label set (25 classes).
PADDLE_LABELS = {
    "abstract", "algorithm", "aside_text", "chart", "content", "display_formula", "doc_title", "figure_title",
    "footer", "footer_image", "footnote", "formula_number", "header", "header_image", "image", "inline_formula",
    "number", "paragraph_title", "reference", "reference_content", "seal", "table", "text", "vertical_text",
    "vision_footnote",
}


def test_every_paddleocr_label_is_mapped():
    assert PADDLE_LABELS <= set(labels.PADDLEOCR)
    assert all(isinstance(kind, BlockKind) for kind in labels.PADDLEOCR.values())


def test_unknown_label_keeps_its_content_as_other():
    assert not labels.is_known("paddleocr", "new_label")
    assert labels.to_kind("paddleocr", "new_label") == BlockKind.OTHER


def test_page_furniture_kinds():
    assert {labels.to_kind("paddleocr", x) for x in ("header", "footer", "number")} <= labels.FURNITURE
