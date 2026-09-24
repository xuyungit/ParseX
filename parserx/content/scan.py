"""Scan page engine output → Observations and Blocks (guide §6.4, plan P1-4).

``paddleocr`` recognises whole pages: pages are sent as one sub-PDF per batch
(``batch_pdf``: byte-stable, so the response cache and v1 share entries) and
every ``parsing_res_list`` entry becomes one Observation in the analysed
image's pixel space (``image_px`` + transform to ``page_pt``), one Block and
one ledger item.  Kinds come only from ``layout/labels.py``.  Page furniture
(header, footer, page number) is excluded with a Decision; figures are cropped
from a page render and keep the engine's in-image text as evidence.
"""

from __future__ import annotations

import io
import re
from dataclasses import dataclass, field

import fitz
from PIL import Image

from parserx.ir import ids
from parserx.ir.anchor import AssetAnchor, PdfAnchor
from parserx.ir.asset import Asset
from parserx.ir.base import BBox
from parserx.ir.block import Block
from parserx.ir.decision import Decision
from parserx.ir.enums import BlockKind, BlockStatus, DecisionStage, ObservationStatus, RelationKind, TaskKind
from parserx.ir.observation import Observation
from parserx.ir.relation import Relation
from parserx.ir.state import LedgerEntry
from parserx.layout import labels
from parserx.tables.grid import TableGrid

ENGINE = "paddleocr"
ACTOR = "program:content.scan"
_FIGURE_KINDS = frozenset({BlockKind.FIGURE})


@dataclass(frozen=True)
class PageScan:
    """One page of a scan-engine response."""

    page: int
    raw: dict  # the page's layoutParsingResults entry
    raw_ref: str  # cache key of the request that produced it
    engine_version: str


@dataclass
class PageScanResult:
    blocks: list[Block] = field(default_factory=list)
    ledger: list[LedgerEntry] = field(default_factory=list)
    assets: list[Asset] = field(default_factory=list)
    asset_bytes: dict[str, bytes] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)
    relations: list[Relation] = field(default_factory=list)  # a table or text ``contains`` the pictures cut from it


# A picture inside a table cell or a paragraph: the engine leaves a reference to its own crop, named by the box in
# its page pixels (``imgs/img_in_image_box_x0_y0_x1_y1.jpg``), as an HTML <img> or a Markdown image.
_PICTURE = re.compile(r'<img[^>]*?src="[^"]*?img_in_image_box_(\d+)_(\d+)_(\d+)_(\d+)\.\w+"[^>]*>'
                      r"|!\[[^\]]*\]\([^)\s]*?img_in_image_box_(\d+)_(\d+)_(\d+)_(\d+)\.\w+\)")


def take_pictures(content: str) -> tuple[str, list[BBox]]:
    """The content with each picture reference replaced by 〔图k〕, and the pictures' boxes in engine pixels."""
    boxes: list[BBox] = []

    def mark(match: re.Match) -> str:
        values = [int(v) for v in match.groups() if v is not None]
        boxes.append((float(values[0]), float(values[1]), float(values[2]), float(values[3])))
        return f"〔图{len(boxes)}〕"

    return _PICTURE.sub(mark, content), boxes


def batch_pdf(src: fitz.Document, pages: list[int]) -> bytes:
    """A sub-PDF of *pages* (1-based); identical pages give identical bytes."""
    temp = fitz.open()
    for n in pages:
        temp.insert_pdf(src, from_page=n - 1, to_page=n - 1)
    data = temp.tobytes(no_new_id=True)
    temp.close()
    return data


def render_page(src: fitz.Document, n: int, width_px: int) -> tuple[bytes, int, int, float]:
    """PNG of page *n* rendered at the width the engine analysed; (bytes, width, height, dpi)."""
    page = src[n - 1]
    dpi = 72.0 * width_px / page.rect.width
    pix = page.get_pixmap(dpi=round(dpi))
    return pix.tobytes("png"), pix.width, pix.height, float(round(dpi))


def render_page_at(src: fitz.Document, n: int, dpi: int) -> tuple[bytes, int, int]:
    """PNG of page *n* at *dpi*; (bytes, width, height)."""
    pix = src[n - 1].get_pixmap(dpi=dpi)
    return pix.tobytes("png"), pix.width, pix.height


def scan_order(boxes: list, orders: list[int | None]) -> list[int]:
    """Engine reading order; regions outside the text flow (order None) are placed by position.

    Unplaced regions that touch vertically (a caption and its table) are placed together, top down, after
    the latest region in the sequence that starts above them and overlaps them horizontally, so a
    right-column table follows the right column and a full-width one follows both.  With no such region
    they go before the first region that starts below them.
    """
    result = sorted((i for i, o in enumerate(orders) if o is not None), key=lambda i: (orders[i], i))
    groups: list[list[int]] = []
    for i in sorted((i for i, o in enumerate(orders) if o is None), key=lambda i: (boxes[i][1], boxes[i][0], i)):
        if groups and _touches(boxes[groups[-1][-1]], boxes[i], [boxes[j] for j in result]):
            groups[-1].append(i)
        else:
            groups.append([i])
    for group in groups:
        box = (min(boxes[i][0] for i in group), boxes[group[0]][1], max(boxes[i][2] for i in group))
        above = [k for k, j in enumerate(result) if boxes[j][1] < box[1] and boxes[j][0] < box[2] and boxes[j][2] > box[0]]
        at = max(above) + 1 if above else next((k for k, j in enumerate(result) if boxes[j][1] > box[1]), len(result))
        result[at:at] = group
    return result


def _touches(a, b, placed: list) -> bool:
    """*b* sits right under *a* (a gap under half the smaller height), overlapping it, nothing placed between."""
    gap = b[1] - a[3]
    if gap > 0.5 * min(a[3] - a[1], b[3] - b[1]) or not (a[0] < b[2] and a[2] > b[0]):
        return False
    return not any(a[3] <= p[1] and p[3] <= b[1] and p[0] < max(a[2], b[2]) and p[2] > min(a[0], b[0]) for p in placed)


def page_blocks(
    scan: PageScan,
    *,
    page_size: tuple[float, float],
    first_seq: int,
    first_item: int,
    page_image: tuple[bytes, int, int, float] | None = None,
) -> PageScanResult:
    """Blocks for one scanned page; ids continue from *first_seq* / *first_item* on that page."""
    pruned = scan.raw.get("prunedResult") or {}
    width, height = int(pruned.get("width") or 0), int(pruned.get("height") or 0)
    entries = pruned.get("parsing_res_list") or []
    out = PageScanResult()
    if not entries:
        return out
    sx = page_size[0] / width if width else 1.0
    sy = page_size[1] / height if height else 1.0
    transform = (sx, 0.0, 0.0, sy, 0.0, 0.0)
    render: Asset | None = None
    if page_image is not None and any(labels.to_kind(ENGINE, e.get("block_label", "")) in _FIGURE_KINDS
                                      or _PICTURE.search(str(e.get("block_content") or "")) for e in entries):
        data, rw, rh, dpi = page_image
        render = _add(out, Asset.from_bytes(data, media_type="image/png", width=rw, height=rh, role="render", dpi=dpi,
                                            source=PdfAnchor(page=scan.page, bbox=(0, 0, *page_size),
                                                             coord_space="page_pt")), data)

    boxes = [_bbox(e) for e in entries]
    order = scan_order(boxes, [e.get("block_order") for e in entries])
    for offset, index in enumerate(order):
        entry, box = entries[index], boxes[index]
        label = str(entry.get("block_label", ""))
        if not labels.is_known(ENGINE, label):
            out.warnings.append(f"page {scan.page}: unknown {ENGINE} label {label!r} kept as other")
        kind = labels.to_kind(ENGINE, label)
        block_id = ids.block_id_pdf(scan.page, first_seq + offset)
        page_box = (round(box[0] * sx, 2), round(box[1] * sy, 2), round(box[2] * sx, 2), round(box[3] * sy, 2))
        pixel_anchor = PdfAnchor(page=scan.page, bbox=box, coord_space="image_px", image_size=(width, height),
                                 transform=transform)
        content, pictures = take_pictures(str(entry.get("block_content") or ""))
        grid, status = None, BlockStatus.OK
        content = engine_text(content, line_break="<br>" if kind == BlockKind.TABLE else "\n")
        if kind == BlockKind.TABLE:
            try:
                grid = TableGrid.from_html(content)
            except ValueError as exc:
                # tables hold content only in cells: an unconvertible one keeps its HTML as text
                kind, status = BlockKind.OTHER, BlockStatus.DEGRADED
                out.warnings.append(f"{block_id}: table HTML not convertible ({exc}); kept as text")
        obs = Observation(
            id=ids.observation_id(block_id, ENGINE, 1), engine=ENGINE, engine_version=scan.engine_version,
            task=TaskKind.RECOGNIZE, anchor=pixel_anchor, raw_ref=scan.raw_ref, label=label,
            text=None if grid is not None else content, cells=grid,
            status=ObservationStatus.OK if content.strip() else ObservationStatus.EMPTY,
        )
        anchors: list = [PdfAnchor(page=scan.page, bbox=page_box, coord_space="page_pt")]
        decisions = [Decision(stage=DecisionStage.CONTENT_SOURCE, choice="scan_engine",
                              reason=f"{ENGINE} region labelled {label!r}",
                              evidence={"label": label, "engine_order": entry.get("block_order") or -1},
                              actor=ACTOR, refs=[obs.id])]
        if kind in labels.FURNITURE:
            status = BlockStatus.EXCLUDED
            decisions.append(Decision(stage=DecisionStage.EXCLUDE, choice=kind.value,
                                      reason=f"page furniture: engine label {label!r}", evidence={"label": label},
                                      actor=ACTOR, refs=[obs.id]))
        figure = kind in _FIGURE_KINDS
        if figure and render is not None:
            crop = _crop(out, render, page_image[0], box, scan.page, page_box)
            anchors.append(AssetAnchor(asset=crop.id, bbox=(0, 0, crop.width, crop.height),
                                       image_size=(crop.width, crop.height), transform=crop.transform))
        # A figure's in-image text is evidence for its description, not body text.
        out.blocks.append(Block(
            id=block_id, kind=kind, order=len(out.blocks), status=status, anchors=anchors, observations=[obs],
            chosen_observation=None if figure else obs.id,
            text="" if figure or grid is not None else content, cells=grid, decisions=decisions,
        ))
        chars = _grid_chars(grid) if grid is not None else len("".join(content.split()))
        out.ledger.append(LedgerEntry(
            item=ids.ledger_item_pdf(scan.page, first_item + offset), unit="ocr_block",
            source=pixel_anchor, chars=chars,
            disposition="excluded" if status == BlockStatus.EXCLUDED else "output", block=block_id))
        if render is not None and status != BlockStatus.EXCLUDED:
            for k, pbox in enumerate(pictures, 1):
                _picture(out, scan, block_id, k, pbox, render, page_image[0], (sx, sy), (width, height), transform)
    return out


def _picture(out: PageScanResult, scan: PageScan, parent: str, k: int, box: BBox, render: Asset, render_png: bytes,
             scale: tuple[float, float], size: tuple[int, int], transform) -> None:
    """A picture of *parent* (〔图k〕 in its text): a figure block right after it, cut from the page render."""
    block_id = f"{parent}-p{k:02d}"
    page_box = (round(box[0] * scale[0], 2), round(box[1] * scale[1], 2), round(box[2] * scale[0], 2),
                round(box[3] * scale[1], 2))
    pixel_anchor = PdfAnchor(page=scan.page, bbox=box, coord_space="image_px", image_size=size, transform=transform)
    crop = _crop(out, render, render_png, box, scan.page, page_box)
    obs = Observation(id=ids.observation_id(block_id, ENGINE, 1), engine=ENGINE, engine_version=scan.engine_version,
                      task=TaskKind.RECOGNIZE, anchor=pixel_anchor, raw_ref=scan.raw_ref, label="image",
                      status=ObservationStatus.EMPTY)
    out.blocks.append(Block(
        id=block_id, kind=BlockKind.FIGURE, order=len(out.blocks), observations=[obs],
        anchors=[PdfAnchor(page=scan.page, bbox=page_box, coord_space="page_pt"),
                 AssetAnchor(asset=crop.id, bbox=(0, 0, crop.width, crop.height),
                             image_size=(crop.width, crop.height), transform=crop.transform)],
        decisions=[Decision(stage=DecisionStage.CONTENT_SOURCE, choice="scan_engine", actor=ACTOR, refs=[obs.id],
                            reason=f"picture inside {parent} (〔图{k}〕 there), cut from the page render",
                            evidence={"parent": parent, "marker": k})]))
    out.ledger.append(LedgerEntry(item=f"i-{block_id}", unit="ocr_block", source=pixel_anchor, chars=0,
                                  disposition="output", block=block_id))
    out.relations.append(Relation(id=ids.relation_id(RelationKind.CONTAINS, parent, block_id),
                                  kind=RelationKind.CONTAINS, src=parent, dst=block_id))


def image_batch_pdf(images: list[tuple[bytes, int, int]]) -> bytes:
    """One page per image (the page is the image's pixel size in points), for the scan engine; stable bytes."""
    doc = fitz.open()
    for data, width, height in images:
        page = doc.new_page(width=width, height=height)
        page.insert_image(page.rect, stream=data)
    out = doc.tobytes(no_new_id=True)
    doc.close()
    return out


def image_blocks(scan: PageScan, asset: Asset, *, figure: str) -> PageScanResult:
    """Text and tables the scan engine read inside an embedded image (Q42, guide §6.5 SCAN / MIXED routes).

    Each region becomes a block anchored in the image's pixels (the image's own position is on its Asset), with
    ids and ledger items derived from the figure block (``<figure>-rNNN``).  A picture region inside the image is
    shown by the image itself: its ledger item is merged into the figure block.  Page furniture labels are
    excluded, as on scanned pages.
    """
    pruned = scan.raw.get("prunedResult") or {}
    width, height = int(pruned.get("width") or 0), int(pruned.get("height") or 0)
    entries = pruned.get("parsing_res_list") or []
    out = PageScanResult()
    sx = asset.width / width if width else 1.0
    sy = asset.height / height if height else 1.0
    boxes = [_bbox(e) for e in entries]
    for offset, index in enumerate(scan_order(boxes, [e.get("block_order") for e in entries]), 1):
        entry, box = entries[index], boxes[index]
        label = str(entry.get("block_label", ""))
        kind = labels.to_kind(ENGINE, label)
        item = f"i-{figure}-r{offset:03d}"
        image_box = (round(box[0] * sx, 2), round(box[1] * sy, 2), round(box[2] * sx, 2), round(box[3] * sy, 2))
        anchor = AssetAnchor(asset=asset.id, bbox=image_box, image_size=(asset.width, asset.height))
        if kind in _FIGURE_KINDS:
            out.ledger.append(LedgerEntry(item=item, unit="ocr_block", source=anchor, chars=0, disposition="merged",
                                          block=figure))
            continue
        block_id = f"{figure}-r{offset:03d}"
        # a picture inside it is shown by the image itself: its reference becomes the marker only
        content = engine_text(take_pictures(str(entry.get("block_content") or ""))[0],
                              line_break="<br>" if kind == BlockKind.TABLE else "\n")
        grid, status = None, BlockStatus.OK
        if kind == BlockKind.TABLE:
            try:
                grid = TableGrid.from_html(content)
            except ValueError as exc:
                kind, status = BlockKind.OTHER, BlockStatus.DEGRADED
                out.warnings.append(f"{block_id}: table HTML not convertible ({exc}); kept as text")
        obs = Observation(
            id=ids.observation_id(block_id, ENGINE, 1), engine=ENGINE, engine_version=scan.engine_version,
            task=TaskKind.RECOGNIZE, anchor=anchor, raw_ref=scan.raw_ref, label=label,
            text=None if grid is not None else content, cells=grid,
            status=ObservationStatus.OK if content.strip() else ObservationStatus.EMPTY)
        decisions = [Decision(stage=DecisionStage.CONTENT_SOURCE, choice="scan_engine",
                              reason=f"{ENGINE} region labelled {label!r} inside image {figure}",
                              evidence={"label": label, "engine_order": entry.get("block_order") or -1},
                              actor=ACTOR, refs=[obs.id])]
        if kind in labels.FURNITURE:
            status = BlockStatus.EXCLUDED
            decisions.append(Decision(stage=DecisionStage.EXCLUDE, choice=kind.value,
                                      reason=f"page furniture: engine label {label!r}", evidence={"label": label},
                                      actor=ACTOR, refs=[obs.id]))
        out.blocks.append(Block(id=block_id, kind=kind, order=0, status=status, anchors=[anchor],
                                observations=[obs], chosen_observation=obs.id,
                                text="" if grid is not None else content, cells=grid, decisions=decisions))
        chars = _grid_chars(grid) if grid is not None else len("".join(content.split()))
        out.ledger.append(LedgerEntry(item=item, unit="ocr_block", source=anchor, chars=chars,
                                      disposition="excluded" if status == BlockStatus.EXCLUDED else "output",
                                      block=block_id))
    return out


_MATH = re.compile(r"(\$\$.*?\$\$|\$[^$]*\$)", re.S)


def engine_text(content: str, *, line_break: str = "\n") -> str:
    """The engine writes a line break inside a table cell or text as a literal backslash-n; inside a formula
    (``$…$``) the same two characters are LaTeX (``\\nu``), so only the text outside formulas changes.
    Table HTML gets ``<br>``, which the cell parser keeps as an in-cell line break."""
    if "\\n" not in content:
        return content
    parts = _MATH.split(content)
    return "".join(part if i % 2 else part.replace("\\n", line_break) for i, part in enumerate(parts))


def _add(out: PageScanResult, asset: Asset, data: bytes) -> Asset:
    if asset.path not in out.asset_bytes:
        out.assets.append(asset)
        out.asset_bytes[asset.path] = data
    return asset


def _crop(out: PageScanResult, render: Asset, render_png: bytes, box: BBox, page: int, page_box: BBox) -> Asset:
    image = Image.open(io.BytesIO(render_png))
    left, top = int(box[0]), int(box[1])
    region = image.crop((left, top, int(round(box[2])), int(round(box[3]))))
    buf = io.BytesIO()
    region.save(buf, "PNG")
    data = buf.getvalue()
    asset = Asset.from_bytes(data, media_type="image/png", width=region.width, height=region.height, role="crop",
                             derived_from=render.id, transform=(1.0, 0.0, 0.0, 1.0, float(left), float(top)),
                             source=PdfAnchor(page=page, bbox=page_box, coord_space="page_pt"))
    return _add(out, asset, data)


def _bbox(entry: dict) -> BBox:
    box = entry.get("block_bbox")
    if isinstance(box, (list, tuple)) and len(box) == 4:
        return tuple(float(v) for v in box)  # type: ignore[return-value]
    points = entry.get("block_polygon_points") or []
    if points:
        xs, ys = [float(p[0]) for p in points], [float(p[1]) for p in points]
        return (min(xs), min(ys), max(xs), max(ys))
    return (0.0, 0.0, 0.0, 0.0)


def _grid_chars(grid: TableGrid) -> int:
    return sum(len("".join(c.content.split())) for c in grid.cells)
