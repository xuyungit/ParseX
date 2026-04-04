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

import logging
from pathlib import Path

import numpy as np
from PIL import Image

from parserx.config.schema import ImageProcessorConfig
from parserx.models.elements import Document, PageElement

log = logging.getLogger(__name__)

# ── Image classification thresholds ─────────────────────────────────────
# (migrated from doc-refine pdf_extract.py L137-142)

BLANK_STD_THRESHOLD = 1.0  # pixel std dev below this → blank
TRIVIAL_AREA_MAX = 12000
TRIVIAL_LONG_EDGE_MAX = 160
STRIP_SHORT_EDGE_MAX = 80
STRIP_ASPECT_MIN = 12.0
MIN_DIMENSION = 30  # min width/height for meaningful image


class ImageClassification:
    """Classification result for an image."""

    DECORATIVE = "decorative"
    INFORMATIONAL = "informational"
    TABLE_IMAGE = "table_image"
    TEXT_IMAGE = "text_image"
    BLANK = "blank"


def classify_image_element(elem: PageElement) -> str:
    """Classify an image element using heuristic rules.

    Uses bounding box dimensions from the element metadata.
    Does NOT load the actual image file — classification is based on
    geometric properties extracted during PDF parsing.
    """
    width = elem.metadata.get("width", 0)
    height = elem.metadata.get("height", 0)

    if width == 0 or height == 0:
        return ImageClassification.BLANK

    short_edge = min(width, height)
    long_edge = max(width, height)
    area = width * height
    aspect = long_edge / max(short_edge, 1)

    # Very small → decorative (icons, bullets)
    if short_edge <= 4:
        return ImageClassification.DECORATIVE

    # Thin strips → decorative (lines, borders)
    if short_edge <= STRIP_SHORT_EDGE_MAX and aspect >= STRIP_ASPECT_MIN:
        return ImageClassification.DECORATIVE

    # Small area → decorative
    if area <= TRIVIAL_AREA_MAX and long_edge <= TRIVIAL_LONG_EDGE_MAX:
        return ImageClassification.DECORATIVE

    # Too small to be meaningful
    if width < MIN_DIMENSION or height < MIN_DIMENSION:
        return ImageClassification.DECORATIVE

    # Check layout_type if set by LayoutBuilder
    layout = elem.layout_type
    if layout == "table":
        return ImageClassification.TABLE_IMAGE
    if layout == "text":
        return ImageClassification.TEXT_IMAGE

    # Default: informational (will get VLM description)
    return ImageClassification.INFORMATIONAL


def classify_image_file(image_path: Path) -> str:
    """Classify an image file by loading and analyzing pixels.

    Supplements element-level classification with pixel analysis.
    Only called when element classification returns INFORMATIONAL
    and we want to double-check it's not blank.
    """
    try:
        arr = np.array(Image.open(image_path).convert("L"))
    except Exception:
        return ImageClassification.BLANK

    if arr.std() < BLANK_STD_THRESHOLD:
        return ImageClassification.BLANK

    return ImageClassification.INFORMATIONAL


class ImageProcessor:
    """Classify images and prepare VLM descriptions for informational ones.

    Flow:
    1. Classify each image element (heuristic)
    2. Skip decorative/blank images
    3. For informational images: call VLM if configured
    4. Store classification and description in element.metadata
    """

    def __init__(self, config: ImageProcessorConfig | None = None):
        self._config = config or ImageProcessorConfig()

    def process(self, doc: Document) -> Document:
        if not self._config.enabled:
            return doc

        stats = {"decorative": 0, "informational": 0, "table": 0, "text": 0, "blank": 0}

        for page in doc.pages:
            for elem in page.elements:
                if elem.type != "image":
                    continue

                # Step 1: Classify
                classification = classify_image_element(elem)
                elem.metadata["image_class"] = classification

                # Count stats
                if classification == ImageClassification.DECORATIVE:
                    stats["decorative"] += 1
                elif classification == ImageClassification.BLANK:
                    stats["blank"] += 1
                elif classification == ImageClassification.TABLE_IMAGE:
                    stats["table"] += 1
                elif classification == ImageClassification.TEXT_IMAGE:
                    stats["text"] += 1
                else:
                    stats["informational"] += 1

                # Step 2: Handle by classification
                if classification == ImageClassification.DECORATIVE:
                    if self._config.skip_decorative:
                        elem.metadata["skipped"] = True
                        elem.metadata["description"] = ""
                elif classification == ImageClassification.BLANK:
                    elem.metadata["skipped"] = True
                    elem.metadata["description"] = ""
                elif classification == ImageClassification.INFORMATIONAL:
                    # VLM description will be added in a future step
                    # when VLM service is integrated into the pipeline
                    elem.metadata["needs_vlm"] = True

        log.info(
            "Images: %d informational, %d decorative, %d table, %d text, %d blank",
            stats["informational"], stats["decorative"],
            stats["table"], stats["text"], stats["blank"],
        )
        return doc
