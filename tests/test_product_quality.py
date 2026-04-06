"""Tests for ProductQualityChecker — semi-automatic product-quality checks."""

from __future__ import annotations

import tempfile
from pathlib import Path

from parserx.models.elements import Document, Page, PageElement
from parserx.verification.product_quality import ProductQualityChecker


def _empty_doc() -> Document:
    return Document(pages=[Page(number=1, elements=[])])


# ------------------------------------------------------------------
# Check 1: Placeholder / debug text leakage
# ------------------------------------------------------------------


def test_detects_placeholder_leakage():
    md = "# Title\n\n![Text content preserved in OCR body text.](img.png)\n\nBody"
    warnings = ProductQualityChecker().check(_empty_doc(), md)
    assert any("Placeholder/debug text" in w for w in warnings)


def test_detects_metadata_key_leakage():
    md = "# Title\n\nThe description_source was set incorrectly.\n\nBody"
    warnings = ProductQualityChecker().check(_empty_doc(), md)
    assert any("description_source" in w for w in warnings)


def test_no_warning_for_clean_markdown():
    md = "# Title\n\n正文内容\n\n| A | B |\n|---|---|\n| 1 | 2 |"
    warnings = ProductQualityChecker().check(_empty_doc(), md)
    placeholder_warnings = [w for w in warnings if "Placeholder" in w]
    assert placeholder_warnings == []


def test_ignores_html_comments_for_placeholder_check():
    md = "<!-- preserved in body text -->\n\n# Title\n\nBody"
    warnings = ProductQualityChecker().check(_empty_doc(), md)
    placeholder_warnings = [w for w in warnings if "Placeholder" in w]
    assert placeholder_warnings == []


# ------------------------------------------------------------------
# Check 2: HTML table leakage
# ------------------------------------------------------------------


def test_detects_html_table_leakage():
    md = "# Title\n\n<table><tr><td>Cell</td></tr></table>\n\nBody"
    warnings = ProductQualityChecker().check(_empty_doc(), md)
    assert any("HTML table markup" in w for w in warnings)


def test_detects_multiple_html_tables():
    md = "<table><tr><td>A</td></tr></table>\n\n<table><tr><td>B</td></tr></table>"
    warnings = ProductQualityChecker().check(_empty_doc(), md)
    html_warnings = [w for w in warnings if "HTML table markup" in w]
    assert len(html_warnings) == 1
    assert "2 occurrence" in html_warnings[0]


def test_no_html_warning_for_markdown_tables():
    md = "# Title\n\n| A | B |\n|---|---|\n| 1 | 2 |"
    warnings = ProductQualityChecker().check(_empty_doc(), md)
    html_warnings = [w for w in warnings if "HTML table" in w]
    assert html_warnings == []


# ------------------------------------------------------------------
# Check 3: Image asset linkage
# ------------------------------------------------------------------


def test_image_ref_missing_file():
    md = "![Chart](images/chart.png)\n\nBody"
    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp)
        (out / "images").mkdir()
        warnings = ProductQualityChecker().check(_empty_doc(), md, out)
    assert any("not found on disk" in w for w in warnings)


def test_orphan_image_file():
    md = "# Title\n\nBody only"
    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp)
        (out / "images").mkdir()
        (out / "images" / "unused.png").write_bytes(b"\x89PNG")
        warnings = ProductQualityChecker().check(_empty_doc(), md, out)
    assert any("not referenced" in w for w in warnings)


def test_all_images_linked():
    md = "![Chart](images/chart.png)\n\nBody"
    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp)
        (out / "images").mkdir()
        (out / "images" / "chart.png").write_bytes(b"\x89PNG")
        warnings = ProductQualityChecker().check(_empty_doc(), md, out)
    asset_warnings = [w for w in warnings if "Image" in w]
    assert asset_warnings == []


def test_skips_linkage_check_when_no_output_dir():
    md = "![Chart](images/chart.png)"
    warnings = ProductQualityChecker().check(_empty_doc(), md, None)
    asset_warnings = [w for w in warnings if "Image" in w]
    assert asset_warnings == []


# ------------------------------------------------------------------
# Check 4: Duplicate body text
# ------------------------------------------------------------------


def test_duplicate_body_text_detected():
    body_text = "采购金额与项目范围说明，包括具体的技术要求和交付标准" * 3
    text_elem = PageElement(type="text", page_number=1, content=body_text)
    img_elem = PageElement(
        type="image",
        page_number=1,
        metadata={
            "text_heavy_image": True,
            "description": body_text,
        },
    )
    doc = Document(pages=[Page(number=1, elements=[text_elem, img_elem])])
    md = f"# Title\n\n{body_text}"

    warnings = ProductQualityChecker().check(doc, md)
    assert any("duplicates" in w for w in warnings)


def test_no_duplicate_warning_when_description_differs():
    text_elem = PageElement(
        type="text", page_number=1, content="项目背景与需求分析报告",
    )
    img_elem = PageElement(
        type="image",
        page_number=1,
        metadata={
            "text_heavy_image": True,
            "description": "A flowchart showing process steps for procurement",
        },
    )
    doc = Document(pages=[Page(number=1, elements=[text_elem, img_elem])])
    md = "# Title\n\n项目背景与需求分析报告"

    warnings = ProductQualityChecker().check(doc, md)
    dup_warnings = [w for w in warnings if "duplicates" in w]
    assert dup_warnings == []


def test_no_duplicate_warning_for_skipped_images():
    body_text = "采购金额与项目范围说明" * 5
    text_elem = PageElement(type="text", page_number=1, content=body_text)
    img_elem = PageElement(
        type="image",
        page_number=1,
        metadata={
            "text_heavy_image": True,
            "description": body_text,
            "skipped": True,
        },
    )
    doc = Document(pages=[Page(number=1, elements=[text_elem, img_elem])])
    md = f"# Title\n\n{body_text}"

    warnings = ProductQualityChecker().check(doc, md)
    dup_warnings = [w for w in warnings if "duplicates" in w]
    assert dup_warnings == []
