"""Completeness checks for rendered Markdown output."""

from __future__ import annotations

import re

from parserx.assembly.markdown import get_image_reference_text
from parserx.models.elements import Document, PageElement
from parserx.text_utils import normalize_for_comparison

_PAGE_MARKER_RE = re.compile(r"<!-- PAGE (\d+) -->")
_TABLE_ROW_RE = re.compile(r"^\|.*\|$")
_TABLE_SEP_RE = re.compile(r"^\|?(?:[\s\-:]+\|)+\s*$")
_IMAGE_RE = re.compile(r"!\[([^\]]*)\]\([^)]+\)")
_BLOCKQUOTE_RE = re.compile(r"^\s*>\s?", re.MULTILINE)


def _is_renderable(element: PageElement) -> bool:
    if element.metadata.get("skip_render"):
        return False
    if element.type in {"header", "footer"}:
        return False
    if element.type == "image":
        # The renderer emits VLM-corrected content even for skipped
        # images (no image file), so check corrected fields first.
        has_vlm_content = bool(
            element.metadata.get("vlm_corrected_text")
            or element.metadata.get("vlm_corrected_table")
        )
        if has_vlm_content:
            return True
        if element.metadata.get("skipped"):
            return False
        # An image is renderable only when the renderer will actually
        # produce output: either a saved file path exists, or the
        # description survives get_image_reference_text() suppression.
        has_path = bool(element.metadata.get("saved_path"))
        has_desc = bool(get_image_reference_text(element))
        return has_path or has_desc
    return bool(element.content.strip())


def _count_rendered_tables(markdown: str) -> int:
    count = 0
    lines = markdown.splitlines()
    i = 0

    while i < len(lines) - 1:
        if _TABLE_ROW_RE.match(lines[i].strip()) and _TABLE_SEP_RE.match(lines[i + 1].strip()):
            count += 1
            i += 2
            while i < len(lines) and _TABLE_ROW_RE.match(lines[i].strip()):
                i += 1
            continue
        i += 1

    return count


def _normalize_markdown_for_volume(markdown: str) -> str:
    """Normalize rendered Markdown while discounting markup-only overhead."""
    markdown = _IMAGE_RE.sub(r"\1", markdown)
    markdown = _BLOCKQUOTE_RE.sub("", markdown)
    return normalize_for_comparison(markdown)


class CompletenessChecker:
    """Check whether rendered Markdown preserved the processed document."""

    def __init__(self, text_tolerance: float = 0.2):
        self._text_tolerance = text_tolerance

    def check(self, doc: Document, markdown: str) -> list[str]:
        warnings: list[str] = []
        warnings.extend(self._check_page_markers(doc, markdown))
        warnings.extend(self._check_text_volume(doc, markdown))
        warnings.extend(self._check_image_references(doc, markdown))
        warnings.extend(self._check_table_count(doc, markdown))
        return warnings

    def _check_page_markers(self, doc: Document, markdown: str) -> list[str]:
        rendered_pages = {int(m.group(1)) for m in _PAGE_MARKER_RE.finditer(markdown)}
        expected_pages = {
            page.number for page in doc.pages
            if any(_is_renderable(elem) for elem in page.elements)
        }
        if rendered_pages == expected_pages:
            return []
        return [
            "Page marker mismatch: "
            f"expected {len(expected_pages)} rendered page(s), got {len(rendered_pages)}."
        ]

    def _check_text_volume(self, doc: Document, markdown: str) -> list[str]:
        parts: list[str] = []
        for elem in doc.all_elements:
            if elem.type in {"text", "table", "formula"} and elem.content.strip():
                parts.append(elem.content)
            # VLM-corrected content on image elements is rendered by the
            # assembler but not present in the element's content field.
            if elem.type == "image":
                vlm_text = str(elem.metadata.get("vlm_corrected_text", "")).strip()
                vlm_table = str(elem.metadata.get("vlm_corrected_table", "")).strip()
                if vlm_text:
                    parts.append(vlm_text)
                if vlm_table:
                    parts.append(vlm_table)
        source_text = "\n".join(parts)
        source_len = len(normalize_for_comparison(source_text))
        output_len = len(_normalize_markdown_for_volume(markdown))

        if source_len == 0:
            return []

        delta = abs(output_len - source_len) / source_len
        if delta <= self._text_tolerance:
            return []

        return [
            "Rendered text volume drifted beyond tolerance: "
            f"source={source_len} chars, output={output_len} chars."
        ]

    def _check_image_references(self, doc: Document, markdown: str) -> list[str]:
        warnings: list[str] = []

        for elem in doc.elements_by_type("image"):
            if not _is_renderable(elem):
                continue
            if elem.metadata.get("skipped") or not elem.metadata.get("needs_vlm"):
                continue

            saved_path = str(elem.metadata.get("saved_path", "")).strip()
            description = get_image_reference_text(elem)

            # Description intentionally suppressed (e.g. text-heavy OCR
            # overlap already in body) and no saved image file — the
            # renderer correctly produces nothing for this image.
            if not saved_path and not description:
                continue

            referenced = False

            if saved_path and saved_path in markdown:
                referenced = True
            elif description and description in markdown:
                referenced = True

            if not referenced:
                warnings.append(
                    f"Page {elem.page_number}: image output missing rendered reference."
                )

        return warnings

    def _check_table_count(self, doc: Document, markdown: str) -> list[str]:
        expected_tables = len(
            [e for e in doc.elements_by_type("table")
             if not e.metadata.get("skip_render")]
        )
        # VLM-corrected tables on image elements also render as table
        # blocks when the content uses markdown table syntax.
        for elem in doc.elements_by_type("image"):
            vlm_table = str(elem.metadata.get("vlm_corrected_table", "")).strip()
            if vlm_table and _count_rendered_tables(vlm_table) > 0:
                expected_tables += 1
        rendered_tables = _count_rendered_tables(markdown)
        if expected_tables == rendered_tables:
            return []
        return [
            "Table count mismatch: "
            f"document has {expected_tables}, markdown rendered {rendered_tables}."
        ]
