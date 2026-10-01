"""Glyphs the text layer maps to no readable character (private use, U+FFFD, control codes), read from the page image.

A font draws the same glyph wherever it uses it, so a glyph read in two places is read everywhere in the document
(the same font, glyph and code).  A reading of the region of a line it stands in (``read``: the local recognizer
behind the page reading, ``reading/local.py`` — free, offline) counts where the glyph's neighbours, up to ``CONTEXT``
characters each side and at least one of them readable, read as the text layer has them, with one character in the
glyph's place.  Two lines of different text that agree, and none that disagrees, make the reading (the rule both
teams arrived at in round 2); a glyph read nowhere stays as it is, and the agent finds it listed as unreadable.

A line with no readable character left is no text where the page shows it as part of a picture (the layout detector's
region: the image holds it), nor where the page image shows no text at all (an ornament): it is excluded, its text
kept in the sidecar.  A display formula's lines stay: the formula tool reads the formula from the image and checks its
reading against them.
"""

from __future__ import annotations

import re
import unicodedata
from collections import Counter, defaultdict
from collections.abc import Callable

import pymupdf

from parserx.content.text_audit import is_unreadable
from parserx.ir.decision import Decision
from parserx.ir.enums import DecisionStage
from parserx.layout import labels

READ = "glyph_read"
NOT_TEXT = "no_readable_text"
CONTEXT = 4  # characters each side of a glyph compared with a reading (sampling, not a decision)
LINES = 8  # lines read at most per glyph
AGREE = 2  # lines of different text that must agree
DPI = 300
FLAGS = pymupdf.TEXT_PRESERVE_WHITESPACE  # as the pipeline reads the text layer: a code without Unicode is U+FFFD

Box = tuple[float, float, float, float]
Read = Callable[[pymupdf.Page, Box], str]  # what the page image shows in a region of a page (unrotated points)
Key = tuple[str, int, str]  # (font, glyph id or -1, the character the text layer gives)


def reading_of(source: str, index: int, reading: str) -> str | None:
    """The one character standing for ``source[index]`` in *reading*: its neighbours, up to ``CONTEXT`` characters
    each side (spaces aside), read as the text layer has them — at least one readable, other unreadable glyphs any
    one character, the same glyph the same one — at one place of the reading, or at several with the same answer."""
    target = source[index]
    window = [source[j] for j in window_of(source, index)]
    if not any(not is_unreadable(ch) for ch in window):
        return None
    parts, seen = [], False
    for ch in window:
        if ch == target:
            parts.append("(?P=g)" if seen else "(?P<g>.)")
            seen = True
        else:
            parts.append("." if is_unreadable(ch) else re.escape(unicodedata.normalize("NFKC", ch)))
    text = "".join(ch for ch in unicodedata.normalize("NFKC", reading) if not ch.isspace())
    found = {m.group("g") for m in re.finditer("".join(parts), text)}
    value = found.pop() if len(found) == 1 else None
    return value if value and not is_unreadable(value) and value.isprintable() else None


def window_of(source: str, index: int) -> list[int]:
    """The positions of the glyph at *index* and its neighbours, up to ``CONTEXT`` characters each side, spaces
    aside: what a reading of the glyph is checked against."""
    shown = [j for j, ch in enumerate(source) if not _space(ch)]
    at = shown.index(index)
    return shown[max(0, at - CONTEXT):at + CONTEXT + 1]


def agreed(readings: dict[str, str | None]) -> str | None:
    """The reading of a glyph from its lines' readings (line text → character or None): the one character at least
    ``AGREE`` lines give, and no line another."""
    given = Counter(v for v in readings.values() if v is not None)
    return next(iter(given)) if len(given) == 1 and sum(given.values()) >= AGREE else None


def read_glyphs(doc: pymupdf.Document, read: Read,
                layout: Callable[[pymupdf.Page], list[tuple[str, Box]]] | None = None,
                usable: Callable[[pymupdf.Page], bool] | None = None) -> dict[Key, str]:
    """The readings of the document's unreadable glyphs (key → character), each from the lines it stands in, read
    whole once, until lines agree or ``LINES`` lines were read.  Glyphs in a picture or a display formula the layout
    detector sees (*layout*) are not read: the image holds a picture's text, and the formula tool reads formulas; nor
    are those of a page whose text layer is not *usable* (the scan engine reads it)."""
    places: dict[Key, list[tuple[pymupdf.Page, str, Box, int]]] = defaultdict(list)
    for page in doc:
        if page.rotation or not any(is_unreadable(ch) for ch in page.get_text("text", flags=FLAGS)):
            continue
        if usable is not None and not usable(page):
            continue
        ids = glyph_ids(page)
        skip = [box for label, box in (layout(page) if layout is not None else []) if label in labels.NOT_PROSE]
        for block in page.get_text("rawdict", flags=FLAGS).get("blocks", []):
            for line in block.get("lines", []):
                box = tuple(line["bbox"])
                if any(_centre_in(box, other) for other in skip):
                    continue
                chars = [(ch.get("c", ""), span.get("font", ""), ch) for span in line.get("spans", [])
                         for ch in span.get("chars", ())]
                text = "".join(c for c, _, _ in chars)
                for k, (c, font, ch) in enumerate(chars):
                    if c and is_unreadable(c):
                        places[key_of(font, c, ch, ids)].append((page, text, box, k))
    out: dict[Key, str] = {}
    seen: dict[tuple[int, Box], str] = {}
    for key, where in places.items():
        readings: dict[str, str | None] = {}
        for page, text, box, k in where:
            if text in readings:
                continue
            if len(readings) == LINES or agreed(readings) is not None:
                break
            if (page.number, box) not in seen:
                seen[(page.number, box)] = read(page, box)
            readings[text] = reading_of(text, k, seen[(page.number, box)])
        value = agreed(readings)
        if value is not None:
            out[key] = value
    return out


def _centre_in(inner: Box, outer: Box) -> bool:
    cx, cy = (inner[0] + inner[2]) / 2, (inner[1] + inner[3]) / 2
    return outer[0] <= cx <= outer[2] and outer[1] <= cy <= outer[3]


def glyph_ids(page: pymupdf.Page) -> dict[tuple[float, float], int]:
    """Glyph ids of the page's characters by their origin (the text trace; its characters may differ from the
    dictionary's for unmapped codes, so they are matched by place)."""
    return {(round(origin[0], 1), round(origin[1], 1)): gid for span in page.get_texttrace()
            for _cp, gid, origin, _box in span.get("chars", ())}


def key_of(font: str, char: str, ch: dict, ids: dict[tuple[float, float], int]) -> Key:
    origin = ch.get("origin", (0.0, 0.0))
    return font, ids.get((round(origin[0], 1), round(origin[1], 1)), -1), char


def decision(read: Counter, actor: str) -> Decision | None:
    """The record of a block's glyphs read from the page image (original → reading), or None."""
    n = sum(read.values())
    if not n:
        return None
    pairs = ", ".join(f"U+{ord(a):04X}→{b!r}" + (f" ×{k}" if k > 1 else "") for (a, b), k in read.most_common())
    return Decision(stage=DecisionStage.CONTENT_SOURCE, choice=READ, actor=actor, evidence={"glyphs": n},
                    reason=f"{n} glyph{'s' if n > 1 else ''} the text layer maps to no character, read from the page "
                           f"image (lines of different text agree, every neighbour read as the text layer has it): "
                           f"{pairs}")


def _space(ch: str) -> bool:
    return ch.isspace() and not is_unreadable(ch)  # U+001C–U+001F are control codes, not spaces
