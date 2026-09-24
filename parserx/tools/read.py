"""``read``: blocks, observations and images of a page or a block (no side effects on the state)."""

from __future__ import annotations

from typing import Literal

from pydantic import model_validator

from parserx.ir.anchor import AssetAnchor, PdfAnchor
from parserx.ir.base import IRModel
from parserx.ir.enums import BlockKind
from parserx.tools.context import ToolContext, ToolOutput, output
from parserx.tools.envelope import Failure, FailureCode, ToolFailure
from parserx.tools.imaging import page_render, region_crop, write_once
from parserx.tools.views import BlockView, ImageRef, ObservationView, block_view, observation_view
from parserx.workspace.queries import HIDDEN, block_map, block_unit, ordered


class ReadRequest(IRModel):
    page: int | None = None
    block: str | None = None
    image: Literal["none", "page", "crop"] = "none"
    context: int = 0  # neighbouring blocks on each side (block reads)
    dpi: int | None = None  # default tools.read_dpi
    pad_pt: float | None = None  # default tools.crop_pad_pt
    observations: bool = False
    include_hidden: bool = False  # also blocks superseded or excluded (duplicate / merged / excluded)
    geometry: bool = False  # anchors and coordinates

    @model_validator(mode="after")
    def _one_target(self) -> "ReadRequest":
        if (self.page is None) == (self.block is None):
            raise ValueError("give exactly one of page or block")
        if self.image == "crop" and self.block is None:
            raise ValueError("image=crop needs a block")
        return self


class ReadResult(IRModel):
    image: ImageRef | None
    blocks: list[BlockView]
    observations: list[ObservationView] | None


def run(ctx: ToolContext, req: ReadRequest) -> ToolOutput[ReadResult]:
    state = ctx.ws.load()
    blocks_by_id = block_map(state)
    shown = [b for b in ordered(state) if req.include_hidden or b.status not in HIDDEN or b.id == req.block]
    if req.page is not None:
        if all(p.n != req.page for p in state.pages):
            raise ToolFailure(FailureCode.NOT_FOUND, f"no page {req.page}", targets=[f"p{req.page}"])
        blocks = [b for b in shown if block_unit(state, b) == req.page]
    else:
        if req.block not in blocks_by_id:
            raise ToolFailure(FailureCode.NOT_FOUND, f"no block {req.block}", targets=[req.block])
        at = next(i for i, b in enumerate(shown) if b.id == req.block)
        blocks = shown[max(0, at - req.context): at + req.context + 1]
    failures: list[Failure] = []
    image = None
    if req.image != "none":
        image, problem = _image(ctx, state, req, blocks_by_id)
        if problem:
            failures.append(Failure(code=FailureCode.INVALID_REQUEST, message=problem, retryable=False,
                                    targets=[req.block or f"p{req.page}"]))
    observations = None
    if req.observations:
        observations = [observation_view(b, o, geometry=req.geometry) for b in blocks for o in b.observations]
    return output(ReadResult(image=image, blocks=[block_view(state, b, geometry=req.geometry) for b in blocks],
                             observations=observations), failures=failures)


def _image(ctx: ToolContext, state, req: ReadRequest, blocks_by_id) -> tuple[ImageRef | None, str | None]:
    dpi = req.dpi or ctx.config.tools.read_dpi
    pad = ctx.config.tools.crop_pad_pt if req.pad_pt is None else req.pad_pt
    renders = ctx.ws.root / "renders"
    if req.image == "page":
        if state.format != "pdf":
            return None, "DOCX has no page images; figure blocks can be read with image=crop"
        n = req.page if req.page is not None else block_unit(state, blocks_by_id[req.block])
        page = next((p for p in state.pages if p.n == n), None)
        if page is None:
            return None, f"block {req.block} has no page to render"
        asset, data, transform = page_render(ctx.ws.source_path, page.n, dpi, page.size_pt)
        path = renders / f"{asset.id}.png"
        write_once(path, data)
        return ImageRef(asset=asset.id, path=str(path.resolve()), width=asset.width, height=asset.height,
                        transform=transform), None
    block = blocks_by_id[req.block]
    assets = {a.id: a for a in state.assets}
    if block.kind in (BlockKind.FIGURE, BlockKind.SCAN):
        anchor = next((a for a in block.anchors if isinstance(a, AssetAnchor) and a.asset in assets), None)
        if anchor is not None:
            asset = assets[anchor.asset]
            transform = None
            if isinstance(asset.source, PdfAnchor) and asset.role != "crop":
                b = asset.source.bbox
                transform = ((b[2] - b[0]) / asset.width, 0.0, 0.0, (b[3] - b[1]) / asset.height, b[0], b[1])
            return ImageRef(asset=asset.id, path=str((ctx.ws.root / asset.path).resolve()), width=asset.width,
                            height=asset.height, transform=transform), None
    first = block.anchors[0]
    if state.format != "pdf" or not isinstance(first, PdfAnchor):
        return None, "this block has no page geometry to crop (DOCX text)"
    page = next(p for p in state.pages if p.n == first.page)
    crop, data, transform, _render, _png = region_crop(ctx.ws.source_path, page.n, first.bbox, dpi, pad, page.size_pt)
    path = renders / f"{crop.id}.png"
    write_once(path, data)
    return ImageRef(asset=crop.id, path=str(path.resolve()), width=crop.width, height=crop.height,
                    transform=transform), None
