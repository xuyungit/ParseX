"""Tests for verification layer checks."""

from pathlib import Path

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


def test_count_rendered_tables_counts_distinct_blocks():
    markdown = (
        "正文\n\n"
        "| A | B |\n|---|---|\n| 1 | 2 |\n\n"
        "| C |\n|---|\n| 3 |"
    )
    assert _count_rendered_tables(markdown) == 2
