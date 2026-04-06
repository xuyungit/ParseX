"""Tests for content-value scoring and low-information suppression."""

from parserx.config.schema import ContentValueConfig
from parserx.models.elements import Document, Page, PageElement
from parserx.processors.content_value import ContentValueProcessor


def test_content_value_suppresses_banner_like_shell_text():
    doc = Document(
        pages=[
            Page(
                number=1,
                width=2000,
                height=1200,
                elements=[
                    PageElement(
                        type="text",
                        content="ChatGPT\n登录\n注册",
                        bbox=(40, 20, 1900, 50),
                    ),
                    PageElement(
                        type="text",
                        content="这是 ChatGPT 与匿名用户之间的对话副本。",
                        bbox=(820, 80, 1080, 110),
                    ),
                    PageElement(
                        type="text",
                        content="举报内容",
                        bbox=(900, 120, 980, 145),
                    ),
                    PageElement(
                        type="text",
                        content="我想研究 DeepSeek R1/V3 发布后对国内外大模型公司的影响。",
                        bbox=(780, 160, 1280, 230),
                    ),
                ],
            )
        ]
    )

    ContentValueProcessor(ContentValueConfig()).process(doc)

    assert doc.pages[0].elements[0].metadata["skip_render"] is True
    assert "wide_sparse_banner" in doc.pages[0].elements[0].metadata["informational_value_reason"]
    assert "skip_render" not in doc.pages[0].elements[2].metadata


def test_content_value_suppresses_repeated_small_assets():
    doc = Document(
        pages=[
            Page(
                number=1,
                width=1600,
                height=1200,
                elements=[
                    PageElement(
                        type="image",
                        bbox=(50, 50, 178, 178),
                        metadata={"xref": 29, "image_class": "informational", "needs_vlm": True},
                    ),
                    PageElement(
                        type="image",
                        bbox=(1400, 50, 1528, 178),
                        metadata={"xref": 29, "image_class": "informational", "needs_vlm": True},
                    ),
                ],
            )
        ]
    )

    ContentValueProcessor(ContentValueConfig()).process(doc)

    for image in doc.elements_by_type("image"):
        assert image.metadata["skipped"] is True
        assert image.metadata["needs_vlm"] is False
        assert image.metadata["low_information_value"] == "repeated_small_asset"


def test_content_value_preserves_text_heavy_image_with_evidence():
    image = PageElement(
        type="image",
        bbox=(100, 100, 700, 500),
        metadata={
            "xref": 12,
            "needs_vlm": True,
            "text_heavy_image": True,
            "description": "保留文字证据",
        },
    )
    doc = Document(pages=[Page(number=1, width=1000, height=1400, elements=[image])])

    ContentValueProcessor(ContentValueConfig()).process(doc)

    assert image.metadata.get("skipped") is not True
    assert image.metadata["informational_value_reason"] == "text_evidence_image"


def test_content_value_preserves_compact_list_after_colon_prompt():
    doc = Document(
        pages=[
            Page(
                number=1,
                width=1600,
                height=1200,
                elements=[
                    PageElement(
                        type="text",
                        content="你希望研究的影响范围包括哪些方面？例如：",
                        bbox=(500, 200, 1100, 240),
                    ),
                    PageElement(
                        type="text",
                        content="价格调整（是否有降价现象）",
                        bbox=(560, 260, 980, 300),
                    ),
                    PageElement(
                        type="text",
                        content="新产品推出（是否加快了新版本或竞品的推出）",
                        bbox=(560, 320, 1080, 360),
                    ),
                    PageElement(
                        type="text",
                        content="给 ChatGPT 发送消息",
                        bbox=(560, 380, 760, 420),
                    ),
                    PageElement(
                        type="image",
                        bbox=(520, 420, 648, 548),
                        metadata={"xref": 29, "image_class": "informational", "needs_vlm": True},
                    ),
                    PageElement(
                        type="image",
                        bbox=(1320, 420, 1448, 548),
                        metadata={"xref": 30, "image_class": "informational", "needs_vlm": True},
                    ),
                ],
            )
        ]
    )

    ContentValueProcessor(ContentValueConfig()).process(doc)

    assert doc.pages[0].elements[0].metadata.get("skip_render") is not True
    assert doc.pages[0].elements[1].metadata.get("skip_render") is not True
    assert doc.pages[0].elements[2].metadata.get("skip_render") is not True
    assert doc.pages[0].elements[3].metadata["skip_render"] is True


def test_content_value_preserves_cover_title_and_cover_metadata():
    doc = Document(
        pages=[
            Page(
                number=1,
                width=1000,
                height=1400,
                elements=[
                    PageElement(
                        type="text",
                        content="2025 年个人工作总结",
                        bbox=(250, 40, 520, 90),
                    ),
                    PageElement(
                        type="text",
                        content="总结岗位：硫化",
                        bbox=(60, 100, 220, 140),
                    ),
                    PageElement(
                        type="text",
                        content="总结人：李飞",
                        bbox=(60, 150, 200, 190),
                    ),
                    PageElement(
                        type="text",
                        content="## 一、本年安全质量问题分析",
                        bbox=(120, 260, 620, 320),
                        metadata={"heading_level": 2},
                    ),
                ],
            )
        ]
    )

    ContentValueProcessor(ContentValueConfig()).process(doc)

    assert doc.pages[0].elements[0].metadata.get("skip_render") is not True
    assert doc.pages[0].elements[1].metadata.get("skip_render") is not True
    assert doc.pages[0].elements[2].metadata.get("skip_render") is not True


def test_content_value_preserves_trailing_signature_and_date():
    doc = Document(
        pages=[
            Page(
                number=3,
                width=1000,
                height=1400,
                elements=[
                    PageElement(
                        type="text",
                        content="该同志工作积极主动，能配合车间完成各项生产任务，能发现生产过程中存在的问题。",
                        bbox=(80, 200, 900, 280),
                    ),
                    PageElement(
                        type="text",
                        content="秦燕刚",
                        bbox=(80, 320, 180, 360),
                    ),
                    PageElement(
                        type="text",
                        content="2026.1.26",
                        bbox=(80, 380, 220, 420),
                    ),
                ],
            )
        ]
    )

    ContentValueProcessor(ContentValueConfig()).process(doc)

    assert doc.pages[0].elements[1].metadata.get("skip_render") is not True
    assert doc.pages[0].elements[2].metadata.get("skip_render") is not True
