"""Pipeline orchestrator for ParserX.

Coordinates the flow: Provider → Builders → Processors → Renderer.
"""

from __future__ import annotations

import logging
from pathlib import Path

from parserx.assembly.chapter import ChapterAssembler
from parserx.assembly.markdown import MarkdownRenderer
from parserx.builders.metadata import MetadataBuilder
from parserx.builders.ocr import OCRBuilder
from parserx.config.schema import ParserXConfig
from parserx.models.elements import Document
from parserx.processors.base import Processor
from parserx.processors.chapter import ChapterProcessor
from parserx.processors.header_footer import HeaderFooterProcessor
from parserx.processors.image import ImageProcessor
from parserx.processors.text_clean import TextCleanProcessor
from parserx.providers.pdf import PDFProvider

log = logging.getLogger(__name__)


class Pipeline:
    """Document parsing pipeline.

    Full flow:
      Provider → MetadataBuilder → [OCRBuilder] →
      HeaderFooter → Chapter → Image → TextClean →
      Renderer
    """

    def __init__(self, config: ParserXConfig | None = None):
        self._config = config or ParserXConfig()
        self._metadata_builder = MetadataBuilder(self._config.builders.metadata)
        self._ocr_builder = OCRBuilder(self._config.builders.ocr) if self._config.builders.ocr.engine != "none" else None
        self._processors: list[Processor] = self._build_processors()
        self._renderer = MarkdownRenderer(self._config.output)

    def parse(self, path: str | Path) -> str:
        """Parse a document and return Markdown output."""
        path = Path(path)
        if not path.exists():
            raise FileNotFoundError(f"Document not found: {path}")

        doc = self._run_pipeline(path)

        log.info("Rendering Markdown")
        markdown = self._renderer.render(doc)
        log.info("Done: %d characters output", len(markdown))
        return markdown

    def parse_to_dir(self, path: str | Path, output_dir: str | Path) -> Path:
        """Parse a document and write chapter files + index to output_dir."""
        doc = self.parse_to_document(path)
        assembler = ChapterAssembler(self._config.output)
        return assembler.assemble(doc, Path(output_dir))

    def parse_to_document(self, path: str | Path) -> Document:
        """Parse and return the Document model (for programmatic use)."""
        path = Path(path)
        if not path.exists():
            raise FileNotFoundError(f"Document not found: {path}")
        return self._run_pipeline(path)

    def _run_pipeline(self, path: Path) -> Document:
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

        # Step 3: OCR (only for pages that need it)
        if self._ocr_builder and path.suffix.lower() == ".pdf":
            scanned_pages = sum(
                1 for p in doc.pages if p.page_type.value in ("scanned", "mixed")
            )
            if scanned_pages > 0:
                log.info("Running selective OCR (%d pages need it)", scanned_pages)
                doc = self._ocr_builder.build(doc, path)
            else:
                log.info("OCR: all pages native, skipping")

        # Step 4: Processors
        for processor in self._processors:
            name = type(processor).__name__
            log.info("Processing: %s", name)
            doc = processor.process(doc)

        return doc

    def _extract(self, path: Path) -> Document:
        """Select provider based on file extension and extract."""
        suffix = path.suffix.lower()
        if suffix == ".pdf":
            return PDFProvider().extract(path)
        raise ValueError(f"Unsupported format: {suffix}")

    def _build_processors(self) -> list[Processor]:
        """Build the processor chain based on config.

        Order: HeaderFooter → Chapter → Image → TextClean.
        """
        processors: list[Processor] = []

        if self._config.processors.header_footer.enabled:
            processors.append(HeaderFooterProcessor(
                config=self._config.processors.header_footer,
                metadata_config=self._config.builders.metadata,
            ))

        if self._config.processors.chapter.enabled:
            processors.append(ChapterProcessor(self._config.processors.chapter))

        if self._config.processors.image.enabled:
            processors.append(ImageProcessor(self._config.processors.image))

        if self._config.processors.text_clean.enabled:
            processors.append(TextCleanProcessor(self._config.processors.text_clean))

        return processors
