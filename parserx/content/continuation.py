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

A footnote cut the same way continues in the next footnote block — at the foot of the next column or of the next
page, with the text of the column between (R2): the note stops mid-sentence and the next note block does not start
with a note marker (a number, a superscript, ①, *, †) or a label ("作者简介：") and starts in lower case, or in the
note's own wide script right after a character of it (not after a closing bracket).

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
_NOTE_MARKER = re.compile(r"\s*(?:\$\s*\^|<sup>|\[\d|[0-9０-９①-⑳⑴-⒇*†‡§¶])")  # how a note begins
_NOTE_LABEL = re.compile(r"\s*[^\s，。：:,.]{1,8}[：:]")  # "作者简介：", "Received:": a note of its own
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
        changes.append({"op": "join", "first": first.id, "second": second.id,
                        "reason": "a paragraph cut by the page break continues on the next page"})
    return changes + _footnote_continuations(state, linked_from, linked_to)


def _footnote_continuations(state: DocumentState, linked_from: set[str], linked_to: set[str]) -> list[dict]:
    """Footnotes cut at a column or page break: each note block and the next one, in reading order."""
    notes = [b for b in ordered(state) if b.kind == BlockKind.FOOTNOTE and b.status not in HIDDEN and b.text]
    changes = []
    for first, second in zip(notes, notes[1:]):
        pages = (block_unit(state, first), block_unit(state, second))
        if None in pages or pages[1] - pages[0] > 1 or first.id in linked_from or second.id in linked_to:
            continue
        head, tail = second.text.lstrip(), first.text.rstrip()
        if not _open_ended(first.text) or _NOTE_MARKER.match(head) or _NOTE_LABEL.match(head) or _starts_item(head):
            continue
        if head[0].islower() or (_wide(head[0]) and _wide(tail[-1]) and tail[-1].isalnum()):
            changes.append({"op": "join", "first": first.id, "second": second.id,
                            "reason": "a footnote cut at a column or page break continues in the next note block"})
    return changes


def _wide(ch: str) -> bool:
    import unicodedata

    return unicodedata.east_asian_width(ch) in ("W", "F")


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
