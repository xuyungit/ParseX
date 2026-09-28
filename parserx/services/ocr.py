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

Every job goes through ``ServiceGateway`` (cache, counting, budget, transport
retries).  Submitted job ids are kept in a ``JobStore`` under the request key:
a retry, or a new process after an interruption, polls the same job instead of
submitting again.  A result whose page count differs from the submitted page
count is a failure, never a partial answer.
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import requests

from parserx.cache import bytes_digest, endpoint_identity, request_key
from parserx.config.schema import OCRBuilderConfig
from parserx.scheduling import JobStore, PageCountMismatch, RequestMeter, ServiceGateway, TransientError

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
                "PaddleOCR requires 'endpoint' and 'token': put the token under builders.ocr in the personal "
                "config (~/.config/parserx/config.yaml; `parserx init` writes it)."
            )
        self._url = cfg.endpoint.rstrip("/")
        self._headers = {"Authorization": f"bearer {cfg.token}"}
        self._model = cfg.model
        self._timeout = 600  # Budget (s) for queue-full waits and for polling
        self.on_wait = None  # (job state, seconds waited) while a job is not done: progress for the console
        # Cache, counting, budget and retries for every job; the pipeline sets
        # its per-document gateway, otherwise a private one is used.
        self.gateway: ServiceGateway = ServiceGateway(RequestMeter())
        # Submitted jobs by request key (in memory unless the pipeline gives a directory).
        self.job_store: JobStore = JobStore()

    @property
    def model(self) -> str:
        return self._model

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

    def request_key(self, file_bytes: bytes, mime: str) -> str:
        """Cache key of the job for *file_bytes* (recorded as ``raw_ref`` on the Observations it yields)."""
        return request_key("ocr", self._material(file_bytes, mime))

    def _material(self, file_bytes: bytes, mime: str) -> dict:
        return {
            "endpoint": endpoint_identity(self._url),
            "model": self._model,
            "options": _OPTIONS,
            "mime": mime,
            "file_sha256": bytes_digest(file_bytes),
        }

    def _run_with_retries(self, file_bytes: bytes, filename: str, mime: str) -> dict:
        """The single transport exit: raw merged result for one file, via the gateway.

        The cache key covers the file bytes, mime type, model, endpoint and
        pipeline options; the upload filename (often a temp name) is not part
        of the request semantics.  Transport retries happen in the gateway.
        """
        material = self._material(file_bytes, mime)
        pages = _page_count(file_bytes, mime)
        job_key = request_key("ocr", material)
        return self.gateway.call(
            "ocr",
            material,
            lambda: self._run_checked(file_bytes, filename, mime, job_key, pages),
            pages=pages,
        )

    def _run_checked(self, file_bytes: bytes, filename: str, mime: str, job_key: str, pages: int) -> dict:
        """One job; its result must hold exactly the submitted pages (0 = count unknown)."""
        result = self._run_job(file_bytes, filename, mime, job_key)
        got = len(result.get("layoutParsingResults", []))
        if pages and got != pages:
            raise PageCountMismatch(pages, got)
        return result

    def _run_job(self, file_bytes: bytes, filename: str, mime: str, job_key: str | None = None) -> dict:
        """Submit (or resume a stored job), poll until done, download and merge the JSONL result."""
        job_id = self.job_store.get(job_key) if job_key else None
        if job_id:
            log.info("OCR resuming job %s for %s", job_id, filename)
        else:
            job_id = self._submit(file_bytes, filename, mime)
            if job_key:
                self.job_store.put(job_key, job_id)
        try:
            data = self._wait_done(job_id)
        except _JobGone:
            if job_key:
                self.job_store.drop(job_key)  # the next attempt submits afresh
            raise

        json_url = (data.get("resultUrl") or {}).get("jsonUrl")
        if not json_url:
            raise RuntimeError(f"OCR job {job_id} done without result URL: {data}")
        resp = requests.get(json_url, timeout=self._SUBMIT_TIMEOUT)
        resp.raise_for_status()
        merged = _merge_jsonl_results(resp.text)
        if job_key:
            self.job_store.drop(job_key)
        return merged

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
            if resp.status_code == 429 or resp.status_code >= 500:
                raise TransientError(f"OCR submit HTTP {resp.status_code}: {resp.text[:300]}")
            if not resp.ok:
                raise RuntimeError(f"OCR submit HTTP {resp.status_code}: {resp.text[:300]}")
            job_id = (payload.get("data") or {}).get("jobId")
            if code not in (0, "0") or not job_id:
                raise RuntimeError(f"OCR submit failed: {payload}")
            return str(job_id)

    def _wait_done(self, job_id: str) -> dict:
        """Poll a job until it finishes; return its ``data`` object."""
        started = time.monotonic()
        deadline = started + self._timeout
        interval = self._POLL_INTERVAL
        while True:
            resp = requests.get(f"{self._url}/{job_id}", headers=self._headers, timeout=60)
            if resp.status_code == 404:
                raise _JobGone(f"OCR job {job_id} not found")
            resp.raise_for_status()
            payload = _json_or_none(resp)
            if payload is None:
                raise TransientError(f"OCR job {job_id}: unreadable poll response")
            data = payload.get("data") or {}
            state = data.get("state")
            log.debug("OCR job %s: %s %s", job_id, state, data.get("extractProgress") or "")
            if state == "done":
                return data
            if state == "failed":
                raise _JobGone(f"OCR job {job_id} failed: {data.get('errorMsg')}")
            if time.monotonic() > deadline:
                raise TimeoutError(f"OCR job {job_id} still '{state}' after {self._timeout}s")
            if self.on_wait is not None:
                self.on_wait(state, time.monotonic() - started)
            time.sleep(interval)
            interval = min(interval * 1.5, self._POLL_INTERVAL_MAX)


class _JobGone(TransientError):
    """The job failed or expired on the server; a retry submits it again."""


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


def _page_count(file_bytes: bytes, mime: str) -> int:
    """Pages submitted in one job: the PDF page count, or 1 for an image."""
    if mime != "application/pdf":
        return 1
    import pymupdf  # PyMuPDF

    try:
        with pymupdf.open(stream=file_bytes, filetype="pdf") as doc:
            return doc.page_count
    except Exception:
        return 0


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
