"""Numbered title sequences as worklist signals (Phase 3 D4, P7): the numbers say what the outline may lack.

- **Gaps**: the titles under one parent count on (5.1, 5.2, 5.3), and a dotted number hangs under its parent (6.1
  under 6).  When a number is missing — 5.2 between 5.1 and 5.3, or the 6 that 6.1 hangs under — and a paragraph
  between the neighbours starts with exactly that number, the paragraph is proposed as the missing title: at its
  siblings' level, or one above its children.
- **Successors** (``series_successors``): after a title numbered in any numerals (``B.``, ``（三）``, ``IV.``), a
  paragraph before the next title as high or higher that starts with the next number of the same style and is not
  a finished sentence is proposed as the next title of the series, at the same level (``C. Self-Regulatory …``
  after the title ``B. Self-Regulatory …``, which the scan engine labelled body text).
- **Unclear nesting** (``unclear_nesting``): in a text cut from a document — the style above began at a number
  other than its first too — a numbering style in other numerals first seen right after it, not starting at its
  first number either (``III.`` after ``B.`` on a page that opens with ``B.``).  Either the text began inside a
  section of the new style, and the style above belongs one level below it, or the new list's earlier items were
  not found and it nests in the title above.  The numbers cannot tell; the page can (italic or bold,
  indentation).  A dotted number under its parent (``3.1`` after ``3``) says its nesting itself.

The number alone does not decide (a paragraph may start with a number, "6支" may be a quantity); the agent
confirms.
"""

from __future__ import annotations

import re

from parserx.hierarchy.legality import numbering_signature
from parserx.hierarchy.levels import leading_number, number_value
from parserx.ir.block import Block
from parserx.ir.enums import BlockKind
from parserx.ir.state import DocumentState
from parserx.workspace.queries import HIDDEN, ordered


def numbering_gaps(state: DocumentState) -> list[tuple[str, str, int, dict]]:
    """(block id, text, proposed level, evidence) for paragraphs that fill a gap in a numbered title sequence."""
    seq = [b for b in ordered(state) if b.status not in HIDDEN]
    titles = [(i, b, n) for i, b in enumerate(seq)
              if b.kind == BlockKind.TITLE and b.level and (n := leading_number(b.text)) is not None]
    wanted: list[tuple[tuple[int, ...], int, int, int, str]] = []  # number, level, search from, to, evidence
    numbers = {n for _, _, n in titles}
    for k, (i, block, number) in enumerate(titles):
        before = titles[k - 1] if k else None
        if len(number) > 1 and number[:-1] not in numbers and (before is None or before[2][:len(number) - 1] != number[:-1]):
            wanted.append((number[:-1], block.level - 1, before[0] if before else -1, i,
                           f"the parent of {_text(number)}"))
        sibling = next((t for t in reversed(titles[:k]) if len(t[2]) == len(number) and t[2][:-1] == number[:-1]), None)
        if sibling is not None and number[-1] > sibling[2][-1] + 1 and sibling[1].level == block.level:
            for missing in range(sibling[2][-1] + 1, number[-1]):
                wanted.append((number[:-1] + (missing,), block.level, sibling[0], i,
                               f"between {_text(sibling[2])} and {_text(number)}"))
    out: list[tuple[str, str, int, dict]] = []
    for number, level, start, end, evidence in wanted:
        filler = next((b for b in seq[start + 1:end] if b.kind == BlockKind.TEXT and _starts_with(b, number)), None)
        if filler is not None and filler.id not in {o[0] for o in out}:
            out.append((filler.id, (filler.text or "").strip(), max(1, level), {"numbering": evidence}))
    return out


def _text(number: tuple[int, ...]) -> str:
    return ".".join(str(p) for p in number)


def _starts_with(block: Block, number: tuple[int, ...]) -> bool:
    """The paragraph starts with exactly *number*: not a longer number, not a decimal."""
    pattern = r"\s*" + r"[.．]".join(str(p) for p in number) + r"(?![\d]|[.．]\d)"
    return re.match(pattern, block.text or "") is not None


_SENTENCE_END = re.compile(r"[。！？；!?;]\s*$|(?<=[^\d\s.])\.\s*$")
_PAGE_REFERENCE = re.compile(r"[\s.．·…_\-]+\d+\s*$")  # an entry of a table of contents


def series_successors(state: DocumentState) -> list[tuple[str, str, int, dict]]:
    """(block id, text, proposed level, evidence) for paragraphs that carry the next number of a title's series."""
    seq = [b for b in ordered(state) if b.status not in HIDDEN]
    out: list[tuple[str, str, int, dict]] = []
    taken: set[str] = set()
    for i, block in enumerate(seq):
        if block.kind != BlockKind.TITLE or not block.level:
            continue
        text = (block.text or "").strip()
        style, value = numbering_signature(text), number_value(text)
        if style is None or value is None:
            continue
        for later in seq[i + 1:]:
            later_text = (later.text or "").strip()
            if later.kind == BlockKind.TITLE and later.level and later.level <= block.level:
                break  # the series' next title, or the section is over
            if later.kind == BlockKind.TEXT and later.id not in taken and numbering_signature(later_text) == style \
                    and number_value(later_text) == value + 1 and not _SENTENCE_END.search(later_text) \
                    and not _PAGE_REFERENCE.search(later_text):
                out.append((later.id, later_text, block.level, {"numbering": f"the next number after {text[:40]}"}))
                taken.add(later.id)
                break
    return out


def unclear_nesting(state: DocumentState) -> list[tuple[str, str, str]]:
    """(block id, its text, the text of the title above) for the titles whose nesting the numbers cannot tell (see the
    module docstring)."""
    out: list[tuple[str, str, str]] = []
    first_value: dict[str, int | None] = {}  # each style's number where it first appears
    above = None
    for block in ordered(state):
        if block.kind != BlockKind.TITLE or block.status in HIDDEN or not block.level:
            continue
        text = (block.text or "").strip()
        style = numbering_signature(text)
        if style is None:
            continue
        if style not in first_value:
            value = number_value(text)
            first_value[style] = value
            if above is not None and value not in (None, 1) and first_value[above[0]] not in (None, 1) \
                    and _numerals(style) != _numerals(above[0]):
                out.append((block.id, text, above[1]))
        above = (style, text)
    return out


def _numerals(style: str) -> str:
    """The kind of numerals of a numbering style: arabic, Chinese, Roman (upper, lower), letters; dotted numbers and
    their parents are one kind."""
    return style if style.startswith("第") else next((c for c in style if c in "NCRrL"), style)
