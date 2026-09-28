"""Two-way comparison of the output with an independent local reading of each page (guide §9.5, Q56).

A signal, not a verdict: a place where the output and the page image disagree is listed for review; nothing is
changed — with one exception, text the output lacks altogether (``missed_lines``, below).

- **Seen on the page, accounted for by no block** (``unaccounted_lines``): a line of the local reading that the
  blocks at its place do not contain and that is nowhere else on its page either.  Blocks account for text
  when they are output, excluded with a reason or merged into another block; a superseded reading (DUPLICATE)
  is no destination.  Text inside a figure block, or in a region the layout detector calls picture or formula
  content, is not prose and is not compared.  A line unaccounted for on more than one page is page furniture
  (the cross-page repetition evidence of ``content/furniture.py``), not an omission.
- **Output, not seen on the page** (``unseen_segments``): a segment of a shown block — a table cell, a
  sentence — that the local reading does not see where the block sits (on every page the block spans) nor
  anywhere on those pages.
- **Inside a table** (``within_tables``): unaccounted lines whose place is a shown table's region are what the table
  lacks — a head row, a row, part of a cell — and are listed on the table rather than on the page.
- **Added by the program** (``missed_lines``, Q133): an unaccounted line outside every table, where no shown block
  lies at all — the scan engine or the text layer gave nothing there — read with confidence ``ADD_SCORE`` or more.
  The fixed pipeline adds it to the output rather than lose it, and lists it for review (``text_added``).  A line
  that disagrees with a block at its place (formula notation, a misreading of that block's text) is only listed:
  adding it would repeat the block.  Measured on the corpus (2026-09-28, 47 unaccounted lines): the rule keeps the
  three lines lost from the output and none of the rest (formula passages 0.82–0.99 lie on their blocks; readings
  of a QR code or a watermark score 0.60–0.78).

The readings are compared on letters and digits only (NFKC, full width folded, markup dropped): punctuation,
spacing and LaTeX commands are not differences.  The two tolerances are measurement tolerances between two
independent readings, calibrated once on the whole corpus (guide §9.5, 2026-09-25: 26 PDFs, 6125 local lines,
4426 output segments), not judgments about content.
"""

from __future__ import annotations

import re
import unicodedata
from collections import defaultdict
from dataclasses import dataclass

from rapidfuzz import fuzz

from parserx.ir.anchor import AssetAnchor, PdfAnchor
from parserx.ir.base import BBox
from parserx.ir.block import Block
from parserx.ir.enums import BlockKind, BlockStatus, RelationKind, TaskKind
from parserx.layout.labels import NOT_PROSE
from parserx.ir.state import ClosedItem, DocumentState, ReadLine
from parserx.content.text import normalize_fullwidth_ascii

NEAR = 0.5  # share of a text's adjacent-character pairs the other reading has at the text's place
SOMEWHERE = 80  # rapidfuzz partial_ratio of the text against all of its page(s): a contiguous near match
ADD_SCORE = 0.9  # the local recognizer's own confidence for a line the program adds by itself (Q133)
READING_ACTOR = "program:page_reading"  # the Decision actor of text added from the local reading

_ACCOUNTS = frozenset({BlockStatus.OK, BlockStatus.DEGRADED, BlockStatus.EXCLUDED, BlockStatus.MERGED})
_SHOWN = frozenset({BlockStatus.OK, BlockStatus.DEGRADED})
_NOT_PROSE = frozenset({BlockKind.FIGURE, BlockKind.SCAN, BlockKind.FORMULA})
_MARKUP = re.compile(r"\\[A-Za-z]+|<[^>]+>")
_SEGMENT_END = re.compile(r"(?<=[。！？；!?;])|(?<=\.)\s+|\n")


def unaccounted_lines(state: DocumentState) -> dict[int, list[ReadLine]]:
    """Page → the lines of its local reading that no block accounts for (page furniture left out)."""
    places = _places(state)
    found: dict[int, list[ReadLine]] = {}
    for reading in state.readings:
        if not reading.lines:  # the page read as empty: no evidence either way
            continue
        blocks = [(b, box) for b, box in places.get(reading.n, []) if b.status in _ACCOUNTS]
        # a shown figure's text goes to its description or transcription; a hidden one is no destination (§11.5)
        pictures = [box for b, box in places.get(reading.n, [])
                    if b.kind == BlockKind.FIGURE and b.status in _SHOWN] + list(reading.not_prose)
        page_text = normalize(" ".join(_text(b) for b, _ in blocks))
        for line in reading.lines:
            text = normalize(line.text)
            if len(text) < 2 or any(_inside(_centre(line.bbox), box) for box in pictures):
                continue
            holders = ([b for b, box in blocks if _inside(_centre(line.bbox), box)]
                       or [b for b, box in blocks if _overlap(line.bbox, box)])
            if holders and pairs_share(text, normalize(" ".join(_text(b) for b in holders))) >= NEAR:
                continue
            if page_text and fuzz.partial_ratio(text, page_text) >= SOMEWHERE:
                continue
            found.setdefault(reading.n, []).append(line)
    pages_of: dict[str, set[int]] = defaultdict(set)
    for n, lines in found.items():
        for line in lines:
            pages_of[normalize(line.text)].add(n)
    return {n: kept for n, lines in sorted(found.items())
            if (kept := [ln for ln in lines if len(pages_of[normalize(ln.text)]) < 2])}


@dataclass
class TableLines:
    """Unaccounted lines inside a shown table's region: a head row, a row or part of a cell the table lacks."""

    table: str
    lines: list[ReadLine]
    sharing: list[str]  # other shown tables whose region holds these lines: the parts of one frame (Q93)


def within_tables(state: DocumentState, found: dict[int, list[ReadLine]]
                  ) -> tuple[list[TableLines], dict[int, list[ReadLine]]]:
    """Split unaccounted lines into those inside a shown table's region, by table (tables in reading order), and the
    rest, by page.  Tables that share a region — the parts one frame was cut into — share their lines: the lines go
    to the first of them, and the others are named."""
    tables = [b for b in sorted(state.blocks, key=lambda b: b.order)
              if b.kind == BlockKind.TABLE and b.status in _SHOWN]
    places = [(t.id, a.page, a.bbox) for t in tables for a in t.anchors
              if isinstance(a, PdfAnchor) and a.coord_space == "page_pt"]
    by_table: dict[str, TableLines] = {}
    rest: dict[int, list[ReadLine]] = {}
    for n, lines in found.items():
        for line in lines:
            holders = list(dict.fromkeys(t for t, page, box in places if page == n and _inside(_centre(line.bbox), box)))
            if not holders:
                rest.setdefault(n, []).append(line)
                continue
            entry = by_table.setdefault(holders[0], TableLines(holders[0], [], []))
            entry.lines.append(line)
            entry.sharing += [t for t in holders[1:] if t not in entry.sharing]
    order = {t.id: i for i, t in enumerate(tables)}
    return sorted(by_table.values(), key=lambda e: order[e.table]), rest


def missed_lines(state: DocumentState) -> dict[int, list[ReadLine]]:
    """Page → the unaccounted lines the program adds to the output by itself (Q133, see the module docstring)."""
    _, rest = within_tables(state, unaccounted_lines(state))
    places = _places(state)
    out: dict[int, list[ReadLine]] = {}
    for n, lines in rest.items():
        shown = [box for b, box in places.get(n, []) if b.status in _SHOWN]
        kept = [ln for ln in lines if ln.score >= ADD_SCORE
                and not any(_inside(_centre(ln.bbox), box) or _overlap(ln.bbox, box) for box in shown)]
        if kept:
            out[n] = kept
    return out


def added_from_reading(block: Block) -> bool:
    """Whether the program added *block* from the local page reading (``missed_lines``)."""
    return any(d.actor == READING_ACTOR and d.choice == "added" for d in block.decisions)


def unseen_segments(state: DocumentState) -> dict[str, list[str]]:
    """Block id → the segments of a shown block the local reading does not see (blocks in reading order)."""
    readings = {r.n: r for r in state.readings if r.lines}  # a page read as empty is no evidence either way
    out: dict[str, list[str]] = {}
    for block in sorted(state.blocks, key=lambda b: b.order):
        if block.status not in _SHOWN or block.kind in _NOT_PROSE or not isinstance(block.anchors[0], PdfAnchor):
            continue
        spans = [(a.page, a.bbox) for a in block.anchors
                 if isinstance(a, PdfAnchor) and a.coord_space == "page_pt" and a.page in readings]
        if not spans or any(_inside(_centre(spans[0][1]), box) for box in readings[spans[0][0]].not_prose):
            continue
        near = normalize(" ".join(ln.text for n, box in spans for ln in readings[n].lines if _inside(_centre(ln.bbox), box)))
        pages = normalize(" ".join(ln.text for n in sorted({n for n, _ in spans}) for ln in readings[n].lines))
        unseen = [seg for seg in _segments(block)
                  if len(text := normalize(seg)) >= 2 and pairs_share(text, near) < NEAR
                  and fuzz.partial_ratio(text, pages) < SOMEWHERE]
        if unseen:
            out[block.id] = unseen
    return out


def text_at(state: DocumentState, page: int, box: BBox) -> str | None:
    """What the local reading of *page* shows inside *box* (lines by their centre); None: the page was not read."""
    reading = next((r for r in state.readings if r.n == page and r.lines), None)
    if reading is None:
        return None
    return " ".join(ln.text for ln in reading.lines if _inside(_centre(ln.bbox), box))


def text_near(state: DocumentState, block: Block) -> str | None:
    """What the local reading shows where *block* sits (every page it spans); None: none of them was read."""
    seen = [text_at(state, a.page, a.bbox) for a in block.anchors
            if isinstance(a, PdfAnchor) and a.coord_space == "page_pt"]
    return None if all(s is None for s in seen) else " ".join(s for s in seen if s)


def holders_of(state: DocumentState, page: int, box: BBox, text: str) -> list[str]:
    """Blocks at *box* of *page* that account for *text* already (output, excluded or merged)."""
    wanted = normalize(text)
    return [b.id for b, where in _places(state).get(page, []) if b.status in _ACCOUNTS and _overlap(box, where)
            and pairs_share(wanted, normalize(_text(b))) >= NEAR]


# ── Helpers ─────────────────────────────────────────────────────────────


def read_inside(state: DocumentState) -> dict[str, list[Block]]:
    """Shown figure id → the shown blocks of the text read inside its image (``contains``), in reading order."""
    blocks = {b.id: b for b in state.blocks}
    out: dict[str, list[Block]] = {}
    for relation in state.relations:
        src, dst = blocks.get(relation.src), blocks.get(relation.dst)
        if relation.kind == RelationKind.CONTAINS and src is not None and dst is not None \
                and src.kind == BlockKind.FIGURE and src.status in _SHOWN and dst.status in _SHOWN:
            out.setdefault(src.id, []).append(dst)
    return {k: sorted(v, key=lambda b: (b.order, b.id)) for k, v in out.items()}


def lacking_in_transcription(state: DocumentState, figure: Block) -> list[str] | None:
    """Lines of the local reading of *figure*'s image that the text read inside it does not account for — compared
    as a page is (IO6-5): letters and digits, a contiguous near match; text the scan engine read and excluded (page
    furniture) accounts for its lines; a line inside a region the layout detector calls picture, seal, chart or
    formula content is not prose and is not compared.  None when it cannot be checked: the image was not read
    locally, or its text has formulas, which the local reader does not read."""
    asset = next((a.asset for a in figure.anchors if isinstance(a, AssetAnchor)), None)
    record = next((r for r in state.images if r.id == asset), None)
    blocks = {b.id: b for b in state.blocks}
    inside = [blocks[r.dst] for r in state.relations if r.kind == RelationKind.CONTAINS and r.src == figure.id
              and r.dst in blocks and blocks[r.dst].status in _ACCOUNTS]
    if record is None or record.reading is None or any(
            b.kind == BlockKind.FORMULA or has_math(b.text or "") for b in inside):
        return None
    pictures = [o.anchor.bbox for o in figure.observations if o.task == TaskKind.LAYOUT and o.label in NOT_PROSE
                and isinstance(o.anchor, AssetAnchor)]
    text = normalize(" ".join(_text(b) for b in inside))
    return [line.text for line in record.reading
            if not any(_inside(_centre(line.bbox), box) for box in pictures)
            and len(norm := normalize(line.text)) >= 2 and (not text or fuzz.partial_ratio(norm, text) < SOMEWHERE)]


def text_stands_for_image(state: DocumentState, figure: Block) -> bool:
    """Whether the text read inside *figure*'s image can stand in its place: the local reading finds nothing it
    lacks, or what it found was checked against the image and closed (the item for exactly these lines)."""
    lacking = lacking_in_transcription(state, figure)
    if lacking is None:
        return False
    quotes = ClosedItem.quoted(lacking)
    return not lacking or any(c.target == figure.id and c.kind == "text_unaccounted" and c.quotes == quotes
                              for c in state.closed)


def _places(state: DocumentState) -> dict[int, list[tuple[Block, BBox]]]:
    places: dict[int, list[tuple[Block, BBox]]] = defaultdict(list)
    for block in state.blocks:
        for anchor in block.anchors:
            if isinstance(anchor, PdfAnchor) and anchor.coord_space == "page_pt":
                places[anchor.page].append((block, anchor.bbox))
    return places


def _segments(block: Block) -> list[str]:
    if block.cells is not None:
        return [c.content.strip() for c in block.cells.cells if c.content.strip()]
    return [s.strip() for s in _SEGMENT_END.split(block.text or "") if s.strip()]


def _text(block: Block) -> str:
    cells = " ".join(c.content for c in block.cells.cells) if block.cells is not None else ""
    return f"{block.text or ''} {cells}"


_MATH = re.compile(r"\$|\\\(|\\\[|\\begin\{")


def has_math(text: str) -> bool:
    """Whether *text* holds a formula written as LaTeX (delimited, or an environment)."""
    return bool(_MATH.search(text))


def normalize(text: str) -> str:
    """Letters and digits only (NFKC, full width folded, markup dropped): what the two readings are compared on."""
    text = _MARKUP.sub("", unicodedata.normalize("NFKC", normalize_fullwidth_ascii(text)))
    return "".join(ch.lower() for ch in text if ch.isalnum())


def pairs_share(text: str, other: str) -> float:
    """Share of *text*'s adjacent-character pairs found in *other* (both normalized)."""
    pairs = [text[i:i + 2] for i in range(len(text) - 1)]
    have = {other[i:i + 2] for i in range(len(other) - 1)}
    return sum(p in have for p in pairs) / len(pairs) if pairs else 1.0


def _centre(box: BBox) -> tuple[float, float]:
    return (box[0] + box[2]) / 2, (box[1] + box[3]) / 2


def _inside(point: tuple[float, float], box: BBox) -> bool:
    return box[0] <= point[0] <= box[2] and box[1] <= point[1] <= box[3]


def _overlap(a: BBox, b: BBox) -> bool:
    return a[0] < b[2] and b[0] < a[2] and a[1] < b[3] and b[1] < a[3]
