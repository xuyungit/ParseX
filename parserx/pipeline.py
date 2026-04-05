"""Pipeline orchestrator for ParserX.

Coordinates the flow: Provider → Builders → Processors → Renderer.
"""

from __future__ import annotations

import logging
import tempfile
from pathlib import Path

from parserx.assembly.chapter import ChapterAssembler
from parserx.assembly.crossref import CrossReferenceResolver
from parserx.assembly.markdown import MarkdownRenderer
from parserx.builders.image_extract import ImageExtractor
from parserx.builders.metadata import MetadataBuilder
from parserx.builders.ocr import OCRBuilder
from parserx.config.schema import ParserXConfig
from parserx.models.elements import Document
from parserx.models.results import ParseResult
from parserx.processors.base import Processor
from parserx.processors.chapter import ChapterProcessor
from parserx.processors.header_footer import HeaderFooterProcessor
from parserx.processors.image import ImageProcessor
from parserx.processors.line_unwrap import LineUnwrapProcessor
from parserx.processors.table import TableProcessor
from parserx.processors.text_clean import TextCleanProcessor
from parserx.providers.docx import DOCXProvider
from parserx.providers.pdf import PDFProvider
from parserx.services.llm import create_llm_service, create_vlm_service
from parserx.verification import (
    CompletenessChecker,
    HallucinationDetector,
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
        self._ocr_builder = (
            OCRBuilder(self._config.builders.ocr)
            if self._config.builders.ocr.engine != "none" else None
        )
        self._image_extractor = ImageExtractor()
        self._llm_service = self._create_llm_service()
        self._vlm_service = self._create_vlm_service()
        self._processors: list[Processor] = self._build_processors()
        self._crossref_resolver = CrossReferenceResolver()
        self._renderer = MarkdownRenderer(self._config.output)
        self._structure_validator = StructureValidator()
        self._completeness_checker = CompletenessChecker()
        self._hallucination_detector = HallucinationDetector(self._config.verification)

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
        result = ParseResult(
            markdown=markdown,
            page_count=len(doc.pages),
            element_count=len(doc.all_elements),
            api_calls=self._collect_api_calls(doc),
            warnings=list(doc.metadata.verification_warnings),
        )
        return result

    def parse_to_dir(self, path: str | Path, output_dir: str | Path) -> Path:
        """Parse a document and write chapter files + index to output_dir."""
        path = Path(path)
        output_dir = Path(output_dir)
        if not path.exists():
            raise FileNotFoundError(f"Document not found: {path}")

        doc = self._run_pipeline(path, output_dir=output_dir)
        assembler = ChapterAssembler(self._config.output)
        final_path = assembler.assemble(doc, output_dir)

        markdown = self._renderer.render(doc)
        self._verify_all(doc, markdown, chapter_dir=output_dir)

        return final_path

    def parse_to_document(self, path: str | Path) -> Document:
        """Parse and return the Document model (for programmatic use)."""
        path = Path(path)
        if not path.exists():
            raise FileNotFoundError(f"Document not found: {path}")
        doc = self._run_pipeline(path, output_dir=None)
        self._verify_all(doc)
        return doc

    def _run_pipeline(self, path: Path, output_dir: Path | None) -> Document:
        """Execute the full pipeline: extract → build → process."""
        # Step 1: Extract
        log.info("Extracting: %s", path.name)
        doc = self._extract(path)
        log.info("Extracted %d pages, %d elements", len(doc.pages), len(doc.all_elements))

        # Step 2: Build metadata
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

        # Step 3: OCR (selective)
        if self._ocr_builder and path.suffix.lower() == ".pdf":
            scanned_pages = sum(
                1 for p in doc.pages if p.page_type.value in ("scanned", "mixed")
            )
            if scanned_pages > 0:
                log.info("Running selective OCR (%d pages need it)", scanned_pages)
                doc = self._ocr_builder.build(doc, path)
            else:
                log.info("OCR: all pages native, skipping")

        # Step 4: Run processors
        for processor in self._processors:
            name = type(processor).__name__
            log.info("Processing: %s", name)
            doc = processor.process(doc)

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
        elif suffix == ".docx":
            doc = self._image_extractor.extract_docx(doc, source, images_dir)

        if self._vlm_service and self._config.processors.image.vlm_description:
            vlm_processor = ImageProcessor(
                config=self._config.processors.image,
                vlm_service=self._vlm_service,
                max_concurrent=self._config.services.vlm.max_concurrent,
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
        raise ValueError(f"Unsupported format: {suffix}")

    def _create_vlm_service(self):
        """Create VLM service if configured with endpoint and key."""
        cfg = self._config.services.vlm
        if cfg.endpoint and cfg.api_key:
            log.info("VLM service configured: %s / %s", cfg.endpoint, cfg.model)
            return create_vlm_service(cfg)
        return None

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
        )
        llm_calls = sum(
            1
            for elem in doc.all_elements
            if elem.metadata.get("llm_fallback_used")
        )
        return {
            "ocr": len(ocr_pages),
            "vlm": vlm_images,
            "llm": llm_calls,
        }

    def _build_processors(self) -> list[Processor]:
        """Build the processor chain based on config.

        Order: HeaderFooter → Chapter → Table → Image(+VLM) → LineUnwrap → TextClean.
        """
        processors: list[Processor] = []

        if self._config.processors.header_footer.enabled:
            processors.append(HeaderFooterProcessor(
                config=self._config.processors.header_footer,
                metadata_config=self._config.builders.metadata,
            ))

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
            ))

        if self._config.processors.line_unwrap.enabled:
            processors.append(LineUnwrapProcessor(self._config.processors.line_unwrap))

        if self._config.processors.text_clean.enabled:
            processors.append(TextCleanProcessor(self._config.processors.text_clean))

        return processors
