"""Scan engine: GLM-OCR through Zhipu's layout-parsing API (docs/v2_ocr_engines.md §8).

One synchronous request per file — a PDF (``data:application/pdf;base64,…``, at most 100 pages) or an image — to
``POST {endpoint}`` with ``{"model": "glm-ocr", "file": …}``.  The answer holds ``layout_details``: per page, the
regions in reading order, each with its PP-DocLayout label (``native_label``), its box in the pixels of the page
as the service rendered it (``bbox_2d``, the page's ``width`` and ``height`` beside it) and its content as Markdown.

``glm_pages`` writes those regions as the PaddleOCR-VL scan engine's ``layoutParsingResults`` entries, so
everything that reads the scan engine (``content/scan.py`` and the tools) reads GLM-OCR unchanged; the labels are
the same set.  What the conversion changes, and only there, is the Markdown wrapping GLM-OCR puts around a
region's text: a title's leading ``#``s, a centring ``<div>``, a picture region's reference to its own crop (a
picture inside a text region is named the way the PaddleOCR-VL engine names its crops), and the spaces it writes
between the digits of a number inside LaTeX (``\\frac{1 2}{4 8}``: typeset, those spaces are not there).  The service's answer is what the cache keeps, so a change here needs no new request.

What the service leaves out, nothing here can add back (§4.10): it returns no header, footer, page-number,
footnote or aside-text regions and few formula numbers, and it does not read the text inside picture regions.
"""

from __future__ import annotations

import base64
import re
import requests

from parserx.cache import bytes_digest, endpoint_identity, request_key
from parserx.config.schema import GlmOcrConfig, ParserXConfig
from parserx.scheduling import JobStore, PageCountMismatch, RequestMeter, ServiceGateway, TransientError
from parserx.services.ocr import OCRResult, _page_count, _parse_page

_FIGURES = frozenset({"image", "chart", "seal", "header_image", "footer_image"})
_TITLES = frozenset({"doc_title", "paragraph_title"})
_HEADING = re.compile(r"^\s*#{1,6}\s+")
_CENTRED = re.compile(r"</?div\b[^>]*>", re.I)
_MATH = re.compile(r"(\$\$.*?\$\$|\$[^$\n]*?\$)", re.S)
_SPACED_DIGITS = re.compile(r"(?<=\d)[ \t]+(?=\d)")
# A picture inside a region's text, by its box in the page's pixels: written the way the PaddleOCR-VL engine names
# its crops, which ``scan.take_pictures`` reads.
_PICTURE = re.compile(r"!\[[^\]]*\]\(page=\d+,\s*bbox=\[\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)\s*\]\)")


def glm_api_key(config: ParserXConfig) -> str:
    """The Zhipu key for GLM-OCR: its own, else that of the ``models`` entry on the same account."""
    glm = config.builders.ocr.glm
    if glm.api_key:
        return glm.api_key
    entry = config.models.get(glm.account)
    return entry.api_key if entry is not None else ""


class GlmOcrService:
    """GLM-OCR with the scan engine's interface (``recognize``, ``recognize_pdf``, ``request_key``, ``gateway``)."""

    _TIMEOUT = 600

    def __init__(self, config: GlmOcrConfig, api_key: str):
        if not config.endpoint or not api_key:
            raise ValueError("GLM-OCR requires an endpoint and a key (builders.ocr.glm, or the models entry it names)")
        self._url = config.endpoint
        self._headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
        self._model = config.model
        self.on_wait = None  # one synchronous request: nothing to report while waiting
        self.gateway: ServiceGateway = ServiceGateway(RequestMeter())
        self.job_store: JobStore = JobStore()  # unused (no jobs); kept for the scan engine's interface

    @property
    def model(self) -> str:
        return self._model

    def recognize(self, image_path) -> OCRResult:
        pages = self._pages(image_path.read_bytes(), "image/png")
        return _parse_page(pages[:1], raw={"layoutParsingResults": pages[:1]})

    def recognize_pdf(self, pdf_bytes: bytes) -> list[OCRResult]:
        return [_parse_page([page], raw={"layoutParsingResults": [page]})
                for page in self._pages(pdf_bytes, "application/pdf")]

    def request_key(self, file_bytes: bytes, mime: str) -> str:
        return request_key("ocr", self._material(file_bytes, mime))

    def _material(self, file_bytes: bytes, mime: str) -> dict:
        return {"endpoint": endpoint_identity(self._url), "model": self._model, "mime": mime,
                "file_sha256": bytes_digest(file_bytes)}

    def _pages(self, file_bytes: bytes, mime: str) -> list[dict]:
        pages = _page_count(file_bytes, mime)
        answer = self.gateway.call("ocr", self._material(file_bytes, mime),
                                   lambda: self._ask(file_bytes, mime, pages), pages=pages)
        return glm_pages(answer)

    def _ask(self, file_bytes: bytes, mime: str, pages: int) -> dict:
        """The service's answer; it must hold exactly the submitted pages (0 = count unknown)."""
        body = {"model": self._model, "file": f"data:{mime};base64,{base64.b64encode(file_bytes).decode()}"}
        resp = requests.post(self._url, json=body, headers=self._headers, timeout=self._TIMEOUT)
        if resp.status_code == 429 or resp.status_code >= 500:
            raise TransientError(f"GLM-OCR HTTP {resp.status_code}: {resp.text[:300]}")
        if not resp.ok:
            raise RuntimeError(f"GLM-OCR HTTP {resp.status_code}: {resp.text[:300]}")
        answer = resp.json()
        got = len(answer.get("layout_details") or [])
        if pages and got != pages:
            raise PageCountMismatch(pages, got)
        return answer


def glm_pages(answer: dict) -> list[dict]:
    """The answer's pages as ``layoutParsingResults`` entries of the PaddleOCR-VL scan engine."""
    out = []
    for regions in answer.get("layout_details") or []:
        width = height = 0
        entries = []
        for n, region in enumerate(regions):
            width, height = region.get("width") or width, region.get("height") or height
            label = str(region.get("native_label") or region.get("label") or "text")
            figure = label in _FIGURES
            entries.append({
                "block_id": n, "group_id": n, "block_label": label,
                "block_bbox": [float(v) for v in region.get("bbox_2d") or (0, 0, 0, 0)],
                "block_order": None if figure else n + 1,  # pictures are placed by position (scan.scan_order)
                "block_content": "" if figure else region_text(label, str(region.get("content") or "")),
            })
        out.append({"prunedResult": {"width": width, "height": height, "parsing_res_list": entries}})
    return out


def region_text(label: str, content: str) -> str:
    """A region's content without GLM-OCR's Markdown wrapping (see the module docstring)."""
    text = _PICTURE.sub(lambda m: "![](imgs/img_in_image_box_{}_{}_{}_{}.jpg)".format(*m.groups()), content)
    text = _CENTRED.sub("", text).strip()
    if label in _TITLES:
        text = _HEADING.sub("", text)
    text = _MATH.sub(lambda m: _SPACED_DIGITS.sub("", m.group(0)), text)
    if label == "display_formula" and text.startswith("$$") and text.endswith("$$"):
        text = f"$$ {text[2:-2].strip()} $$"  # one line, as the scan engine writes a formula
    return text

