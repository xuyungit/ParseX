"""Embedded image routing (guide §6.5): cheap judgments only decide whether a call is worth it.

1. Cheap filter — a decorative candidate (thin side, blank, rule-shaped, or a
   trivial icon) is saved, neither recognised nor shown — unless the detector
   finds text or a formula in it (a one-line equation has the shape of a rule):
   then its content routes it (P4-4).  The layout step detects such small
   images on a page-sized canvas, where the detector is reliable.
2. Detected areas — ``t`` (text-like) and ``f`` (figure-like) over the whole
   image decide SCAN / FIGURE / MIXED / UNCERTAIN.

Phase 1 records every route (ImageRecord + ``image_route`` Decision) but only
the cheap filter acts; the detector-based route is a shadow until Phase 4.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from parserx.config.schema import RoutingConfig
from parserx.ir.enums import ImageRoute
from parserx.layout.area import coverage
from parserx.layout.detector import Region


@dataclass(frozen=True)
class RouteResult:
    route: ImageRoute
    t: float | None
    f: float | None
    regions: int
    reason: str
    evidence: dict[str, float | int | str | bool] = field(default_factory=dict)


def cheap_filter(width: int, height: int, *, pixel_std: float, config: RoutingConfig) -> str | None:
    """Why an image is a decorative candidate, or None."""
    short, long = min(width, height), max(width, height)
    if short < config.decorative_short_side:
        return "short_side"
    if pixel_std < config.decorative_std:
        return "blank"
    if short and long / short >= config.decorative_aspect:
        return "strip"
    if config.trivial_max_area is not None and width * height <= config.trivial_max_area \
            and long <= config.trivial_max_long_edge:
        return "trivial"
    return None


def route(*, width: int, height: int, pixel_std: float, regions: list[Region], config: RoutingConfig,
          source: str = "layout") -> RouteResult:
    evidence: dict[str, float | int | str | bool] = {"width": width, "height": height,
                                                     "pixel_std": round(pixel_std, 2), "regions": len(regions)}
    decorative = cheap_filter(width, height, pixel_std=pixel_std, config=config)
    if decorative:
        content = _by_content(width, height, regions, config, source, dict(evidence)) \
            if decorative != "blank" and regions else None
        if content is not None and content.route in (ImageRoute.SCAN, ImageRoute.MIXED):
            # the shape of an ornament, but text or a formula is detected in it (a one-line equation): content
            content.evidence["decorative_shape"] = decorative
            return RouteResult(content.route, content.t, content.f, content.regions,
                               f"decorative shape ({decorative}), but {content.reason}", content.evidence)
        evidence["decorative"] = decorative
        return RouteResult(ImageRoute.DECORATIVE, None, None, len(regions), f"decorative candidate: {decorative}",
                           evidence)
    return _by_content(width, height, regions, config, source, evidence)


def _by_content(width: int, height: int, regions: list[Region], config: RoutingConfig, source: str,
                evidence: dict) -> RouteResult:
    t, f = coverage(regions, width=width, height=height, source=source)
    evidence.update(t=t, f=f)
    if regions and all(r.score < config.low_confidence for r in regions):
        return RouteResult(ImageRoute.UNCERTAIN, t, f, len(regions), "every detection has low confidence", evidence)
    if t >= config.scan_min_t and f <= config.scan_max_f:
        return RouteResult(ImageRoute.SCAN, t, f, len(regions), "mostly text", evidence)
    if f >= config.figure_min_f and t <= config.figure_max_t:
        return RouteResult(ImageRoute.FIGURE, t, f, len(regions), "mostly figure", evidence)
    if t < config.low and f < config.low:
        return RouteResult(ImageRoute.UNCERTAIN, t, f, len(regions), "little detected content", evidence)
    return RouteResult(ImageRoute.MIXED, t, f, len(regions), "text and figure", evidence)


def pixel_std(png: bytes) -> float:
    import io

    import numpy as np
    from PIL import Image

    with Image.open(io.BytesIO(png)) as image:
        return float(np.asarray(image.convert("L"), dtype=np.float32).std())
