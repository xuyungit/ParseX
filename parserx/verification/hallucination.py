"""Cross-check VLM image descriptions against OCR/native text."""

from __future__ import annotations

import re

from parserx.config.schema import VerificationConfig
from parserx.models.elements import Document, PageElement
from parserx.text_utils import compute_edit_distance


TEXT_IMAGE_CLASS = "text_image"
TABLE_IMAGE_CLASS = "table_image"


def _bbox_overlap_ratio(
    a: tuple[float, float, float, float],
    b: tuple[float, float, float, float],
) -> float:
    ax0, ay0, ax1, ay1 = a
    bx0, by0, bx1, by1 = b
    inter_x0 = max(ax0, bx0)
    inter_y0 = max(ay0, by0)
    inter_x1 = min(ax1, bx1)
    inter_y1 = min(ay1, by1)
    if inter_x1 <= inter_x0 or inter_y1 <= inter_y0:
        return 0.0
    inter_area = (inter_x1 - inter_x0) * (inter_y1 - inter_y0)
    a_area = max((ax1 - ax0) * (ay1 - ay0), 1.0)
    b_area = max((bx1 - bx0) * (by1 - by0), 1.0)
    return inter_area / min(a_area, b_area)


def _has_bbox(element: PageElement) -> bool:
    return element.bbox != (0.0, 0.0, 0.0, 0.0)


def _table_shape(markdown: str) -> tuple[int, int] | None:
    rows = [line for line in markdown.splitlines() if line.strip().startswith("|")]
    if len(rows) < 2:
        return None
    header = rows[0].strip().strip("|")
    cols = len([cell for cell in header.split("|")])
    data_rows = max(len(rows) - 2, 0)
    return data_rows, cols


def _extract_numbers(text: str) -> list[str]:
    return re.findall(r"\d+(?:\.\d+)?", text)


class HallucinationDetector:
    """Flag VLM descriptions that diverge too far from OCR/native evidence."""

    def __init__(self, config: VerificationConfig | None = None):
        cfg = config or VerificationConfig()
        self._threshold = cfg.hallucination_threshold

    def detect(self, doc: Document) -> list[str]:
        warnings: list[str] = []

        for page in doc.pages:
            for image in page.elements:
                if image.type != "image" or image.metadata.get("skipped"):
                    continue

                description = str(image.metadata.get("description", "")).strip()
                if not description:
                    continue
                if image.metadata.get("vlm_skipped_due_to_large_text_overlap"):
                    image.metadata["low_confidence"] = False
                    continue

                evidence = self._collect_evidence(image, page.elements)
                if not evidence:
                    continue

                distance = compute_edit_distance(description, evidence)
                confidence = round(max(0.0, 1.0 - distance), 4)
                image.metadata["vlm_confidence"] = confidence
                image.metadata["vlm_vs_source_distance"] = round(distance, 4)

                image_shape = _table_shape(description)
                evidence_shape = _table_shape(evidence)
                description_numbers = _extract_numbers(description)
                evidence_numbers = _extract_numbers(evidence)
                table_shape_mismatch = (
                    image_shape is not None
                    and evidence_shape is not None
                    and image_shape != evidence_shape
                )
                number_mismatch = (
                    bool(description_numbers)
                    and bool(evidence_numbers)
                    and description_numbers != evidence_numbers
                )
                if image_shape:
                    image.metadata["vlm_table_shape"] = image_shape
                if evidence_shape:
                    image.metadata["source_table_shape"] = evidence_shape

                if distance > self._threshold or table_shape_mismatch or number_mismatch:
                    image.metadata["low_confidence"] = True
                    reason = (
                        "table shape mismatch"
                        if table_shape_mismatch else
                        "number mismatch"
                        if number_mismatch else
                        f"distance={distance:.2f}"
                    )
                    warnings.append(
                        f"Page {image.page_number}: low-confidence VLM description ({reason})."
                    )
                else:
                    image.metadata["low_confidence"] = False

        return warnings

    def _collect_evidence(
        self,
        image: PageElement,
        page_elements: list[PageElement],
    ) -> str:
        overlapping: list[tuple[float, str]] = []

        for elem in page_elements:
            if elem is image or elem.type not in {"text", "table"}:
                continue
            if elem.source not in {"ocr", "native"}:
                continue
            if not elem.content.strip():
                continue

            overlap = 0.0
            if _has_bbox(image) and _has_bbox(elem):
                overlap = _bbox_overlap_ratio(image.bbox, elem.bbox)

            if overlap > 0:
                overlapping.append((overlap, elem.content))

        if overlapping:
            overlapping.sort(key=lambda item: item[0], reverse=True)
            return "\n".join(text for _, text in overlapping)

        image_class = image.metadata.get("image_class")
        if image_class not in {TEXT_IMAGE_CLASS, TABLE_IMAGE_CLASS}:
            return ""

        fallback = [
            elem.content
            for elem in page_elements
            if elem is not image
            and elem.type in {"text", "table"}
            and elem.source in {"ocr", "native"}
            and elem.content.strip()
        ]
        return "\n".join(fallback[:3])
