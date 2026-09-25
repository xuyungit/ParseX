"""Native PDF extraction (guide §6.3, plan P1-4).

One pass over PyMuPDF's ``rawdict`` per page:

- every non-empty text line is a ledger item (``native_line``);
- PyMuPDF text blocks become TEXT blocks with a PdfAnchor, a native
  Observation and ``TextStyle`` evidence (dominant font size, bold);
- ruled tables found by ``page.find_tables`` become TABLE blocks; the lines
  inside a table are accounted to it.  A ruled grid is a table only when it
  holds text and, given a layout detector (``tables_seen``), the detector sees a
  table there too — otherwise the rules frame a drawing or a page of text, and
  the lines stay text (Phase 3 D3).  Each character belongs to one cell, the
  smallest holding it (a frame drawn around cells holds nothing of theirs, D6).
  Cell text is divided among cells only along its own direction: a line in
  another direction than the table's text that is not wholly inside one cell
  (a stamp or watermark drawn across the table) is no cell's content and stays
  a text line of its own.  A row whose rules between items are not drawn is
  split into rows (``_row_bands``, D3);
- placed images become Assets and FIGURE blocks (a page-covering image on a
  page without a usable text layer is a SCAN block);
- repeated margin text (running headers, footers, page numbers) and text in
  another direction than its page's text that repeats across pages
  (watermarks) are excluded as page furniture (``content/furniture.py``);
- the native layer quality check decides whether the page's content comes
  from here (page ``done``) or from the scan engine (page ``pending``); the
  native blocks of a pending page stay as the fallback until the scan engine
  succeeds.  On such a page every embedded image is part of the scan (SCAN
  block): the scan engine reads the whole page render, figures included.

Phase 1 organises content by PyMuPDF blocks; assignment by layout-detector
boxes is decided in Phase 4 from the shadow run (plan R4).
"""

from __future__ import annotations

import io
import os
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

os.environ.setdefault("PYMUPDF_SUGGEST_LAYOUT_ANALYZER", "0")  # PyMuPDF prints an advert to stdout otherwise

import fitz  # noqa: E402
from pymupdf import table as pymupdf_table  # noqa: E402  (the page characters of the last find_tables)

from parserx.content.extraction import Extraction  # noqa: E402
from parserx.content.furniture import mark_furniture, mark_watermarks  # noqa: E402
from parserx.content.order import reading_order, row_order  # noqa: E402
from parserx.content.quality import NativeVerdict, PageSignals, assess_native_layer  # noqa: E402
from parserx.content.text import join_wrapped  # noqa: E402
from parserx.ir import ids  # noqa: E402
from parserx.ir.anchor import AssetAnchor, PdfAnchor  # noqa: E402
from parserx.ir.asset import Asset  # noqa: E402
from parserx.ir.base import BBox  # noqa: E402
from parserx.ir.block import Block  # noqa: E402
from parserx.ir.decision import Decision  # noqa: E402
from parserx.ir.enums import BlockKind, DecisionStage, ObservationStatus, PageStatus, TaskKind  # noqa: E402
from parserx.ir.observation import Observation, TextStyle  # noqa: E402
from parserx.ir.state import LedgerEntry, PageState  # noqa: E402
from parserx.processors.text_clean import normalize_fullwidth_ascii  # noqa: E402
# v1 algorithms reused as-is (guide §11.1): gap-based word spaces, same-row line joining.
from parserx.providers.pdf import _join_block_lines, _reconstruct_line_from_chars  # noqa: E402
from parserx.tables.grid import Cell, TableGrid  # noqa: E402

ENGINE = "native_pdf"
ENGINE_VERSION = f"pymupdf-{fitz.VersionBind}"
ACTOR = "program:content.pdf_native"
_RENDER_DPI = 144  # inline images without an xref are rendered from the page


@dataclass
class _Line:
    text: str
    bbox: BBox
    block: int  # PyMuPDF block index
    chars: int  # non-whitespace characters
    size: float
    bold: bool
    font: str = ""
    direction: tuple[float, float] = (1.0, 0.0)  # PyMuPDF line "dir" (cos, sin), rounded
    glyphs: tuple[tuple[str, float, float, float, float], ...] = ()  # (char, x0, y0, x1, y1)
    origins: tuple[tuple[str, float, float], ...] = ()  # (char, origin in PDF space): the key of PyMuPDF's table chars
    item: str = ""


@dataclass
class _Region:
    kind: BlockKind
    bbox: BBox
    lines: list[_Line]
    grid: TableGrid | None = None
    asset: Asset | None = None


def extract_pdf(path: Path | str, *, tables_seen: Callable[[fitz.Page], list[BBox]] | None = None) -> Extraction:
    """*tables_seen*: the table regions a layout detector sees on a page (page points), asked only for pages where
    ruled grids are found; None reads every ruled grid that holds text as a table."""
    ext = Extraction(format="pdf", engines={ENGINE: ENGINE_VERSION})
    off_direction: dict[str, str] = {}  # block id → text of blocks written across their page's text direction
    with fitz.open(path) as doc:
        for index in range(doc.page_count):
            off_direction.update(_extract_page(doc, doc[index], index + 1, ext, tables_seen))
    mark_furniture(ext)
    mark_watermarks(ext, off_direction)
    return ext


def _extract_page(doc: fitz.Document, page: fitz.Page, n: int, ext: Extraction,
                  tables_seen: Callable[[fitz.Page], list[BBox]] | None = None) -> dict[str, str]:
    rect = page.rect
    lines = _lines(page)
    for seq, line in enumerate(lines, 1):
        line.item = ids.ledger_item_pdf(n, seq)
    images = [info for info in page.get_image_info(xrefs=True) if info.get("bbox")]
    verdict = assess_native_layer(_signals(page, lines, images))

    regions: list[_Region] = []
    free = lines
    if verdict.ok:
        grids = [t for t in _tables(page) if any(_glyph_in(g, t.bbox) for ln in lines for g in ln.glyphs if g[0].strip())]
        seen = tables_seen(page) if grids and tables_seen is not None else None
        for table in grids:
            if seen is not None and not any(_overlap(table.bbox, box) for box in seen):
                ext.warnings.append(f"page {n}: ruled lines the layout detector does not see as a table "
                                    "were read as text")
                continue
            across = {id(ln) for ln in _across(lines, table)}
            inside = [ln for ln in free if _centre_in(ln.bbox, table.bbox) and id(ln) not in across]
            free = [ln for ln in free if ln not in inside]
            regions.append(_Region(BlockKind.TABLE, tuple(table.bbox), inside,
                                   grid=_grid(table, [ln for ln in lines if id(ln) not in across],
                                              [ln for ln in lines if id(ln) in across])))
    by_block: dict[int, list[_Line]] = {}
    for line in free:
        by_block.setdefault(line.block, []).append(line)
    for group in by_block.values():
        group = [group[i] for i in row_order([ln.bbox for ln in group])]  # streams can be out of visual order
        regions.append(_Region(BlockKind.TEXT, _union([ln.bbox for ln in group]), group))
    for info in images:
        asset = ext.add_asset(*_image_asset(doc, page, n, info))
        kind = BlockKind.FIGURE if verdict.ok else BlockKind.SCAN
        regions.append(_Region(kind, _round(info["bbox"]), [], asset=asset))

    decision = _source_decision(verdict)
    main = _main_direction(lines)
    off_direction: dict[str, str] = {}
    line_items: list[LedgerEntry] = []
    image_regions: list[tuple[str, BBox]] = []
    for seq, index in enumerate(reading_order([r.bbox for r in regions]), 1):
        region = regions[index]
        block_id = ids.block_id_pdf(n, seq)
        ext.blocks.append(_block(block_id, len(ext.blocks), n, region, decision))
        if region.kind == BlockKind.TEXT and region.lines and all(ln.direction != main for ln in region.lines):
            off_direction[block_id] = ext.blocks[-1].text
        for line in region.lines:
            line_anchor = PdfAnchor(page=n, bbox=line.bbox, coord_space="page_pt")
            line_items.append(LedgerEntry(item=line.item, unit="native_line", source=line_anchor,
                                          chars=line.chars, disposition="output", block=block_id))
        if region.asset is not None:
            image_regions.append((block_id, region.bbox))
    # Line items in extraction order, then image items numbered on in reading order.
    ext.ledger.extend(sorted(line_items, key=lambda e: e.item))
    for offset, (block_id, bbox) in enumerate(image_regions, len(lines) + 1):
        ext.ledger.append(LedgerEntry(item=ids.ledger_item_pdf(n, offset), unit="pdf_image",
                                      source=PdfAnchor(page=n, bbox=bbox, coord_space="page_pt"),
                                      chars=0, disposition="output", block=block_id))
    ext.pages.append(PageState(n=n, unit="pdf_page", status=PageStatus.DONE if verdict.ok else PageStatus.PENDING,
                               size_pt=(round(rect.width, 2), round(rect.height, 2))))
    return off_direction


# ── Lines, signals, tables ──────────────────────────────────────────────


def _lines(page: fitz.Page) -> list[_Line]:
    raw = page.get_text("rawdict", flags=fitz.TEXT_PRESERVE_WHITESPACE)
    ctm = page.transformation_matrix
    out: list[_Line] = []
    for index, block in enumerate(raw.get("blocks", [])):
        if block.get("type") != 0:
            continue
        for line in block.get("lines", []):
            spans = line.get("spans", [])
            text = normalize_fullwidth_ascii(_reconstruct_line_from_chars(spans))
            if not text.strip():
                continue
            weights: Counter[tuple[float, bool, str]] = Counter()
            for span in spans:
                weights[(round(span.get("size", 0.0), 1), bool(span.get("flags", 0) & 16), span.get("font", ""))] += \
                    len(span.get("chars", []))
            (size, bold, font), _ = weights.most_common(1)[0]
            chars = [ch for span in spans for ch in span.get("chars", ())]
            origins = tuple(_origin_key(ch["c"], fitz.Point(ch["origin"]) * ctm) for ch in chars)
            out.append(_Line(text=text.strip(), bbox=_round(line["bbox"]), block=index,
                             chars=len("".join(text.split())), size=size, bold=bold, font=font,
                             direction=(round(line["dir"][0], 2), round(line["dir"][1], 2)),
                             glyphs=tuple((ch["c"], *ch["bbox"]) for ch in chars), origins=origins))
    return out


def _main_direction(lines: list[_Line]) -> tuple[float, float]:
    """The direction most of the characters are written in."""
    weights: Counter[tuple[float, float]] = Counter()
    for ln in lines:
        weights[ln.direction] += ln.chars
    return weights.most_common(1)[0][0] if weights else (1.0, 0.0)


def _across(lines: list[_Line], table) -> list[_Line]:
    """Lines drawn across *table* rather than written in its cells: a line in another direction than the table's
    text that is not wholly inside one cell (a stamp, a watermark).  Such a line's characters are no cell's content:
    cell text is divided among cells only along its own direction."""
    cells = [cell for row in table.rows for cell in row.cells if cell is not None]
    touching = [ln for ln in lines if any(_glyph_in(g, table.bbox) for g in ln.glyphs)]
    in_one_cell = {id(ln) for ln in touching if any(all(_glyph_in(g, cell) for g in ln.glyphs) for cell in cells)}
    main = _main_direction([ln for ln in touching if id(ln) in in_one_cell] or lines)  # what the cells are written in
    return [ln for ln in touching if ln.direction != main and id(ln) not in in_one_cell]


def _glyph_in(glyph: tuple[str, float, float, float, float], bbox) -> bool:
    """The glyph's centre is in *bbox*: how PyMuPDF assigns characters to cells."""
    _, x0, y0, x1, y1 = glyph
    h, v = (x0 + x1) / 2, (y0 + y1) / 2
    return bbox[0] <= h < bbox[2] and bbox[1] <= v < bbox[3]


def _signals(page: fitz.Page, lines: list[_Line], images: list[dict]) -> PageSignals:
    page_area = page.rect.width * page.rect.height or 1.0
    areas = [_area(tuple(info["bbox"])) for info in images]
    dominant = images[areas.index(max(areas))]["bbox"] if images else None
    text = "".join(ln.text for ln in lines)
    return PageSignals(
        page_area=page_area,
        text_chars=sum(ln.chars for ln in lines),
        replacement_chars=text.count("�"),
        private_use_chars=sum(1 for ch in text if 0xE000 <= ord(ch) <= 0xF8FF),
        image_coverage=sum(areas) / page_area,
        dominant_image_ratio=max(areas, default=0.0) / page_area,
        chars_in_dominant_image=sum(ln.chars for ln in lines if dominant and _centre_in(ln.bbox, dominant)),
        drawings=len(page.get_drawings()),
        invisible_chars=sum(len(span.get("chars", ())) for span in page.get_texttrace() if span.get("type") == 3),
    )


def _tables(page: fitz.Page) -> list:
    try:
        return list(page.find_tables().tables)
    except Exception:  # noqa: BLE001 - table detection is optional evidence; the lines stay as text
        return []


def _grid(table, lines: list[_Line], across: list[_Line]) -> TableGrid:
    rows = _cell_texts(table, lines, {key for ln in across for key in ln.origins}) or []
    n_cols = max((len(r) for r in rows), default=0)
    cells = [
        Cell(row=r, col=c, content=join_wrapped(normalize_fullwidth_ascii(row[c] or "").split("\n"))
             if c < len(row) else "")
        for r, row in enumerate(rows) for c in range(n_cols)
    ]
    return TableGrid(n_rows=len(rows), n_cols=n_cols, cells=cells)


def _cell_texts(table, lines: list[_Line], drop: set[tuple[str, float, float]]) -> list[list[str | None]]:
    """The text of each cell.  ``Table.extract`` gives it unless a character lies in more than one cell, characters
    are dropped (``_across``) or a row splits (``_row_bands``); then each character goes to the smallest cell holding
    it, and a split row's characters to the band of their sub-row."""
    chars = [ch for ch in pymupdf_table.CHARS if _origin_key(ch["text"], ch["matrix"][4:]) not in drop]
    places = [(r, c, cell) for r, row in enumerate(table.rows) for c, cell in enumerate(row.cells) if cell is not None]
    boxes = [cell for _, _, cell in places]
    held = [[k for k, box in enumerate(boxes) if _glyph_in(_box_of(ch), box)] for ch in chars]
    bands = {r: b for r, row in enumerate(table.rows) if (b := _row_bands(row, lines))}
    if not drop and not bands and all(len(h) <= 1 for h in held):
        return table.extract()
    owned: dict[int, list[dict]] = {}
    for ch, holders in zip(chars, held):
        if holders:
            owned.setdefault(min(holders, key=lambda k: _area(boxes[k])), []).append(ch)
    index = {(r, c): k for k, (r, c, _) in enumerate(places)}
    rows: list[list[str | None]] = []
    for r, row in enumerate(table.rows):
        for top, bottom in bands.get(r, [(float("-inf"), float("inf"))]):
            rows.append([None if cell is None else pymupdf_table.extract_text(
                [ch for ch in owned.get(index[(r, c)], []) if top <= (ch["top"] + ch["bottom"]) / 2 < bottom])
                for c, cell in enumerate(row.cells)])
    return rows


def _owner(glyph_box, cells: list) -> int | None:
    """The smallest of *cells* holding the glyph's centre: a frame drawn around cells holds nothing of theirs."""
    holders = [k for k, cell in enumerate(cells) if _glyph_in(("", *glyph_box), cell)]
    return min(holders, key=lambda k: _area(cells[k])) if holders else None


def _row_bands(row, lines: list[_Line]) -> list[tuple[float, float]] | None:
    """Sub-rows of a table row whose rules between items are not drawn, as vertical bands; None: one row.

    Every non-empty cell holds the same number (two or more) of lines, the lines align one to one across the cells,
    and at least one line break was not forced by the cell's width — the next word would have fitted — so the lines
    are items, one per row.  A row whose cells only wrap, or with a cell of a single line, stays one row."""
    columns = []
    for cell in (c for c in row.cells if c is not None):
        touching = [ln for ln in lines if any(_glyph_in(g, cell) for g in ln.glyphs)]
        inside = [ln for ln in touching if all(_glyph_in(g, cell) for g in ln.glyphs)]
        if len(inside) != len(touching):
            return None  # a line across cells: no line-per-row reading
        if inside:
            columns.append((cell, sorted(inside, key=lambda ln: ln.bbox[1])))
    if len(columns) < 2 or len({len(col) for _, col in columns}) != 1 or len(columns[0][1]) < 2:
        return None
    k = len(columns[0][1])
    if any(max(col[i].bbox[1] for _, col in columns) >= min(col[i].bbox[3] for _, col in columns) for i in range(k)):
        return None  # the lines do not align across the cells
    if not any(_chosen_break(cell, col, i) for cell, col in columns for i in range(k - 1)):
        return None
    edges = [row.bbox[1]] + [(max(col[i].bbox[3] for _, col in columns) + min(col[i + 1].bbox[1] for _, col in columns)) / 2
                             for i in range(k - 1)] + [row.bbox[3]]
    return list(zip(edges, edges[1:]))


def _chosen_break(cell, lines: list[_Line], i: int) -> bool:
    """The break after line *i* was not forced by the width: the first word of the next line would have fitted
    (the cell's right padding taken equal to its left)."""
    room = cell[2] - (min(ln.bbox[0] for ln in lines) - cell[0])
    glyphs = list(lines[i + 1].glyphs)
    while glyphs and not glyphs[0][0].strip():
        glyphs.pop(0)
    word = []
    for g in glyphs:
        if not g[0].strip():
            break
        word.append(g)
        if not g[0].isascii():  # a CJK character is a word of its own
            break
    spaces = [g for ln in lines for g in ln.glyphs if g[0] == " "]
    space = (spaces[0][3] - spaces[0][1]) if spaces else 0.0
    return bool(word) and lines[i].bbox[2] + space + (word[-1][3] - word[0][1]) <= room


def _box_of(ch: dict) -> tuple[str, float, float, float, float]:
    return ch["text"], ch["x0"], ch["top"], ch["x1"], ch["bottom"]


def _origin_key(char: str, origin) -> tuple[str, float, float]:
    """A character by its text and its origin in PDF space (PyMuPDF's table code measures glyph boxes its own way;
    the origin is the same in both)."""
    return char, round(origin[0], 1), round(origin[1], 1)


# ── Images ──────────────────────────────────────────────────────────────


def _image_asset(doc: fitz.Document, page: fitz.Page, n: int, info: dict) -> tuple[Asset, bytes]:
    bbox = _round(info["bbox"])
    source = PdfAnchor(page=n, bbox=bbox, coord_space="page_pt")
    payload = _xref_image(doc, info.get("xref", 0))
    if payload is not None:
        data, media_type, width, height = payload
        return Asset.from_bytes(data, media_type=media_type, width=width, height=height,
                                role="original", source=source), data
    pix = page.get_pixmap(clip=fitz.Rect(bbox), dpi=_RENDER_DPI)
    data = pix.tobytes("png")
    return Asset.from_bytes(data, media_type="image/png", width=pix.width, height=pix.height,
                            role="render", source=source, dpi=_RENDER_DPI), data


def _xref_image(doc: fitz.Document, xref: int) -> tuple[bytes, str, int, int] | None:
    """Original image bytes; formats browsers cannot show are converted to PNG."""
    if not xref:
        return None
    try:
        info = doc.extract_image(xref)
    except Exception:  # noqa: BLE001
        return None
    if not info or not info.get("image"):
        return None
    data, ext = info["image"], info.get("ext", "")
    if "/ImageMask true" in doc.xref_object(xref):
        # Stencil masks are stored inverted (black = paint); flip to what a reader shows.
        from PIL import Image, ImageOps

        image = ImageOps.invert(Image.open(io.BytesIO(data)).convert("L"))
        buf = io.BytesIO()
        image.point(lambda v: 0 if v < 128 else 255, "1").save(buf, "PNG")
        return buf.getvalue(), "image/png", image.width, image.height
    if ext in ("png", "jpeg"):
        return data, f"image/{ext}", info["width"], info["height"]
    pix = fitz.Pixmap(doc, xref)
    if pix.n - pix.alpha >= 4:
        pix = fitz.Pixmap(fitz.csRGB, pix)
    return pix.tobytes("png"), "image/png", pix.width, pix.height


# ── Blocks ──────────────────────────────────────────────────────────────


def _source_decision(verdict: NativeVerdict) -> Decision:
    if verdict.ok:
        return Decision(stage=DecisionStage.CONTENT_SOURCE, choice="native", reason="native text layer usable",
                        evidence=verdict.evidence, actor=ACTOR)
    return Decision(stage=DecisionStage.CONTENT_SOURCE, choice="scan_engine",
                    reason=f"{verdict.reason}: native text layer unusable; the page goes to the scan engine",
                    evidence=verdict.evidence, actor=ACTOR)


def _block(block_id: str, order: int, n: int, region: _Region, decision: Decision) -> Block:
    anchors: list = [PdfAnchor(page=n, bbox=region.bbox, coord_space="page_pt")]
    observations: list[Observation] = []
    text = ""
    if region.asset is not None:
        asset = region.asset
        anchors.append(AssetAnchor(asset=asset.id, bbox=(0, 0, asset.width, asset.height),
                                   image_size=(asset.width, asset.height)))
    elif region.kind == BlockKind.TABLE:
        observations.append(Observation(
            id=ids.observation_id(block_id, ENGINE, 1), engine=ENGINE, engine_version=ENGINE_VERSION,
            task=TaskKind.EXTRACT, anchor=anchors[0], cells=region.grid, status=ObservationStatus.OK))
    else:
        text = _join_block_lines([(ln.text, ln.bbox) for ln in region.lines])
        observations.append(Observation(
            id=ids.observation_id(block_id, ENGINE, 1), engine=ENGINE, engine_version=ENGINE_VERSION,
            task=TaskKind.EXTRACT, anchor=anchors[0], text=text, style=_style(region.lines),
            status=ObservationStatus.OK))
    return Block(
        id=block_id, kind=region.kind, order=order, anchors=anchors, observations=observations,
        chosen_observation=observations[0].id if observations else None, text=text,
        cells=region.grid, decisions=[decision],
    )


def _style(lines: list[_Line]) -> TextStyle:
    sizes: Counter[float] = Counter()
    fonts: Counter[str] = Counter()
    bold = 0
    for ln in lines:
        sizes[ln.size] += ln.chars
        fonts[ln.font] += ln.chars
        bold += ln.chars if ln.bold else 0
    total = sum(sizes.values()) or 1
    return TextStyle(font_size=sizes.most_common(1)[0][0], bold=bold * 2 > total, font=fonts.most_common(1)[0][0] or None)


# ── Geometry ────────────────────────────────────────────────────────────


def _round(bbox) -> BBox:
    return tuple(round(float(v), 2) for v in bbox)  # type: ignore[return-value]


def _overlap(a, b) -> bool:
    return a[0] < b[2] and b[0] < a[2] and a[1] < b[3] and b[1] < a[3]


def _area(b: BBox) -> float:
    return max(0.0, b[2] - b[0]) * max(0.0, b[3] - b[1])


def _union(boxes: list[BBox]) -> BBox:
    return (min(b[0] for b in boxes), min(b[1] for b in boxes), max(b[2] for b in boxes), max(b[3] for b in boxes))


def _centre_in(inner: BBox, outer) -> bool:
    cx, cy = (inner[0] + inner[2]) / 2, (inner[1] + inner[3]) / 2
    return outer[0] <= cx <= outer[2] and outer[1] <= cy <= outer[3]
