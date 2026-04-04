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

    Migrated from doc-refine pipeline.py _call_paddleocr_sync.
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
        self._max_retries = 3
        self._timeout = 600

    def recognize(self, image_path: Path) -> OCRResult:
        """Send image to PaddleOCR sync API and parse response."""
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
        headers = {
            "Authorization": f"token {self._token}",
            "Content-Type": "application/json",
        }

        last_error: Exception | None = None
        for attempt in range(1, self._max_retries + 1):
            try:
                log.debug("OCR request: %s (attempt %d)", image_path.name, attempt)
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
                if attempt < self._max_retries:
                    wait = 2 * attempt
                    log.warning("OCR retry %d: %s (wait %ds)", attempt, exc, wait)
                    time.sleep(wait)

        raise RuntimeError(f"OCR failed after {self._max_retries} attempts: {last_error}")

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

                if label == "table":
                    has_tables = True

                blocks.append(OCRBlock(
                    text=content,
                    label=label,
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


def create_ocr_service(config: OCRBuilderConfig | None = None) -> PaddleOCRService:
    """Factory: create OCR service from config.

    Future: switch between PaddleOCR, RapidOCR, Tesseract, remote API
    based on config.engine.
    """
    return PaddleOCRService(config)
