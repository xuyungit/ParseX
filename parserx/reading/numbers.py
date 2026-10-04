"""Numbers the local reading sees that the output does not write as one number at that place (Q148).

The two-way comparison (``compare.py``) reads letters and digits only, so it cannot see a number written apart
("7. 3.5.2" for "7.3.5.2") nor a formula number left out ("(6)" is one character there, too short to compare).
Here each number of a local line is compared, as a number, with the output at the line's place:

- **written apart** (``split``): the page shows the number with a point or a colon inside ("7.3.5.2", "12:30"), and
  the output writes those digits with a space at that point ("7. 3.5.2").  A space before groups of three digits is
  a thousands space ("1 720") and stands; a comma followed by a space is a list, not a number.
- **left out** (``missing``): the output at the line's place does not have the number.  Only strong evidence counts:
  a number in brackets ("(6)", a formula or item number), which the output must have in brackets too (or as
  ``\\tag{6}``); a number with a point or a colon inside; a number of two digits or more outside formulas.  In a
  formula the local recognizer reads poorly (a δ comes back as 8, a script is glued to its base), so there only
  numbers in brackets or with a point count.

Both readings are compared as they write numbers: NFKC; LaTeX scripts set beside their base, as the local reader
writes them (``10^{-3}`` → 10-3), other commands and braces dropped.  Between the digits of a number the output may
have punctuation or spaces the local reader left out ("95-383" read as 95383, "1 720" as 1720), except where the
local reading has a point or colon, which is the split.

The place of a line: the blocks that account for text (shown, or merged into another block: a formula number merged
into its formula) it lies in or overlaps, else those beside it on its row (a formula number beside its formula); the
placeholder of a scanned page's image holds nothing.  A line no block holds is left to ``compare.py``.  Only content no text layer vouches for is compared: the scan engine's reading of a scanned page,
of a formula page, of an image (``read_inside``); text-layer numbers are exact.  Lines in page furniture, pictures,
formula regions of the page, or blocks excluded with a reason are not compared.  A signal, not a verdict: nothing is
changed.  Measured on the corpus (2026-10-04, 30 documents, fixed pipeline, GLM-OCR and PaddleOCR-VL): about 20
places that are wrong in the output (the nine section numbers written apart on a blurred book page; formula numbers
left out of formula images) and about 15 local misreadings, which the agent closes at a look.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass

from parserx.content.select import NATIVE_ENGINES
from parserx.content.text import normalize_fullwidth_ascii
from parserx.ir.anchor import AssetAnchor
from parserx.ir.block import Block
from parserx.ir.enums import BlockKind, BlockStatus, TaskKind
from parserx.ir.state import DocumentState
from parserx.layout.labels import FURNITURE, NOT_PROSE, to_kind
from parserx.reading.compare import _ACCOUNTS, _SHOWN, _centre, _inside, _overlap, _places, _text, has_math, read_inside

SPLIT, MISSING = "split", "missing"

_NUMBER = re.compile(r"\d+(?:[.:]\d+|[, ]\d{3}(?![\d.]))*")  # a comma or a space only between thousands
_SCRIPT = re.compile(r"\s*[\^_]\s*\{([^{}]*)\}")
_TAG = re.compile(r"\\tag\*?\s*\{([^{}]*)\}")
_OPEN, _CLOSE = r"[(\[【]", r"[)\]】]"  # full-width brackets are folded by NFKC
_JOIN = r"([^\w]{0,3})"  # what may stand between two digits of one number in the output
_PICTURES = NOT_PROSE - {"display_formula"}  # in an image: a formula number may lie in the formula's region


@dataclass
class NumberFinding:
    target: str  # the block holding the place, the image's figure block, or "p<n>" for a page
    lines: list[str]  # the local reading's lines, as it reads them
    numbers: list[tuple[str, str]]  # (the number as the local reading writes it, SPLIT or MISSING)


def number_findings(state: DocumentState) -> list[NumberFinding]:
    """Places whose numbers the output does not write as the local reading sees them: pages in order, then images."""
    found: dict[str, NumberFinding] = {}

    def add(target: str, line: str, numbers: list[tuple[str, str]]) -> None:
        entry = found.setdefault(target, NumberFinding(target, [], []))
        entry.lines.append(line)
        entry.numbers += [n for n in numbers if n not in entry.numbers]

    places = _places(state)
    for reading in state.readings:
        here = places.get(reading.n, [])
        holding = [(b, box) for b, box in here if b.status in _ACCOUNTS and b.kind != BlockKind.SCAN]
        skip = ([box for b, box in here if b.status == BlockStatus.EXCLUDED
                 or (b.kind == BlockKind.FIGURE and b.status in _SHOWN)]
                + list(reading.not_prose)
                + [r.bbox for r in reading.roles if to_kind("layout", r.label) in FURNITURE])
        for line in reading.lines:
            centre = _centre(line.bbox)
            if not _NUMBER.search(line.text) or any(_inside(centre, box) for box in skip):
                continue
            holders = _holders(holding, line.bbox)
            if not holders or all(_engine(b) in NATIVE_ENGINES for b in holders):
                continue
            numbers = line_numbers(line.text, " ".join(_text(b) for b in holders), in_math=_in_math(holders))
            if numbers:  # listed on a shown block of the scan engine's where there is one
                scanned = [b for b in holders if _engine(b) not in NATIVE_ENGINES]
                add(next((b for b in scanned if b.status in _SHOWN), scanned[0]).id, line.text, numbers)
    blocks = {b.id: b for b in state.blocks}
    records = {r.id: r for r in state.images}
    for figure_id, inside in read_inside(state).items():
        figure = blocks[figure_id]
        asset = next((a.asset for a in figure.anchors if isinstance(a, AssetAnchor)), None)
        record = records.get(asset)
        if record is None or not record.reading or all(_engine(b) in NATIVE_ENGINES for b in inside):
            continue
        pictures = [o.anchor.bbox for o in figure.observations if o.task == TaskKind.LAYOUT
                    and o.label in _PICTURES and isinstance(o.anchor, AssetAnchor)]
        text, in_math = " ".join(_text(b) for b in inside), _in_math(inside)
        for line in record.reading:
            if not any(_inside(_centre(line.bbox), box) for box in pictures):
                if numbers := line_numbers(line.text, text, in_math=in_math):
                    add(figure_id, line.text, numbers)
    return list(found.values())


def line_numbers(line: str, output: str, *, in_math: bool) -> list[tuple[str, str]]:
    """The numbers of a local *line* that *output* (the text at its place) does not write as one number."""
    local, out = number_view(line), number_view(output)
    found = []
    for number in dict.fromkeys(_NUMBER.findall(local)):
        bracketed = re.search(_OPEN + r"\s*" + re.escape(number) + r"\s*" + _CLOSE, local) is not None
        inner = "." in number or ":" in number
        if not (bracketed or inner or (not in_math and len(re.sub(r"\D", "", number)) >= 2)):
            continue
        verdict = _compare(number, out, bracketed=bracketed)
        if verdict is not None:
            found.append((number, verdict))
    return found


def number_view(text: str) -> str:
    """*text* as the numbers are compared: NFKC, \\tag{n} as (n), scripts beside their base, commands dropped."""
    text = _TAG.sub(r"(\1)", unicodedata.normalize("NFKC", normalize_fullwidth_ascii(text)))
    for _ in range(4):  # scripts inside scripts
        text = _SCRIPT.sub(r"\1", text)
    text = re.sub(r"\s*[\^_]\s*", "", text)
    return re.sub(r"\\[A-Za-z]+", " ", text).replace("{", " ").replace("}", " ")


def _compare(number: str, out: str, *, bracketed: bool) -> str | None:
    """None when *out* writes *number* as one number, else SPLIT or MISSING."""
    digits = re.sub(r"\D", "", number)
    inner = [bool(re.match(r"[.:]", number[m.end():])) for m in re.finditer(r"\d", number)][:-1]
    body = digits[0] + "".join(_JOIN + d for d in digits[1:])
    pattern = rf"{_OPEN}\s*{body}\s*{_CLOSE}" if bracketed else rf"(?<!\d){body}(?!\d)"
    split = False
    for match in re.finditer(pattern, out):
        apart = [k for k, join in enumerate(match.groups()) if inner[k] and re.search(r"\s", join)
                 and not (re.fullmatch(r"\s+", join) and (len(digits) - k - 1) % 3 == 0)]  # 1 720: thousands
        if not apart:
            return None
        split = True
    return SPLIT if split else MISSING


def _holders(holding: list[tuple[Block, tuple]], box: tuple) -> list[Block]:
    """The blocks a line lies in, else overlaps, else sits beside on its row."""
    centre = _centre(box)
    height = box[3] - box[1]
    return ([b for b, where in holding if _inside(centre, where)]
            or [b for b, where in holding if _overlap(box, where)]
            or [b for b, where in holding if min(where[3], box[3]) - max(where[1], box[1]) >= height / 2])


def _in_math(blocks: list[Block]) -> bool:
    return any(b.kind == BlockKind.FORMULA or has_math(b.text or "") for b in blocks)


def _engine(block: Block) -> str | None:
    chosen = next((o for o in block.observations if o.id == block.chosen_observation), None)
    return chosen.engine if chosen is not None else None
