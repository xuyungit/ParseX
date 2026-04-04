"""Image processor — classify images and generate VLM descriptions.

Strategy: classify first, process selectively.

Classification (heuristic, no model needed):
- decorative: small icons, thin lines, near-blank images → skip
- informational: diagrams, charts, meaningful photos → VLM description
- table_image: detected as table region → route to table pipeline
- text_image: detected as text region → OCR only

Only informational images get VLM descriptions. This is ParserX's
key differentiator vs open-source tools that just extract images
without understanding them.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

import numpy as np
from PIL import Image

from parserx.config.schema import ImageProcessorConfig
from parserx.models.elements import Document, PageElement
from parserx.services.llm import OpenAICompatibleService

log = logging.getLogger(__name__)

# ── Image classification thresholds ─────────────────────────────────────

BLANK_STD_THRESHOLD = 1.0
TRIVIAL_AREA_MAX = 12000
TRIVIAL_LONG_EDGE_MAX = 160
STRIP_SHORT_EDGE_MAX = 80
STRIP_ASPECT_MIN = 12.0
MIN_DIMENSION = 30


class ImageClassification:
    DECORATIVE = "decorative"
    INFORMATIONAL = "informational"
    TABLE_IMAGE = "table_image"
    TEXT_IMAGE = "text_image"
    BLANK = "blank"


def classify_image_element(elem: PageElement) -> str:
    """Classify an image element using heuristic rules."""
    width = elem.metadata.get("width", 0)
    height = elem.metadata.get("height", 0)

    if width == 0 or height == 0:
        return ImageClassification.BLANK

    short_edge = min(width, height)
    long_edge = max(width, height)
    area = width * height
    aspect = long_edge / max(short_edge, 1)

    if short_edge <= 4:
        return ImageClassification.DECORATIVE
    if short_edge <= STRIP_SHORT_EDGE_MAX and aspect >= STRIP_ASPECT_MIN:
        return ImageClassification.DECORATIVE
    if area <= TRIVIAL_AREA_MAX and long_edge <= TRIVIAL_LONG_EDGE_MAX:
        return ImageClassification.DECORATIVE
    if width < MIN_DIMENSION or height < MIN_DIMENSION:
        return ImageClassification.DECORATIVE

    layout = elem.layout_type
    if layout == "table":
        return ImageClassification.TABLE_IMAGE
    if layout == "text":
        return ImageClassification.TEXT_IMAGE

    return ImageClassification.INFORMATIONAL


def classify_image_file(image_path: Path) -> str:
    """Classify by loading pixels — catches blank images."""
    try:
        arr = np.array(Image.open(image_path).convert("L"))
    except Exception:
        return ImageClassification.BLANK
    if arr.std() < BLANK_STD_THRESHOLD:
        return ImageClassification.BLANK
    return ImageClassification.INFORMATIONAL


# ── VLM prompt (adapted from doc-refine openai_image_reads.py) ──────────

VLM_SYSTEM_PROMPT = """\
你是一个文档图片解读助手。请根据图片内容生成准确的中文描述。

规则：
- 只描述图片中可以稳定确认的内容，不要补充猜测性细节
- 如果图片包含表格，输出 Markdown 表格格式
- 如果图片包含文字，准确转录
- 如果图片包含公式，使用 LaTeX 格式（$...$）
- 如果是示意图/流程图，描述其结构和含义
- 保持简洁准确"""


def _build_vlm_prompt(elem: PageElement, context_before: str = "") -> str:
    """Build VLM prompt with surrounding text context."""
    parts = ["请描述这张图片的内容。"]

    if context_before:
        parts.append(f"\n图片前文上下文（仅供参考，不是图片内容）：\n{context_before[:500]}")

    return "\n".join(parts)


def _get_context_before(elem: PageElement, page_elements: list[PageElement]) -> str:
    """Get text elements before this image on the same page for context."""
    context_lines = []
    for e in page_elements:
        if e is elem:
            break
        if e.type == "text" and e.content.strip():
            context_lines.append(e.content.strip())

    # Last 3 text blocks before image
    return "\n".join(context_lines[-3:])


class ImageProcessor:
    """Classify images and optionally generate VLM descriptions.

    Flow:
    1. Classify each image (heuristic)
    2. Skip decorative/blank
    3. For informational images with saved files: call VLM if service provided
    4. Store classification and description in element.metadata
    """

    def __init__(
        self,
        config: ImageProcessorConfig | None = None,
        vlm_service: OpenAICompatibleService | None = None,
    ):
        self._config = config or ImageProcessorConfig()
        self._vlm = vlm_service

    def process(self, doc: Document) -> Document:
        if not self._config.enabled:
            return doc

        stats = {"decorative": 0, "informational": 0, "table": 0, "text": 0, "blank": 0, "vlm_called": 0}

        for page in doc.pages:
            for elem in page.elements:
                if elem.type != "image":
                    continue

                # Step 1: Classify
                classification = classify_image_element(elem)
                elem.metadata["image_class"] = classification

                if classification == ImageClassification.DECORATIVE:
                    stats["decorative"] += 1
                    if self._config.skip_decorative:
                        elem.metadata["skipped"] = True
                        elem.metadata["description"] = ""
                elif classification == ImageClassification.BLANK:
                    stats["blank"] += 1
                    elem.metadata["skipped"] = True
                    elem.metadata["description"] = ""
                elif classification == ImageClassification.TABLE_IMAGE:
                    stats["table"] += 1
                    elem.metadata["needs_vlm"] = True
                elif classification == ImageClassification.TEXT_IMAGE:
                    stats["text"] += 1
                elif classification == ImageClassification.INFORMATIONAL:
                    stats["informational"] += 1
                    elem.metadata["needs_vlm"] = True

                # Step 2: VLM description for informational images
                if (self._vlm
                        and self._config.vlm_description
                        and elem.metadata.get("needs_vlm")
                        and elem.metadata.get("saved_abs_path")):
                    saved_path = Path(elem.metadata["saved_abs_path"])
                    if saved_path.exists():
                        # Double-check with pixel analysis
                        file_class = classify_image_file(saved_path)
                        if file_class == ImageClassification.BLANK:
                            elem.metadata["image_class"] = ImageClassification.BLANK
                            elem.metadata["skipped"] = True
                            elem.metadata["description"] = ""
                            stats["blank"] += 1
                            continue

                        description = self._describe_image(elem, page.elements, saved_path)
                        if description:
                            elem.metadata["description"] = description
                            stats["vlm_called"] += 1

        log.info(
            "Images: %d informational, %d decorative, %d table, %d text, %d blank, %d VLM calls",
            stats["informational"], stats["decorative"],
            stats["table"], stats["text"], stats["blank"],
            stats["vlm_called"],
        )
        return doc

    def _describe_image(
        self, elem: PageElement, page_elements: list[PageElement], image_path: Path
    ) -> str:
        """Call VLM to describe an image with context."""
        context = _get_context_before(elem, page_elements)
        prompt = _build_vlm_prompt(elem, context)

        try:
            result = self._vlm.describe_image(
                image_path,
                prompt,
                context=VLM_SYSTEM_PROMPT,
                temperature=0.1,
                max_tokens=4096,
            )
            return result.strip()
        except Exception as exc:
            log.warning("VLM failed for %s: %s", image_path.name, exc)
            return ""
