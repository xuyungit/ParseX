"""v2 intermediate representation (guide §4, docs/v2_phase1_interfaces.md §2).

Import models from their submodules (``parserx.ir.block``, ``parserx.ir.state``
…). This package init stays light on purpose: ``parserx.tables.grid`` depends on
``ir.anchor`` and ``ir.base``, and the block / observation models depend on
``TableGrid``, so eager imports here would form a cycle.
"""

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
