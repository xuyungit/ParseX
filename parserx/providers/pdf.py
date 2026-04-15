"""PDF provider using PyMuPDF for character-level text extraction."""

from __future__ import annotations

import logging
from pathlib import Path

import fitz  # PyMuPDF

from parserx.models.elements import (
    Document,
    DocumentMetadata,
    FontInfo,
    Page,
    PageElement,
    PageType,
)
from parserx.processors.text_clean import normalize_fullwidth_ascii

log = logging.getLogger(__name__)


def _is_cjk_or_fullwidth_punct(ch: str) -> bool:
    """Return True if *ch* is CJK or fullwidth punctuation.

    Fullwidth ASCII *letters* (Ａ-Ｚ, ａ-ｚ) and *digits* (０-９) are
    excluded — they are Latin text rendered in wide form, and word-space
    detection must still apply between them.
    """
    cp = ord(ch)
    return (
        0x3400 <= cp <= 0x4DBF  # CJK Unified Extension A
        or 0x4E00 <= cp <= 0x9FFF  # CJK Unified Ideographs
        or 0xF900 <= cp <= 0xFAFF  # CJK Compatibility Ideographs
        or 0x20000 <= cp <= 0x2A6DF  # CJK Extension B
        or 0x3000 <= cp <= 0x303F  # CJK Symbols and Punctuation
        or 0xFF01 <= cp <= 0xFF0F  # Fullwidth punctuation ！＂＃…／
        or 0xFF1A <= cp <= 0xFF20  # Fullwidth ：；＜＝＞？＠
        or 0xFF3B <= cp <= 0xFF40  # Fullwidth ［＼］＾＿｀
        or 0xFF5B <= cp <= 0xFF65  # Fullwidth ｛｜｝～ + halfwidth forms
        or 0xFE30 <= cp <= 0xFE4F  # CJK Compatibility Forms
    )


def _join_block_lines(line_entries: list[tuple[str, tuple]]) -> str:
    """Join text lines within a block, merging same-visual-row segments.

    PyMuPDF sometimes splits a single visual line into multiple ``line``
    objects when there is a large horizontal gap between text segments
    (e.g., ``"1"`` and ``"Introduction"`` rendered with a wide space).
    Both lines share the same y-coordinate range, so we detect overlap
    and join them with a space instead of a newline.
    """
    if not line_entries:
        return ""
    parts: list[str] = [line_entries[0][0]]
    for i in range(1, len(line_entries)):
        _text, bbox = line_entries[i]
        prev_bbox = line_entries[i - 1][1]
        # Vertical overlap ratio: if the y-ranges overlap by >50% of
        # the shorter line's height, the two lines are on the same row.
        overlap = min(prev_bbox[3], bbox[3]) - max(prev_bbox[1], bbox[1])
        min_height = min(prev_bbox[3] - prev_bbox[1], bbox[3] - bbox[1])
        if min_height > 0 and overlap / min_height > 0.5:
            parts.append(" " + _text)
        else:
            parts.append("\n" + _text)
    return "".join(parts)


def _merge_line_segments(
    line_entries: list[tuple[str, tuple]],
    line_segment_lists: list[list[dict]],
) -> list[dict]:
    """Merge per-line segments into block-level segments.

    Mirrors ``_join_block_lines`` (same-row → " ", different-row → "\\n")
    but at formatting-segment granularity. Adjacent segments with the same
    (bold, italic) merge, so a uniform-format block collapses to a single
    segment.
    """
    merged: list[dict] = []

    def _same_fmt(a: dict, b: dict) -> bool:
        return (
            a.get("bold") == b.get("bold")
            and a.get("italic") == b.get("italic")
            and a.get("underline", False) == b.get("underline", False)
            and a.get("sup", False) == b.get("sup", False)
        )

    def _append(seg: dict) -> None:
        if not seg["text"]:
            return
        # Whitespace-only formatted segments render as noise (** **) and
        # can be absorbed into either neighbor without changing formatting
        # semantics. Attach to the previous segment if present, else keep
        # as plain so the next meaningful segment opens a clean run.
        if seg["text"].strip() == "":
            if merged:
                merged[-1]["text"] += seg["text"]
                return
            merged.append({"text": seg["text"], "bold": False, "italic": False, "underline": False, "sup": False})
            return
        if merged and _same_fmt(merged[-1], seg):
            merged[-1]["text"] += seg["text"]
        else:
            merged.append(dict(seg))

    for i, segments in enumerate(line_segment_lists):
        if not segments:
            continue
        if merged:
            prev_bbox = line_entries[i - 1][1]
            curr_bbox = line_entries[i][1]
            overlap = min(prev_bbox[3], curr_bbox[3]) - max(prev_bbox[1], curr_bbox[1])
            min_height = min(prev_bbox[3] - prev_bbox[1], curr_bbox[3] - curr_bbox[1])
            same_row = min_height > 0 and overlap / min_height > 0.5
            if same_row:
                # Same visual row: space can merge into adjacent formatted segment
                merged[-1]["text"] += " "
            else:
                # Newline joiner: emit as a plain (unformatted) segment so
                # bold/italic markers close before the newline and reopen
                # after. Markdown bold across newlines is parsed inconsistently
                # by downstream tools (and strip-regex in evaluators that uses
                # non-DOTALL matching leaves stray ** markers in text).
                merged.append({"text": "\n", "bold": False, "italic": False, "underline": False, "sup": False})
        for seg in segments:
            _append(seg)
    return merged


def _char_is_underlined(char_bbox: tuple, underline_rects: list[tuple]) -> bool:
    """Return True if a drawn-rectangle underline sits just below *char_bbox*.

    ``underline_rects`` is a list of ``(x0, y0, x1, y1)`` for thin horizontal
    rectangles harvested from ``page.get_drawings()``. A char is underlined
    when its horizontal span overlaps a rect whose top edge lies within a
    small band directly below the char's bottom edge.
    """
    cx0, _, cx1, cy1 = char_bbox
    if cx1 <= cx0:
        return False
    for ux0, uy0, ux1, uy1 in underline_rects:
        if uy0 < cy1 - 1:  # must be below char (allow tiny overlap)
            continue
        if uy0 > cy1 + 3:  # too far below
            continue
        if ux1 <= cx0 or ux0 >= cx1:
            continue
        # Require ≥ 50% horizontal coverage to avoid tick-mark false positives
        overlap = min(ux1, cx1) - max(ux0, cx0)
        if overlap / max(cx1 - cx0, 0.01) >= 0.3:
            return True
    return False


def _reconstruct_line_segments(
    line_spans: list[dict],
    underline_rects: list[tuple] | None = None,
) -> list[dict]:
    """Reconstruct a line as a list of formatting-run segments.

    Each segment is ``{"text": str, "bold": bool, "italic": bool,
    "underline": bool, "sup": bool}``. Concatenated ``text`` fields match
    ``_reconstruct_line_from_chars``. Adjacent chars with the same
    formatting quadruple are merged; gap-inserted spaces attach to the
    following segment.
    """
    rects = underline_rects or []
    # Flatten chars with geometry + per-char formatting.
    chars: list[tuple[str, float, float, float, bool, bool, bool, bool]] = []
    for span in line_spans:
        font_size = span.get("size", 12.0)
        flags = span.get("flags", 0)
        bold = bool(flags & 2**4)
        italic = bool(flags & 2**1)
        sup = bool(flags & 2**0)
        for ch_dict in span.get("chars", []):
            c = ch_dict.get("c", "")
            if not c:
                continue
            bbox = ch_dict.get("bbox", (0, 0, 0, 0))
            underlined = _char_is_underlined(bbox, rects) if rects else False
            chars.append((c, bbox[0], bbox[2], font_size, bold, italic, underlined, sup))

    if not chars:
        return []

    def _seg(ch: str, b: bool, i: bool, u: bool, sp: bool) -> dict:
        return {"text": ch, "bold": b, "italic": i, "underline": u, "sup": sp}

    segments: list[dict] = [_seg(chars[0][0], chars[0][4], chars[0][5], chars[0][6], chars[0][7])]
    for i in range(1, len(chars)):
        prev_ch, _, prev_x1, prev_sz, _, _, _, _ = chars[i - 1]
        curr_ch, curr_x0, _, curr_sz, curr_b, curr_i, curr_u, curr_sp = chars[i]
        gap = curr_x0 - prev_x1
        threshold = (prev_sz + curr_sz) * 0.125
        needs_space = gap > threshold and not (
            _is_cjk_or_fullwidth_punct(prev_ch) and _is_cjk_or_fullwidth_punct(curr_ch)
        )
        last = segments[-1]
        if (
            last["bold"] == curr_b
            and last["italic"] == curr_i
            and last["underline"] == curr_u
            and last["sup"] == curr_sp
        ):
            last["text"] += (" " if needs_space else "") + curr_ch
        else:
            segments.append(_seg((" " if needs_space else "") + curr_ch, curr_b, curr_i, curr_u, curr_sp))
    return segments


def _reconstruct_line_from_chars(line_spans: list[dict]) -> str:
    """Rebuild a text line from rawdict spans, inserting spaces at gaps.

    PDFs often encode word boundaries as physical gaps between character
    positions rather than explicit space characters.  This function detects
    those gaps by comparing adjacent character bboxes and inserts a space
    when the gap exceeds a font-size-relative threshold.

    Between two adjacent CJK ideographs no space is inserted regardless of
    the gap, because CJK scripts do not use inter-word spaces.
    """
    # Flatten all chars across spans, keeping font size.
    chars: list[tuple[str, float, float, float]] = []  # (ch, x0, x1, font_size)
    for span in line_spans:
        font_size = span.get("size", 12.0)
        for ch_dict in span.get("chars", []):
            c = ch_dict.get("c", "")
            if not c:
                continue
            bbox = ch_dict.get("bbox", (0, 0, 0, 0))
            chars.append((c, bbox[0], bbox[2], font_size))

    if not chars:
        return ""

    parts: list[str] = [chars[0][0]]
    for i in range(1, len(chars)):
        prev_ch, _, prev_x1, prev_sz = chars[i - 1]
        curr_ch, curr_x0, _, curr_sz = chars[i]
        gap = curr_x0 - prev_x1
        threshold = (prev_sz + curr_sz) * 0.125  # 0.25 * avg font size
        if gap > threshold and not (_is_cjk_or_fullwidth_punct(prev_ch) and _is_cjk_or_fullwidth_punct(curr_ch)):
            parts.append(" ")
        parts.append(curr_ch)

    return "".join(parts)


class PDFProvider:
    """Extract text, tables, and images from PDF using PyMuPDF.

    Unlike the old legacy pipeline approach (PyMuPDF4LLM → Markdown string),
    this provider extracts character-level metadata (font name, size, bold)
    which enables rule-based heading detection in MetadataBuilder.
    """

    def extract(self, path: Path) -> Document:
        doc = fitz.open(str(path))
        pages: list[Page] = []

        for page_idx in range(len(doc)):
            fitz_page = doc[page_idx]
            page = self._extract_page(fitz_page, page_idx + 1)
            pages.append(page)

        doc.close()

        metadata = DocumentMetadata(
            page_count=len(pages),
            source_format="pdf",
            source_path=str(path),
        )

        return Document(pages=pages, metadata=metadata)

    def _extract_page(self, fitz_page: fitz.Page, page_number: int) -> Page:
        """Extract all elements from a single PDF page."""
        rect = fitz_page.rect
        page = Page(
            number=page_number,
            width=rect.width,
            height=rect.height,
        )

        # Extract text blocks with font metadata
        text_elements = self._extract_text_elements(fitz_page, page_number)

        # Extract tables
        table_elements = self._extract_tables(fitz_page, page_number)

        # Remove text blocks that overlap with table regions to avoid
        # duplicating table cell text as both prose and markdown table.
        text_elements = self._remove_table_overlapping_text(
            text_elements, table_elements
        )

        # Extract images
        image_elements = self._extract_images(fitz_page, page_number)

        # Merge all elements and sort by visual position (top-to-bottom,
        # left-to-right) so mid-page tables/images stay in reading order.
        all_elements = text_elements + table_elements + image_elements
        all_elements.sort(key=lambda e: (e.bbox[1], e.bbox[0]))
        page.elements.extend(all_elements)

        # Classify page type
        page.page_type = self._classify_page(fitz_page, text_elements, image_elements)

        return page

    @staticmethod
    def _collect_underline_rects(fitz_page: fitz.Page) -> list[tuple]:
        """Extract thin horizontal rectangles from page drawings.

        These are the typical baked-in underline marks (PDF renders
        underlines as filled rectangles, not as a character flag).
        Filtered to height < 1.5pt and width > 3pt to exclude tick marks
        and large filled boxes.
        """
        rects: list[tuple] = []
        try:
            drawings = fitz_page.get_drawings()
        except Exception:
            return rects
        for dr in drawings:
            rect = dr.get("rect")
            if rect is None:
                continue
            x0, y0, x1, y1 = rect.x0, rect.y0, rect.x1, rect.y1
            h = y1 - y0
            w = x1 - x0
            if 0 < h < 1.5 and w > 3:
                rects.append((x0, y0, x1, y1))
        return rects

    def _extract_text_elements(
        self, fitz_page: fitz.Page, page_number: int
    ) -> list[PageElement]:
        """Extract text with character-level font metadata.

        Uses page.get_text("rawdict") to get per-character bounding boxes
        alongside font name, size, and flags for each text span.  Character-
        level positioning allows detecting inter-word gaps so that spaces
        are correctly reconstructed (PDFs often encode word boundaries as
        physical gaps rather than explicit space characters).
        """
        elements: list[PageElement] = []
        page_dict = fitz_page.get_text("rawdict", flags=fitz.TEXT_PRESERVE_WHITESPACE)
        underline_rects = self._collect_underline_rects(fitz_page)

        for block in page_dict.get("blocks", []):
            if block.get("type") != 0:  # type 0 = text block
                continue

            block_bbox = (
                block["bbox"][0],
                block["bbox"][1],
                block["bbox"][2],
                block["bbox"][3],
            )

            # Collect all lines in this block, with their bounding boxes
            # so we can detect same-visual-row lines that PyMuPDF splits
            # due to horizontal gaps (e.g., "1" and "Introduction" on the
            # same line but with a wide space between them).
            line_entries: list[tuple[str, tuple]] = []  # (text, line_bbox)
            line_segment_lists: list[list[dict]] = []  # per-line formatting runs
            dominant_font = FontInfo()
            max_font_chars = 0

            for line in block.get("lines", []):
                line_spans = line.get("spans", [])
                # Narrow the underline-rect pool to the line's y-band to
                # keep the per-char O(rects) check cheap.
                lbbox = line.get("bbox", (0, 0, 0, 0))
                line_rects = [
                    r for r in underline_rects
                    if r[1] >= lbbox[1] - 2 and r[1] <= lbbox[3] + 4
                ] if underline_rects else []
                segments = _reconstruct_line_segments(line_spans, line_rects)
                # Apply fullwidth normalization to each segment text so the
                # concatenation matches _reconstruct_line_from_chars output.
                for seg in segments:
                    seg["text"] = normalize_fullwidth_ascii(seg["text"])
                line_text = "".join(seg["text"] for seg in segments)

                # Track dominant font (by character count)
                for span in line_spans:
                    char_count = len(span.get("chars", []))
                    if char_count > max_font_chars:
                        max_font_chars = char_count
                        flags = span.get("flags", 0)
                        dominant_font = FontInfo(
                            name=span.get("font", ""),
                            size=round(span.get("size", 0.0), 1),
                            bold=bool(flags & 2**4),  # bit 4 = bold
                            italic=bool(flags & 2**1),  # bit 1 = italic
                        )

                if line_text.strip():
                    line_entries.append((line_text, tuple(line["bbox"])))
                    line_segment_lists.append(segments)

            # Join lines: same visual row → space, different row → newline.
            # PyMuPDF sometimes splits a single visual line into multiple
            # "line" objects when there is a large horizontal gap between
            # text segments.  We detect this by checking y-coordinate overlap.
            content = _join_block_lines(line_entries)
            if not content.strip():
                continue

            metadata: dict = {}
            # Emit per-span formatting only when the block actually has mixed
            # bold/italic. Uniform-format blocks are captured by dominant_font
            # and emitting inline_spans would add noise (and risk splitting
            # text around zero-width artifacts).
            block_segments = _merge_line_segments(line_entries, line_segment_lists)
            has_mixed = len({
                (s["bold"], s["italic"], s.get("underline", False), s.get("sup", False))
                for s in block_segments
            }) > 1
            if has_mixed:
                metadata["inline_spans"] = block_segments

            elements.append(
                PageElement(
                    type="text",
                    content=content,
                    bbox=block_bbox,
                    page_number=page_number,
                    font=dominant_font,
                    source="native",
                    metadata=metadata,
                )
            )

        return elements

    def _extract_tables(
        self, fitz_page: fitz.Page, page_number: int
    ) -> list[PageElement]:
        """Extract tables using PyMuPDF's built-in table finder."""
        elements: list[PageElement] = []

        try:
            tables = fitz_page.find_tables()
        except Exception:
            return elements

        for table in tables:
            bbox = table.bbox
            # Convert table to markdown
            md_lines: list[str] = []
            extracted = table.extract()

            if not extracted:
                continue

            # Build markdown table
            for row_idx, row in enumerate(extracted):
                cells = [
                    normalize_fullwidth_ascii(str(cell).replace("\n", " ").strip())
                    if cell else ""
                    for cell in row
                ]
                md_lines.append("| " + " | ".join(cells) + " |")
                if row_idx == 0:
                    md_lines.append("|" + "|".join(["---"] * len(cells)) + "|")

            if md_lines:
                elements.append(
                    PageElement(
                        type="table",
                        content="\n".join(md_lines),
                        bbox=(bbox[0], bbox[1], bbox[2], bbox[3]),
                        page_number=page_number,
                        source="native",
                        metadata={"rows": len(extracted), "cols": len(extracted[0]) if extracted else 0},
                    )
                )

        return elements

    def _extract_images(
        self, fitz_page: fitz.Page, page_number: int
    ) -> list[PageElement]:
        """Extract image references from the page."""
        elements: list[PageElement] = []

        for img_info in fitz_page.get_image_info(xrefs=True):
            bbox = img_info.get("bbox")
            if not bbox:
                continue

            width = img_info.get("width", 0)
            height = img_info.get("height", 0)

            # Skip tiny images (likely decorative)
            if width < 10 or height < 10:
                continue

            elements.append(
                PageElement(
                    type="image",
                    content="",  # Image content extracted later if needed
                    bbox=(bbox[0], bbox[1], bbox[2], bbox[3]),
                    page_number=page_number,
                    source="native",
                    metadata={
                        "width": width,
                        "height": height,
                        "xref": img_info.get("xref", 0),
                    },
                )
            )

        return elements

    def _classify_page(
        self,
        fitz_page: fitz.Page,
        text_elements: list[PageElement],
        image_elements: list[PageElement],
    ) -> PageType:
        """Classify page as native, scanned, or mixed.

        Per-page classification (not per-document) addresses P15.

        Detects OCR-layered scanned PDFs by checking spatial
        relationships: if a dominant image covers most of the page
        and most text characters are located *inside* that image's
        bounding box, the page is a scan with an overlaid OCR text
        layer — not a genuine native PDF.
        """
        total_text_chars = sum(len(e.content) for e in text_elements)
        page_area = fitz_page.rect.width * fitz_page.rect.height

        if page_area == 0:
            return PageType.NATIVE

        # Check if large images cover most of the page (likely scanned)
        total_image_area = 0.0
        dominant_image: PageElement | None = None
        dominant_image_area = 0.0
        for img in image_elements:
            img_w = img.bbox[2] - img.bbox[0]
            img_h = img.bbox[3] - img.bbox[1]
            area = img_w * img_h
            total_image_area += area
            if area > dominant_image_area:
                dominant_image_area = area
                dominant_image = img

        image_coverage = total_image_area / page_area

        if total_text_chars < 50 and image_coverage > 0.5:
            return PageType.SCANNED

        # Vector-drawn PDFs (e.g. print-to-PDF from web pages with embedded
        # SVG, rasterized scans re-saved as vector paths). No images, little
        # or no extractable text, but a dense set of drawing operations that
        # visually render text. Without OCR these pages emit empty output.
        if total_text_chars < 500:
            try:
                drawing_count = len(fitz_page.get_drawings())
            except Exception:
                drawing_count = 0
            if drawing_count > 200:
                return PageType.SCANNED

        # Detect OCR text layer on scanned pages: a dominant image
        # covers >50% of the page and >70% of text chars sit inside it.
        if (
            dominant_image is not None
            and dominant_image_area / page_area > 0.5
            and total_text_chars > 0
        ):
            chars_inside = self._count_chars_inside_bbox(
                text_elements, dominant_image.bbox,
            )
            if chars_inside / total_text_chars > 0.7:
                return PageType.SCANNED

        if total_text_chars < 200 and image_coverage > 0.3:
            return PageType.MIXED

        # Detect garbled text from fonts with missing encoding tables
        # (e.g. CFF Type1 fonts without ToUnicode CMap).  PyMuPDF returns
        # U+FFFD for unmappable characters.  A high ratio means the page
        # needs OCR to recover the lost text.
        #
        # Use SCANNED (not MIXED) so OCR fully replaces native text.
        # MIXED would only add missing text via dedup, keeping the garbled
        # native elements.  Since the page is vector-rendered, OCR on
        # the rasterized image should produce good results for all text.
        if total_text_chars > 0:
            replacement_chars = sum(
                e.content.count("\ufffd") for e in text_elements
            )
            if replacement_chars / total_text_chars > 0.05:
                return PageType.SCANNED

        return PageType.NATIVE

    @staticmethod
    def _count_chars_inside_bbox(
        text_elements: list[PageElement],
        bbox: tuple[float, float, float, float],
    ) -> int:
        """Count how many text characters are spatially inside a bbox."""
        bx0, by0, bx1, by1 = bbox
        inside = 0
        for elem in text_elements:
            # Use element bbox center to determine containment.
            ex0, ey0, ex1, ey1 = elem.bbox
            cx = (ex0 + ex1) / 2
            cy = (ey0 + ey1) / 2
            if bx0 <= cx <= bx1 and by0 <= cy <= by1:
                inside += len(elem.content)
        return inside

    # ------------------------------------------------------------------
    # Deduplication helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _bbox_overlap_ratio(
        inner: tuple[float, float, float, float],
        outer: tuple[float, float, float, float],
    ) -> float:
        """Return fraction of *inner* area that overlaps with *outer*."""
        x0 = max(inner[0], outer[0])
        y0 = max(inner[1], outer[1])
        x1 = min(inner[2], outer[2])
        y1 = min(inner[3], outer[3])

        if x1 <= x0 or y1 <= y0:
            return 0.0

        intersection = (x1 - x0) * (y1 - y0)
        inner_area = (inner[2] - inner[0]) * (inner[3] - inner[1])
        if inner_area <= 0:
            return 0.0
        return intersection / inner_area

    def _remove_table_overlapping_text(
        self,
        text_elements: list[PageElement],
        table_elements: list[PageElement],
    ) -> list[PageElement]:
        """Drop text blocks whose bbox is mostly inside a table region."""
        if not table_elements:
            return text_elements

        _OVERLAP_THRESHOLD = 0.5
        table_bboxes = [t.bbox for t in table_elements]
        kept: list[PageElement] = []

        for te in text_elements:
            overlaps = any(
                self._bbox_overlap_ratio(te.bbox, tb) >= _OVERLAP_THRESHOLD
                for tb in table_bboxes
            )
            if not overlaps:
                kept.append(te)

        dropped = len(text_elements) - len(kept)
        if dropped:
            log.debug("Dropped %d text blocks overlapping with tables", dropped)

        return kept
