"""Page renders and crops for tools.

``read`` keeps its images out of the document state (files under
``<ws>/renders/``, content-addressed) so reading never changes the workspace
version; review tools register the crops they use as evidence as Assets.
"""

from __future__ import annotations

import io
from pathlib import Path

import fitz
from PIL import Image

from parserx.content.scan import render_page_at
from parserx.ir.anchor import PdfAnchor
from parserx.ir.asset import Asset
from parserx.ir.base import Affine, BBox


def page_png(source: Path, n: int, dpi: int) -> tuple[bytes, int, int]:
    with fitz.open(source) as doc:
        return render_page_at(doc, n, dpi)


def page_render(source: Path, n: int, dpi: int, size_pt: tuple[float, float]) -> tuple[Asset, bytes, Affine]:
    data, width, height = page_png(source, n, dpi)
    asset = Asset.from_bytes(data, media_type="image/png", width=width, height=height, role="render", dpi=float(dpi),
                             source=PdfAnchor(page=n, bbox=(0.0, 0.0, *size_pt), coord_space="page_pt"))
    return asset, data, (72.0 / dpi, 0.0, 0.0, 72.0 / dpi, 0.0, 0.0)


def region_crop(source: Path, n: int, bbox_pt: BBox, dpi: int, pad_pt: float,
                size_pt: tuple[float, float]) -> tuple[Asset, bytes, Affine, Asset, bytes]:
    """Crop of a page region with padding; also returns the page render it derives from."""
    render, render_png, _ = page_render(source, n, dpi, size_pt)
    scale = dpi / 72.0
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
    transform = (1.0 / scale, 0.0, 0.0, 1.0 / scale, box[0] / scale, box[1] / scale)
    crop = Asset.from_bytes(data, media_type="image/png", width=image.width, height=image.height, role="crop",
                            derived_from=render.id, transform=(1.0, 0.0, 0.0, 1.0, float(box[0]), float(box[1])),
                            source=PdfAnchor(page=n, bbox=(round(x0, 2), round(y0, 2), round(x1, 2), round(y1, 2)),
                                             coord_space="page_pt"))
    return crop, data, transform, render, render_png


def write_once(path: Path, data: bytes) -> None:
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_bytes(data)
        tmp.replace(path)
