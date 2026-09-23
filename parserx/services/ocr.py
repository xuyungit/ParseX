"""OCR service: PaddleOCR-VL via the AI Studio async jobs API.

Protocol (``POST {endpoint}`` → poll ``GET {endpoint}/{jobId}`` → download
the JSONL at ``resultUrl.jsonUrl``):

- Submit: multipart upload with ``model`` and ``optionalPayload`` (JSON
  string of pipeline options), ``Authorization: bearer <token>``.
- Poll: ``data.state`` goes ``pending`` → ``running`` → ``done`` | ``failed``.
- Result: each JSONL line holds ``result.layoutParsingResults`` for a slice
  of pages; line order is page order.

A full submission queue is signalled by HTTP 400 with ``code 10010``; that is
back-pressure, so it is waited out rather than treated as an error.
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import requests

from parserx.config.schema import OCRBuilderConfig

log = logging.getLogger(__name__)

# Pipeline options sent with every job.
_OPTIONS: dict[str, Any] = {
    "useDocOrientationClassify": True,
    "useDocUnwarping": False,
    "useLayoutDetection": True,
    "useOcrForImageBlock": True,
    "useChartRecognition": False,
}


@dataclass
class OCRBlock:
    """A single recognized block from OCR."""

    text: str = ""
    label: str = ""  # e.g. "text", "table", "paragraph_title", "image"
    bbox: tuple[float, float, float, float] = (0.0, 0.0, 0.0, 0.0)
    confidence: float = 1.0
    order: int | None = None  # Reading order; None for blocks outside the text flow (images)


@dataclass
class OCRResult:
    """Result from OCR processing of a single image/page."""

    blocks: list[OCRBlock] = field(default_factory=list)
    full_text: str = ""
    markdown: str = ""
    has_tables: bool = False
    raw: dict = field(default_factory=dict)
    render_width: float = 0.0   # Width (px) of the image the OCR server analysed
    render_height: float = 0.0  # Height (px) — used to convert bbox → PDF points

    @property
    def text_content(self) -> str:
        """Get combined text from all blocks, or full_text fallback."""
        if self.full_text:
            return self.full_text
        return "\n".join(b.text for b in self.blocks if b.text)


class PaddleOCRService:
    """PaddleOCR-VL client for the AI Studio async jobs API.

    - ``recognize``: one image → one OCRResult.
    - ``recognize_pdf``: a multi-page PDF → OCRResults in page order.  One
      job for the whole document is much faster than one per page.
    """

    _SUBMIT_TIMEOUT = 120
    _POLL_INTERVAL = 2.0
    _POLL_INTERVAL_MAX = 5.0
    _QUEUE_FULL_CODE = 10010
    _QUEUE_FULL_WAIT = 5
    _QUEUE_FULL_WAIT_MAX = 60

    def __init__(self, config: OCRBuilderConfig | None = None):
        cfg = config or OCRBuilderConfig()
        if not cfg.endpoint or not cfg.token:
            raise ValueError(
                "PaddleOCR requires 'endpoint' and 'token'. "
                "Set PADDLE_OCR_ENDPOINT / PADDLE_OCR_TOKEN in environment "
                "or provide them in parserx.yaml under builders.ocr."
            )
        self._url = cfg.endpoint.rstrip("/")
        self._headers = {"Authorization": f"bearer {cfg.token}"}
        self._model = cfg.model
        self._max_retries = 3
        self._timeout = 600  # Budget (s) for queue-full waits and for polling

    def recognize(self, image_path: Path) -> OCRResult:
        """OCR a single image."""
        result = self._run_with_retries(image_path.read_bytes(), image_path.name, "image/png")
        return _parse_page(result.get("layoutParsingResults", []), raw=result)

    def recognize_pdf(self, pdf_bytes: bytes) -> list[OCRResult]:
        """OCR a multi-page PDF, returning one OCRResult per page in order."""
        result = self._run_with_retries(pdf_bytes, "document.pdf", "application/pdf")
        return [
            _parse_page([page], raw={"layoutParsingResults": [page]})
            for page in result.get("layoutParsingResults", [])
        ]

    # ── Transport ─────────────────────────────────────────────────────

    def _run_with_retries(self, file_bytes: bytes, filename: str, mime: str) -> dict:
        """Run one job, retrying transient failures with exponential backoff."""
        for attempt in range(1, self._max_retries + 1):
            try:
                return self._run_job(file_bytes, filename, mime)
            except Exception as exc:
                if attempt == self._max_retries:
                    raise RuntimeError(
                        f"OCR failed for {filename} after {attempt} attempts: {exc}"
                    ) from exc
                wait = 2 ** attempt
                log.warning("OCR retry %d for %s: %s (wait %ds)", attempt, filename, exc, wait)
                time.sleep(wait)
        raise AssertionError("unreachable")

    def _run_job(self, file_bytes: bytes, filename: str, mime: str) -> dict:
        """Submit, poll until done, download and merge the JSONL result."""
        job_id = self._submit(file_bytes, filename, mime)
        data = self._wait_done(job_id)

        json_url = (data.get("resultUrl") or {}).get("jsonUrl")
        if not json_url:
            raise RuntimeError(f"OCR job {job_id} done without result URL: {data}")
        resp = requests.get(json_url, timeout=self._SUBMIT_TIMEOUT)
        resp.raise_for_status()
        return _merge_jsonl_results(resp.text)

    def _submit(self, file_bytes: bytes, filename: str, mime: str) -> str:
        """Submit a job, waiting out "queue full" rejections on their own budget."""
        deadline = time.monotonic() + self._timeout
        wait = self._QUEUE_FULL_WAIT
        while True:
            resp = requests.post(
                self._url,
                headers=self._headers,
                data={"model": self._model, "optionalPayload": json.dumps(_OPTIONS)},
                files={"file": (filename, file_bytes, mime)},
                timeout=self._SUBMIT_TIMEOUT,
            )
            payload = _json_or_none(resp) or {}
            code = payload.get("code")
            if code in (self._QUEUE_FULL_CODE, str(self._QUEUE_FULL_CODE)):
                if time.monotonic() + wait > deadline:
                    raise RuntimeError(
                        f"OCR queue still full after {self._timeout}s: {payload.get('msg')}"
                    )
                log.info("OCR queue full, resubmitting %s in %ds", filename, wait)
                time.sleep(wait)
                wait = min(wait * 2, self._QUEUE_FULL_WAIT_MAX)
                continue
            if not resp.ok:
                raise RuntimeError(f"OCR submit HTTP {resp.status_code}: {resp.text[:300]}")
            job_id = (payload.get("data") or {}).get("jobId")
            if code not in (0, "0") or not job_id:
                raise RuntimeError(f"OCR submit failed: {payload}")
            return str(job_id)

    def _wait_done(self, job_id: str) -> dict:
        """Poll a job until it finishes; return its ``data`` object."""
        deadline = time.monotonic() + self._timeout
        interval = self._POLL_INTERVAL
        while True:
            resp = requests.get(f"{self._url}/{job_id}", headers=self._headers, timeout=60)
            resp.raise_for_status()
            data = resp.json().get("data") or {}
            state = data.get("state")
            log.debug("OCR job %s: %s %s", job_id, state, data.get("extractProgress") or "")
            if state == "done":
                return data
            if state == "failed":
                raise RuntimeError(f"OCR job {job_id} failed: {data.get('errorMsg')}")
            if time.monotonic() > deadline:
                raise TimeoutError(f"OCR job {job_id} still '{state}' after {self._timeout}s")
            time.sleep(interval)
            interval = min(interval * 1.5, self._POLL_INTERVAL_MAX)


def create_ocr_service(
    config: OCRBuilderConfig | None = None,
) -> PaddleOCRService | None:
    """Factory: create OCR service from config.

    Returns None when engine is "none" (useful for tests / no-OCR runs).
    """
    cfg = config or OCRBuilderConfig()
    if cfg.engine == "none":
        return None
    return PaddleOCRService(cfg)


# ── Response parsing ──────────────────────────────────────────────────


def _json_or_none(resp: requests.Response) -> dict | None:
    try:
        payload = resp.json()
    except ValueError:
        return None
    return payload if isinstance(payload, dict) else None


def _merge_jsonl_results(text: str) -> dict:
    """Concatenate ``layoutParsingResults`` across JSONL result lines."""
    pages: list[dict] = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        record = json.loads(line)
        if record.get("errorCode") not in (None, 0, "0"):
            raise RuntimeError(f"OCR result error: {record.get('errorMsg') or record}")
        pages.extend((record.get("result") or {}).get("layoutParsingResults", []))
    return {"layoutParsingResults": pages}


def _parse_page(pages: list[dict], raw: dict) -> OCRResult:
    """Build an OCRResult from ``layoutParsingResults`` entries of one page."""
    blocks: list[OCRBlock] = []
    render_width = render_height = 0.0

    for page in pages:
        pruned = page.get("prunedResult") or {}
        # Size (px) of the image the server analysed; needed to map bbox → PDF points.
        if pruned.get("width") and pruned.get("height"):
            render_width = float(pruned["width"])
            render_height = float(pruned["height"])

        for block in pruned.get("parsing_res_list", []):
            blocks.append(OCRBlock(
                text=block.get("block_content", ""),
                label=block.get("block_label", ""),
                bbox=_extract_bbox(block),
                order=block.get("block_order"),
            ))

    return OCRResult(
        blocks=blocks,
        full_text="\n".join(b.text for b in blocks if b.text and b.label != "table"),
        markdown="\n\n".join(b.text for b in blocks if b.text),
        has_tables=any(b.label == "table" for b in blocks),
        raw=raw,
        render_width=render_width,
        render_height=render_height,
    )


def _extract_bbox(block: dict[str, Any]) -> tuple[float, float, float, float]:
    """Axis-aligned bbox from ``block_bbox``, falling back to the polygon."""
    bbox = block.get("block_bbox")
    if isinstance(bbox, (list, tuple)) and len(bbox) == 4:
        x0, y0, x1, y1 = (float(v) for v in bbox)
        return (x0, y0, x1, y1)

    points = block.get("block_polygon_points")
    if points:
        xs = [float(p[0]) for p in points]
        ys = [float(p[1]) for p in points]
        return (min(xs), min(ys), max(xs), max(ys))

    return (0.0, 0.0, 0.0, 0.0)
