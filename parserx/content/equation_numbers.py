"""Equation numbers: the number a display formula carries at the right of its line — "(12)", "（3.1）" — is the
formula's, not a paragraph of its own.  The scan engine returns it as an item of its own and the text layer as a
block of its own; rendered where it stands it came out on the line after the formula.

A shown text block is a display formula's number when

- its text is a number in brackets (``NUMBER``), after at most what the formula itself carries — a superscript the
  text layer cut off with the number ("-1 （12）": the formula's ``^{-1}``);
- it stands on the formula's lines — its middle between the formula's top and bottom — right of the formula's
  middle, on the same page (compared as the page is shown); of several such formulas, the one it is nearest to;
- it is the only number beside that formula: a block holding several numbered equations keeps its numbers where they
  stand (which row each one numbers is not known).

The number block is merged into the formula (a ``numbers`` relation, nothing deleted) and the formula is rendered
with ``\\tag{12}``: LaTeX's own way to number a display, set at the right of the formula.  A formula whose reading
has a numbered tag takes the text layer's number in it (the reading may take "(10)" for ``\\tag{1}``); one with a tag
of words (a misplaced "故") keeps it, and the block stays where it is.
"""

from __future__ import annotations

import re
from collections import Counter

from parserx.content.latex import characters
from parserx.ir import ids
from parserx.ir.anchor import PdfAnchor
from parserx.ir.block import Block
from parserx.ir.decision import Decision
from parserx.ir.enums import BlockKind, BlockStatus, DecisionStage, RelationKind
from parserx.ir.relation import Relation
from parserx.ir.rotation import shown
from parserx.ir.state import DocumentState
from parserx.reading.compare import normalize
from parserx.workspace.queries import HIDDEN

ACTOR = "program:content.equation_numbers"
NUMBER = re.compile(r"(?P<before>.*?)[（(]\s*(?P<n>[0-9０-９]+(?:\s*[.．\-－]\s*[0-9０-９]+)*\s*[a-zA-Z]?)\s*[)）]\s*",
                    re.S)
_DISPLAY = re.compile(r"\s*\$\$.*\$\$\s*", re.S)
_TAG = re.compile(r"\\tag\*?\s*\{")
_TAG_NUMBER = re.compile(r"(\\tag\*?\s*\{)\s*[0-9０-９][0-9０-９.．\-－\s]*[a-zA-Z]?\s*\}")


def number_equations(state: DocumentState) -> list[str]:
    """Merge each equation number into its display formula; the ids of the formulas numbered."""
    pages = {p.n: p for p in state.pages}
    formulas = [(b, box) for b in state.blocks if _display(b) and _numbered_or_not(b.text)
                and (box := _box(b, pages)) is not None]
    found: dict[str, list[tuple[Block, str]]] = {}  # formula id → its numbers
    formula_of = {b.id: b for b, _ in formulas}
    for block in state.blocks:
        match = NUMBER.fullmatch(block.text or "") if block.kind == BlockKind.TEXT and block.status not in HIDDEN \
            else None
        box = _box(block, pages) if match else None
        if box is None:
            continue
        middle = (box[1] + box[3]) / 2
        near = [(box[0] - f[2], formula) for formula, f in formulas
                if _page(formula) == _page(block) and f[1] <= middle <= f[3] and box[0] >= (f[0] + f[2]) / 2
                and _carries(formula, match.group("before"))]
        if near:
            formula = min(near, key=lambda t: abs(t[0]))[1]
            found.setdefault(formula.id, []).append((block, _digits(match.group("n"))))
    numbered: list[str] = []
    for formula_id, numbers in found.items():
        if len(numbers) == 1:  # a block of several numbered equations: which row is which is not known
            _merge(state, numbers[0][0], formula_of[formula_id], numbers[0][1])
            numbered.append(formula_id)
    return numbered


def number_of(state: DocumentState) -> dict[str, str]:
    """The formulas' numbers (formula id → "12"), for rendering."""
    blocks = {b.id: b for b in state.blocks}
    out = {}
    for relation in state.relations:
        number = blocks.get(relation.src)
        if relation.kind == RelationKind.NUMBERS and number is not None:
            match = NUMBER.fullmatch(number.text or "")
            if match:
                out[relation.dst] = _digits(match.group("n"))
    return out


def tagged(text: str, number: str) -> str:
    """A display formula's LaTeX with its number: ``\\tag{number}`` before the closing delimiter, or in place of the
    reading's own tag (the text layer's number is exact: the reading takes "(10)" for ``\\tag{1}``)."""
    if _TAG_NUMBER.search(text):
        return _TAG_NUMBER.sub(lambda m: m.group(1) + number + "}", text, count=1)
    stripped = text.rstrip()
    close = _last_display_close(stripped)
    if close is None:  # LaTeX without delimiters: the renderer puts it in a display
        return f"{stripped} \\tag{{{number}}}"
    start, end = close
    after = stripped[end:]
    shown = _WRITTEN_NUMBER.fullmatch(after)
    if shown and _digits(shown.group(1)) == _digits(number):  # written after the formula already: it moves inside
        after = ""
    return f"{stripped[:start].rstrip()} \\tag{{{number}}} {stripped[start:end]}{after}"


_WRITTEN_NUMBER = re.compile(r"\s*[（(]\s*([0-9０-９.．\-－\s]+[a-zA-Z]?)\s*[)）]\s*")
_DISPLAY_DOLLARS = re.compile(r"(?<!\\)\$\$")


def _last_display_close(text: str) -> tuple[int, int] | None:
    """Where the text's last display formula closes ($$ or \\]), or None: a number belongs inside it, wherever the
    formula stands in the text (a passage written whole has prose after it)."""
    dollars = list(_DISPLAY_DOLLARS.finditer(text))
    candidates = [(dollars[-1].start(), dollars[-1].end())] if len(dollars) >= 2 and len(dollars) % 2 == 0 else []
    bracket = text.rfind("\\]")
    if bracket > 0 and "\\[" in text[:bracket]:
        candidates.append((bracket, bracket + 2))
    return max(candidates) if candidates else None


def _merge(state: DocumentState, number: Block, formula: Block, value: str) -> None:
    number.status = BlockStatus.MERGED
    state.relations.append(Relation(id=ids.relation_id(RelationKind.NUMBERS, number.id, formula.id),
                                    kind=RelationKind.NUMBERS, src=number.id, dst=formula.id))
    reason = "the number at the right of a display formula's line is the formula's (rendered \\tag)"
    number.decisions.append(Decision(stage=DecisionStage.STRUCTURE, choice="equation_number", actor=ACTOR,
                                     reason=reason, refs=[formula.id], evidence={"number": value}))
    formula.decisions.append(Decision(stage=DecisionStage.STRUCTURE, choice="numbered", actor=ACTOR,
                                      reason=reason, refs=[number.id], evidence={"number": value}))
    for entry in state.ledger:
        if entry.block == number.id:
            entry.disposition = "merged"


def _numbered_or_not(text: str) -> bool:
    """No tag, or one tag that is a number: a tag of words (the reading's misplaced "故") is left alone."""
    tags = _TAG.findall(text or "")
    return not tags or (len(tags) == 1 and _TAG_NUMBER.search(text) is not None)


def _display(block: Block) -> bool:
    if block.status in HIDDEN or not block.text:
        return False
    return block.kind == BlockKind.FORMULA or (block.kind == BlockKind.TEXT and bool(_DISPLAY.fullmatch(block.text)))


def _carries(formula: Block, before: str) -> bool:
    """What stands before the number is the formula's (a cut-off superscript), or nothing."""
    if not before.strip():
        return True
    return not Counter(normalize(before)) - Counter(normalize(characters(formula.text or "")))


def _box(block: Block, pages: dict):
    anchor = block.anchors[0] if block.anchors else None
    if not isinstance(anchor, PdfAnchor) or anchor.coord_space != "page_pt":
        return None
    return shown(pages.get(anchor.page), anchor.bbox)


def _page(block: Block) -> int:
    return block.anchors[0].page


def _digits(text: str) -> str:
    return "".join(ch for ch in text.translate(_FULL) if not ch.isspace())


_FULL = str.maketrans("０１２３４５６７８９．－", "0123456789.-")
