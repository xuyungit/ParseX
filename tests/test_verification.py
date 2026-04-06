"""Tests for verification layer checks."""

from pathlib import Path

from parserx.assembly.markdown import MarkdownRenderer
from parserx.models.elements import Document, Page, PageElement
from parserx.verification import (
    CompletenessChecker,
    HallucinationDetector,
    StructureValidator,
)
from parserx.verification.completeness import _count_rendered_tables


def test_structure_validator_detects_hierarchy_issues(tmp_path: Path):
    doc = Document(
        pages=[
            Page(
                number=1,
                elements=[
                    PageElement(type="text", content="第一章 总则", metadata={"heading_level": 1}),
                    PageElement(type="text", content="三级标题", metadata={"heading_level": 3}),
                    PageElement(type="text", content="", metadata={"heading_level": 2}),
                ],
            )
        ]
    )
    chapters_dir = tmp_path / "chapters"
    chapters_dir.mkdir()
    (chapters_dir / "ch_01.md").write_text("", encoding="utf-8")

    warnings = StructureValidator().validate(doc, tmp_path)

    assert any("jump from H1 to H3" in warning for warning in warnings)
    assert any("orphan H3" in warning for warning in warnings)
    assert any("empty heading" in warning for warning in warnings)
    assert any("Chapter file is empty" in warning for warning in warnings)


def test_completeness_checker_detects_missing_references():
    image = PageElement(
        type="image",
        page_number=1,
        metadata={
            "needs_vlm": True,
            "saved_path": "images/figure-1.png",
            "description": "流程图说明",
        },
    )
    table = PageElement(
        type="table",
        page_number=1,
        content="| A | B |\n|---|---|\n| 1 | 2 |",
    )
    text = PageElement(type="text", page_number=1, content="正文内容")
    doc = Document(
        pages=[Page(number=1, elements=[text, image, table])],
    )

    warnings = CompletenessChecker().check(doc, "<!-- PAGE 1 -->\n\n正文内容")

    assert any("image output missing rendered reference" in warning for warning in warnings)
    assert any("Table count mismatch" in warning for warning in warnings)


def test_completeness_checker_stays_quiet_when_output_matches():
    image = PageElement(
        type="image",
        page_number=1,
        metadata={
            "needs_vlm": True,
            "saved_path": "images/figure-1.png",
            "description": "流程图说明",
        },
    )
    table_md = "| A | B |\n|---|---|\n| 1 | 2 |"
    table = PageElement(type="table", page_number=1, content=table_md)
    text = PageElement(type="text", page_number=1, content="正文内容")
    doc = Document(pages=[Page(number=1, elements=[text, image, table])])

    markdown = (
        "<!-- PAGE 1 -->\n\n正文内容\n\n"
        "![流程图说明](images/figure-1.png)\n\n"
        f"{table_md}"
    )
    warnings = CompletenessChecker().check(doc, markdown)

    assert warnings == []


def test_completeness_checker_compacts_ocr_overlap_image_reference():
    """OCR-overlap text-heavy images are suppressed when no saved_path exists."""
    long_text = "采购金额与项目范围说明" * 50
    image = PageElement(
        type="image",
        page_number=1,
        metadata={
            "needs_vlm": True,
            "description": long_text,
            "description_source": "ocr_overlap_evidence",
            "text_heavy_image": True,
        },
    )
    text = PageElement(type="text", page_number=1, content=long_text)
    doc = Document(pages=[Page(number=1, elements=[text, image])])

    markdown = MarkdownRenderer().render(doc)
    warnings = CompletenessChecker().check(doc, markdown)

    # Description is suppressed — no placeholder or duplicate text in output.
    assert "preserved in OCR body text" not in markdown
    assert markdown.count(long_text) == 1
    assert warnings == []


def test_ocr_overlap_text_heavy_with_saved_path_renders_minimal():
    """OCR-overlap text-heavy image with saved_path renders image link only."""
    image = PageElement(
        type="image",
        page_number=1,
        metadata={
            "needs_vlm": True,
            "description": "一些已在正文中出现的文字",
            "description_source": "ocr_overlap_evidence",
            "text_heavy_image": True,
            "saved_path": "images/p1_img1.png",
        },
    )
    doc = Document(pages=[Page(number=1, elements=[image])])

    markdown = MarkdownRenderer().render(doc)

    assert "![](images/p1_img1.png)" in markdown
    assert "一些已在正文中出现的文字" not in markdown


def test_suppressed_ocr_overlap_image_no_page_marker_mismatch():
    """A page with only a suppressed OCR-overlap image should not trigger
    a page-marker mismatch warning — the renderer produces no output for
    that page, and the completeness checker should agree."""
    image = PageElement(
        type="image",
        page_number=1,
        metadata={
            "needs_vlm": True,
            "description": "这些文字已经在正文中",
            "description_source": "ocr_overlap_evidence",
            "text_heavy_image": True,
        },
    )
    doc = Document(pages=[Page(number=1, elements=[image])])

    markdown = MarkdownRenderer().render(doc)
    warnings = CompletenessChecker().check(doc, markdown)

    assert "Page marker mismatch" not in " ".join(warnings)
    assert warnings == []


def test_completeness_checker_ignores_non_renderable_images():
    image = PageElement(
        type="image",
        page_number=1,
        metadata={
            "needs_vlm": True,
        },
    )
    doc = Document(pages=[Page(number=1, elements=[image])])

    warnings = CompletenessChecker().check(doc, "")

    assert warnings == []


def test_hallucination_detector_marks_low_confidence_image():
    image = PageElement(
        type="image",
        page_number=1,
        bbox=(0.0, 0.0, 100.0, 100.0),
        metadata={
            "description": "采购金额为 999 万元。",
            "image_class": "text_image",
        },
    )
    ocr_text = PageElement(
        type="text",
        page_number=1,
        bbox=(5.0, 5.0, 95.0, 95.0),
        content="采购金额为 100 万元。",
        source="ocr",
    )
    doc = Document(pages=[Page(number=1, elements=[image, ocr_text])])

    warnings = HallucinationDetector().detect(doc)

    assert image.metadata["low_confidence"] is True
    assert image.metadata["vlm_confidence"] < 0.8
    assert any("low-confidence VLM description" in warning for warning in warnings)


def test_hallucination_detector_stays_quiet_when_evidence_matches():
    image = PageElement(
        type="image",
        page_number=1,
        bbox=(0.0, 0.0, 100.0, 100.0),
        metadata={
            "description": "采购金额为 100 万元。",
            "image_class": "text_image",
        },
    )
    ocr_text = PageElement(
        type="text",
        page_number=1,
        bbox=(5.0, 5.0, 95.0, 95.0),
        content="采购金额为 100 万元。",
        source="ocr",
    )
    doc = Document(pages=[Page(number=1, elements=[image, ocr_text])])

    warnings = HallucinationDetector().detect(doc)

    assert warnings == []
    assert image.metadata["low_confidence"] is False


def test_hallucination_detector_ignores_ocr_overlap_descriptions_skipped_from_vlm():
    image = PageElement(
        type="image",
        page_number=1,
        bbox=(0.0, 0.0, 100.0, 100.0),
        metadata={
            "description": "采购金额为 100 万元。",
            "description_source": "ocr_overlap_evidence",
            "vlm_skipped_due_to_large_text_overlap": True,
        },
    )
    ocr_text = PageElement(
        type="text",
        page_number=1,
        bbox=(5.0, 5.0, 95.0, 95.0),
        content="采购金额为 100 万元。",
        source="ocr",
    )
    doc = Document(pages=[Page(number=1, elements=[image, ocr_text])])

    warnings = HallucinationDetector().detect(doc)

    assert warnings == []
    assert image.metadata["low_confidence"] is False


def test_structure_validator_stays_quiet_for_valid_hierarchy():
    doc = Document(
        pages=[
            Page(
                number=1,
                elements=[
                    PageElement(type="text", content="第一章 总则", metadata={"heading_level": 1}),
                    PageElement(type="text", content="一、基本原则", metadata={"heading_level": 2}),
                    PageElement(type="text", content="（一）范围", metadata={"heading_level": 3}),
                ],
            )
        ]
    )

    warnings = StructureValidator().validate(doc)

    assert warnings == []


def test_structure_validator_allows_document_starting_at_h2():
    doc = Document(
        pages=[
            Page(
                number=1,
                elements=[
                    PageElement(type="text", content="7.3.6 定量杀菌检验", metadata={"heading_level": 2}),
                    PageElement(type="text", content="7.4 乙型肝炎表面抗原破坏试验", metadata={"heading_level": 2}),
                ],
            )
        ]
    )

    warnings = StructureValidator().validate(doc)

    assert warnings == []


def test_count_rendered_tables_counts_distinct_blocks():
    markdown = (
        "正文\n\n"
        "| A | B |\n|---|---|\n| 1 | 2 |\n\n"
        "| C |\n|---|\n| 3 |"
    )
    assert _count_rendered_tables(markdown) == 2


def test_hallucination_detector_skips_vlm_summary_with_corrected_content():
    """VLM summary descriptions are semantic — skip hallucination check when
    the image already has vlm_corrected_text or vlm_corrected_table."""
    image = PageElement(
        type="image",
        page_number=8,
        bbox=(0.0, 0.0, 500.0, 400.0),
        metadata={
            "description": "这是一个节点详情管理页面截图",
            "description_source": "vlm_summary",
            "vlm_corrected_table": "| 节点ID | 02012ee1 |\n|---|---|\n| 资源类 | baremetal-node |",
        },
    )
    ocr_text = PageElement(
        type="text",
        page_number=8,
        bbox=(10.0, 10.0, 490.0, 390.0),
        content="节点ID 02012ee1 资源类 baremetal-node",
        source="native",
    )
    doc = Document(pages=[Page(number=8, elements=[image, ocr_text])])

    warnings = HallucinationDetector().detect(doc)

    assert warnings == []
    assert image.metadata["low_confidence"] is False


def test_is_renderable_true_for_skipped_image_with_vlm_corrections():
    """Skipped images with vlm_corrected_table should be renderable."""
    from parserx.verification.completeness import _is_renderable

    image = PageElement(
        type="image",
        page_number=6,
        metadata={
            "skipped": True,
            "vlm_corrected_table": "| A | B |\n|---|---|\n| 1 | 2 |",
        },
    )
    assert _is_renderable(image) is True

    image_no_vlm = PageElement(
        type="image",
        page_number=6,
        metadata={"skipped": True},
    )
    assert _is_renderable(image_no_vlm) is False


def test_text_volume_includes_vlm_corrected_content():
    """Text volume check should include VLM-corrected content from images."""
    text_elem = PageElement(type="text", content="正文内容", page_number=1)
    image_elem = PageElement(
        type="image",
        page_number=1,
        metadata={
            "vlm_corrected_text": "注册节点 节点信息 驱动详情",
            "vlm_corrected_table": "| A | B |\n|---|---|\n| 1 | 2 |",
            "saved_path": "images/fig1.png",
        },
    )
    doc = Document(pages=[Page(number=1, elements=[text_elem, image_elem])])

    renderer = MarkdownRenderer()
    markdown = renderer.render(doc)

    checker = CompletenessChecker()
    warnings = checker._check_text_volume(doc, markdown)

    assert not any("volume drifted" in w for w in warnings)
