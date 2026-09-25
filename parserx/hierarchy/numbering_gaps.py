"""Gaps in numbered title sequences (Phase 3 D4): a worklist signal.

Numbered titles form sequences: the titles under one parent count on (5.1, 5.2, 5.3), and a dotted number hangs
under its parent (6.1 under 6).  When a number is missing — 5.2 between 5.1 and 5.3, or the 6 that 6.1 hangs
under — and a paragraph between the neighbours starts with exactly that number, the paragraph is proposed as the
missing title: at its siblings' level, or one above its children.  The number alone does not decide (a
paragraph may start with a number, "6支" may be a quantity); the agent confirms.
"""

from __future__ import annotations

import re

from parserx.hierarchy.levels import leading_number
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
