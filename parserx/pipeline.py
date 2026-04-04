"""Pipeline orchestrator for ParserX.

Coordinates the flow: Provider → Processors → Renderer.
"""

from __future__ import annotations

import logging
from pathlib import Path

from parserx.assembly.markdown import MarkdownRenderer
from parserx.config.schema import ParserXConfig
from parserx.models.elements import Document
from parserx.processors.base import Processor
from parserx.processors.text_clean import TextCleanProcessor
from parserx.providers.pdf import PDFProvider

log = logging.getLogger(__name__)


class Pipeline:
    """Document parsing pipeline.

    Orchestrates: detect format → extract → process → render.
    """

    def __init__(self, config: ParserXConfig | None = None):
        self._config = config or ParserXConfig()
        self._processors: list[Processor] = self._build_processors()
        self._renderer = MarkdownRenderer(self._config.output)

    def parse(self, path: str | Path) -> str:
        """Parse a document and return Markdown output.

        This is the main entry point for document parsing.
        """
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

        # Step 2: Run processors sequentially
        for processor in self._processors:
            name = type(processor).__name__
            log.info("Processing: %s", name)
            doc = processor.process(doc)

        # Step 3: Render to Markdown
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
        """Build the processor chain based on config."""
        processors: list[Processor] = []

        # Phase 1: Only TextCleanProcessor is implemented
        if self._config.processors.text_clean.enabled:
            processors.append(TextCleanProcessor(self._config.processors.text_clean))

        # Future processors will be added here in order:
        # HeaderFooterProcessor, ChapterProcessor, TableProcessor,
        # ImageProcessor, FormulaProcessor, LineUnwrapProcessor,
        # ReadingOrderProcessor

        return processors
