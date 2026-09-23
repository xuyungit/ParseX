"""Native PDF extraction (guide §6.3, plan P1-4).

One pass over PyMuPDF's ``rawdict`` per page:

- every non-empty text line is a ledger item (``native_line``);
- PyMuPDF text blocks become TEXT blocks with a PdfAnchor, a native
  Observation and ``TextStyle`` evidence (dominant font size, bold);
- ruled tables found by ``page.find_tables`` become TABLE blocks; the lines
  inside a table are accounted to it;
- placed images become Assets and FIGURE blocks (a page-covering image on a
  page without a usable text layer is a SCAN block);
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
from dataclasses import dataclass
from pathlib import Path

os.environ.setdefault("PYMUPDF_SUGGEST_LAYOUT_ANALYZER", "0")  # PyMuPDF prints an advert to stdout otherwise

import fitz  # noqa: E402

from parserx.content.extraction import Extraction  # noqa: E402
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
    item: str = ""


@dataclass
class _Region:
    kind: BlockKind
    bbox: BBox
    lines: list[_Line]
    grid: TableGrid | None = None
    asset: Asset | None = None


def extract_pdf(path: Path | str) -> Extraction:
    ext = Extraction(format="pdf", engines={ENGINE: ENGINE_VERSION})
    with fitz.open(path) as doc:
        for index in range(doc.page_count):
            _extract_page(doc, doc[index], index + 1, ext)
    return ext


def _extract_page(doc: fitz.Document, page: fitz.Page, n: int, ext: Extraction) -> None:
    rect = page.rect
    lines = _lines(page)
    for seq, line in enumerate(lines, 1):
        line.item = ids.ledger_item_pdf(n, seq)
    images = [info for info in page.get_image_info(xrefs=True) if info.get("bbox")]
    verdict = assess_native_layer(_signals(page, lines, images))

    regions: list[_Region] = []
    free = lines
    if verdict.ok:
        for table in _tables(page):
            inside = [ln for ln in free if _centre_in(ln.bbox, table.bbox)]
            free = [ln for ln in free if ln not in inside]
            regions.append(_Region(BlockKind.TABLE, tuple(table.bbox), inside, grid=_grid(table)))
    by_block: dict[int, list[_Line]] = {}
    for line in free:
        by_block.setdefault(line.block, []).append(line)
    for group in by_block.values():
        group = [group[i] for i in reading_order([ln.bbox for ln in group])]  # streams can be out of visual order
        regions.append(_Region(BlockKind.TEXT, _union([ln.bbox for ln in group]), group))
    for info in images:
        asset = ext.add_asset(*_image_asset(doc, page, n, info))
        kind = BlockKind.FIGURE if verdict.ok else BlockKind.SCAN
        regions.append(_Region(kind, _round(info["bbox"]), [], asset=asset))

    decision = _source_decision(verdict)
    line_items: list[LedgerEntry] = []
    image_regions: list[tuple[str, BBox]] = []
    for seq, index in enumerate(reading_order([r.bbox for r in regions]), 1):
        region = regions[index]
        block_id = ids.block_id_pdf(n, seq)
        ext.blocks.append(_block(block_id, len(ext.blocks), n, region, decision))
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


# ── Lines, signals, tables ──────────────────────────────────────────────


def _lines(page: fitz.Page) -> list[_Line]:
    raw = page.get_text("rawdict", flags=fitz.TEXT_PRESERVE_WHITESPACE)
    out: list[_Line] = []
    for index, block in enumerate(raw.get("blocks", [])):
        if block.get("type") != 0:
            continue
        for line in block.get("lines", []):
            spans = line.get("spans", [])
            text = normalize_fullwidth_ascii(_reconstruct_line_from_chars(spans))
            if not text.strip():
                continue
            weights: Counter[tuple[float, bool]] = Counter()
            for span in spans:
                weights[(round(span.get("size", 0.0), 1), bool(span.get("flags", 0) & 16))] += len(span.get("chars", []))
            (size, bold), _ = weights.most_common(1)[0]
            out.append(_Line(text=text.strip(), bbox=_round(line["bbox"]), block=index,
                             chars=len("".join(text.split())), size=size, bold=bold))
    return out


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


def _grid(table) -> TableGrid:
    rows = table.extract() or []
    n_cols = max((len(r) for r in rows), default=0)
    cells = [
        Cell(row=r, col=c, content=join_wrapped(normalize_fullwidth_ascii(row[c] or "").split("\n"))
             if c < len(row) else "")
        for r, row in enumerate(rows) for c in range(n_cols)
    ]
    return TableGrid(n_rows=len(rows), n_cols=n_cols, cells=cells)


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
    bold = 0
    for ln in lines:
        sizes[ln.size] += ln.chars
        bold += ln.chars if ln.bold else 0
    total = sum(sizes.values()) or 1
    return TextStyle(font_size=sizes.most_common(1)[0][0], bold=bold * 2 > total)


# ── Geometry ────────────────────────────────────────────────────────────


def _round(bbox) -> BBox:
    return tuple(round(float(v), 2) for v in bbox)  # type: ignore[return-value]


def _area(b: BBox) -> float:
    return max(0.0, b[2] - b[0]) * max(0.0, b[3] - b[1])


def _union(boxes: list[BBox]) -> BBox:
    return (min(b[0] for b in boxes), min(b[1] for b in boxes), max(b[2] for b in boxes), max(b[3] for b in boxes))


def _centre_in(inner: BBox, outer) -> bool:
    cx, cy = (inner[0] + inner[2]) / 2, (inner[1] + inner[3]) / 2
    return outer[0] <= cx <= outer[2] and outer[1] <= cy <= outer[3]
