"""``recognize --engine layout``: layout detection and image routing in shadow (plan P1-9, guide §6.2, §6.5).

- Pages (PDF): the page render is detected; each region becomes a ``layout``
  Observation (no text) on the visible block it overlaps most.  Regions that
  overlap no block are counted in a warning.  Phase 4 decides from these
  records whether content is assigned by detector boxes (plan R4).
- Figure blocks: the image is routed.  The route, ``t``, ``f`` and the region
  count go to an ImageRecord and an ``image_route`` Decision; regions become
  Observations in the image's pixels.  Only a decorative candidate is acted
  on (excluded: saved, not shown, not described); every other route is a
  shadow that changes nothing.

Detections are local and kept in the derived cache, so a replay does not
load the model.  Pages and figures already processed are skipped unless
``force``.
"""

from __future__ import annotations


from parserx.ir import ids
from parserx.ir.anchor import AssetAnchor, PdfAnchor
from parserx.ir.decision import Decision
from parserx.ir.enums import BlockKind, BlockStatus, DecisionStage, ImageRoute, ObservationStatus, TaskKind
from parserx.ir.observation import Observation
from parserx.ir.state import ImageRecord
from parserx.layout.detector import Region, detect_cached
from parserx.routing.image import pixel_std, route
from parserx.tools.context import ToolContext, ToolOutput, output
from parserx.tools.envelope import Failure, FailureCode, ToolFailure
from parserx.tools.imaging import page_png
from parserx.tools.views import observation_view
from parserx.workspace.queries import HIDDEN, block_unit
from parserx.workspace.views import page_rows

ACTOR = "program:routing.image"
_SUPERSEDED = frozenset({BlockStatus.DUPLICATE, BlockStatus.MERGED})


def run_layout(ctx: ToolContext, req) -> ToolOutput:
    from parserx.tools.recognize import OBSERVATION_VIEWS, RecognizeResult, SelectionOutcome

    state = ctx.ws.load()
    if not req.pages and not req.blocks:
        raise ToolFailure(FailureCode.INVALID_REQUEST, "give pages (page detection) and/or figure blocks (routing)")
    blocks = {b.id: b for b in state.blocks}
    unknown = [b for b in req.blocks if b not in blocks]
    if unknown:
        raise ToolFailure(FailureCode.NOT_FOUND, f"no blocks {unknown}", targets=unknown)
    failures: list[Failure] = []
    detector = ctx.detector()
    cache = ctx.cache

    pages: list[int] = []
    if req.pages and state.format != "pdf":
        failures.append(Failure(code=FailureCode.INVALID_REQUEST, retryable=False,
                                message="DOCX has no page images; only embedded images are routed"))
    elif req.pages:
        done = {block_unit(state, b) for b in state.blocks for o in b.observations if o.task == TaskKind.LAYOUT
                and isinstance(o.anchor, PdfAnchor)}
        pages = [n for n in sorted(set(req.pages)) if req.force or n not in done]
    routed = {r.id for r in state.images}
    assets = {a.id: a for a in state.assets}
    figures = []
    for block_id in req.blocks:
        block = blocks[block_id]
        anchor = next((a for a in block.anchors if isinstance(a, AssetAnchor) and a.asset in assets), None)
        if block.kind != BlockKind.FIGURE or anchor is None:
            failures.append(Failure(code=FailureCode.INVALID_REQUEST, retryable=False, targets=[block_id],
                                    message=f"{block_id} is not a figure with an image"))
        elif req.force or anchor.asset not in routed:
            figures.append((block_id, anchor))
    if not pages and not figures:
        return output(RecognizeResult(observations=[], observations_total=0, pages=[], selections=[]),
                      failures=failures)

    dpi = ctx.config.layout.page_dpi
    page_regions: dict[int, tuple[list[Region], tuple[int, int]]] = {}
    for n in pages:
        png, width, height = page_png(ctx.ws.source_path, n, dpi)
        page_regions[n] = (detect_cached(detector, png, cache), (width, height))
    figure_routes = []
    for block_id, anchor in figures:
        asset = assets[anchor.asset]
        data = (ctx.ws.root / asset.path).read_bytes()
        try:
            std = pixel_std(data)
        except Exception:  # noqa: BLE001 - an image PIL cannot read (e.g. EMF) cannot be analysed
            figure_routes.append((block_id, anchor, None, []))
            continue
        decorative_first = route(width=asset.width, height=asset.height, pixel_std=std, regions=[],
                                 config=ctx.config.routing)
        regions = [] if decorative_first.route == ImageRoute.DECORATIVE else detect_cached(detector, data, cache)
        result = route(width=asset.width, height=asset.height, pixel_std=std, regions=regions,
                       config=ctx.config.routing)
        figure_routes.append((block_id, anchor, result, regions))

    selections: list[SelectionOutcome] = []
    new_observations = []
    with ctx.ws.txn("tool:recognize:layout") as state:
        blocks = {b.id: b for b in state.blocks}
        for n, (regions, (width, height)) in page_regions.items():
            new_observations += _attach_page(state, n, regions, width, height, dpi, detector.version, req.force)
        records = {r.id: r for r in state.images}
        for block_id, anchor, result, regions in figure_routes:
            block = blocks[block_id]
            if req.force:
                block.observations[:] = [o for o in block.observations if o.task != TaskKind.LAYOUT]
            if result is None:
                result_route, reason, evidence, t, f = ImageRoute.UNCERTAIN, "image cannot be decoded here", {}, None, None
            else:
                result_route, reason, evidence, t, f = result.route, result.reason, result.evidence, result.t, result.f
            acted = result_route == ImageRoute.DECORATIVE
            block.decisions.append(Decision(
                stage=DecisionStage.IMAGE_ROUTE, choice=result_route.value, evidence=evidence, actor=ACTOR,
                reason=reason if acted else f"shadow (Phase 1 records, does not act): {reason}"))
            if acted and block.status not in HIDDEN:
                block.status = BlockStatus.EXCLUDED
                for entry in state.ledger:
                    if entry.block == block_id:
                        entry.disposition = "excluded"
            for index, region in enumerate(regions, 1):
                obs = Observation(
                    id=ids.observation_id(block_id, "layout", index), engine="layout",
                    engine_version=detector.version, task=TaskKind.LAYOUT, label=region.label,
                    det_confidence=region.score, status=ObservationStatus.OK,
                    anchor=AssetAnchor(asset=anchor.asset, bbox=region.bbox, image_size=anchor.image_size))
                block.observations.append(obs)
                new_observations.append((block, obs))
            records[anchor.asset] = ImageRecord(
                id=anchor.asset, route=result_route, shown=block.status not in HIDDEN, t=t, f=f,
                regions=len(regions), complete=None)
            selections.append(SelectionOutcome(target=block_id, choice=result_route.value, adopted=acted,
                                               reason=reason))
        state.images = [records[k] for k in sorted(records)]
        state.engines["layout"] = detector.version
        rows = [r for r in page_rows(state) if r.n in set(page_regions)]
    views = [observation_view(b, o, geometry=False) for b, o in new_observations]
    return output(RecognizeResult(observations=views[:OBSERVATION_VIEWS] if req.observations else [],
                                  observations_total=len(views), pages=rows, selections=selections),
                  failures=failures)


def _attach_page(state, n, regions, width, height, dpi, version, force):
    """Layout observations on the visible block each region overlaps most; returns (block, observation) pairs."""
    scale = 72.0 / dpi
    # Superseded blocks (duplicate / merged) are not candidates; excluded page furniture still is.
    on_page = [b for b in state.blocks if block_unit(state, b) == n and b.status not in _SUPERSEDED
               and isinstance(b.anchors[0], PdfAnchor) and b.anchors[0].coord_space == "page_pt"]
    if force:
        for b in on_page:
            b.observations[:] = [o for o in b.observations if not (o.task == TaskKind.LAYOUT
                                                                   and isinstance(o.anchor, PdfAnchor))]
    attached, unmatched = [], 0
    for region in regions:
        box = tuple(v * scale for v in region.bbox)
        best, best_area = None, 0.0
        for b in on_page:
            area = _overlap(box, b.anchors[0].bbox)
            if area > best_area:
                best, best_area = b, area
        if best is None:
            unmatched += 1
            continue
        n_obs = sum(1 for o in best.observations if o.engine == "layout") + 1
        obs = Observation(
            id=ids.observation_id(best.id, "layout", n_obs), engine="layout", engine_version=version,
            task=TaskKind.LAYOUT, label=region.label, det_confidence=region.score, status=ObservationStatus.OK,
            anchor=PdfAnchor(page=n, bbox=region.bbox, coord_space="image_px", image_size=(width, height),
                             transform=(scale, 0.0, 0.0, scale, 0.0, 0.0)))
        best.observations.append(obs)
        attached.append((best, obs))
    prefix = f"layout shadow p{n}:"
    state.warnings[:] = [w for w in state.warnings if not w.startswith(prefix)]
    if unmatched:
        state.warnings.append(f"{prefix} {unmatched} of {len(regions)} detected regions overlap no block")
    return attached


def _overlap(a, b) -> float:
    return max(0.0, min(a[2], b[2]) - max(a[0], b[0])) * max(0.0, min(a[3], b[3]) - max(a[1], b[1]))
