"""Asset: original images, crops and renders (guide §4.1)."""

from __future__ import annotations

from typing import Literal

from pydantic import model_validator

from parserx.ir.anchor import DocxAnchor, PdfAnchor
from parserx.ir.base import Affine, IRModel


class Asset(IRModel):
    id: str  # ids.asset_id(sha256)
    sha256: str
    path: str  # relative to the workspace root
    media_type: str
    width: int
    height: int
    role: Literal["original", "crop", "render"]
    derived_from: str | None = None  # parent Asset id (crops)
    source: PdfAnchor | DocxAnchor | None = None  # where the original sits in the source file
    transform: Affine | None = None  # this asset's pixels → derived_from's coordinates
    dpi: float | None = None  # renders only

    @model_validator(mode="after")
    def _check_role(self) -> "Asset":
        if self.role == "crop" and self.derived_from is None:
            raise ValueError("a crop needs derived_from")
        if self.dpi is not None and self.role != "render":
            raise ValueError("dpi applies to renders only")
        return self
