"""Sub- and superscripts of the native text layer, from the size and baseline of its glyphs (Q143 ③).

The text layer keeps a script as a glyph set smaller and raised or lowered, often as a line of its own; read as text
alone ``k₁`` is "k1" and ``m²`` "m 2".  Scripts are typesetting facts like bold: a block's text stays as the layer
reads it and carries them as marks (rendering writes them, ``render/emphasis.py``); table cells are written with them.

A glyph is a script of the glyph it is attached to when it is set at most ``SCRIPT_SIZE`` of that glyph's size and its
baseline stands ``SCRIPT_SHIFT`` of that size above or below — conventions of typesetting, not of a document: a script
stands at most ``ROW_SHIFT`` of the larger size off its base's baseline (scripts shift about 0.15–0.45 em; the next row
is a line spacing, over 1 em, away), and it is attached — nothing but smaller glyphs between them, no gap from one
text-layer line to another along them wider than ``ATTACHED`` of the base's size.  It is set smaller than the row's
text too (the largest of its letters and digits): a glyph beside an enlarged operator or parenthesis is the formula's
text.  An opening bracket carries no script, and a script before its base (¹³C) has a letter or digit as that base.
Two lines one over the other are not a row; a glyph attached to a script is judged against that script's base too;
a prime is a character unless it stands inside a script.  Sizes are as set
(a CJK font's from the step between its ideographs, ``pdf_native._font_ems``), and a line's size is that of most of
its letters and digits whatever their faces.

Measured against the annotations (the vision-first branch's audit, scripts/vision_first/script_audit.py, 12 documents,
39 native pages): the branch's candidates version 2 had 436 right, 5 wrong, 6 fragments, 95 undecided; these rules 483
right, 3 wrong (all in display formulas, which the pipeline leaves to the formula tool: ``pdf_native._leave_formulas``),
no fragment, 44 undecided; tables 79 right, 0 wrong in both.

Written (``form``): a run of scripts in its Unicode forms where every character has one (k₁, m², cm⁻¹), else in LaTeX
math (``$_{sd}$``, ``$^{[1]}$``) — as the vision-first adapter writes them.
"""

from __future__ import annotations

import unicodedata
from collections.abc import Sequence
from dataclasses import dataclass

from parserx.content.text_audit import is_unreadable

SCRIPT_SIZE = 0.85
SCRIPT_SHIFT = 0.12
ROW_SHIFT = 0.6
ATTACHED = 0.5
PRIMES = frozenset("′″‴⁗'")

SUB_MARK, SUP_MARK = "﷐", "﷑"  # noncharacters (never in a document's text): the next character is a script
_KIND_OF = {SUB_MARK: "sub", SUP_MARK: "sup"}
MARK_OF = {"sub": SUB_MARK, "sup": SUP_MARK}
MARKS = frozenset(_KIND_OF)
_SUP = str.maketrans("0123456789+-−=()ni", "⁰¹²³⁴⁵⁶⁷⁸⁹⁺⁻⁻⁼⁽⁾ⁿⁱ")
_SUB = str.maketrans("0123456789+-−=()aeoxhklmnpst", "₀₁₂₃₄₅₆₇₈₉₊₋₋₌₍₎ₐₑₒₓₕₖₗₘₙₚₛₜ")
_LATEX_SPECIAL = str.maketrans({c: "\\" + c for c in "{}$%#&_"})


@dataclass(frozen=True)
class Glyph:
    char: str
    x0: float
    x1: float
    top: float
    bottom: float
    size: float
    baseline: float


@dataclass(frozen=True)
class Line:
    """A text-layer line: its glyphs (none for a line not written left to right: no scripts are judged in it) and the
    baseline and size of its main span."""

    glyphs: tuple[Glyph, ...]
    baseline: float = 0.0
    size: float = 0.0


def kinds(lines: Sequence[Line]) -> list[list[str]]:
    """Per line, per glyph: "sup", "sub" or "" (``judge``)."""
    return [[kind for kind, _ in line] for line in judge(lines)]


def judge(lines: Sequence[Line]) -> list[list[tuple[str, tuple[int, int] | None]]]:
    """Per line, per glyph: ("sup" | "sub" | "", (line, glyph) of the glyph it is a script of) — judged in the glyph's
    visual row (the lines overlapping its line whose main baseline is within ``ROW_SHIFT``), because the text layer
    often keeps a raised or lowered glyph as a line of its own."""
    extents = [(min(g.top for g in ln.glyphs), max(g.bottom for g in ln.glyphs)) if ln.glyphs else None for ln in lines]
    rows: dict[tuple[int, int], bool] = {}

    def stacked(i: int, j: int) -> bool:
        """One line over the other: one line's extent holds a whole glyph of the other set larger than its own glyphs
        (a caption's second language, an affiliation under the names, a table head's second line, the parts of a
        fraction).  A script stands beside its base — kerned into it at most, never spanning a larger glyph — and
        nominal sizes differ between fonts, so a size-based shift alone cannot tell."""
        for a, b in ((i, j), (j, i)):
            own = [g for g in lines[a].glyphs if g.char.strip()]
            if not own:
                continue
            left, right, small = min(g.x0 for g in own), max(g.x1 for g in own), min(g.size for g in own)
            if any(h.char.strip() and h.size * SCRIPT_SIZE >= small and left <= h.x0 and h.x1 <= right
                   for h in lines[b].glyphs):
                return True
        return False

    def same_row(i: int, j: int) -> bool:
        if (i, j) not in rows:
            a, b, ea, eb = lines[i], lines[j], extents[i], extents[j]
            rows[(i, j)] = rows[(j, i)] = (
                ea is not None and eb is not None and min(ea[1], eb[1]) > max(ea[0], eb[0])
                and abs(a.baseline - b.baseline) <= ROW_SHIFT * max(a.size, b.size) and (i == j or not stacked(i, j)))
        return rows[(i, j)]

    out: list[list[tuple[str, tuple[int, int] | None]]] = []
    for i, line in enumerate(lines):
        if not line.glyphs:
            out.append([])
            continue
        row = sorted(((g, j, k) for j, other in enumerate(lines) if other.glyphs and same_row(i, j)
                      for k, g in enumerate(other.glyphs)), key=lambda item: (item[0].x0, item[0].x1))
        own: list[tuple[str, tuple[int, int] | None]] = [("", None)] * len(line.glyphs)
        for (_, j, k), (kind, base) in zip(row, _kinds([g for g, _, _ in row], [j for _, j, _ in row], same_row)):
            if j == i:
                own[k] = (kind, row[base][1:] if base is not None else None)
        out.append(own)
    return out


def _kinds(glyphs: list[Glyph], lines: list[int], same_row) -> list[tuple[str, int | None]]:
    """For each glyph of a row, in order along it: ("sup" | "sub" | "", the glyph it is a script of).  Its base is the
    nearest glyph set larger (to its left, else to its right) with nothing but smaller glyphs between them, no gap
    along them wider than ``ATTACHED`` of the base's size, and those glyphs in the base's row too (a line's row holds
    the lines beside it, which need not be beside each other: a reference in the right column is not a script of a
    heading in the left one across the body line between); when that glyph is itself a script, the glyph is judged
    against that script's base too: level with the script, it is part of it (the digits of a citation beside its
    raised brackets); level with the base, it is none (the full stop after the citation); shifted from both, a script
    of the script.  A prime is a script only beside one, and then part of it."""
    marks = [k for k, g in enumerate(glyphs) if g.char.strip()]
    text = max((g.size for g in glyphs if unicodedata.category(g.char)[0] in "LN"), default=float("inf"))
    out = [""] * len(glyphs)
    base_of: dict[int, int] = {}
    side: dict[int, int] = {}
    for at, i in enumerate(marks):
        g = glyphs[i]
        if g.size > SCRIPT_SIZE * text:
            continue  # set at the size of the row's text: no script
        for step in (-1, 1):
            k = at + step
            while 0 <= k < len(marks) and glyphs[marks[k]].size * SCRIPT_SIZE < g.size:  # not set larger: not a base
                k += step
            if not 0 <= k < len(marks):
                continue
            j = marks[k]
            run = marks[min(at, k) + 1:max(at, k)] + [i]
            if _gap(glyphs, lines, marks[min(at, k):max(at, k) + 1]) <= ATTACHED * glyphs[j].size and all(
                    same_row(lines[r], lines[j]) for r in run):
                base_of[i], side[i] = j, step
                out[i] = _shifted(g, glyphs[j])
                break
    while True:  # a base that is no script itself must carry one on that side (a script's own glyphs may: [1])
        dropped = [i for i, j in base_of.items() if out[i] and not out[j] and not _carries(glyphs[j].char, side[i])]
        if not dropped:
            break
        for i in dropped:
            out[i] = ""
            del base_of[i]
    first = dict(enumerate(out))
    base = dict(base_of)
    for i, j in base_of.items():  # a glyph attached to a script, against that script's own base
        if not first[j] or j not in base_of:
            continue
        if not _shifted(glyphs[i], glyphs[base_of[j]]):
            out[i] = ""  # back on the base's line (the full stop after a raised citation)
        elif not first[i]:
            out[i], base[i] = first[j], base_of[j]  # level with the script: part of it (a citation's digits)
        # else shifted from the script too: a script of the script (φ_j raised), as judged
    for at, i in enumerate(marks):  # a prime is a script only beside one (x′ in q^{x′}), and then part of it
        if glyphs[i].char in PRIMES:
            near = [marks[k] for k in (at - 1, at + 1)
                    if 0 <= k < len(marks) and glyphs[marks[k]].char not in PRIMES and out[marks[k]]]
            out[i] = out[near[0]] if near else ""
            if near:
                base[i] = base.get(near[0])
    return [(kind, base.get(i) if kind else None) for i, kind in enumerate(out)]


def _carries(char: str, step: int) -> bool:
    """Whether a glyph carries a script on the side *step* points to it from: an opening bracket none; on its left
    (a script before its base: ¹³C, a footnote mark before a word) only a letter or a digit — a small glyph before a
    closing bracket or a sign has its base out of sight, and is not guessed."""
    category = unicodedata.category(char)
    return category != "Ps" and (step < 0 or category[0] in "LN")


def _gap(glyphs: list[Glyph], lines: list[int], chain: list[int]) -> float:
    """The widest gap along *chain* (glyphs in order along the row, from base to script or back) from one text-layer
    line to another: attached means connected all the way, not only next to the base (a full stop inside a wide glyph
    box is no bridge across a column gap); within a line the text layer holds its glyphs together (the primes of
    z₁′ − z₂′ are one line)."""
    widest, right = float("-inf"), glyphs[chain[0]].x1
    for previous, r in zip(chain, chain[1:]):
        if lines[r] != lines[previous]:
            widest = max(widest, glyphs[r].x0 - right)
        right = max(right, glyphs[r].x1)
    return widest


def _shifted(glyph: Glyph, base: Glyph) -> str:
    shift = (glyph.baseline - base.baseline) / base.size
    return ("sup" if shift < 0 else "sub") if abs(shift) >= SCRIPT_SHIFT else ""


# ── writing ──


def form(kind: str, text: str) -> str:
    """A run of scripts as written: its Unicode forms where every character has one, else LaTeX math (CJK in it as
    ``\\text``)."""
    converted = text.translate(_SUP if kind == "sup" else _SUB)
    if text and all(a != b for a, b in zip(converted, text)):
        return converted
    body = text.translate(_LATEX_SPECIAL)
    if any(unicodedata.east_asian_width(ch) in "WF" for ch in text):  # CJK is text, not math letters
        body = f"\\text{{{body}}}"
    return f"${'^' if kind == 'sup' else '_'}{{{body}}}$"


def runs(chars: Sequence[tuple[str, str]]) -> list[tuple[int, int, str]]:
    """The runs of scripts among (character, kind) pairs, as (start, end, kind): glyphs of one kind in a row, the spaces
    between two of them included; a run of glyphs the text layer does not map to readable characters is none (what
    they show is unknown)."""
    out: list[tuple[int, int, str]] = []
    n = 0
    while n < len(chars):
        kind = chars[n][1]
        if not kind:
            n += 1
            continue
        end = m = n + 1
        while m < len(chars) and (chars[m][1] == kind or (not chars[m][1] and chars[m][0] in " \t")):
            if chars[m][1] == kind:
                end = m + 1
            m += 1
        if not all(is_unreadable(ch) for ch, _ in chars[n:end] if ch.strip()):
            out.append((n, end, kind))
        n = end
    return out


def unmark(text: str) -> list[tuple[str, str]]:
    """Marked text (a mark before each script glyph) as (character, kind) pairs."""
    out: list[tuple[str, str]] = []
    kind = ""
    for ch in text:
        if ch in _KIND_OF:
            kind = _KIND_OF[ch]
            continue
        out.append((ch, kind))
        kind = ""
    return out


def write(text: str) -> str:
    """Marked text written: each run of scripts as ``form`` gives, the spaces between it and the glyph before it
    removed (the text layer's gap before a raised or lowered glyph)."""
    chars = unmark(text)
    if not any(kind for _, kind in chars):
        return text
    out: list[str] = []
    at = 0
    for start, end, kind in runs(chars):
        out.append("".join(ch for ch, _ in chars[at:start]))
        if out[-1].strip(" \t"):
            out[-1] = out[-1].rstrip(" \t")
        out.append(form(kind, "".join(ch for ch, _ in chars[start:end])))
        at = end
    out.append("".join(ch for ch, _ in chars[at:]))
    return "".join(out)


def marked(text: str, glyph_kinds: Sequence[tuple[str, str]]) -> str:
    """*text* (a line as read: spaces at gaps, characters normalised one for one) with a mark before each script
    glyph, from the (character, kind) of the line's glyphs in order; unmarked when the glyphs do not match the text's
    characters one for one."""
    kinds_of = [kind for ch, kind in glyph_kinds if ch.strip()]
    shown = [ch for ch in text if not ch.isspace()]
    if not any(kinds_of) or len(kinds_of) != len(shown):
        return text
    out: list[str] = []
    k = 0
    for ch in text:
        if not ch.isspace():
            if kinds_of[k]:
                out.append(MARK_OF[kinds_of[k]])
            k += 1
        out.append(ch)
    return "".join(out)
