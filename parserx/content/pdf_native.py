"""Native PDF extraction (guide §6.3, plan P1-4).

One pass over PyMuPDF's ``rawdict`` per page:

- every non-empty text line is a ledger item (``native_line``);
- text lines are grouped into paragraphs by the layout detector's text regions (``content/paragraphs.py``, Q80;
  geometry where no region is): TEXT blocks with a PdfAnchor, a native Observation and ``TextStyle`` evidence
  (dominant font size, bold).  The library's own blocks are not used: they change between versions;
- characters are read as the page shows them: full-width ASCII folded, and radical code points a font's glyph
  mapping put in the text layer ("使⽤") read as their equivalent unified ideographs ("使用",
  ``content/text.unify_radicals``) before word spaces are decided; the block lists the replaced characters in a
  ``content_source`` Decision (``unified_ideographs``);
- ruled tables found by ``page.find_tables`` become TABLE blocks; the lines
  inside a table are accounted to it.  A ruled grid is a table only when it
  holds text and, given a layout detector (``layout``), the detector sees a
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
"""

from __future__ import annotations

import io
import os
from collections import Counter, defaultdict
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

os.environ.setdefault("PYMUPDF_SUGGEST_LAYOUT_ANALYZER", "0")  # PyMuPDF prints an advert to stdout otherwise

import pymupdf  # noqa: E402
from pymupdf import table as pymupdf_table  # noqa: E402  (the page characters of the last find_tables)

from parserx.content.extraction import Extraction  # noqa: E402
from parserx.content.furniture import mark_furniture, mark_watermarks  # noqa: E402
from parserx.content.order import reading_order  # noqa: E402
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
from parserx.content.paragraphs import group_lines  # noqa: E402
from parserx.layout import labels  # noqa: E402
from parserx.content.text import normalize_fullwidth_ascii, radicals_decision, radicals_in, unify_radicals  # noqa: E402
from parserx.tables.grid import Cell, TableGrid  # noqa: E402

ENGINE = "native_pdf"
ENGINE_VERSION = f"pymupdf-{pymupdf.VersionBind}"
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
    mono: bool | None = None  # code: set in a monospaced face (``_line_mono``); None: no letter or digit to tell
    mono_face: str = ""  # that face
    trailing_space: bool = False  # the text layer ends the line with a space: a renderer's wrap point (code)


@dataclass
class _Region:
    kind: BlockKind
    bbox: BBox
    lines: list[_Line]
    grid: TableGrid | None = None
    asset: Asset | None = None


def extract_pdf(path: Path | str, *, layout: Callable[[pymupdf.Page], list[tuple[str, BBox]]] | None = None,
                check_tables: bool = True) -> Extraction:
    """*layout*: the regions a layout detector sees on a page, (label, bbox in page points), asked for pages whose
    text layer is usable: text regions make the paragraphs, table regions confirm ruled grids (*check_tables*).
    None: paragraphs by geometry, and every ruled grid that holds text is read as a table."""
    ext = Extraction(format="pdf", engines={ENGINE: ENGINE_VERSION})
    off_direction: dict[str, str] = {}  # block id → text of blocks written across their page's text direction
    with pymupdf.open(path) as doc:
        for index in range(doc.page_count):
            off_direction.update(_extract_page(doc, doc[index], index + 1, ext, layout, check_tables))
    mark_furniture(ext)
    mark_watermarks(ext, off_direction)
    return ext


def _extract_page(doc: pymupdf.Document, page: pymupdf.Page, n: int, ext: Extraction,
                  layout: Callable[[pymupdf.Page], list[tuple[str, BBox]]] | None = None,
                  check_tables: bool = True) -> dict[str, str]:
    rect = page.rect
    lines = _lines(page)
    for seq, line in enumerate(lines, 1):
        line.item = ids.ledger_item_pdf(n, seq)
    images = [info for info in page.get_image_info(xrefs=True) if info.get("bbox")]
    verdict = assess_native_layer(_signals(page, lines, images))

    regions: list[_Region] = []
    free = lines
    detected = layout(page) if verdict.ok and lines and layout is not None else None
    if verdict.ok:
        grids = [t for t in _tables(page) if any(_glyph_in(g, t.bbox) for ln in lines for g in ln.glyphs if g[0].strip())]
        seen = ([box for label, box in detected if labels.LAYOUT.get(label) == BlockKind.TABLE]
                if detected is not None and check_tables else None)
        accepted: list[BBox] = []
        for table in grids:
            if seen is not None and not any(_overlap(table.bbox, box) for box in seen):
                ext.warnings.append(f"page {n}: ruled lines the layout detector does not see as a table "
                                    "were read as text")
                continue
            across = {id(ln) for ln in _across(lines, table)}
            inside = [ln for ln in free if _centre_in(ln.bbox, table.bbox) and id(ln) not in across]
            free = [ln for ln in free if ln not in inside]
            accepted.append(tuple(table.bbox))
            regions.append(_Region(BlockKind.TABLE, tuple(table.bbox), inside,
                                   grid=_grid(table, [ln for ln in lines if id(ln) not in across],
                                              [ln for ln in lines if id(ln) in across])))
        for box in seen or []:  # P4-6: a table without ruled lines (a three-line table), where the detector sees one
            if any(_overlap(box, other) for other in accepted):
                continue
            found = _unruled_table(page, box, free)
            if found is not None:
                inside, grid, bbox = found
                free = [ln for ln in free if ln not in inside]
                accepted.append(bbox)
                regions.append(_Region(BlockKind.TABLE, bbox, inside, grid=grid))
    for group in group_lines(free, detected):  # paragraphs, each in visual order (Q80)
        members = [free[i] for i in group]
        regions.append(_Region(BlockKind.TEXT, _union([ln.bbox for ln in members]), members))
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


def _lines(page: pymupdf.Page) -> list[_Line]:
    raw = page.get_text("rawdict", flags=pymupdf.TEXT_PRESERVE_WHITESPACE)
    ctm = page.transformation_matrix
    faces = _face_verdicts(span for block in raw.get("blocks", []) if block.get("type") == 0
                           for line in block.get("lines", []) for span in line.get("spans", []))
    out: list[_Line] = []
    for index, block in enumerate(raw.get("blocks", [])):
        if block.get("type") != 0:
            continue
        for line in block.get("lines", []):
            spans = line.get("spans", [])
            text = normalize_fullwidth_ascii(_reconstruct_line_from_chars(spans))
            if not text.strip():
                continue
            (size, bold, font) = _line_typography(spans, faces)
            chars = [ch for span in spans for ch in span.get("chars", ())]
            origins = tuple(_origin_key(ch["c"], pymupdf.Point(ch["origin"]) * ctm) for ch in chars)
            mono, mono_face = _line_mono(spans, faces)
            out.append(_Line(text=text.strip(), bbox=_round(line["bbox"]), block=index,
                             chars=len("".join(text.split())), size=size, bold=bold, font=font,
                             direction=(round(line["dir"][0], 2), round(line["dir"][1], 2)),
                             glyphs=tuple((ch["c"], *ch["bbox"]) for ch in chars), origins=origins,
                             mono=mono, mono_face=mono_face, trailing_space=text != text.rstrip()))
    return out


MONO_LETTERS = 5  # distinct ASCII letters a span needs before its glyph widths say anything (sample size)
MONO_SPREAD = 1.01  # widest / narrowest letter advance of a monospaced face: measurement tolerance of glyph boxes


def _line_typography(spans: list[dict], faces: dict[str, bool] | None = None) -> tuple[float, bool, str]:
    """(size, bold, face) of a line: those of most of the letters and digits of its main script — wide (CJK) or
    not.  Spaces, punctuation and a leading number in a Latin face do not outvote the words (``4.  换盘``); a
    Chinese line with inline code keeps its Chinese face, however long the code (a monospaced face — ``faces`` —
    votes only when the whole line is set in such faces)."""
    def key(span: dict) -> tuple[float, bool, str]:
        return round(span.get("size", 0.0), 1), bool(span.get("flags", 0) & 16), span.get("font", "")

    faces = faces or {}
    words = [span for span in spans if not faces.get(span.get("font", ""))]
    if any(ch.get("c", "").isalnum() for span in words for ch in span.get("chars", ())):
        spans = words  # inline code does not set the line's typography
    counts: dict[bool, Counter] = {True: Counter(), False: Counter()}
    for span in spans:
        for ch in span.get("chars", ()):
            c = unify_radicals(ch.get("c", ""))  # a radical code point is the ideograph it draws: it votes
            if c and c.isalnum():
                counts[_wide(c)][key(span)] += 1
    wide, narrow = sum(counts[True].values()), sum(counts[False].values())
    if wide or narrow:
        return counts[wide >= narrow and wide > 0].most_common(1)[0][0]
    fallback: Counter = Counter()  # no letters or digits: every character votes
    for span in spans:
        fallback[key(span)] += len(span.get("chars", []))
    return fallback.most_common(1)[0][0]


def _face_verdicts(spans) -> dict[str, bool]:
    """Face → whether it is monospaced, from all its glyphs on the page: every ASCII letter advances by the same width
    relative to the size (P4-6: the code of a manual; the font's own fixed-pitch flag is often missing).  A face that
    also draws CJK characters gets no verdict (it draws ASCII at one width whatever it is), nor does a face with fewer
    than ``MONO_LETTERS`` distinct letters on the page (too small a sample).  Letters only: digits are equal-width in
    most proportional faces too."""
    advances: dict[str, dict[str, list[float]]] = defaultdict(lambda: defaultdict(list))
    cjk: set[str] = set()
    for span in spans:
        face, size, chars = span.get("font", ""), span.get("size") or 0.0, span.get("chars", ())
        if any(_wide(ch["c"]) for ch in chars):
            cjk.add(face)
            continue
        for ch in chars:
            width = ch["bbox"][2] - ch["bbox"][0]
            if size > 0 and width > 0 and ch["c"].isascii() and ch["c"].isalpha():
                advances[face][ch["c"]].append(width / size)
    verdicts = {}
    for face, letters in advances.items():
        if face in cjk or len(letters) < MONO_LETTERS:
            continue
        widths = [sorted(v)[len(v) // 2] for v in letters.values()]
        verdicts[face] = max(widths) / min(widths) <= MONO_SPREAD
    return verdicts


def _line_mono(spans: list[dict], faces: dict[str, bool]) -> tuple[bool | None, str]:
    """Whether a line is code, and in which face: every letter and digit votes by the verdict on its own face, and
    the line starts in a monospaced face — a step whose words are set in the body's faces with a command at its end
    is prose (text_code_block, P8).  A line in faces without a verdict (a CJK comment) does not vote: None."""
    votes: Counter[str] = Counter()
    proportional = False
    first: bool | None = None
    seen_first = False
    for span in spans:
        face = span.get("font", "")
        verdict = faces.get(face)
        for ch in span.get("chars", ()):
            c = ch["c"]
            if not seen_first and not c.isspace():
                first, seen_first = verdict, True
            if c.isascii() and c.isalnum() and verdict is not None:
                if verdict:
                    votes[face] += 1
                else:
                    proportional = True
    if not votes and not proportional:
        return None, ""
    if proportional or first is not True:
        return False, ""
    return True, votes.most_common(1)[0][0]


def _wide(ch: str) -> bool:
    import unicodedata

    return unicodedata.east_asian_width(ch) in ("W", "F")


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


def _signals(page: pymupdf.Page, lines: list[_Line], images: list[dict]) -> PageSignals:
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


def _tables(page: pymupdf.Page) -> list:
    try:
        return list(page.find_tables().tables)
    except Exception:  # noqa: BLE001 - table detection is optional evidence; the lines stay as text
        return []


def _unruled_table(page: pymupdf.Page, box: BBox, free: list[_Line]) -> tuple[list[_Line], TableGrid, BBox] | None:
    """The table in a region the layout detector calls a table, from the alignment of its text (no ruled lines):
    (lines it holds, grid, bbox), or None unless the text aligns into two or more non-empty rows and columns — the
    detector and the alignment are two independent readings that must agree.  The region grows to every line whose
    centre is in it, so no character is cut off; rows and columns without text are dropped.  A head set above the
    alignment's first row becomes the table's first rows (``_head_rows``); a line running across three or more
    columns (a caption the region took in) is not the table's and stays text (tables T4)."""
    inside = [ln for ln in free if _centre_in(ln.bbox, box)]
    if len(inside) < 2:
        return None
    bbox = _union([box, *(ln.bbox for ln in inside)])
    try:
        found = page.find_tables(clip=pymupdf.Rect(bbox), vertical_strategy="text", horizontal_strategy="text").tables
    except Exception:  # noqa: BLE001 - table detection is optional evidence
        return None
    if not found:
        return None
    table = found[0]
    boxes = [cell for row in table.rows for cell in row.cells if cell is not None]
    top = table.rows[0].bbox[1] if table.rows else bbox[1]

    def in_cells(ln: _Line) -> list[bool]:
        return [any(_glyph_in(g, box) for box in boxes) for g in ln.glyphs if g[0].strip()]

    # a line standing above the first row, even if part of it (a subscript) reaches into it, is a head line
    head_lines = [ln for ln in inside if not all(in_cells(ln)) and (ln.bbox[1] + ln.bbox[3]) / 2 < top]
    across = [ln for ln in inside if len(_columns_of(ln, table)) >= ACROSS_COLUMNS]  # a sentence over the grid
    held = [ln for ln in inside if any(in_cells(ln)) and ln not in head_lines and ln not in across]
    drop = {key for ln in head_lines + across for key in ln.origins}  # so no cell takes a part of them
    rows = [[_cell_text(c) for c in row] for row in _cell_texts(table, inside, drop) or []]
    head, used = _head_rows(head_lines, table)
    rows = [row for row in head + rows if any(c.strip() for c in row)]
    keep = [c for c in range(max((len(r) for r in rows), default=0)) if any(c < len(r) and r[c].strip() for r in rows)]
    if len(rows) < 2 or len(keep) < 2:
        return None
    cells = [Cell(row=r, col=k, content=join_wrapped(row[c].split("\n")) if c < len(row) else "")
             for r, row in enumerate(rows) for k, c in enumerate(keep)]
    return held + used, TableGrid(n_rows=len(rows), n_cols=len(keep), cells=cells), _round(bbox)


ACROSS_COLUMNS = 3  # a line whose characters fall in this many columns runs across the table (tables T4)


def _columns_of(ln: _Line, table) -> set[int]:
    """The columns of *table* the line's characters fall in."""
    return {c for g in ln.glyphs if g[0].strip() for row in table.rows for c, cell in enumerate(row.cells)
            if cell is not None and _glyph_in(g, cell)}


def _head_rows(missed: list[_Line], table) -> tuple[list[list[str]], list[_Line]]:
    """Rows for the lines the text strategy left out above its first row, or only partly in it (tables T4: a head
    set taller than the body — "工况 β1 … β10" — starts above where the strategy's first row does; "k" of "k1"
    stands above it, the subscript in it), each line in the column it stands over; and the lines used.  A line over no column, or a row whose lines would share a column, is not the table's:
    it stays text, so nothing is lost."""
    if not table.rows or not missed:
        return [], []
    top = table.rows[0].bbox[1]
    columns: dict[int, tuple[float, float]] = {}
    for row in table.rows:
        for c, cell in enumerate(row.cells):
            if cell is not None:
                x0, x1 = columns.get(c, (cell[0], cell[2]))
                columns[c] = (min(x0, cell[0]), max(x1, cell[2]))
    above = sorted((ln for ln in missed if (ln.bbox[1] + ln.bbox[3]) / 2 < top), key=lambda ln: ln.bbox[1])
    groups: list[list[_Line]] = []
    for ln in above:  # rows: a line whose centre is above the first-ending line of the row joins it
        if groups and (ln.bbox[1] + ln.bbox[3]) / 2 < min(x.bbox[3] for x in groups[-1]):
            groups[-1].append(ln)
        else:
            groups.append([ln])
    width = max(columns) + 1 if columns else 0
    rows, used = [], []
    for group in groups:
        row = [""] * width
        for ln in sorted(group, key=lambda x: x.bbox[0]):
            centre = (ln.bbox[0] + ln.bbox[2]) / 2
            col = next((c for c, (x0, x1) in columns.items() if x0 <= centre < x1), None)
            if col is None or row[col]:
                break
            row[col] = ln.text
        else:
            rows.append(row)
            used += group
    return rows, used


def _grid(table, lines: list[_Line], across: list[_Line]) -> TableGrid:
    rows = _cell_texts(table, lines, {key for ln in across for key in ln.origins}) or []
    n_cols = max((len(r) for r in rows), default=0)

    def text(r: int, c: int) -> str:
        row = rows[r]
        return join_wrapped(_cell_text(row[c]).split("\n")) if c < len(row) else ""

    flat = TableGrid(n_rows=len(rows), n_cols=n_cols,
                     cells=[Cell(row=r, col=c, content=text(r, c)) for r in range(len(rows)) for c in range(n_cols)])
    spans = _spans(table, lines)
    if spans is None or len(spans[1]) != len(rows):
        return flat
    placed, _ = spans
    covered = {(r + i, c + j) for r, c, rs, cs in placed for i in range(rs) for j in range(cs)}
    cells = [Cell(row=r, col=c, rowspan=rs, colspan=cs, content=text(r, c)) for r, c, rs, cs in placed]
    cells += [Cell(row=r, col=c, content="") for r in range(len(rows)) for c in range(n_cols) if (r, c) not in covered]
    try:
        return TableGrid(n_rows=len(rows), n_cols=n_cols, cells=sorted(cells, key=lambda x: (x.row, x.col)))
    except ValueError:  # the rectangles do not tile the grid: every position its own cell, as before
        return flat


SPAN_EPS = 1.0  # points: a cell reaches a column (or row) that starts this far inside its rectangle


def _spans(table, lines: list[_Line]) -> tuple[list[tuple[int, int, int, int]], list[int]] | None:
    """Merged cells (tables T2): (row, col, rowspan, colspan) of every cell PyMuPDF draws, in the rows ``_cell_texts``
    gives, and the table row each of those rows comes from.  PyMuPDF leaves the positions a merged cell covers empty
    (None) and gives the merged cell its whole rectangle: the cell spans the columns (rows) that start inside it.  A
    row split into item rows (``_row_bands``) keeps its cells' column spans in every band; a cell spanning rows into
    such a row does not span them (its text would have to be split too)."""
    trows = table.rows
    if not trows:
        return None
    n = max(len(row.cells) for row in trows)
    starts: list[float | None] = [None] * n
    for row in trows:
        for c, cell in enumerate(row.cells):
            if cell is not None and starts[c] is None:
                starts[c] = cell[0]
    bands = {r: b for r, row in enumerate(trows) if (b := _row_bands(row, lines))}
    first, origin = [], []
    for r in range(len(trows)):
        first.append(len(origin))
        origin += [r] * len(bands.get(r, [None]))
    placed = []
    for r, row in enumerate(trows):
        for c, cell in enumerate(row.cells):
            if cell is None:
                continue
            colspan = 1
            while (c + colspan < len(row.cells) and row.cells[c + colspan] is None
                   and (starts[c + colspan] is None or starts[c + colspan] < cell[2] - SPAN_EPS)):
                colspan += 1
            rowspan = 1
            while (r + rowspan < len(trows) and trows[r + rowspan].bbox[1] < cell[3] - SPAN_EPS
                   and all(k < len(trows[r + rowspan].cells) and trows[r + rowspan].cells[k] is None
                           for k in range(c, c + colspan))):
                rowspan += 1
            if any(x in bands for x in range(r, r + rowspan)) and rowspan > 1:
                rowspan = 1
            height = first[r + rowspan - 1] + len(bands.get(r + rowspan - 1, [None])) - first[r] if rowspan > 1 else 1
            for band in range(len(bands.get(r, [None]))):
                placed.append((first[r] + band, c, height if band == 0 else 1, colspan))
    return placed, origin


def _cell_text(text: str | None) -> str:
    """A cell's text as the characters it shows: full-width ASCII folded, radical code points unified (as lines)."""
    return unify_radicals(normalize_fullwidth_ascii(text or ""))


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


def _image_asset(doc: pymupdf.Document, page: pymupdf.Page, n: int, info: dict) -> tuple[Asset, bytes]:
    bbox = _round(info["bbox"])
    source = PdfAnchor(page=n, bbox=bbox, coord_space="page_pt")
    payload = _xref_image(doc, info.get("xref", 0))
    if payload is not None:
        data, media_type, width, height = payload
        return Asset.from_bytes(data, media_type=media_type, width=width, height=height,
                                role="original", source=source), data
    pix = page.get_pixmap(clip=pymupdf.Rect(bbox), dpi=_RENDER_DPI)
    data = pix.tobytes("png")
    return Asset.from_bytes(data, media_type="image/png", width=pix.width, height=pix.height,
                            role="render", source=source, dpi=_RENDER_DPI), data


def _xref_image(doc: pymupdf.Document, xref: int) -> tuple[bytes, str, int, int] | None:
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
    pix = pymupdf.Pixmap(doc, xref)
    if pix.n - pix.alpha >= 4:
        pix = pymupdf.Pixmap(pymupdf.csRGB, pix)
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
    # the characters the text layer stores as radicals (lines and cells were read as the ideographs, see _lines);
    # listed before the page's source decision, which stays the last
    unified = radicals_decision(radicals_in("".join(g[0] for ln in region.lines for g in ln.glyphs)), ACTOR)
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
        style = _style(region.lines)
        entries = _unwrap_code(region.lines) if style.monospace else [(ln.text, ln.bbox) for ln in region.lines]
        text = _join_block_lines(entries)
        observations.append(Observation(
            id=ids.observation_id(block_id, ENGINE, 1), engine=ENGINE, engine_version=ENGINE_VERSION,
            task=TaskKind.EXTRACT, anchor=anchors[0], text=text, style=style,
            status=ObservationStatus.OK))
    return Block(
        id=block_id, kind=region.kind, order=order, anchors=anchors, observations=observations,
        chosen_observation=observations[0].id if observations else None, text=text,
        cells=region.grid, decisions=[unified, decision] if unified else [decision],
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
    measured = [ln for ln in lines if ln.mono is not None]  # lines of other faces (CJK comments) do not vote
    mono = None
    if measured and all(ln.mono for ln in measured):
        faces: Counter[str] = Counter()
        for ln in measured:
            faces[ln.mono_face] += ln.chars
        mono = faces.most_common(1)[0][0] or None
    return TextStyle(font_size=sizes.most_common(1)[0][0], bold=bold * 2 > total, font=fonts.most_common(1)[0][0] or None,
                     monospace=mono)


# ── Lines (from v1's provider, guide §11.1): gap-based word spaces, same-row line joining ──


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
        or 0x2E80 <= cp <= 0x2FDF  # CJK Radicals Supplement, Kangxi Radicals (one without an equivalent: ⺀)
    )


def _unwrap_code(lines: list[_Line]) -> list[tuple[str, tuple]]:
    """A code block's lines, a line the renderer wrapped joined back to the line it continues (P9): a wrapped
    command broken over two lines is two commands in a shell.  A line is wrapped when the text layer ends it with a
    space — where the renderer broke it — and the next line's first word would not fit in the room left on it (the
    block's right edge).  A line ending without a space, or with room for the next word, is a line of its own; so is
    a line followed by a "word" as wide as half the block (the rule of a table drawn in characters), or by a line
    that starts with the same punctuation mark (rows of such a table, comment lines, list items)."""
    right = max(ln.bbox[2] for ln in lines)
    left = min(ln.bbox[0] for ln in lines)
    out: list[tuple[str, tuple, _Line]] = []
    for ln in lines:
        if out and _wrapped(out[-1][2], ln, left, right):
            text, box, _ = out[-1]
            out[-1] = (f"{text} {ln.text}", _union([box, ln.bbox]), ln)
        else:
            out.append((ln.text, ln.bbox, ln))
    return [(text, box) for text, box, _ in out]


def _wrapped(line: _Line, following: _Line, left: float, right: float) -> bool:
    words = following.text.split()
    if not line.trailing_space or not words or not line.text:
        return False
    advance = (line.bbox[2] - line.bbox[0]) / len(line.text)  # a monospaced face: one width per character
    word = advance * len(words[0])
    lead, next_lead = line.text.lstrip()[:1], following.text.lstrip()[:1]
    if lead == next_lead and not lead.isalnum():
        return False
    return right - line.bbox[2] < word + advance and word <= (right - left) / 2


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
            c = unify_radicals(ch_dict.get("c", ""))  # before the spacing test: "填⼊与" is "填入与", no spaces
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
