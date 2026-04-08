"""Page-level VLM review processor for OCR correction and missing-text recovery.

Renders selected pages as images, sends them alongside current extraction
results to a VLM "reviewer", and applies structured corrections in-place.

Addresses two problems no other processor can solve:
  - Scanned pages where OCR errors remain uncorrected (no VLM participation)
  - Vector-rendered text (characters as bezier curves, invisible to extraction)
"""

from __future__ import annotations

import json
import logging
import tempfile
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import fitz

from parserx.config.schema import VLMReviewConfig
from parserx.models.elements import Document, Page, PageElement, PageType
from parserx.services.llm import VLMService

log = logging.getLogger(__name__)

# ── VLM prompt ────────────────────────────────────────────────────────────

_REVIEW_SYSTEM_PROMPT = """\
You are a document extraction quality reviewer.
You will receive a page image and the current extraction results for that page.
Your job is to compare the image against the extraction and identify:
1. Text errors: OCR mistakes, wrong characters, garbled text.
2. Missing content: text, headings, or table content visible in the image
   but absent from the extraction.
3. Table errors: wrong structure, missing cells, or incorrect content.

Rules:
- Only report issues you can clearly see in the image.
- Do NOT invent content that is not visible.
- For Chinese text, pay special attention to similar-looking characters
  (e.g. 混凝土 vs 混疑土, 钢筋 vs 钢盘).
- For headings, include the heading level if determinable from visual style.
- Keep corrections concise — provide the corrected text, not explanations.
- If the extraction looks correct, return an empty corrections list.
"""

_REVIEW_USER_PROMPT_TEMPLATE = """\
Here is the current extraction for page {page_number}:

{extraction_summary}

Compare this against the page image. Report any errors or missing content.
"""

_REVIEW_JSON_SCHEMA = {
    "type": "object",
    "required": ["corrections", "page_quality"],
    "properties": {
        "corrections": {
            "type": "array",
            "items": {
                "type": "object",
                "required": ["type"],
                "properties": {
                    "type": {
                        "type": "string",
                        "enum": ["fix_text", "add_missing", "fix_table"],
                    },
                    "element_index": {
                        "type": "integer",
                        "description": "Index in the extraction summary (for fix_text/fix_table)",
                    },
                    "original": {
                        "type": "string",
                        "description": "Original text that needs correction",
                    },
                    "corrected": {
                        "type": "string",
                        "description": "Corrected text",
                    },
                    "content": {
                        "type": "string",
                        "description": "Content to add (for add_missing)",
                    },
                    "content_type": {
                        "type": "string",
                        "enum": ["text", "table", "heading"],
                        "description": "Type of missing content",
                    },
                    "heading_level": {
                        "type": "integer",
                        "description": "Heading level (1-6) if content_type is heading",
                    },
                    "insert_after_index": {
                        "type": "integer",
                        "description": "Insert after this element index (-1 for beginning)",
                    },
                },
            },
        },
        "page_quality": {
            "type": "string",
            "enum": ["ok", "needs_correction"],
        },
    },
}

_REVIEW_JSON_SCHEMA_NAME = "parserx_vlm_review"


# ── Data classes ──────────────────────────────────────────────────────────

@dataclass
class Correction:
    """A single correction returned by VLM review."""

    type: str  # fix_text | add_missing | fix_table
    element_index: int | None = None
    original: str | None = None
    corrected: str | None = None
    content: str | None = None
    content_type: str | None = None
    heading_level: int | None = None
    insert_after_index: int | None = None


# ── Processor ─────────────────────────────────────────────────────────────

class VLMReviewProcessor:
    """Page-level VLM review for OCR correction and missing-text recovery.

    Runs after ImageProcessor.  For each selected page, renders the page as
    an image, sends it with the current extraction results to VLM, and
    applies the returned corrections in-place.
    """

    def __init__(
        self,
        config: VLMReviewConfig | None = None,
        vlm_service: VLMService | None = None,
        source_path: Path | None = None,
        max_concurrent: int = 4,
    ):
        self._config = config or VLMReviewConfig()
        self._vlm = vlm_service
        self._source_path = source_path
        self._max_concurrent = max_concurrent

    def process(self, doc: Document) -> Document:
        if not self._config.enabled:
            return doc
        if not self._vlm:
            log.debug("VLM service not configured; skipping review")
            return doc
        if not self._source_path or not self._source_path.exists():
            log.debug("Source path not available; skipping review")
            return doc

        pages = self._select_pages(doc)
        if not pages:
            log.info("VLM review: no pages need review")
            return doc

        log.info("VLM review: %d page(s) selected for review", len(pages))

        total_corrections = 0
        if len(pages) == 1:
            total_corrections = self._review_page(pages[0])
        else:
            with ThreadPoolExecutor(max_workers=self._max_concurrent) as pool:
                futures = {pool.submit(self._review_page, page): page for page in pages}
                for future in as_completed(futures):
                    page = futures[future]
                    try:
                        n = future.result()
                        total_corrections += n
                    except Exception:
                        log.warning(
                            "VLM review failed on page %d", page.number, exc_info=True,
                        )

        log.info("VLM review: applied %d correction(s)", total_corrections)
        return doc

    # ── Page selection ────────────────────────────────────────────────────

    def _select_pages(self, doc: Document) -> list[Page]:
        """Select pages that need VLM review."""
        if self._config.review_all_pages:
            pages = list(doc.pages)
        else:
            pages = []
            for page in doc.pages:
                if self._needs_review(page):
                    pages.append(page)

        # Cost protection
        if len(pages) > self._config.max_pages_per_doc:
            log.info(
                "VLM review: capping from %d to %d pages",
                len(pages), self._config.max_pages_per_doc,
            )
            pages = pages[: self._config.max_pages_per_doc]

        return pages

    def _needs_review(self, page: Page) -> bool:
        """Determine whether a page needs VLM review."""
        # Always review scanned and mixed pages — OCR errors likely.
        if page.page_type in (PageType.SCANNED, PageType.MIXED):
            return True

        # Review native pages with suspiciously little text —
        # may indicate vector-rendered text loss.
        text_chars = sum(
            len(elem.content)
            for elem in page.elements
            if elem.type == "text" and not elem.metadata.get("skip_render")
        )
        if text_chars < self._config.min_text_chars_for_skip and page.width > 0:
            # Only flag if the page is non-trivial (has some area).
            return True

        return False

    # ── Single-page review ────────────────────────────────────────────────

    def _review_page(self, page: Page) -> int:
        """Review one page: render → summarize → VLM call → apply corrections."""
        image_path = self._render_page(page.number)
        if image_path is None:
            return 0

        try:
            summary = self._build_extraction_summary(page)
            prompt = _REVIEW_USER_PROMPT_TEMPLATE.format(
                page_number=page.number,
                extraction_summary=summary,
            )

            response = self._vlm.describe_image(
                image_path=image_path,
                prompt=prompt,
                context=_REVIEW_SYSTEM_PROMPT,
                temperature=0.0,
                max_tokens=self._config.max_tokens,
                structured_output_mode=self._config.structured_output_mode,
                json_schema=_REVIEW_JSON_SCHEMA,
                json_schema_name=_REVIEW_JSON_SCHEMA_NAME,
            )

            corrections = self._parse_corrections(response)
            if not corrections:
                return 0

            applied = self._apply_corrections(page, corrections)
            if applied > 0:
                log.info(
                    "VLM review page %d: applied %d correction(s)", page.number, applied,
                )
            return applied
        finally:
            # Clean up temp image.
            image_path.unlink(missing_ok=True)

    # ── Page rendering ────────────────────────────────────────────────────

    def _render_page(self, page_number: int) -> Path | None:
        """Render a PDF page as a temporary PNG image."""
        try:
            fitz_doc = fitz.open(str(self._source_path))
        except Exception:
            log.warning("Cannot open %s for rendering", self._source_path, exc_info=True)
            return None

        try:
            if page_number - 1 >= len(fitz_doc):
                log.warning("Page %d out of range (doc has %d pages)", page_number, len(fitz_doc))
                return None

            fitz_page = fitz_doc[page_number - 1]
            scale = self._config.render_dpi / 72
            mat = fitz.Matrix(scale, scale)
            pix = fitz_page.get_pixmap(matrix=mat)

            tmp = tempfile.NamedTemporaryFile(suffix=".png", delete=False)
            pix.save(tmp.name)
            return Path(tmp.name)
        except Exception:
            log.warning("Failed to render page %d", page_number, exc_info=True)
            return None
        finally:
            fitz_doc.close()

    # ── Extraction summary ────────────────────────────────────────────────

    def _build_extraction_summary(self, page: Page) -> str:
        """Build a concise JSON summary of current extraction for VLM context."""
        elements_summary: list[dict[str, Any]] = []
        for idx, elem in enumerate(page.elements):
            if elem.metadata.get("skip_render"):
                continue
            entry: dict[str, Any] = {
                "index": idx,
                "type": elem.type,
            }
            text = elem.content.strip()
            if len(text) > 200:
                entry["text"] = text[:200] + "..."
                entry["text_length"] = len(text)
            else:
                entry["text"] = text

            if elem.metadata.get("heading_level"):
                entry["heading_level"] = elem.metadata["heading_level"]
            if elem.source != "native":
                entry["source"] = elem.source

            elements_summary.append(entry)

        return json.dumps(elements_summary, ensure_ascii=False, indent=2)

    # ── Response parsing ──────────────────────────────────────────────────

    def _parse_corrections(self, response: str) -> list[Correction]:
        """Parse VLM JSON response into a list of Correction objects."""
        try:
            data = _extract_json(response)
        except (json.JSONDecodeError, ValueError):
            log.warning("VLM review: could not parse JSON response")
            return []

        if not isinstance(data, dict):
            return []

        raw_corrections = data.get("corrections", [])
        if not isinstance(raw_corrections, list):
            return []

        corrections: list[Correction] = []
        for item in raw_corrections:
            if not isinstance(item, dict):
                continue
            ctype = item.get("type")
            if ctype not in ("fix_text", "add_missing", "fix_table"):
                continue

            corrections.append(Correction(
                type=ctype,
                element_index=item.get("element_index"),
                original=item.get("original"),
                corrected=item.get("corrected"),
                content=item.get("content"),
                content_type=item.get("content_type"),
                heading_level=item.get("heading_level"),
                insert_after_index=item.get("insert_after_index"),
            ))

        return corrections

    # ── Correction application ────────────────────────────────────────────

    def _apply_corrections(self, page: Page, corrections: list[Correction]) -> int:
        """Apply corrections to page elements. Returns count of applied corrections."""
        applied = 0
        insertions: list[tuple[int, PageElement]] = []

        for corr in corrections:
            if corr.type in ("fix_text", "fix_table"):
                if self._apply_fix(page, corr):
                    applied += 1
            elif corr.type == "add_missing":
                new_elem = self._create_missing_element(page, corr)
                if new_elem is not None:
                    after = corr.insert_after_index if corr.insert_after_index is not None else -1
                    insert_idx = after + 1
                    insertions.append((insert_idx, new_elem))
                    applied += 1

        # Apply insertions in reverse order to preserve indices.
        insertions.sort(key=lambda pair: pair[0], reverse=True)
        for idx, elem in insertions:
            idx = max(0, min(idx, len(page.elements)))
            page.elements.insert(idx, elem)

        return applied

    def _apply_fix(self, page: Page, corr: Correction) -> bool:
        """Apply a fix_text or fix_table correction to an existing element."""
        if corr.element_index is None or corr.corrected is None:
            return False
        if corr.element_index < 0 or corr.element_index >= len(page.elements):
            return False

        elem = page.elements[corr.element_index]

        # Sanity check: if original is provided, verify it matches.
        if corr.original:
            if corr.original not in elem.content:
                log.debug(
                    "VLM review: original text %r not found in element %d, "
                    "applying anyway",
                    corr.original[:50], corr.element_index,
                )

        # Record original for traceability.
        elem.metadata["vlm_review_original"] = elem.content
        if corr.original and corr.original in elem.content:
            elem.content = elem.content.replace(corr.original, corr.corrected, 1)
        else:
            elem.content = corr.corrected
        elem.source = "vlm"
        elem.metadata["vlm_review_applied"] = corr.type
        return True

    def _create_missing_element(self, page: Page, corr: Correction) -> PageElement | None:
        """Create a new PageElement for missing content."""
        if not corr.content:
            return None

        elem_type = "text"
        if corr.content_type == "table":
            elem_type = "table"

        metadata: dict[str, Any] = {
            "vlm_review_applied": "add_missing",
        }
        if corr.content_type == "heading" and corr.heading_level:
            metadata["heading_level"] = corr.heading_level

        return PageElement(
            type=elem_type,
            content=corr.content,
            page_number=page.number,
            source="vlm",
            metadata=metadata,
        )


def _extract_json(text: str) -> Any:
    """Extract JSON from VLM response, handling markdown fences."""
    text = text.strip()
    # Strip markdown code fences if present.
    if text.startswith("```"):
        lines = text.split("\n")
        # Remove first line (```json) and last line (```)
        if lines[-1].strip() == "```":
            lines = lines[1:-1]
        else:
            lines = lines[1:]
        text = "\n".join(lines)

    return json.loads(text)
