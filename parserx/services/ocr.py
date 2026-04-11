"""OCR service abstraction with PaddleOCR online implementation.

Pluggable design: implement OCREngine protocol for any OCR backend.
Current implementation: PaddleOCR sync API (online service).
"""

from __future__ import annotations

import base64
import json
import logging
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import requests

from parserx.config.schema import OCRBuilderConfig

log = logging.getLogger(__name__)


@dataclass
class OCRBlock:
    """A single recognized block from OCR."""

    text: str = ""
    label: str = ""  # e.g. "text", "table", "title", "figure"
    bbox: tuple[float, float, float, float] = (0.0, 0.0, 0.0, 0.0)
    confidence: float = 1.0
    order: int = 0


@dataclass
class OCRResult:
    """Result from OCR processing of a single image/page."""

    blocks: list[OCRBlock] = field(default_factory=list)
    full_text: str = ""
    markdown: str = ""
    has_tables: bool = False
    raw: dict = field(default_factory=dict)

    @property
    def text_content(self) -> str:
        """Get combined text from all blocks, or full_text fallback."""
        if self.full_text:
            return self.full_text
        return "\n".join(b.text for b in self.blocks if b.text)


class PaddleOCRService:
    """PaddleOCR online sync API client.

    Supports two modes:
    - Single-page: send one image, get one OCRResult (``recognize``).
    - Batch: send a PDF containing multiple pages, get a list of
      OCRResults in page order (``recognize_pdf``).
    """

    def __init__(self, config: OCRBuilderConfig | None = None):
        cfg = config or OCRBuilderConfig()
        if not cfg.endpoint or not cfg.token:
            raise ValueError(
                "PaddleOCR requires 'endpoint' and 'token'. "
                "Set PADDLE_OCR_ENDPOINT / PADDLE_OCR_TOKEN in environment "
                "or provide them in parserx.yaml under builders.ocr."
            )
        self._url = cfg.endpoint
        self._token = cfg.token
        self._model = cfg.model
        self._max_retries = 5
        self._timeout = 600

    def recognize(self, image_path: Path) -> OCRResult:
        """Send image to PaddleOCR sync API and parse response.

        Retry strategy:
        1. Up to ``_max_retries`` attempts with exponential backoff.
        2. If all fail, retry once with layout detection disabled (works
           around server-side crashes on certain images).
        """
        with open(image_path, "rb") as f:
            file_base64 = base64.b64encode(f.read()).decode("ascii")

        body = {
            "file": file_base64,
            "fileType": 1,  # 1 = image
            "model": self._model,
            "useDocOrientationClassify": True,
            "useDocUnwarping": False,
            "useLayoutDetection": True,
            "useOcrForImageBlock": True,
            "useChartRecognition": False,
        }

        result = self._post_with_retries(body, image_path.name)
        if result is not None:
            return result

        # Fallback: disable layout detection (works around server-side 500
        # errors on certain images with complex backgrounds/tables).
        log.warning(
            "OCR retries exhausted for %s, retrying without layout detection",
            image_path.name,
        )
        body["useLayoutDetection"] = False
        body["useOcrForImageBlock"] = False
        result = self._post_with_retries(body, image_path.name, max_retries=2)
        if result is not None:
            return result

        raise RuntimeError(
            f"OCR failed for {image_path.name} after retries "
            f"(including fallback without layout detection)"
        )

    def recognize_pdf(self, pdf_bytes: bytes) -> list[OCRResult]:
        """Send a multi-page PDF and return per-page OCRResults.

        The sync API accepts ``fileType: 0`` (PDF) and returns results
        for all pages in a single response.  This is much faster than
        calling ``recognize()`` once per page because:
        - One network round-trip instead of N.
        - Original PDF pages are sent directly (no image rendering),
          so the payload is smaller.
        - The server may process pages in parallel internally.
        """
        file_base64 = base64.b64encode(pdf_bytes).decode("ascii")

        body = {
            "file": file_base64,
            "fileType": 0,  # 0 = PDF
            "model": self._model,
            "useDocOrientationClassify": True,
            "useDocUnwarping": False,
            "useLayoutDetection": True,
            "useOcrForImageBlock": True,
            "useChartRecognition": False,
        }

        results = self._post_pdf_with_retries(body)
        if results is not None:
            return results

        # Fallback: disable layout detection.
        log.warning(
            "Batch OCR retries exhausted, retrying without layout detection",
        )
        body["useLayoutDetection"] = False
        body["useOcrForImageBlock"] = False
        results = self._post_pdf_with_retries(body, max_retries=2)
        if results is not None:
            return results

        raise RuntimeError(
            "Batch OCR failed after retries "
            "(including fallback without layout detection)"
        )

    def _post_pdf_with_retries(
        self,
        body: dict,
        max_retries: int | None = None,
    ) -> list[OCRResult] | None:
        """POST PDF to OCR endpoint with retries. Returns None if all fail."""
        headers = {
            "Authorization": f"token {self._token}",
            "Content-Type": "application/json",
        }
        retries = max_retries if max_retries is not None else self._max_retries
        last_error: Exception | None = None

        for attempt in range(1, retries + 1):
            try:
                log.debug("Batch OCR request (attempt %d)", attempt)
                resp = requests.post(
                    self._url,
                    headers=headers,
                    data=json.dumps(body),
                    timeout=self._timeout,
                )
                resp.raise_for_status()
                payload = resp.json()

                if payload.get("errorCode") not in (0, "0") or "result" not in payload:
                    raise RuntimeError(f"OCR error: {payload}")

                return self._parse_multi_page_result(payload["result"])

            except Exception as exc:
                last_error = exc
                if attempt < retries:
                    wait = min(2 ** attempt, 30)
                    log.warning("Batch OCR retry %d: %s (wait %ds)", attempt, exc, wait)
                    time.sleep(wait)

        return None

    def _parse_multi_page_result(self, result: dict) -> list[OCRResult]:
        """Parse PaddleOCR response with multiple pages into per-page OCRResults."""
        page_results: list[OCRResult] = []
        for page_data in result.get("layoutParsingResults", []):
            # Wrap in the same structure _parse_result expects.
            single_page_result = {"layoutParsingResults": [page_data]}
            page_results.append(self._parse_result(single_page_result))
        return page_results

    def _post_with_retries(
        self,
        body: dict,
        label: str,
        max_retries: int | None = None,
    ) -> OCRResult | None:
        """POST to OCR endpoint with retries. Returns None if all fail."""
        headers = {
            "Authorization": f"token {self._token}",
            "Content-Type": "application/json",
        }
        retries = max_retries if max_retries is not None else self._max_retries
        last_error: Exception | None = None

        for attempt in range(1, retries + 1):
            try:
                log.debug("OCR request: %s (attempt %d)", label, attempt)
                resp = requests.post(
                    self._url,
                    headers=headers,
                    data=json.dumps(body),
                    timeout=self._timeout,
                )
                resp.raise_for_status()
                payload = resp.json()

                if payload.get("errorCode") not in (0, "0") or "result" not in payload:
                    raise RuntimeError(f"OCR error: {payload}")

                return self._parse_result(payload["result"])

            except Exception as exc:
                last_error = exc
                if attempt < retries:
                    wait = min(2 ** attempt, 30)
                    log.warning("OCR retry %d: %s (wait %ds)", attempt, exc, wait)
                    time.sleep(wait)

        return None

    def _parse_result(self, result: dict) -> OCRResult:
        """Parse PaddleOCR response into OCRResult."""
        blocks: list[OCRBlock] = []
        has_tables = False

        for page in result.get("layoutParsingResults", []):
            pruned = page.get("prunedResult") or {}
            for block_data in pruned.get("parsing_res_list", []):
                label = block_data.get("block_label", "")
                content = block_data.get("block_content", "")
                order = block_data.get("block_order", 0)
                bbox = _extract_bbox(block_data)

                if label == "table":
                    has_tables = True

                blocks.append(OCRBlock(
                    text=content,
                    label=label,
                    bbox=bbox,
                    order=order,
                ))

        full_text = "\n".join(b.text for b in blocks if b.text and b.label != "table")
        markdown = "\n\n".join(b.text for b in blocks if b.text)

        return OCRResult(
            blocks=blocks,
            full_text=full_text,
            markdown=markdown,
            has_tables=has_tables,
            raw=result,
        )


def create_ocr_service(
    config: OCRBuilderConfig | None = None,
) -> PaddleOCRService | None:
    """Factory: create OCR service from config.

    Returns None when engine is "none" (useful for tests / no-OCR runs).
    Future: switch between PaddleOCR, RapidOCR, Tesseract, remote API
    based on config.engine.
    """
    cfg = config or OCRBuilderConfig()
    if cfg.engine == "none":
        return None
    return PaddleOCRService(config)


def _extract_bbox(block_data: dict[str, Any]) -> tuple[float, float, float, float]:
    """Best-effort extraction of a rectangular bbox from OCR block payloads."""
    raw = (
        block_data.get("bbox")
        or block_data.get("block_bbox")
        or block_data.get("block_region")
        or block_data.get("coordinate")
    )
    if raw is None:
        return (0.0, 0.0, 0.0, 0.0)

    if isinstance(raw, dict):
        values = (
            raw.get("x0", raw.get("left", 0.0)),
            raw.get("y0", raw.get("top", 0.0)),
            raw.get("x1", raw.get("right", 0.0)),
            raw.get("y1", raw.get("bottom", 0.0)),
        )
        return (
            float(values[0] or 0.0),
            float(values[1] or 0.0),
            float(values[2] or 0.0),
            float(values[3] or 0.0),
        )

    if not isinstance(raw, (list, tuple)):
        return (0.0, 0.0, 0.0, 0.0)

    if len(raw) == 4 and all(isinstance(v, (int, float)) for v in raw):
        x0, y0, x1, y1 = raw
        return (float(x0), float(y0), float(x1), float(y1))

    if raw and all(isinstance(pt, (list, tuple)) and len(pt) >= 2 for pt in raw):
        xs = [float(pt[0]) for pt in raw]
        ys = [float(pt[1]) for pt in raw]
        return (min(xs), min(ys), max(xs), max(ys))

    if len(raw) >= 8 and all(isinstance(v, (int, float)) for v in raw):
        xs = [float(v) for v in raw[0::2]]
        ys = [float(v) for v in raw[1::2]]
        return (min(xs), min(ys), max(xs), max(ys))

    return (0.0, 0.0, 0.0, 0.0)
