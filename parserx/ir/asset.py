"""Asset: original images, crops and renders (guide §4.1)."""

from __future__ import annotations

import hashlib
from typing import Literal

from pydantic import model_validator

from parserx.ir.anchor import DocxAnchor, PdfAnchor
from parserx.ir.base import Affine, IRModel

EXTENSIONS = {"image/png": ".png", "image/jpeg": ".jpg", "image/gif": ".gif", "image/bmp": ".bmp",
              "image/tiff": ".tif", "image/webp": ".webp", "image/x-emf": ".emf", "image/x-wmf": ".wmf"}


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

    @classmethod
    def from_bytes(cls, data: bytes, *, media_type: str, width: int, height: int,
                   role: Literal["original", "crop", "render"], **fields) -> "Asset":
        """The Asset for *data*: id and path derive from its digest (``assets/<id><ext>``)."""
        from parserx.ir.ids import asset_id

        digest = hashlib.sha256(data).hexdigest()
        aid = asset_id(digest)
        return cls(id=aid, sha256=digest, path=f"assets/{aid}{EXTENSIONS.get(media_type, '.bin')}",
                   media_type=media_type, width=width, height=height, role=role, **fields)
