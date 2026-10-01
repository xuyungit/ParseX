"""Read-only queries over a DocumentState, used by the tools (overview, read)."""

from __future__ import annotations

from parserx.ir.anchor import AssetAnchor, DocxAnchor, PdfAnchor
from parserx.ir.block import Block
from parserx.ir.enums import BlockKind, BlockStatus, RelationKind
from parserx.ir.state import DocumentState

# Blocks whose content is represented elsewhere or deliberately left out of the output.
HIDDEN = frozenset({BlockStatus.EXCLUDED, BlockStatus.MERGED, BlockStatus.DUPLICATE})
JOINABLE = frozenset({BlockKind.TEXT, BlockKind.LIST, BlockKind.FOOTNOTE, BlockKind.OTHER})  # join: one paragraph


def current_notes(state: DocumentState) -> list:
    """The agent's notes that no later note revises (Q87)."""
    replaced = {n.replaces for n in state.notes if n.replaces}
    return [n for n in state.notes if n.id not in replaced]


def block_map(state: DocumentState) -> dict[str, Block]:
    return {block.id: block for block in state.blocks}


def ordered(state: DocumentState) -> list[Block]:
    return sorted(state.blocks, key=lambda b: (b.order, b.id))


def block_unit(state: DocumentState, block: Block) -> int | None:
    """The page (PDF) or segment (DOCX) of the block's first anchor, if known."""
    anchor = block.anchors[0]
    if isinstance(anchor, AssetAnchor):
        asset = next((a for a in state.assets if a.id == anchor.asset), None)
        anchor = asset.source if asset else None
    if isinstance(anchor, PdfAnchor):
        return anchor.page
    if isinstance(anchor, DocxAnchor):
        return anchor.segment
    return None


def blocks_on_page(state: DocumentState, n: int) -> list[Block]:
    return [b for b in ordered(state) if block_unit(state, b) == n]


def neighbors(state: DocumentState, block_id: str, k: int) -> list[Block]:
    """The block and up to *k* blocks on each side in reading order."""
    seq = ordered(state)
    index = next((i for i, b in enumerate(seq) if b.id == block_id), None)
    if index is None:
        raise KeyError(block_id)
    return seq[max(0, index - k): index + k + 1]


def outline(state: DocumentState) -> list[Block]:
    return [b for b in ordered(state) if b.kind == BlockKind.TITLE and b.status not in HIDDEN]


def scanned_pages(state: DocumentState) -> set[int]:
    """PDF pages whose content the scan engine read: its items of the page itself (not of an image in it) are
    output, and none of the native text layer's is (a native page with formulas read from its image is not)."""
    inside = {r.dst for r in state.relations if r.kind == RelationKind.CONTAINS}
    kept = ("output", "merged")
    scanned = {e.source.page for e in state.ledger if e.unit == "ocr_block" and e.block not in inside
               and isinstance(e.source, PdfAnchor) and e.disposition in kept}
    return scanned - {e.source.page for e in state.ledger if e.unit == "native_line"
                      and isinstance(e.source, PdfAnchor) and e.disposition in kept}
