"""Tests for figure/table caption resolution."""

from pathlib import Path

from parserx.assembly.crossref import CrossReferenceResolver
from parserx.assembly.markdown import MarkdownRenderer
from parserx.config.schema import ParserXConfig
from parserx.models.elements import Document, Page, PageElement
from parserx.pipeline import Pipeline


def test_resolver_attaches_figure_caption_and_skips_original_text():
    image = PageElement(
        type="image",
        page_number=1,
        bbox=(100, 100, 400, 300),
        metadata={"saved_path": "images/fig-1.png", "width": 300, "height": 200},
    )
    caption = PageElement(
        type="text",
        page_number=1,
        bbox=(150, 310, 350, 330),
        content="图 1 系统架构图",
    )
    doc = Document(pages=[Page(number=1, elements=[image, caption])])

    CrossReferenceResolver().resolve(doc)
    markdown = MarkdownRenderer().render(doc)

    assert image.metadata["caption"] == "图 1 系统架构图"
    assert caption.metadata["skip_render"] is True
    assert markdown.count("图 1 系统架构图") == 1
    assert "*图 1 系统架构图*" in markdown


def test_resolver_attaches_table_caption_above_table():
    caption = PageElement(
        type="text",
        page_number=1,
        bbox=(120, 80, 360, 100),
        content="表 2 关键参数",
    )
    table = PageElement(
        type="table",
        page_number=1,
        bbox=(100, 110, 420, 240),
        content="| 名称 | 值 |\n|---|---|\n| A | 1 |",
    )
    doc = Document(pages=[Page(number=1, elements=[caption, table])])

    CrossReferenceResolver().resolve(doc)
    markdown = MarkdownRenderer().render(doc)

    assert table.metadata["caption"] == "表 2 关键参数"
    assert caption.metadata["skip_render"] is True
    assert "**表 2 关键参数**\n\n| 名称 | 值 |" in markdown


def test_resolver_does_not_misclassify_regular_text_starting_with_tu():
    image = PageElement(
        type="image",
        page_number=1,
        bbox=(100, 100, 400, 300),
        metadata={"saved_path": "images/fig-1.png", "width": 300, "height": 200},
    )
    text = PageElement(
        type="text",
        page_number=1,
        bbox=(150, 310, 350, 330),
        content="图示某某方法的原理",
    )
    doc = Document(pages=[Page(number=1, elements=[image, text])])

    CrossReferenceResolver().resolve(doc)
    markdown = MarkdownRenderer().render(doc)

    assert "caption" not in image.metadata
    assert "skip_render" not in text.metadata
    assert "图示某某方法的原理" in markdown


def test_resolver_matches_multiple_figures_to_nearest_captions():
    image1 = PageElement(
        type="image",
        page_number=1,
        bbox=(50, 100, 250, 220),
        metadata={"saved_path": "images/fig-1.png", "width": 200, "height": 120},
    )
    caption1 = PageElement(
        type="text",
        page_number=1,
        bbox=(70, 225, 230, 245),
        content="图 1 左侧流程",
    )
    image2 = PageElement(
        type="image",
        page_number=1,
        bbox=(300, 100, 520, 220),
        metadata={"saved_path": "images/fig-2.png", "width": 220, "height": 120},
    )
    caption2 = PageElement(
        type="text",
        page_number=1,
        bbox=(330, 225, 500, 245),
        content="图 2 右侧流程",
    )
    doc = Document(pages=[Page(number=1, elements=[image1, caption1, image2, caption2])])

    CrossReferenceResolver().resolve(doc)

    assert image1.metadata["caption"] == "图 1 左侧流程"
    assert image2.metadata["caption"] == "图 2 右侧流程"


def test_resolver_falls_back_to_sequential_match_when_bboxes_overlap():
    image = PageElement(
        type="image",
        page_number=1,
        bbox=(100, 100, 400, 300),
        metadata={"saved_path": "images/fig-1.png", "width": 300, "height": 200},
    )
    caption = PageElement(
        type="text",
        page_number=1,
        bbox=(130, 280, 370, 320),
        content="Fig 1. Overlap case",
    )
    trailing = PageElement(
        type="text",
        page_number=1,
        bbox=(100, 340, 420, 380),
        content="后续正文内容",
    )
    doc = Document(pages=[Page(number=1, elements=[image, caption, trailing])])

    CrossReferenceResolver().resolve(doc)

    assert image.metadata["caption"] == "Fig 1. Overlap case"
    assert caption.metadata["skip_render"] is True


def test_resolver_accepts_caption_at_exact_length_boundary():
    prefix = "Figure 1: "
    title = "A" * (160 - len(prefix))
    caption_text = f"{prefix}{title}"
    image = PageElement(
        type="image",
        page_number=1,
        bbox=(100, 100, 400, 300),
        metadata={"saved_path": "images/fig-1.png", "width": 300, "height": 200},
    )
    caption = PageElement(
        type="text",
        page_number=1,
        bbox=(140, 310, 380, 330),
        content=caption_text,
    )
    doc = Document(pages=[Page(number=1, elements=[image, caption])])

    CrossReferenceResolver().resolve(doc)

    assert len(caption_text) == 160
    assert image.metadata["caption"] == caption_text


def test_pipeline_renders_captioned_output(tmp_path: Path, monkeypatch):
    image = PageElement(
        type="image",
        page_number=1,
        bbox=(100, 100, 400, 300),
        metadata={"saved_path": "images/fig-1.png", "width": 300, "height": 200},
    )
    caption = PageElement(
        type="text",
        page_number=1,
        bbox=(150, 310, 360, 330),
        content="Figure 1. Pipeline overview",
    )
    doc = Document(pages=[Page(number=1, elements=[image, caption])])

    cfg = ParserXConfig()
    cfg.builders.ocr.engine = "none"
    pipeline = Pipeline(cfg)

    monkeypatch.setattr(pipeline, "_extract", lambda path: doc)
    monkeypatch.setattr(pipeline, "_extract_and_describe_images", lambda d, source, images_dir: d)

    dummy = tmp_path / "dummy.pdf"
    dummy.write_bytes(b"%PDF-1.4 fake")

    pipeline.parse_to_dir(dummy, tmp_path)
    markdown = (tmp_path / "final.md").read_text(encoding="utf-8")

    assert "![](images/fig-1.png)" in markdown
    assert markdown.count("Figure 1. Pipeline overview") == 1
    assert "*Figure 1. Pipeline overview*" in markdown
