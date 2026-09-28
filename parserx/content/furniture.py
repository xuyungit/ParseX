"""Page furniture on native PDF pages, by cross-page repetition (guide §6.9, Q34).

A short text block inside the top or bottom margin band of its page (the
outer tenth; a bare page number may sit up to the outer 15 % when nothing on
the page is further out) is furniture when the same text shape (whitespace dropped, digit runs and bare
roman numerals as ``#``) sits in the same band at about the same height on
another page: running headers and footers, page numbers.  Such a block becomes
a HEADER / FOOTER / PAGE_NUMBER block, excluded from the Markdown with a
Decision; its text stays in the sidecar.  A shape with no letters besides the
number is a page number.

Text written in another direction than the rest of its page (a diagonal
stamp, a rotated margin label) is a watermark when the same text shape sits
on another page; it becomes a WATERMARK block, excluded the same way.  One
page alone is no evidence: such text stays.

Only pages whose native layer passed the quality check are considered here: the
scan engine labels the furniture of the pages it reads.  Its labels are not
always consistent (a scanning app's mark in the margin is a header on one page,
side text on the next), so ``mark_scan_furniture`` adds the same evidence for
scanned pages: a text or an image in a margin band (the outer tenth of the page
on any side) repeated at about the same place on another page (P3).
"""

from __future__ import annotations

import re
from collections import defaultdict

from parserx.content.extraction import Extraction
from parserx.ir.anchor import PdfAnchor
from parserx.ir.decision import Decision
from parserx.ir.enums import BlockKind, BlockStatus, DecisionStage, PageStatus
from parserx.ir.state import DocumentState

ACTOR = "program:content.furniture"
MARGIN_BAND = 0.1  # share of the page height at the top and at the bottom
EDGE_BAND = 0.15  # a page number that is the outermost block of its page may sit this far in
MAX_HEIGHT = 0.04  # a furniture block is at most this share of the page height
SAME_HEIGHT = 0.03  # centres within this share of the page height are the same position

_ROMAN = re.compile(r"[ivxlcdm]{1,6}", re.IGNORECASE)


def mark_furniture(ext: Extraction) -> None:
    """Exclude repeated margin text of native pages as page furniture (in place)."""
    sizes = {p.n: p.size_pt for p in ext.pages if p.status == PageStatus.DONE and p.size_pt}
    boxes: dict[int, list] = defaultdict(list)
    for block in ext.blocks:
        if isinstance(block.anchors[0], PdfAnchor):
            boxes[block.anchors[0].page].append(block.anchors[0].bbox)
    found: dict[tuple[str, str], list[tuple[int, int, float]]] = defaultdict(list)  # → (block index, page, centre)
    for index, block in enumerate(ext.blocks):
        anchor = block.anchors[0]
        if block.kind != BlockKind.TEXT or block.status != BlockStatus.OK or not isinstance(anchor, PdfAnchor) \
                or anchor.page not in sizes:
            continue
        height = sizes[anchor.page][1]
        top, bottom = anchor.bbox[1], anchor.bbox[3]
        if not height or bottom - top > MAX_HEIGHT * height:
            continue
        shape = _shape(block.text)
        number = _is_number(shape)
        top_reach = EDGE_BAND if number and top <= min(b[1] for b in boxes[anchor.page]) else MARGIN_BAND
        bottom_reach = EDGE_BAND if number and bottom >= max(b[3] for b in boxes[anchor.page]) else MARGIN_BAND
        band = ("top" if bottom <= top_reach * height
                else "bottom" if top >= (1 - bottom_reach) * height else None)
        if band is not None and shape:
            found[(band, shape)].append((index, anchor.page, (top + bottom) / 2 / height))
    furniture: set[str] = set()
    for (band, shape), places in sorted(found.items()):
        for index, page, centre in places:
            pages = {p for _, p, c in places if abs(c - centre) <= SAME_HEIGHT}
            if len(pages) < 2:
                continue
            block = ext.blocks[index]
            kind = BlockKind.PAGE_NUMBER if _is_number(shape) else BlockKind.HEADER if band == "top" else BlockKind.FOOTER
            block.kind, block.status = kind, BlockStatus.EXCLUDED
            block.decisions.append(Decision(
                stage=DecisionStage.EXCLUDE, choice=kind.value, actor=ACTOR,
                reason=f"page furniture: the same text in the {band} margin on {len(pages)} pages",
                evidence={"band": band, "pages": len(pages), "shape": shape[:60]}))
            furniture.add(block.id)
    for entry in ext.ledger:
        if entry.block in furniture and entry.disposition == "output":
            entry.disposition = "excluded"


def mark_watermarks(ext: Extraction, off_direction: dict[str, str]) -> None:
    """Exclude text written across its page's direction that repeats on other pages (in place).
    *off_direction* maps the id of each block whose lines all run in another direction than its page's text to
    the block's text."""
    done = {p.n for p in ext.pages if p.status == PageStatus.DONE}
    pages_of: dict[str, set[int]] = defaultdict(set)
    for block in ext.blocks:
        if block.id in off_direction and block.anchors[0].page in done and _shape(off_direction[block.id]):
            pages_of[_shape(off_direction[block.id])].add(block.anchors[0].page)
    marked: set[str] = set()
    for block in ext.blocks:
        shape = _shape(off_direction.get(block.id, ""))
        if block.id not in off_direction or block.anchors[0].page not in done or len(pages_of.get(shape, ())) < 2:
            continue
        pages = len(pages_of[shape])
        block.kind, block.status = BlockKind.WATERMARK, BlockStatus.EXCLUDED
        block.decisions.append(Decision(
            stage=DecisionStage.EXCLUDE, choice=BlockKind.WATERMARK.value, actor=ACTOR,
            reason=f"watermark: text across its page's text direction, the same on {pages} pages",
            evidence={"pages": pages, "shape": shape[:60]}))
        marked.add(block.id)
    for entry in ext.ledger:
        if entry.block in marked and entry.disposition == "output":
            entry.disposition = "excluded"


_SCAN_ENGINES = frozenset({"paddleocr"})


def mark_scan_furniture(state: DocumentState) -> list[str]:
    """Exclude text and images of scanned pages that repeat in a margin band at the same place (in place); the ids."""
    sizes = {p.n: p.size_pt for p in state.pages if p.size_pt}
    found: dict[tuple, list[tuple]] = defaultdict(list)  # (band, what) → (block, page, centre x, centre y)
    for block in state.blocks:
        anchor = block.anchors[0] if block.anchors else None
        if block.kind not in (BlockKind.TEXT, BlockKind.FIGURE) or block.status not in (BlockStatus.OK,
                BlockStatus.DEGRADED) or not isinstance(anchor, PdfAnchor) or anchor.page not in sizes \
                or not any(o.engine in _SCAN_ENGINES for o in block.observations):
            continue
        width, height = sizes[anchor.page]
        x0, y0, x1, y1 = anchor.bbox
        band = ("top" if y1 <= MARGIN_BAND * height else "bottom" if y0 >= (1 - MARGIN_BAND) * height
                else "left" if x1 <= MARGIN_BAND * width else "right" if x0 >= (1 - MARGIN_BAND) * width else None)
        what = _shape(block.text or "") if block.kind == BlockKind.TEXT else \
            ("image", round((x1 - x0) / width, 2), round((y1 - y0) / height, 2))
        if band is not None and what:
            found[(band, what)].append((block, anchor.page, (x0 + x1) / 2 / width, (y0 + y1) / 2 / height))
    marked: list[str] = []
    for (band, what), places in sorted(found.items(), key=lambda kv: str(kv[0])):
        for block, page, cx, cy in places:
            pages = {p for _, p, x, y in places if abs(x - cx) <= SAME_HEIGHT and abs(y - cy) <= SAME_HEIGHT}
            if len(pages) < 2:
                continue
            if block.kind == BlockKind.TEXT:
                block.kind = {"top": BlockKind.HEADER, "bottom": BlockKind.FOOTER}.get(band, BlockKind.WATERMARK)
            block.status = BlockStatus.EXCLUDED
            block.decisions.append(Decision(
                stage=DecisionStage.EXCLUDE, choice=block.kind.value, actor=ACTOR,
                reason=f"page furniture: the same {'text' if isinstance(what, str) else 'image'} in the {band} "
                       f"margin of {len(pages)} scanned pages",
                evidence={"band": band, "pages": len(pages)}))
            marked.append(block.id)
    for entry in state.ledger:
        if entry.block in marked and entry.disposition == "output":
            entry.disposition = "excluded"
    return marked


def _is_number(shape: str) -> bool:
    """A page number: the number and punctuation only, no letters."""
    return "#" in shape and not re.search(r"[^\W\d_]", shape.replace("#", ""))


def _shape(text: str) -> str:
    compact = re.sub(r"\s+", "", text)
    if _ROMAN.fullmatch(compact):
        return "#"
    return re.sub(r"\d+", "#", compact)
