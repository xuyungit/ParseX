"""Pages a /Rotate turns (guide §4.1).

Every PdfAnchor box in page points, like PyMuPDF's text, drawings and image boxes, is in the unrotated page: PDF
space.  The page as shown — its render, what a reader sees as top and left — is that page turned ``rotation``
degrees clockwise (``PageState.rotation``, the page's /Rotate); ``PageState.size_pt`` is its size as shown.

- ``shown`` turns a box to the page as shown: to cut it out of a render, and wherever top, left, a margin or
  reading order is meant.
- ``unturned`` brings a box on the shown page (a render's, the scan engine's) back to the unrotated page.
- ``onto_page`` makes a transform onto the shown page (a render's pixels → points) end in the unrotated page.

On a page no /Rotate turns each returns what it was given, unchanged.
"""

from __future__ import annotations

from parserx.ir.base import Affine, BBox
from parserx.ir.state import PageState


def turned(page: PageState | None) -> bool:
    return page is not None and bool(page.rotation) and page.size_pt is not None


def to_shown(rotation: int, size: tuple[float, float]) -> Affine:
    """Unrotated page points → the page as shown (*size*: the size as shown); PyMuPDF's ``Page.rotation_matrix``."""
    w, h = size
    return {0: (1.0, 0.0, 0.0, 1.0, 0.0, 0.0), 90: (0.0, 1.0, -1.0, 0.0, w, 0.0),
            180: (-1.0, 0.0, 0.0, -1.0, w, h), 270: (0.0, -1.0, 1.0, 0.0, 0.0, h)}[rotation]


def from_shown(rotation: int, size: tuple[float, float]) -> Affine:
    """The page as shown → unrotated page points; PyMuPDF's ``Page.derotation_matrix``."""
    w, h = size
    return {0: (1.0, 0.0, 0.0, 1.0, 0.0, 0.0), 90: (0.0, -1.0, 1.0, 0.0, 0.0, w),
            180: (-1.0, 0.0, 0.0, -1.0, w, h), 270: (0.0, 1.0, -1.0, 0.0, h, 0.0)}[rotation]


def transform_box(m: Affine, box: BBox) -> BBox:
    """*box* through *m* (a turn by a multiple of 90°, a scale, a shift): the box of its two corners."""
    a, b, c, d, e, f = m
    xs = (a * box[0] + c * box[1] + e, a * box[2] + c * box[3] + e)
    ys = (b * box[0] + d * box[1] + f, b * box[2] + d * box[3] + f)
    return (min(xs), min(ys), max(xs), max(ys))


def compose(first: Affine, then: Affine) -> Affine:
    """*first*, then *then* (PDF matrix convention, ``base.Affine``)."""
    a, b, c, d, e, f = first
    ta, tb, tc, td, te, tf = then
    return (a * ta + b * tc, a * tb + b * td, c * ta + d * tc, c * tb + d * td,
            e * ta + f * tc + te, e * tb + f * td + tf)


def shown(page: PageState | None, box: BBox) -> BBox:
    """*box* (unrotated page points) on the page as shown."""
    return transform_box(to_shown(page.rotation, page.size_pt), box) if turned(page) else box


def unturned(page: PageState | None, box: BBox) -> BBox:
    """*box* on the page as shown, in unrotated page points."""
    return transform_box(from_shown(page.rotation, page.size_pt), box) if turned(page) else box


def whole(page: PageState) -> BBox:
    """The whole page, in unrotated page points."""
    return unturned(page, (0.0, 0.0, *page.size_pt))


def onto_page(page: PageState | None, transform: Affine) -> Affine:
    """*transform* (→ points of the page as shown) ending in unrotated page points instead."""
    return compose(transform, from_shown(page.rotation, page.size_pt)) if turned(page) else transform
