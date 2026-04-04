"""Chapter assembler — split document into per-chapter files with index.

Takes a processed Document with heading_level annotations and produces:
- index.md: table of contents with links
- chapters/ch_01.md, ch_02.md, ...: per-chapter content
- final.md: complete document (from MarkdownRenderer)

Split points are H1 headings. Each chapter includes its H2/H3 subsections.
"""

from __future__ import annotations

import logging
from pathlib import Path

from parserx.assembly.markdown import MarkdownRenderer
from parserx.config.schema import OutputConfig
from parserx.models.elements import Document, Page, PageElement

log = logging.getLogger(__name__)


class ChapterAssembler:
    """Split a processed document into chapter files with an index."""

    def __init__(self, config: OutputConfig | None = None):
        self._config = config or OutputConfig()
        self._renderer = MarkdownRenderer(self._config)

    def assemble(self, doc: Document, output_dir: Path) -> Path:
        """Write chapter files and index to output_dir.

        Returns the path to final.md.
        """
        output_dir.mkdir(parents=True, exist_ok=True)

        # Render full document
        full_markdown = self._renderer.render(doc)
        final_path = output_dir / "final.md"
        final_path.write_text(full_markdown, encoding="utf-8")
        log.info("Written final.md (%d chars)", len(full_markdown))

        if not self._config.chapter_split:
            return final_path

        # Collect heading info for TOC and splitting
        headings = self._collect_headings(doc)

        if not headings:
            log.info("No headings found, skipping chapter split")
            return final_path

        # Split into chapters at H1 boundaries
        chapters = self._split_into_chapters(doc, headings)

        # Write chapter files
        chapters_dir = output_dir / "chapters"
        chapters_dir.mkdir(exist_ok=True)

        for idx, (title, chapter_doc) in enumerate(chapters, 1):
            chapter_md = self._renderer.render(chapter_doc)
            chapter_path = chapters_dir / f"ch_{idx:02d}.md"
            chapter_path.write_text(chapter_md, encoding="utf-8")

        log.info("Written %d chapter files", len(chapters))

        # Write index
        index_md = self._build_index(chapters)
        index_path = output_dir / "index.md"
        index_path.write_text(index_md, encoding="utf-8")
        log.info("Written index.md")

        return final_path

    def _collect_headings(self, doc: Document) -> list[dict]:
        """Collect all elements with heading_level for TOC generation."""
        headings = []
        for page in doc.pages:
            for elem in page.elements:
                level = elem.metadata.get("heading_level")
                if level:
                    first_line = elem.content.split("\n")[0].strip()
                    headings.append({
                        "level": level,
                        "title": first_line,
                        "page": page.number,
                        "element": elem,
                    })
        return headings

    def _split_into_chapters(
        self, doc: Document, headings: list[dict]
    ) -> list[tuple[str, Document]]:
        """Split document at H1 boundaries into separate Document objects."""
        # Find H1 heading elements (these are split points)
        h1_elements = [h["element"] for h in headings if h["level"] == 1]

        if not h1_elements:
            # No H1 headings — treat entire document as one chapter
            title = headings[0]["title"] if headings else "Document"
            return [(title, doc)]

        chapters: list[tuple[str, Document]] = []
        h1_set = set(id(e) for e in h1_elements)

        # Group pages/elements between H1 boundaries
        current_title = "前言"
        current_pages: list[Page] = []
        current_page_elements: list[PageElement] = []
        current_page: Page | None = None

        for page in doc.pages:
            for elem in page.elements:
                if id(elem) in h1_set:
                    # Save previous chapter
                    if current_page and current_page_elements:
                        current_pages.append(Page(
                            number=current_page.number,
                            width=current_page.width,
                            height=current_page.height,
                            page_type=current_page.page_type,
                            elements=current_page_elements,
                        ))
                    if current_pages or current_page_elements:
                        chapter_doc = Document(
                            pages=current_pages,
                            metadata=doc.metadata,
                        )
                        chapters.append((current_title, chapter_doc))

                    # Start new chapter
                    current_title = elem.content.split("\n")[0].strip()
                    current_pages = []
                    current_page_elements = [elem]
                    current_page = page
                else:
                    if current_page and current_page.number != page.number:
                        # New page — flush previous
                        current_pages.append(Page(
                            number=current_page.number,
                            width=current_page.width,
                            height=current_page.height,
                            page_type=current_page.page_type,
                            elements=current_page_elements,
                        ))
                        current_page_elements = []
                    current_page = page
                    current_page_elements.append(elem)

        # Flush last chapter
        if current_page and current_page_elements:
            current_pages.append(Page(
                number=current_page.number,
                width=current_page.width,
                height=current_page.height,
                page_type=current_page.page_type,
                elements=current_page_elements,
            ))
        if current_pages:
            chapter_doc = Document(pages=current_pages, metadata=doc.metadata)
            chapters.append((current_title, chapter_doc))

        return chapters

    def _build_index(self, chapters: list[tuple[str, Document]]) -> str:
        """Generate a table-of-contents Markdown file."""
        lines = ["# 目录", ""]

        for idx, (title, chapter_doc) in enumerate(chapters, 1):
            filename = f"ch_{idx:02d}.md"
            lines.append(f"- [{title}](chapters/{filename})")

            # Add H2/H3 sub-entries
            for page in chapter_doc.pages:
                for elem in page.elements:
                    level = elem.metadata.get("heading_level")
                    if level and level >= 2:
                        sub_title = elem.content.split("\n")[0].strip()
                        indent = "  " * (level - 1)
                        lines.append(f"{indent}- {sub_title}")

        lines.append("")
        return "\n".join(lines)
