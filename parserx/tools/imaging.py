"""Page renders and crops for tools.

``read`` keeps its images out of the document state (files under
``<ws>/renders/``, content-addressed) so reading never changes the workspace
version; review tools register the crops they use as evidence as Assets.

A render shows the page as shown; boxes are in the unrotated page (``ir/rotation.py``): a region is turned to the
render to be cut out, and a crop's place and transform lead back to the unrotated page.
"""

from __future__ import annotations

import io
from pathlib import Path

import pymupdf
from PIL import Image

from parserx.content.scan import render_page_at
from parserx.ir.anchor import PdfAnchor
from parserx.ir.asset import Asset
from parserx.ir.base import Affine, BBox
from parserx.ir.rotation import onto_page, shown, turned, unturned, whole
from parserx.ir.state import PageState


def page_png(source: Path, n: int, dpi: int) -> tuple[bytes, int, int]:
    with pymupdf.open(source) as doc:
        return render_page_at(doc, n, dpi)


def page_render(source: Path, page: PageState, dpi: int) -> tuple[Asset, bytes, Affine]:
    """The page render; its transform takes its pixels to page points."""
    data, width, height = page_png(source, page.n, dpi)
    asset = Asset.from_bytes(data, media_type="image/png", width=width, height=height, role="render", dpi=float(dpi),
                             source=PdfAnchor(page=page.n, bbox=whole(page), coord_space="page_pt"))
    return asset, data, onto_page(page, (72.0 / dpi, 0.0, 0.0, 72.0 / dpi, 0.0, 0.0))


def region_crop(source: Path, page: PageState, bbox_pt: BBox, dpi: int,
                pad_pt: float) -> tuple[Asset, bytes, Affine, Asset, bytes]:
    """Crop of a page region with padding; also returns the page render it derives from."""
    n, size_pt = page.n, page.size_pt
    render, render_png, _ = page_render(source, page, dpi)
    scale = dpi / 72.0
    bbox_pt = shown(page, bbox_pt)
    x0 = max(0.0, bbox_pt[0] - pad_pt)
    y0 = max(0.0, bbox_pt[1] - pad_pt)
    x1 = min(size_pt[0], bbox_pt[2] + pad_pt)
    y1 = min(size_pt[1], bbox_pt[3] + pad_pt)
    box = (int(x0 * scale), int(y0 * scale), max(int(x0 * scale) + 1, int(round(x1 * scale))),
           max(int(y0 * scale) + 1, int(round(y1 * scale))))
    image = Image.open(io.BytesIO(render_png)).crop(box)
    buf = io.BytesIO()
    image.save(buf, "PNG")
    data = buf.getvalue()
    transform = onto_page(page, (1.0 / scale, 0.0, 0.0, 1.0 / scale, box[0] / scale, box[1] / scale))
    place = (round(x0, 2), round(y0, 2), round(x1, 2), round(y1, 2))
    if turned(page):
        place = tuple(round(v, 2) for v in unturned(page, place))
    crop = Asset.from_bytes(data, media_type="image/png", width=image.width, height=image.height, role="crop",
                            derived_from=render.id, transform=(1.0, 0.0, 0.0, 1.0, float(box[0]), float(box[1])),
                            source=PdfAnchor(page=n, bbox=place, coord_space="page_pt"))
    return crop, data, transform, render, render_png


def places(block) -> list[PdfAnchor]:
    """A block's places on the first page it is on (page points): one, or several where its text runs on from one
    region of the page into another (a paragraph across columns, ``content/scan._run_on``)."""
    on_pages = [a for a in block.anchors if isinstance(a, PdfAnchor) and a.coord_space == "page_pt"]
    return [a for a in on_pages if a.page == on_pages[0].page]


def stacked(images: list[bytes], gap: int = 16) -> tuple[bytes, int, int]:
    """PNG images one under another on white, in order (the places a block is printed in, read as one)."""
    parts = [Image.open(io.BytesIO(data)).convert("RGB") for data in images]
    width, height = max(p.width for p in parts), sum(p.height for p in parts) + gap * (len(parts) - 1)
    canvas, y = Image.new("RGB", (width, height), "white"), 0
    for part in parts:
        canvas.paste(part, (0, y))
        y += part.height + gap
    buf = io.BytesIO()
    canvas.save(buf, "PNG")
    return buf.getvalue(), width, height


def write_once(path: Path, data: bytes) -> None:
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_bytes(data)
        tmp.replace(path)


def image_crop(parent: Asset, data: bytes, bbox_px: BBox, pad_px: float) -> tuple[Asset, bytes]:
    """Crop of a region inside an image (a block read inside an embedded image), in the image's own pixels."""
    with Image.open(io.BytesIO(data)) as image:
        x0, y0 = max(0, int(bbox_px[0] - pad_px)), max(0, int(bbox_px[1] - pad_px))
        box = (x0, y0, max(x0 + 1, min(image.width, int(round(bbox_px[2] + pad_px)))),
               max(y0 + 1, min(image.height, int(round(bbox_px[3] + pad_px)))))
        crop = image.convert("RGB").crop(box)
    buf = io.BytesIO()
    crop.save(buf, "PNG")
    out = buf.getvalue()
    asset = Asset.from_bytes(out, media_type="image/png", width=crop.width, height=crop.height, role="crop",
                             derived_from=parent.id, transform=(1.0, 0.0, 0.0, 1.0, float(box[0]), float(box[1])))
    return asset, out


def seam_image(source: Path, page: PageState, dpi: int) -> tuple[Asset, bytes]:
    """The bottom half of *page* above the top half of the next page, with a grey rule between them: what
    continues across the page break (a table, a sentence) in one image."""
    n, size_pt = page.n, page.size_pt
    halves = []
    for number, top in ((n, False), (n + 1, True)):
        data, width, height = page_png(source, number, dpi)
        with Image.open(io.BytesIO(data)) as image:
            box = (0, 0, width, height // 2) if top else (0, height - height // 2, width, height)
            halves.append(image.convert("RGB").crop(box))
    rule = max(2, dpi // 36)
    out = Image.new("RGB", (max(h.width for h in halves), sum(h.height for h in halves) + rule), (160, 160, 160))
    out.paste(halves[0], (0, 0))
    out.paste(halves[1], (0, halves[0].height + rule))
    buf = io.BytesIO()
    out.save(buf, "PNG")
    data = buf.getvalue()
    bottom = unturned(page, (0.0, size_pt[1] / 2, *size_pt))  # of the page as shown
    asset = Asset.from_bytes(data, media_type="image/png", width=out.width, height=out.height, role="render",
                             dpi=float(dpi), source=PdfAnchor(page=n, bbox=bottom, coord_space="page_pt"))
    return asset, data

