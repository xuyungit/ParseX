"""Paragraphs cut by a page break (guide §6.9): ``continues`` between the two parts, so they render as one.

Page i's last body block and page i+1's first are one paragraph when page i stops mid-sentence and page i+1 does
not start something new.  Only page furniture and notes (header, footer, page number, footnote) may lie between
the two parts, as for tables continued on the next page: after a figure or table the text may as well be a new
step (a screenshot in a how-to) as the rest of the sentence (a float in a paper), so it is left to the agent.
The evidence, all of which must hold:

- the first part stops mid-sentence: it ends (inside closing quotes and brackets) with a letter, digit or
  character, or with punctuation that continues a sentence (，、- …) — not with a full stop, colon or semicolon,
  and not with a symbol (an interface label such as "管理订阅 ›" is not a sentence);
- neither part starts with a figure or table label (图 3, Table 2: a caption, or a drawing's label), and the
  second part does not start with a number or bullet (a new item or section) and is not the same text (a line
  repeated on every page);
- where both parts carry font evidence, size and weight are the same.

PDF only: a DOCX paragraph is one block whatever the pagination.  Blocks already linked are left alone.  Proposals
go through ``apply_structure`` like any change, so the renderer joins them and the sidecar keeps both blocks.
"""

from __future__ import annotations

import re

from parserx.hierarchy.legality import numbering_signature
from parserx.ir.block import Block
from parserx.ir.enums import BlockKind, RelationKind
from parserx.ir.observation import TextStyle
from parserx.ir.state import DocumentState
from parserx.workspace.queries import HIDDEN, block_unit, ordered

ACTOR = "program:content.continuation"
_FURNITURE = frozenset({BlockKind.HEADER, BlockKind.FOOTER, BlockKind.PAGE_NUMBER, BlockKind.WATERMARK,
                        BlockKind.FOOTNOTE})
_FIRST = frozenset({BlockKind.TEXT, BlockKind.LIST})  # the part that is cut: body text or a list item
_CONTINUING = frozenset("，、,-–—/（(《“‘「『[")  # punctuation inside a sentence
_CLOSING = frozenset("”’」』）)]】》〉\"'")
_BULLETS = frozenset("•·●○■□◆◇▪▫►▶-–—*※√✓")
_CAPTION_LABEL = re.compile(r"^\s*(?:Figure|Fig\.|Table|Tab\.|图|表)\s*[0-9０-９]", re.IGNORECASE)


def propose_continuations(state: DocumentState) -> list[dict]:
    if state.format != "pdf":
        return []
    linked_from = {r.src for r in state.relations if r.kind == RelationKind.CONTINUES}
    linked_to = {r.dst for r in state.relations if r.kind == RelationKind.CONTINUES}
    pages: dict[int, list[Block]] = {}
    for block in ordered(state):
        if block.status in HIDDEN or block.kind in _FURNITURE:
            continue
        page = block_unit(state, block)
        if page is not None:
            pages.setdefault(page, []).append(block)
    changes = []
    for n in sorted(pages):
        if n + 1 not in pages:
            continue
        first, second = pages[n][-1], pages[n + 1][0]
        if first.kind not in _FIRST or second.kind != BlockKind.TEXT:
            continue
        if first.id in linked_from or second.id in linked_to:
            continue
        if not _open_ended(first.text) or _starts_item(second.text) or not _same_type(first, second):
            continue
        if first.text.strip() == second.text.strip() or _CAPTION_LABEL.match(first.text):
            continue
        changes.append({"op": "link", "kind": RelationKind.CONTINUES.value, "src": first.id,
                        "dst": second.id})
    return changes


def _open_ended(text: str) -> bool:
    stripped = text.rstrip()
    while stripped and stripped[-1] in _CLOSING:
        stripped = stripped[:-1].rstrip()
    return bool(stripped) and (stripped[-1].isalnum() or stripped[-1] in _CONTINUING)


def _starts_item(text: str) -> bool:
    stripped = text.lstrip()
    return (not stripped or stripped[0] in _BULLETS or numbering_signature(stripped) is not None
            or _CAPTION_LABEL.match(stripped) is not None)


def _same_type(a: Block, b: Block) -> bool:
    sa, sb = _style(a), _style(b)
    if sa is None or sb is None:
        return True
    if sa.font_size is not None and sb.font_size is not None and abs(sa.font_size - sb.font_size) > 0.5:
        return False
    return sa.bold is None or sb.bold is None or sa.bold == sb.bold


def _style(block: Block) -> TextStyle | None:
    chosen = next((o for o in block.observations if o.id == block.chosen_observation), None)
    return chosen.style if chosen is not None else None
