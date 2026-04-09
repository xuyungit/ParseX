"""Pipeline orchestrator for ParserX.

Coordinates the flow: Provider → Builders → Processors → Renderer.
"""

from __future__ import annotations

import logging
import tempfile
import time
from pathlib import Path

from parserx.assembly.chapter import ChapterAssembler
from parserx.assembly.crossref import CrossReferenceResolver
from parserx.assembly.markdown import MarkdownRenderer
from parserx.builders.image_extract import ImageExtractor
from parserx.builders.metadata import MetadataBuilder
from parserx.builders.reading_order import ReadingOrderBuilder
from parserx.builders.ocr import OCRBuilder
from parserx.config.schema import ParserXConfig
from parserx.models.elements import Document, PageType
from parserx.models.results import ParseResult
from parserx.processors.base import Processor
from parserx.processors.chapter import ChapterProcessor
from parserx.processors.code_block import CodeBlockProcessor
from parserx.processors.content_value import ContentValueProcessor
from parserx.processors.formula import FormulaProcessor
from parserx.processors.header_footer import HeaderFooterProcessor
from parserx.processors.image import ImageProcessor
from parserx.processors.line_unwrap import LineUnwrapProcessor
from parserx.processors.table import TableProcessor
from parserx.processors.text_clean import TextCleanProcessor
from parserx.processors.vlm_review import VLMReviewProcessor
from parserx.providers.docx import DOCXProvider
from parserx.providers.pdf import PDFProvider
from parserx.services.llm import create_llm_service, create_vlm_service
from parserx.verification import (
    CompletenessChecker,
    HallucinationDetector,
    ProductQualityChecker,
    StructureValidator,
)

log = logging.getLogger(__name__)


class Pipeline:
    """Document parsing pipeline.

    Full flow:
      Provider → MetadataBuilder → [OCRBuilder] →
      HeaderFooter → Chapter → [ImageExtract] → Image(+VLM) → TextClean →
      Renderer
    """

    def __init__(self, config: ParserXConfig | None = None):
        self._config = config or ParserXConfig()
        self._metadata_builder = MetadataBuilder(self._config.builders.metadata)
        self._reading_order_builder = ReadingOrderBuilder(
            self._config.processors.reading_order,
        )
        self._ocr_builder = self._create_ocr_builder()
        self._image_extractor = ImageExtractor()
        self._llm_service = self._create_llm_service()
        self._vlm_service = self._create_vlm_service()
        self._processors: list[Processor] = self._build_processors()
        self._crossref_resolver = CrossReferenceResolver()
        self._renderer = MarkdownRenderer(self._config.output)
        self._structure_validator = StructureValidator()
        self._completeness_checker = CompletenessChecker()
        self._hallucination_detector = HallucinationDetector(self._config.verification)
        self._outlined_text_pages: set[int] = set()
        self._product_quality_checker = ProductQualityChecker()

    def parse(self, path: str | Path) -> str:
        """Parse a document and return Markdown output."""
        return self.parse_result(path).markdown

    def parse_result(self, path: str | Path) -> ParseResult:
        """Parse a document and return Markdown plus quality warnings."""
        path = Path(path)
        if not path.exists():
            raise FileNotFoundError(f"Document not found: {path}")

        doc = self._run_pipeline(path, output_dir=None)
        markdown = self._renderer.render(doc)
        self._verify_all(doc, markdown)
        images_total = len(doc.elements_by_type("image"))
        images_skipped = sum(
            1 for e in doc.elements_by_type("image")
            if e.metadata.get("skipped")
        )
        llm_fallback_hits = sum(
            1
            for elem in doc.all_elements
            if elem.metadata.get("llm_fallback_used")
        )
        result = ParseResult(
            markdown=markdown,
            page_count=len(doc.pages),
            element_count=len(doc.all_elements),
            api_calls=self._collect_api_calls(doc),
            images_total=images_total,
            images_skipped=images_skipped,
            llm_fallback_hits=llm_fallback_hits,
            warnings=list(doc.metadata.verification_warnings),
        )
        return result

    def parse_to_dir(self, path: str | Path, output_dir: str | Path) -> Path:
        """Parse a document and write chapter files + index to output_dir."""
        return self.parse_result_to_dir(path, output_dir).markdown_path

    def parse_result_to_dir(
        self, path: str | Path, output_dir: str | Path,
    ) -> ParseResult:
        """Parse a document, write to output_dir, and return stats.

        Writes output.md + images/ (and optionally index.md + chapters/
        when chapter_split is enabled in config).

        The returned ParseResult includes the markdown text and all
        statistics.  ``result.markdown_path`` points to the written file.
        """
        path = Path(path)
        output_dir = Path(output_dir)
        if not path.exists():
            raise FileNotFoundError(f"Document not found: {path}")

        doc = self._run_pipeline(path, output_dir=output_dir)
        assembler = ChapterAssembler(self._config.output)
        md_path = assembler.assemble(doc, output_dir)

        markdown = self._renderer.render(doc)
        self._verify_all(doc, markdown, chapter_dir=output_dir)

        images_total = len(doc.elements_by_type("image"))
        images_skipped = sum(
            1 for e in doc.elements_by_type("image")
            if e.metadata.get("skipped")
        )
        llm_fallback_hits = sum(
            1 for elem in doc.all_elements
            if elem.metadata.get("llm_fallback_used")
        )
        from collections import Counter
        pt_counter: Counter[str] = Counter()
        for p in doc.pages:
            pt_counter[p.page_type.value] += 1

        return ParseResult(
            markdown=markdown,
            markdown_path=md_path,
            page_count=len(doc.pages),
            element_count=len(doc.all_elements),
            api_calls=self._collect_api_calls(doc),
            images_total=images_total,
            images_skipped=images_skipped,
            llm_fallback_hits=llm_fallback_hits,
            page_types=dict(pt_counter),
            warnings=list(doc.metadata.verification_warnings),
        )

    def parse_to_document(self, path: str | Path) -> Document:
        """Parse and return the Document model (for programmatic use)."""
        path = Path(path)
        if not path.exists():
            raise FileNotFoundError(f"Document not found: {path}")
        doc = self._run_pipeline(path, output_dir=None)
        self._verify_all(doc)
        return doc

    # Processors that rely on bounding-box geometry and are meaningless for
    # flow-based DOCX documents (no page coordinates).
    _GEOMETRY_PROCESSORS = (
        HeaderFooterProcessor,
        CodeBlockProcessor,
        ContentValueProcessor,
        VLMReviewProcessor,
    )

    def _run_pipeline(self, path: Path, output_dir: Path | None) -> Document:
        """Execute the full pipeline: extract → build → process."""
        is_docx = path.suffix.lower() in (".docx", ".doc")

        # Step 1: Extract
        log.info("Extracting: %s", path.name)
        doc = self._extract(path)
        log.info("Extracted %d pages, %d elements", len(doc.pages), len(doc.all_elements))

        # Step 2: Build metadata (font stats are meaningful for PDF only)
        if not is_docx:
            log.info("Building metadata")
            doc = self._metadata_builder.build(doc)
            if doc.metadata.font_stats.body_font.size > 0:
                body = doc.metadata.font_stats.body_font
                log.info(
                    "Body font: %s %.1fpt%s, %d heading candidate(s), %d numbering pattern(s)",
                    body.name, body.size,
                    " bold" if body.bold else "",
                    len(doc.metadata.font_stats.heading_candidates),
                    len(doc.metadata.numbering_patterns),
                )

        # Step 2.5: LLM quality check — detect formula fragmentation (PDF only)
        if (
            self._llm_service
            and not is_docx
            and self._config.builders.quality_check.enabled
        ):
            self._check_page_quality(doc)

        # Step 3: OCR (PDF only)
        if self._ocr_builder and not is_docx:
            scanned_pages = sum(
                1 for p in doc.pages if p.page_type.value in ("scanned", "mixed")
            )
            if scanned_pages > 0:
                log.info("Running selective OCR (%d pages need it)", scanned_pages)
                t0 = time.monotonic()
                doc = self._ocr_builder.build(doc, path)
                log.info("OCR done (%.1fs)", time.monotonic() - t0)
            else:
                log.info("OCR: all pages native, skipping")

        # Step 3.5: Reading order — column detection uses geometry (PDF only)
        if self._reading_order_builder and not is_docx:
            doc = self._reading_order_builder.build(doc)

        # Step 4: Run processors (skip geometry-dependent ones for DOCX)
        processors = self._processors
        if is_docx:
            processors = [
                p for p in processors
                if not isinstance(p, self._GEOMETRY_PROCESSORS)
            ]
            log.info(
                "DOCX mode: running %d processors (skipped geometry-dependent)",
                len(processors),
            )
        total_processors = len(processors)
        for proc_idx, processor in enumerate(processors, 1):
            name = type(processor).__name__
            log.info("[%d/%d] %s", proc_idx, total_processors, name)
            t0 = time.monotonic()
            doc = processor.process(doc)
            elapsed = time.monotonic() - t0
            if elapsed > 1.0:
                log.info("[%d/%d] %s done (%.1fs)", proc_idx, total_processors, name, elapsed)

            # After ImageProcessor classifies: extract images to disk and
            # run VLM descriptions.  When output_dir is None we still need
            # to extract + describe so that parse()/parse_to_document()
            # produce the same content as parse_to_dir(); we just use a
            # temporary directory and strip the disk paths afterwards.
            if isinstance(processor, ImageProcessor):
                if output_dir:
                    doc = self._extract_and_describe_images(doc, path, output_dir)
                else:
                    with tempfile.TemporaryDirectory() as tmp:
                        doc = self._extract_and_describe_images(
                            doc, path, Path(tmp),
                        )
                        # Temp dir is about to vanish — strip paths that
                        # would be meaningless to the caller.
                        for elem in doc.all_elements:
                            if elem.type == "image":
                                elem.metadata.pop("saved_path", None)
                                elem.metadata.pop("saved_abs_path", None)

                # Page-level VLM review (after images are extracted/described).
                if (
                    self._config.processors.vlm_review.enabled
                    and self._vlm_service
                    and not is_docx
                ):
                    review_proc = VLMReviewProcessor(
                        config=self._config.processors.vlm_review,
                        vlm_service=self._vlm_service,
                        source_path=path,
                        max_concurrent=self._config.services.vlm.max_concurrent,
                    )
                    log.info("[VLM Review] reviewing pages")
                    t0 = time.monotonic()
                    doc = review_proc.process(doc)
                    elapsed = time.monotonic() - t0
                    if elapsed > 1.0:
                        log.info("[VLM Review] done (%.1fs)", elapsed)

        log.info("Resolving figure/table captions")
        doc = self._crossref_resolver.resolve(doc)

        return doc

    def _extract_and_describe_images(
        self, doc: Document, source: Path, images_dir: Path,
    ) -> Document:
        """Extract non-skipped images to *images_dir* and run VLM if configured."""
        suffix = source.suffix.lower()
        log.info("Extracting images to %s", images_dir / "images")
        if suffix == ".pdf":
            doc = self._image_extractor.extract(doc, source, images_dir)
        elif suffix in (".docx", ".doc"):
            doc = self._image_extractor.extract_docx(doc, source, images_dir)

        if self._vlm_service and self._config.processors.image.vlm_description:
            vlm_processor = ImageProcessor(
                config=self._config.processors.image,
                vlm_service=self._vlm_service,
                max_concurrent=self._config.services.vlm.max_concurrent,
                table_config=self._config.processors.table,
            )
            doc = vlm_processor.process(doc)
        return doc

    def _extract(self, path: Path) -> Document:
        """Select provider based on file extension and extract."""
        suffix = path.suffix.lower()
        if suffix == ".pdf":
            return PDFProvider().extract(path)
        if suffix == ".docx":
            return DOCXProvider().extract(path)
        if suffix == ".doc":
            docx_path = self._convert_doc_to_docx(path)
            doc = DOCXProvider().extract(docx_path)
            doc.metadata.source_path = str(path)
            return doc
        raise ValueError(f"Unsupported format: {suffix}")

    @staticmethod
    def _convert_doc_to_docx(path: Path) -> Path:
        """Convert .doc to .docx using LibreOffice."""
        import subprocess

        out_dir = path.parent
        log.info("Converting .doc to .docx: %s", path.name)
        result = subprocess.run(
            [
                "soffice",
                "--headless",
                "--convert-to",
                "docx",
                "--outdir",
                str(out_dir),
                str(path),
            ],
            capture_output=True,
            text=True,
            timeout=120,
        )
        if result.returncode != 0:
            detail = result.stderr.strip() or result.stdout.strip()
            raise RuntimeError(
                f"LibreOffice conversion failed: {detail or 'exit ' + str(result.returncode)}"
            )
        docx_path = out_dir / (path.stem + ".docx")
        if not docx_path.exists():
            raise FileNotFoundError(
                f"LibreOffice conversion produced no output: {docx_path}"
            )
        log.info("Converted: %s", docx_path.name)
        return docx_path

    def _create_vlm_service(self):
        """Create VLM service if configured with endpoint and key."""
        cfg = self._config.services.vlm
        if cfg.endpoint and cfg.api_key:
            log.info("VLM service configured: %s / %s", cfg.endpoint, cfg.model)
            return create_vlm_service(cfg)
        return None

    def _create_ocr_builder(self) -> OCRBuilder | None:
        cfg = self._config.builders.ocr
        if cfg.engine == "none":
            return None
        if not cfg.endpoint or not cfg.token:
            log.info("OCR service not configured; selective OCR disabled")
            return None
        return OCRBuilder(
            cfg,
            skip_scan_image_marking=self._config.processors.image.vlm_refine_all_ocr,
        )

    # ------------------------------------------------------------------
    # LLM-based page quality check
    # ------------------------------------------------------------------

    _QUALITY_CHECK_SYSTEM = """\
You are a document extraction quality checker. Analyze the extracted text
from a single PDF page and determine whether it contains **fragmented
mathematical formulas**.

Fragmentation symptoms:
- Fraction numerators and denominators appear on separate lines
- Isolated single digits, operators (+, -, =, ×), or parentheses
- Variable names and their superscripts/subscripts split across lines
- Equation numbers like (1), (2) appearing as isolated fragments

If the page text looks like normal prose, tables, code, or reference
lists, answer false — even if it contains some short lines.

Respond with ONLY valid JSON: {"has_formula_fragments": true} or {"has_formula_fragments": false}
"""

    def _check_page_quality(self, doc: Document) -> None:
        """Check NATIVE pages for quality issues and reclassify for OCR.

        Three checks run in order:
        1. Deterministic layout complexity — pages with many tiny fragments
           or single-char heading-font elements are likely figure-heavy and
           benefit from OCR layout detection over font-based analysis.
        2. Outlined text detection — pages with tables whose header cells
           are mostly empty, indicating vector-rendered text (e.g. Word
           exports). Reclassified to MIXED so OCR can recover the text
           while preserving usable native elements.
        3. LLM formula fragmentation — pages with many short lines that
           look like split formulas.

        Pages flagged by checks 1/3 are reclassified to SCANNED (full OCR
        replacement). Pages flagged by check 2 are reclassified to MIXED.
        """
        cfg = self._config.builders.quality_check
        flagged = 0

        # ── Deterministic: layout complexity check ──
        if cfg.layout_complexity_check:
            body_size = doc.metadata.font_stats.body_font.size or 10.0
            for page in doc.pages:
                if page.page_type != PageType.NATIVE:
                    continue
                text_elems = [
                    e for e in page.elements
                    if e.type == "text" and e.bbox != (0.0, 0.0, 0.0, 0.0)
                ]
                if len(text_elems) < 6:
                    continue

                pw = page.width
                tiny = sum(
                    1 for e in text_elems
                    if (e.bbox[2] - e.bbox[0]) < pw * 0.15
                )
                tiny_ratio = tiny / len(text_elems)

                single_char_big = sum(
                    1 for e in text_elems
                    if len(e.content.strip()) <= 2
                    and e.font
                    and e.font.size > body_size * 1.05
                )

                if tiny_ratio > 0.5 or single_char_big > 2:
                    page.page_type = PageType.SCANNED
                    flagged += 1
                    log.info(
                        "Quality check: page %d layout complexity → OCR"
                        " (tiny=%.0f%%, single_char_big=%d)",
                        page.number,
                        tiny_ratio * 100,
                        single_char_big,
                    )

        # ── Deterministic: outlined text detection ──
        for page in doc.pages:
            if page.page_type != PageType.NATIVE:
                continue
            for elem in page.elements:
                if elem.type != "table":
                    continue
                lines = elem.content.split("\n")
                if len(lines) < 2:
                    continue
                raw_cells = lines[0].split("|")
                if len(raw_cells) < 4:  # need interior cells (leading + 3+ + trailing)
                    continue
                cells = [c.strip() for c in raw_cells[1:-1]]
                if len(cells) < 3:
                    continue
                empty = sum(1 for c in cells if c == "")
                if empty / len(cells) >= 0.5:
                    page.page_type = PageType.SCANNED
                    self._outlined_text_pages.add(page.number)
                    flagged += 1
                    log.info(
                        "Quality check: page %d outlined text (table with"
                        " %d/%d empty header cells) → OCR",
                        page.number, empty, len(cells),
                    )
                    break  # one signal per page is enough

        # ── LLM: formula fragmentation check ──
        for page in doc.pages:
            if page.page_type != PageType.NATIVE:
                continue

            # Cheap pre-filter: skip pages with few text elements
            text_elems = [e for e in page.elements if e.type == "text"]
            if len(text_elems) < 10:
                continue

            # Count lines and short lines
            total_lines = 0
            short_lines = 0
            for elem in text_elems:
                for line in elem.content.split("\n"):
                    line = line.strip()
                    if not line:
                        continue
                    total_lines += 1
                    if len(line) <= 5:
                        short_lines += 1

            if total_lines == 0:
                continue

            # Pre-filter: only send to LLM if short-line ratio exceeds
            # a loose threshold (avoids LLM calls on clean pages).
            if short_lines / total_lines < cfg.pre_filter_short_ratio:
                continue

            # Build text summary for LLM
            text_parts = [e.content for e in text_elems]
            summary = "\n".join(text_parts)[: cfg.max_text_chars]

            try:
                response = self._llm_service.complete(
                    self._QUALITY_CHECK_SYSTEM,
                    summary,
                    temperature=0.0,
                    max_tokens=64,
                )
                has_fragments = False
                try:
                    import json
                    result = json.loads(response)
                    has_fragments = bool(result.get("has_formula_fragments"))
                except (json.JSONDecodeError, AttributeError):
                    log.warning("Quality check: could not parse LLM JSON response")
                    has_fragments = False

                if has_fragments:
                    page.page_type = PageType.SCANNED
                    flagged += 1
                    log.info(
                        "Quality check: page %d flagged as formula-fragmented → OCR",
                        page.number,
                    )
            except Exception as exc:
                log.debug("Quality check LLM call failed for page %d: %s", page.number, exc)

        if flagged:
            log.info("Quality check: %d page(s) reclassified for OCR", flagged)

    def _create_llm_service(self):
        """Create LLM service if configured with endpoint and key."""
        cfg = self._config.services.llm
        if cfg.endpoint and cfg.api_key:
            log.info("LLM service configured: %s / %s", cfg.endpoint, cfg.model)
            return create_llm_service(cfg)
        return None

    def _verify_all(
        self,
        doc: Document,
        markdown: str | None = None,
        chapter_dir: Path | None = None,
    ) -> None:
        if markdown is not None:
            log.info("Rendered Markdown (%d characters)", len(markdown))

        warnings: list[str] = []

        if self._config.verification.hallucination_detection:
            warnings.extend(self._hallucination_detector.detect(doc))

        if self._config.verification.structure_validation:
            warnings.extend(self._structure_validator.validate(doc, chapter_dir=chapter_dir))

        if markdown is not None and self._config.verification.completeness_check:
            warnings.extend(self._completeness_checker.check(doc, markdown))

        if markdown is not None and self._config.verification.product_quality_check:
            warnings.extend(
                self._product_quality_checker.check(doc, markdown, chapter_dir)
            )

        self._store_warnings(doc, warnings)
        self._log_warnings(warnings)

    def _store_warnings(self, doc: Document, warnings: list[str]) -> None:
        for warning in warnings:
            if warning not in doc.metadata.verification_warnings:
                doc.metadata.verification_warnings.append(warning)

    def _log_warnings(self, warnings: list[str]) -> None:
        for warning in warnings:
            log.warning("Verification: %s", warning)

    def _collect_api_calls(self, doc: Document) -> dict[str, int]:
        ocr_pages = {
            elem.page_number for elem in doc.all_elements if elem.source == "ocr"
        }
        vlm_images = sum(
            1
            for elem in doc.elements_by_type("image")
            if elem.metadata.get("description")
            and not elem.metadata.get("vlm_skipped_due_to_large_text_overlap")
        )
        llm_calls = sum(
            1
            for elem in doc.all_elements
            if elem.metadata.get("llm_fallback_used")
        )
        llm_calls = doc.metadata.processing_stats.get("llm_calls", llm_calls)
        return {
            "ocr": len(ocr_pages),
            "vlm": vlm_images,
            "llm": llm_calls,
        }

    def _build_processors(self) -> list[Processor]:
        """Build the processor chain based on config.

        Order: HeaderFooter → CodeBlock → Chapter → Table → Image(+VLM) → Formula
        → LineUnwrap → TextClean → ContentValue.
        """
        processors: list[Processor] = []

        if self._config.processors.header_footer.enabled:
            processors.append(HeaderFooterProcessor(
                config=self._config.processors.header_footer,
                metadata_config=self._config.builders.metadata,
            ))

        if self._config.processors.code_block.enabled:
            processors.append(CodeBlockProcessor(self._config.processors.code_block))

        if self._config.processors.chapter.enabled:
            processors.append(ChapterProcessor(
                self._config.processors.chapter,
                llm_service=self._llm_service,
            ))

        if self._config.processors.table.enabled:
            processors.append(TableProcessor(self._config.processors.table))

        if self._config.processors.image.enabled:
            processors.append(ImageProcessor(
                config=self._config.processors.image,
                vlm_service=self._vlm_service,
                table_config=self._config.processors.table,
            ))

        # VLMReviewProcessor: page-level review runs after image extraction
        # (injected during _run, not here, because it needs source_path)

        if self._config.processors.formula.enabled:
            processors.append(FormulaProcessor(
                self._config.processors.formula,
                vlm_service=self._vlm_service,
            ))

        if self._config.processors.line_unwrap.enabled:
            processors.append(LineUnwrapProcessor(self._config.processors.line_unwrap))

        if self._config.processors.text_clean.enabled:
            processors.append(TextCleanProcessor(self._config.processors.text_clean))

        if self._config.processors.content_value.enabled:
            processors.append(ContentValueProcessor(
                self._config.processors.content_value,
                llm_service=self._llm_service,
            ))

        return processors
