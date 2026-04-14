"""Markdown renderer — converts Document to Markdown output."""

from __future__ import annotations

from parserx.config.schema import OutputConfig
from parserx.models.elements import Document, PageElement

_INTERNAL_MARKER_FRAGMENTS = frozenset({
    "preserved in OCR body text",
    "preserved in body text",
})


def get_image_reference_text(element: PageElement) -> str:
    """Return the text that should appear in rendered image references.

    Returns empty string when the image content is already covered by
    surrounding body text (OCR overlap evidence on text-heavy images)
    or when the description contains internal marker text that should
    never be user-visible.
    """
    description = str(element.metadata.get("description", "")).replace("\n", " ").strip()
    if (
        description
        and element.metadata.get("description_source") == "ocr_overlap_evidence"
        and element.metadata.get("text_heavy_image")
    ):
        return ""
    if description and any(frag in description for frag in _INTERNAL_MARKER_FRAGMENTS):
        return ""
    return description


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
        """Render text element, applying heading level or code fence if detected."""
        if element.metadata.get("code_block"):
            return f"```\n{element.content}\n```"
        heading_level = element.metadata.get("heading_level")
        if heading_level:
            prefix = "#" * heading_level
            return f"{prefix} {element.content}"

        # Apply inline formatting from merged DOCX / PDF spans.
        # Validate that spans still reflect current content — processors
        # (e.g. line_unwrap) may have mutated element.content without
        # updating the span records, in which case the spans are stale
        # and would truncate the rendered text. Fall back to plain content
        # when the concatenated span text no longer matches.
        inline_spans = element.metadata.get("inline_spans")
        if inline_spans:
            concat = "".join(s.get("text", "") for s in inline_spans)
            if concat == element.content:
                return self._render_inline_spans(inline_spans)

        # Single-element underline — the only formatting that's meaningful
        # for non-merged elements.  Bold/italic on whole paragraphs is
        # noisy and would regress PDF metrics (where bold comes from font
        # analysis).  Mixed bold/italic/underline within a paragraph is
        # handled by the inline_spans path above.
        if element.metadata.get("underline"):
            return f"<u>{element.content}</u>"
        return element.content

    def _render_inline_spans(self, spans: list[dict]) -> str:
        """Render merged text spans with per-span formatting.

        Consolidates adjacent spans with the same bold/italic state to
        avoid redundant marker pairs like ``**a****b**`` → ``**ab**``.
        Underline uses HTML ``<u>`` tags which nest cleanly inside bold/italic,
        so underline differences don't break consolidation.
        """
        # Group consecutive spans by (bold, italic) to reduce marker noise
        groups: list[tuple[bool, bool, list[tuple[str, bool]]]] = []
        for span in spans:
            text = span.get("text", "")
            if not text:
                continue
            bold = span.get("bold", False)
            italic = span.get("italic", False)
            underline = span.get("underline", False)
            if groups and groups[-1][0] == bold and groups[-1][1] == italic:
                groups[-1][2].append((text, underline))
            else:
                groups.append((bold, italic, [(text, underline)]))

        parts: list[str] = []
        for bold, italic, sub_spans in groups:
            inner = "".join(
                f"<u>{t}</u>" if ul else t for t, ul in sub_spans
            )
            parts.append(self._apply_inline_format(inner, bold, italic, False))
        return "".join(parts)

    @staticmethod
    def _apply_inline_format(
        text: str, bold: bool, italic: bool, underline: bool
    ) -> str:
        """Wrap text with markdown/HTML inline formatting markers."""
        if not text:
            return text
        if underline:
            text = f"<u>{text}</u>"
        if bold and italic:
            text = f"***{text}***"
        elif bold:
            text = f"**{text}**"
        elif italic:
            text = f"*{text}*"
        return text

    def _render_image(self, element: PageElement) -> str:
        """Render image with description.

        If description is short (single line), use as alt text in ![alt](path).
        If description is multi-line, render as image link + blockquote description.

        For DOCX embedded document images (scanned pages, certificates,
        etc.) the rendering is always: **image + description first, then
        extracted text/table content below**.  This mirrors the PDF
        workflow where scanned pages show both the page image and the
        OCR-extracted content.
        """
        description = element.metadata.get("description", "")
        image_path = element.metadata.get("saved_path", "")
        caption = str(element.metadata.get("caption", "")).strip()
        skipped = element.metadata.get("skipped", False)

        # VLM correction takes priority: even if the image is "skipped"
        # (no image file to render), corrected text/table content from
        # VLM still needs to appear in the output.
        # Exception: vector figures should always render as images with
        # descriptions, not as transcribed text — VLM often reads the
        # diagram labels and routes them as "correction", which would
        # re-introduce the same text we suppressed from native elements.
        is_vector_figure = element.metadata.get("vector_figure", False)
        vlm_image_type = element.metadata.get("vlm_image_type", "")
        if not description:
            # VLM correction route doesn't populate description — use
            # the raw summary instead.
            vlm_raw = element.metadata.get("vlm_raw") or {}
            description = vlm_raw.get("summary", "")
        corrected_table = str(element.metadata.get("vlm_corrected_table", "")).strip()
        corrected_text = str(element.metadata.get("vlm_corrected_text", "")).strip()
        # Skip correction for diagrams/charts/vector figures — VLM reads
        # visible text labels and transcribes them, which duplicates
        # already-suppressed native text or produces noisy output.
        # Correction is only useful for table/text images.
        skip_correction = is_vector_figure or vlm_image_type in (
            "diagram", "chart",
        )

        # ── DOCX embedded images: image-first, corrections-below ──────
        # Embedded scanned documents in DOCX are standalone images —
        # unlike PDF where corrections replace overlapping OCR text,
        # here the image itself is the only visual evidence and must
        # always be shown.  Extracted text/tables appear below as
        # supplementary transcription.
        is_embedded_doc_image = element.metadata.get("embedded_document_image", False)
        if is_embedded_doc_image and image_path:
            return self._render_embedded_doc_image(
                element, image_path, description, caption,
                corrected_text, corrected_table, skip_correction,
            )

        if (corrected_table or corrected_text) and not skip_correction:
            parts: list[str] = []
            if corrected_text:
                parts.append(corrected_text)
            if corrected_table:
                parts.append(corrected_table)
            if description:
                ref = get_image_reference_text(element)
                if ref and image_path:
                    parts.append(f"![{ref}]({image_path})")
                elif ref:
                    parts.append(f"*{ref}*")
            if caption:
                parts.append(f"*{caption}*")
            return "\n\n".join(parts)

        if skipped:
            return ""

        # Normalize description for embedding
        desc_oneline = description.replace("\n", " ").strip() if description else ""
        # When description came from vlm_raw fallback (not stored in
        # metadata), pass it through the reference-text filter manually.
        if desc_oneline and not element.metadata.get("description"):
            reference_text = desc_oneline
        else:
            reference_text = get_image_reference_text(element)
        body = ""

        if image_path and description:
            # Always render description as visible text below the image.
            ref = reference_text or desc_oneline
            body = f"![{ref}]({image_path})\n\n> {desc_oneline}"
        elif image_path:
            body = f"![]({image_path})"
        elif description:
            if not reference_text:
                body = ""
            else:
                body = f"> [图片] {reference_text or desc_oneline}"

        if not body:
            return ""
        if caption:
            return f"{body}\n\n*{caption}*"
        return body

    @staticmethod
    def _render_embedded_doc_image(
        element: PageElement,
        image_path: str,
        description: str,
        caption: str,
        corrected_text: str,
        corrected_table: str,
        skip_correction: bool,
    ) -> str:
        """Render a DOCX-embedded scanned image: image first, then content.

        Layout::

            ![description](path)
            > description

            <extracted text>

            <extracted table>
        """
        parts: list[str] = []

        # 1. Image always comes first
        desc_oneline = description.replace("\n", " ").strip() if description else ""
        if desc_oneline:
            parts.append(f"![{desc_oneline}]({image_path})")
        else:
            # Fallback: use VLM image_type as minimal alt text
            vlm_type = element.metadata.get("vlm_image_type", "")
            alt = vlm_type or "document image"
            parts.append(f"![{alt}]({image_path})")

        # 2. Description as blockquote (when available)
        if desc_oneline:
            parts.append(f"> {desc_oneline}")

        # 3. Caption
        if caption:
            parts.append(f"*{caption}*")

        # 4. Extracted content below the image
        if not skip_correction:
            if corrected_text:
                parts.append(corrected_text)
            if corrected_table:
                parts.append(corrected_table)

        return "\n\n".join(parts)

    def _render_table(self, element: PageElement) -> str:
        """Render table with an optional caption line above it."""
        caption = str(element.metadata.get("caption", "")).strip()
        if caption:
            return f"**{caption}**\n\n{element.content}"
        return element.content

    def _render_formula(self, element: PageElement) -> str:
        """Render formula as LaTeX."""
        is_inline = element.metadata.get("inline", False)
        if is_inline:
            return f"${element.content}$"
        return f"$$\n{element.content}\n$$"
