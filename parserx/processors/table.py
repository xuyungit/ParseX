"""Table processor with cross-page table merging."""

from __future__ import annotations

import logging
import re

from parserx.config.schema import TableProcessorConfig
from parserx.models.elements import Document, PageElement

log = logging.getLogger(__name__)
_PURE_PAGE_NUMBER_RE = re.compile(r"^[\s\-—]*\d{1,5}[\s\-—]*$")


def _parse_md_table(content: str) -> tuple[list[str], list[str], list[list[str]]]:
    """Parse a Markdown table into header cells, separator, and data rows.

    Returns:
        (header_cells, separator_parts, data_rows)
        where data_rows is a list of lists of cell strings.
    """
    lines = [l for l in content.strip().splitlines() if l.strip()]
    if len(lines) < 2:
        return [], [], []

    def split_row(line: str) -> list[str]:
        # Strip leading/trailing pipes, split by |
        stripped = line.strip()
        if stripped.startswith("|"):
            stripped = stripped[1:]
        if stripped.endswith("|"):
            stripped = stripped[:-1]
        return [cell.strip() for cell in stripped.split("|")]

    header_cells = split_row(lines[0])

    # Check if line 1 is a separator (---|---|...)
    sep_line = lines[1].strip()
    if re.match(r"^\|?[\s\-:|]+(\|[\s\-:|]+)+\|?$", sep_line):
        sep_parts = split_row(sep_line)
        data_rows = [split_row(l) for l in lines[2:]]
    else:
        sep_parts = ["---"] * len(header_cells)
        data_rows = [split_row(l) for l in lines[1:]]

    return header_cells, sep_parts, data_rows


def _build_md_table(
    header: list[str], separator: list[str], rows: list[list[str]]
) -> str:
    """Rebuild a Markdown table from components."""
    lines = []
    lines.append("| " + " | ".join(header) + " |")
    lines.append("|" + "|".join(separator) + "|")
    for row in rows:
        # Pad row to match header length
        padded = row + [""] * (len(header) - len(row))
        lines.append("| " + " | ".join(padded[: len(header)]) + " |")
    return "\n".join(lines)


def _is_at_page_bottom(element: PageElement, page_height: float) -> bool:
    """Check if element is near the bottom of the page."""
    if page_height <= 0:
        # No page geometry — use position heuristic
        return element.bbox[3] > 0
    return element.bbox[3] > page_height * 0.75


def _is_at_page_top(element: PageElement, page_height: float) -> bool:
    """Check if element is near the top of the page."""
    if page_height <= 0:
        return element.bbox[1] >= 0
    return element.bbox[1] < page_height * 0.25


def _headers_match(h1: list[str], h2: list[str]) -> bool:
    """Check if two headers are the same (ignoring whitespace)."""
    if len(h1) != len(h2):
        return False
    return all(a.strip() == b.strip() for a, b in zip(h1, h2))


def _is_ignorable_bridge_element(element: PageElement) -> bool:
    """Bridge pages may contain only ignorable artifacts such as page numbers."""
    if element.metadata.get("skip_render"):
        return True
    if element.type in {"header", "footer"}:
        return True
    if element.type == "text" and _PURE_PAGE_NUMBER_RE.match(element.content.strip()):
        return True
    return False


def _page_allows_table_bridge(page) -> bool:
    return all(_is_ignorable_bridge_element(element) for element in page.elements)


def _normalized_row_signature(row: list[str]) -> tuple[str, ...]:
    return tuple(cell.strip().lower() for cell in row)


def _is_degenerate_table_artifact(element: PageElement) -> bool:
    """Detect low-information UI/layout tables that should not render as data tables."""
    header, _sep, rows = _parse_md_table(element.content)
    if not header:
        return False

    all_rows = [header] + rows
    if len(all_rows) > 3 or len(header) > 3:
        return False

    normalized_rows = [_normalized_row_signature(row) for row in all_rows]
    non_empty_cells = [
        cell.strip()
        for row in all_rows
        for cell in row
        if cell.strip()
    ]
    if not non_empty_cells:
        return True

    has_numbers = any(any(ch.isdigit() for ch in cell) for cell in non_empty_cells)
    if has_numbers:
        return False

    unique_cells = {cell.lower() for cell in non_empty_cells}
    empty_cells = sum(1 for row in all_rows for cell in row if not cell.strip())
    total_cells = sum(len(row) for row in all_rows)
    repeated_header = len(normalized_rows) >= 2 and normalized_rows[0] == normalized_rows[1]
    trailing_blank_row = bool(rows) and all(not cell.strip() for cell in rows[-1])

    return (
        repeated_header
        and trailing_blank_row
        and len(unique_cells) <= 3
        and empty_cells / max(total_cells, 1) >= 0.25
    )


class TableProcessor:
    """Process tables: cross-page merging and future VLM fallback.

    Cross-page merge conditions:
      1. Page N ends with a table, page N+1 starts with a table
      2. Column count matches
      3. Page N+1 table header matches N's (repeated header) or has no header
    """

    def __init__(self, config: TableProcessorConfig | None = None):
        self._config = config or TableProcessorConfig()

    def process(self, doc: Document) -> Document:
        if not self._config.enabled:
            return doc

        self._suppress_degenerate_tables(doc)

        if self._config.cross_page_merge:
            doc = self._merge_cross_page_tables(doc)

        return doc

    def _suppress_degenerate_tables(self, doc: Document) -> None:
        removed = 0
        for page in doc.pages:
            kept: list[PageElement] = []
            for element in page.elements:
                if element.type == "table" and _is_degenerate_table_artifact(element):
                    removed += 1
                    continue
                kept.append(element)
            page.elements = kept
        if removed:
            log.info("Suppressed %d degenerate table artifact(s)", removed)

    def _merge_cross_page_tables(self, doc: Document) -> Document:
        """Detect and merge tables split across page boundaries."""
        merged_count = 0

        for i in range(len(doc.pages) - 1):
            page_curr = doc.pages[i]
            last_table = self._find_last_table(page_curr)
            if last_table is None:
                continue
            if not _is_at_page_bottom(last_table, page_curr.height):
                continue
            next_idx = i + 1
            while next_idx < len(doc.pages):
                page_next = doc.pages[next_idx]
                first_table = self._find_first_table(page_next)
                if first_table is None:
                    if _page_allows_table_bridge(page_next):
                        next_idx += 1
                        continue
                    break
                if not _is_at_page_top(first_table, page_next.height):
                    break

                h1, sep1, rows1 = _parse_md_table(last_table.content)
                h2, sep2, rows2 = _parse_md_table(first_table.content)
                if not h1 or not h2:
                    break
                if len(h1) != len(h2):
                    break

                if _headers_match(h1, h2):
                    merged_rows = rows1 + rows2
                else:
                    merged_rows = rows1 + [h2] + rows2

                last_table.content = _build_md_table(h1, sep1, merged_rows)
                last_table.metadata["rows"] = len(merged_rows) + 1
                merged_from_pages = list(
                    last_table.metadata.get("merged_from_pages", [page_curr.number])
                )
                if page_next.number not in merged_from_pages:
                    merged_from_pages.append(page_next.number)
                last_table.metadata["merged_from_pages"] = merged_from_pages

                page_next.elements.remove(first_table)
                merged_count += 1

                log.info(
                    "Merged table across pages %d-%d (%d cols, %d total rows)",
                    merged_from_pages[0],
                    page_next.number,
                    len(h1),
                    len(merged_rows),
                )
                next_idx += 1

        if merged_count:
            log.info("Merged %d cross-page table(s)", merged_count)

        return doc

    def _find_last_table(self, page) -> PageElement | None:
        """Find the last table element on a page."""
        tables = [e for e in page.elements if e.type == "table"]
        return tables[-1] if tables else None

    def _find_first_table(self, page) -> PageElement | None:
        """Find the first table element on a page."""
        for e in page.elements:
            if e.type == "table":
                return e
        return None
