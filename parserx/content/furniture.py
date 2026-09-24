"""Page furniture on native PDF pages, by cross-page repetition (guide §6.9, Q34).

A short text block inside the top or bottom margin band of its page (the
outer tenth; a bare page number may sit up to the outer 15 % when nothing on
the page is further out) is furniture when the same text shape (whitespace dropped, digit runs and bare
roman numerals as ``#``) sits in the same band at about the same height on
another page: running headers and footers, page numbers.  Such a block becomes
a HEADER / FOOTER / PAGE_NUMBER block, excluded from the Markdown with a
Decision; its text stays in the sidecar.  A shape with no letters besides the
number is a page number.

Only pages whose native layer passed the quality check are considered: the
scan engine labels the furniture of the pages it reads.
"""

from __future__ import annotations

import re
from collections import defaultdict

from parserx.content.extraction import Extraction
from parserx.ir.anchor import PdfAnchor
from parserx.ir.decision import Decision
from parserx.ir.enums import BlockKind, BlockStatus, DecisionStage, PageStatus

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


def _is_number(shape: str) -> bool:
    """A page number: the number and punctuation only, no letters."""
    return "#" in shape and not re.search(r"[^\W\d_]", shape.replace("#", ""))


def _shape(text: str) -> str:
    compact = re.sub(r"\s+", "", text)
    if _ROMAN.fullmatch(compact):
        return "#"
    return re.sub(r"\d+", "#", compact)
