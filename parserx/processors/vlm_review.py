"""Page-level VLM review processor for OCR correction and missing-text recovery.

Renders selected pages as images, sends them alongside current extraction
results to a VLM "reviewer", and applies structured corrections in-place.

Currently scoped to SCANNED/MIXED pages only (OCR error correction).
NATIVE page review (vector-rendered text recovery) is not yet reliable
enough with current VLM models — see iteration backlog for details.
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
Compare a page image against extraction results to find errors.
Respond ONLY with a JSON object in the exact format specified.
"""

_REVIEW_USER_PROMPT_TEMPLATE = """\
Compare the page image against this extraction for page {page_number}:

{extraction_summary}

Find these issues:
1. Text errors: wrong characters, OCR mistakes (especially similar Chinese chars: 混凝土/混疑土, 钢筋/钢盘)
2. Missing content: text, headings, or tables visible in the image but absent from extraction.
3. Table errors: wrong structure, missing column headers, missing cells, incorrect content

Rules:
- Only report issues clearly visible in the image. Do NOT invent content.
- CRITICAL: Provide the EXACT text as shown in the image, character by character. \
Do NOT rephrase, correct grammar, improve wording, or normalize formatting. \
Transcribe faithfully what the image shows.
- For fix_text: provide element_index, the exact original text, and the exact \
corrected text as visible in the image.
- For add_missing: provide the full content exactly as shown in the image.
- For fix_table: provide element_index and the corrected full table in Markdown.
- If extraction is correct, return empty corrections list.

You MUST respond with this exact JSON format:
{{"corrections": [...], "page_quality": "ok" or "needs_correction"}}

Example with corrections:
{{"corrections": [{{"type": "fix_text", "element_index": 3, "original": "钢盘混疑土", "corrected": "钢筋混凝土"}}, {{"type": "add_missing", "content": "## 第3章 设计规定", "content_type": "heading", "heading_level": 2, "insert_after_index": 5}}, {{"type": "fix_table", "element_index": 8, "original": "| 1 | 2 |", "corrected": "| 序号 | 名称 |\\n| 1 | 2 |"}}], "page_quality": "needs_correction"}}

Example with no issues:
{{"corrections": [], "page_quality": "ok"}}
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
            pages = [p for p in doc.pages if self._needs_review(p)]

        # Cost protection
        if len(pages) > self._config.max_pages_per_doc:
            log.info(
                "VLM review: capping from %d to %d pages",
                len(pages), self._config.max_pages_per_doc,
            )
            pages = pages[: self._config.max_pages_per_doc]

        return pages

    def _needs_review(self, page: Page) -> bool:
        """Determine whether a page needs VLM review.

        Currently only SCANNED/MIXED pages are reviewed — OCR errors are
        expected on these pages.  NATIVE page review is deferred until VLM
        models are reliable enough to avoid introducing more errors than
        they fix (see iteration backlog).
        """
        return page.page_type in (PageType.SCANNED, PageType.MIXED)

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
            log.debug(
                "VLM review page %d: raw response: %.500s",
                page.number, response,
            )
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

        # Standard format: {"corrections": [...], "page_quality": "..."}
        if isinstance(data, dict):
            raw_corrections = data.get("corrections", [])
        elif isinstance(data, list):
            # Fallback: VLM returned a bare array of corrections.
            raw_corrections = data
        else:
            return []

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
        # Strip trailing "..." that VLM may echo from the truncated summary.
        if corr.original:
            original = corr.original
            if original.endswith("..."):
                original = original[:-3]
                corr.original = original
            if original not in elem.content:
                log.debug(
                    "VLM review: original text %r not found in element %d, "
                    "skipping correction",
                    corr.original[:50], corr.element_index,
                )
                return False

        # Record original for traceability.
        elem.metadata["vlm_review_original"] = elem.content
        if corr.original and corr.original in elem.content:
            elem.content = elem.content.replace(corr.original, corr.corrected, 1)
        else:
            # No original provided — full replacement (e.g. fully garbled text).
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
