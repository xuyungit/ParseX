"""Markdown renderer — converts Document to Markdown output."""

from __future__ import annotations

from parserx.config.schema import OutputConfig
from parserx.models.elements import Document, PageElement


class MarkdownRenderer:
    """Render a processed Document as Markdown text."""

    def __init__(self, config: OutputConfig | None = None):
        self._config = config or OutputConfig()

    def render(self, doc: Document) -> str:
        """Render the full document as a single Markdown string."""
        parts: list[str] = []

        for page in doc.pages:
            page_parts = self._render_page(page.elements, page.number)
            if page_parts:
                parts.append(page_parts)

        return "\n\n".join(parts)

    def _render_page(self, elements: list[PageElement], page_number: int) -> str:
        """Render all elements on a single page."""
        parts: list[str] = []

        for element in elements:
            rendered = self._render_element(element)
            if rendered:
                parts.append(rendered)

        if not parts:
            return ""

        # Add page marker for cross-reference
        page_marker = f"<!-- PAGE {page_number} -->"
        return page_marker + "\n" + "\n\n".join(parts)

    def _render_element(self, element: PageElement) -> str:
        """Render a single element to Markdown."""
        if element.metadata.get("skip_render"):
            return ""
        if element.type == "text":
            return self._render_text(element)
        if element.type == "table":
            return self._render_table(element)
        if element.type == "image":
            return self._render_image(element)
        if element.type == "formula":
            return self._render_formula(element)
        # Skip headers/footers (should be removed by processor)
        if element.type in ("header", "footer"):
            return ""
        return element.content

    def _render_text(self, element: PageElement) -> str:
        """Render text element, applying heading level if detected."""
        heading_level = element.metadata.get("heading_level")
        if heading_level:
            prefix = "#" * heading_level
            return f"{prefix} {element.content}"
        return element.content

    def _render_image(self, element: PageElement) -> str:
        """Render image with description.

        If description is short (single line), use as alt text in ![alt](path).
        If description is multi-line, render as image link + blockquote description.
        """
        description = element.metadata.get("description", "")
        image_path = element.metadata.get("saved_path", "")
        caption = str(element.metadata.get("caption", "")).strip()
        skipped = element.metadata.get("skipped", False)

        if skipped:
            return ""

        # Normalize description for embedding
        desc_oneline = description.replace("\n", " ").strip() if description else ""
        body = ""

        if image_path and description:
            # Short description → alt text; long → separate block
            if len(desc_oneline) <= 120:
                body = f"![{desc_oneline}]({image_path})"
            else:
                body = f"![]({image_path})\n\n> {desc_oneline}"
        elif image_path:
            body = f"![]({image_path})"
        elif description:
            body = f"> [图片] {desc_oneline}"

        if not body:
            return ""
        if caption:
            return f"{body}\n\n*{caption}*"
        return body

    def _render_table(self, element: PageElement) -> str:
        """Render table with an optional caption line above it."""
        caption = str(element.metadata.get("caption", "")).strip()
        if caption:
            return f"{caption}\n\n{element.content}"
        return element.content

    def _render_formula(self, element: PageElement) -> str:
        """Render formula as LaTeX."""
        is_inline = element.metadata.get("inline", False)
        if is_inline:
            return f"${element.content}$"
        return f"$$\n{element.content}\n$$"
