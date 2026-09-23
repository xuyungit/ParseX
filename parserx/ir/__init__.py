"""v2 intermediate representation (guide §4). Phase 0 ships the parts evaluation needs."""

from parserx.ir.anchor import AssetAnchor, DocxAnchor, PdfAnchor, SourceAnchor
from parserx.ir.base import Affine, BBox, IRModel

__all__ = [
    "Affine",
    "AssetAnchor",
    "BBox",
    "DocxAnchor",
    "IRModel",
    "PdfAnchor",
    "SourceAnchor",
]
