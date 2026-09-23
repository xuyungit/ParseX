"""SourceAnchor: where a piece of content lives in the source file (guide §4.1)."""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import Field, model_validator

from parserx.ir.base import Affine, BBox, IRModel


class PdfAnchor(IRModel):
    type: Literal["pdf"] = "pdf"
    page: int = Field(ge=1)  # physical page number
    bbox: BBox
    coord_space: Literal["page_pt", "image_px"]  # image_px: boxes on a page render
    image_size: tuple[int, int] | None = None  # required for image_px
    transform: Affine | None = None  # image_px → page_pt

    @model_validator(mode="after")
    def _pixel_space_needs_size(self) -> "PdfAnchor":
        if self.coord_space == "image_px" and self.image_size is None:
            raise ValueError("image_px coordinates need image_size")
        return self


class DocxAnchor(IRModel):
    type: Literal["docx"] = "docx"
    part: str  # e.g. "word/document.xml"
    node_path: str  # XPath-like, e.g. "/w:body/w:tbl[3]/w:tr[2]/w:tc[1]/w:p[1]"
    run_range: tuple[int, int] | None = None  # [start, end) run indices
    segment: int | None = Field(None, ge=1)  # docx_segment n: explicit page / section breaks split the body


class AssetAnchor(IRModel):
    """A region inside an image asset, in that asset's pixel coordinates."""

    type: Literal["asset"] = "asset"
    asset: str
    bbox: BBox
    coord_space: Literal["image_px"] = "image_px"
    image_size: tuple[int, int]
    transform: Affine | None = None  # asset px → parent coordinates (None for DOCX parents)


SourceAnchor = Annotated[PdfAnchor | DocxAnchor | AssetAnchor, Field(discriminator="type")]
