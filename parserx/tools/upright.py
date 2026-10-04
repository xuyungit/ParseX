"""Embedded images turned upright before anything reads them (Q150).

Scanned certificates, statements and forms are often put into a document turned a quarter or upside down — 57 of
the 385 images of one bid document.  Everything that reads an image does worse on a turned one: the scan engine
misread a company name and a form number on an upside-down statement and a digit of a turned certificate's number
that it read right upright, and the service model and the agent see it turned too.  So before the layout
detector, the scan engine and the service model see a figure's image, the image is turned upright:

- the way up is read from the image itself (``reading/local.upright``): the turn whose local reading has the most
  text in level lines, taken when clearly ahead of the image as it stands;
- an image too bare of text to tell (a photo) is shown the way the document shows it (``shown_turn``, recorded by
  the readers): Word's turn of the picture, or how a PDF page places the image (and its /Rotate).  Word also turns
  wide tables a quarter to fit a portrait page; a turn the text contradicts is not followed, the text decides;
- the upright image is a new asset (role ``original``: it is the document's image, upright) derived from the image
  as stored, with the transform from its pixels back to the stored image's; the figure's anchor points to it, so
  the Markdown links it and every later step reads it.  The figure records the turn (a Decision).

Renders and crops are not turned: they are cut from pages as shown.  Scanned pages are the scan engine's to turn.
"""

from __future__ import annotations

from parserx.ir.anchor import AssetAnchor
from parserx.ir.decision import Decision
from parserx.ir.enums import BlockKind, DecisionStage
from parserx.ir.rotation import from_shown
from parserx.ir.state import DocumentState
from parserx.reading.local import ENOUGH, turn_image, upright
from parserx.tools.context import ToolContext
from parserx.workspace.queries import HIDDEN

ACTOR = "program:upright"
CHOICE = "upright"
_TURNABLE = frozenset({"image/png", "image/jpeg", "image/gif", "image/bmp", "image/tiff", "image/webp"})


def todo(state: DocumentState) -> list[str]:
    """Figures whose image is an embedded original not yet looked at for its way up."""
    assets = {a.id: a for a in state.assets}
    out = []
    for block in state.blocks:
        anchor = next((a for a in block.anchors if isinstance(a, AssetAnchor) and a.asset in assets), None)
        if block.kind != BlockKind.FIGURE or block.status in HIDDEN or anchor is None:
            continue
        asset = assets[anchor.asset]
        if asset.role == "original" and asset.derived_from is None and asset.media_type in _TURNABLE \
                and not any(d.choice == CHOICE for d in block.decisions):
            out.append(block.id)
    return out


def turn_upright(ctx: ToolContext, blocks: list[str]) -> int:
    """Turn the images of *blocks* (figures) upright where they are turned; the number turned."""
    state = ctx.ws.load()
    by_id = {b.id: b for b in state.blocks}
    assets = {a.id: a for a in state.assets}
    found: dict[str, tuple] = {}  # block → (turn, by, levels, new asset, anchor index)

    def read(block_id: str):  # the readings side by side (speed plan P2); the turns taken below, in order
        anchor = next(a for a in by_id[block_id].anchors if isinstance(a, AssetAnchor))
        data = (ctx.ws.root / assets[anchor.asset].path).read_bytes()
        return data, upright(ctx.reader(), data, ctx.cache)

    for outcome in ctx.map_local(read, blocks):
        if outcome.status != "ok":  # an image the reader cannot decode stays as it is
            continue
        block_id, (data, way) = outcome.task, outcome.value
        block = by_id[block_id]
        index, anchor = next((i, a) for i, a in enumerate(block.anchors) if isinstance(a, AssetAnchor))
        asset = assets[anchor.asset]
        turn, by = way.turn, "reading"
        if not turn and max(way.level.values()) < ENOUGH:
            turn, by = _shown_turn(block), "document"
        levels = {f"level_text_{t}": round(v, 1) for t, v in sorted(way.level.items())}  # by turn read
        if not turn:
            found[block_id] = (0, by, levels, None, index)
            continue
        out = turn_image(data, turn, asset.media_type if asset.media_type == "image/jpeg" else "image/png")
        width, height = (asset.height, asset.width) if turn in (90, 270) else (asset.width, asset.height)
        media = "image/jpeg" if asset.media_type == "image/jpeg" else "image/png"
        new = ctx.ws.add_asset(out, media_type=media, width=width, height=height, role="original",
                               derived_from=asset.id, source=asset.source,
                               transform=from_shown(turn, (float(width), float(height))))
        found[block_id] = (turn, by, levels, new, index)
    if not found:
        return 0
    turned = 0
    with ctx.ws.txn("tool:process:upright") as state:
        have = {a.id for a in state.assets}
        by_id = {b.id: b for b in state.blocks}
        for block_id, (turn, by, levels, new, index) in found.items():
            block = by_id[block_id]
            if new is not None:
                if new.id not in have:
                    state.assets.append(new)
                    have.add(new.id)
                block.anchors[index] = AssetAnchor(asset=new.id, bbox=(0, 0, new.width, new.height),
                                                   image_size=(new.width, new.height))
                turned += 1
            block.decisions.append(Decision(
                stage=DecisionStage.CONTENT_SOURCE, choice=CHOICE, actor=ACTOR, refs=[new.id] if new else [],
                reason=(f"turned {turn}° clockwise to stand upright ("
                        + ("its text reads level that way" if by == "reading" else "as the document shows it; "
                           "too little text to read the way up from") + ")") if turn else "upright as stored",
                evidence={"turn": turn, "by": by, **levels}))
    return turned


def _shown_turn(block) -> int:
    """The turn the document shows the image with (the DOCX reader's ``shown_turn``), 0 when it says none."""
    return next((int(d.evidence["shown_turn"]) for d in block.decisions if "shown_turn" in d.evidence), 0)
