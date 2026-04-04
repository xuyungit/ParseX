"""Pipeline orchestrator for ParserX.

Coordinates the flow: Provider → Builders → Processors → Renderer.
"""

from __future__ import annotations

import logging
from pathlib import Path

from parserx.assembly.markdown import MarkdownRenderer
from parserx.builders.metadata import MetadataBuilder
from parserx.config.schema import ParserXConfig
from parserx.models.elements import Document
from parserx.processors.base import Processor
from parserx.processors.chapter import ChapterProcessor
from parserx.processors.header_footer import HeaderFooterProcessor
from parserx.processors.text_clean import TextCleanProcessor
from parserx.providers.pdf import PDFProvider

log = logging.getLogger(__name__)


class Pipeline:
    """Document parsing pipeline.

    Orchestrates: detect format → extract → build metadata → process → render.
    """

    def __init__(self, config: ParserXConfig | None = None):
        self._config = config or ParserXConfig()
        self._metadata_builder = MetadataBuilder(self._config.builders.metadata)
        self._processors: list[Processor] = self._build_processors()
        self._renderer = MarkdownRenderer(self._config.output)

    def parse(self, path: str | Path) -> str:
        """Parse a document and return Markdown output."""
        path = Path(path)
        if not path.exists():
            raise FileNotFoundError(f"Document not found: {path}")

        # Step 1: Extract via appropriate provider
        log.info("Extracting: %s", path.name)
        doc = self._extract(path)
        log.info(
            "Extracted %d pages, %d elements",
            len(doc.pages),
            len(doc.all_elements),
        )

        # Step 2: Build document metadata (deterministic analysis)
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

        # Step 3: Run processors sequentially
        for processor in self._processors:
            name = type(processor).__name__
            log.info("Processing: %s", name)
            doc = processor.process(doc)

        # Step 4: Render to Markdown
        log.info("Rendering Markdown")
        markdown = self._renderer.render(doc)

        log.info("Done: %d characters output", len(markdown))
        return markdown

    def parse_to_document(self, path: str | Path) -> Document:
        """Parse and return the Document model (for programmatic use)."""
        path = Path(path)
        if not path.exists():
            raise FileNotFoundError(f"Document not found: {path}")

        doc = self._extract(path)
        doc = self._metadata_builder.build(doc)
        for processor in self._processors:
            doc = processor.process(doc)
        return doc

    def _extract(self, path: Path) -> Document:
        """Select provider based on file extension and extract."""
        suffix = path.suffix.lower()

        if suffix == ".pdf":
            provider = PDFProvider()
            return provider.extract(path)
        # Future: .docx, .png/.jpg etc.
        raise ValueError(f"Unsupported format: {suffix}")

    def _build_processors(self) -> list[Processor]:
        """Build the processor chain based on config.

        Order matters: HeaderFooter → Chapter → TextClean.
        Remove noise first, then detect structure, then clean text.
        """
        processors: list[Processor] = []

        # 1. Remove headers/footers first (before chapter detection)
        if self._config.processors.header_footer.enabled:
            processors.append(HeaderFooterProcessor(
                config=self._config.processors.header_footer,
                metadata_config=self._config.builders.metadata,
            ))

        # 2. Detect chapter/heading structure
        if self._config.processors.chapter.enabled:
            processors.append(ChapterProcessor(self._config.processors.chapter))

        # 3. Clean text artifacts
        if self._config.processors.text_clean.enabled:
            processors.append(TextCleanProcessor(self._config.processors.text_clean))

        # Future: TableProcessor, ImageProcessor, FormulaProcessor,
        # LineUnwrapProcessor, ReadingOrderProcessor

        return processors
