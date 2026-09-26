"""``skim``: the document's text the way a person skims it (no side effects on the state).

One mode per call:

- **scroll** (the default): *after* blocks from *start* (the document's first block when not given) and *before*
  blocks before it; ``after_id`` / ``before_id`` continue the scroll;
- **page**: every block of a page;
- **find**: the blocks whose text contains a phrase (Unicode-normalised, spacing and case ignored);
- **cls**: every block of one style class;
- **outline**: the document's conventions at a glance — its style classes (blocks set alike and numbered alike),
  and the current titles and the lines numbered or laid out like one, each with the start of what follows it.
  Lines only set apart by their typography are not listed one by one (bold phrases and form labels are many): the
  class table shows them, ``cls`` lists a class.

Paragraphs are shortened to their start unless *full* is asked for; ``read`` shows a block with its details.  Each
line names its style class: blocks of one class are set alike, so a decision about one is usually a decision about
the class.
"""

from __future__ import annotations

import re
import unicodedata
from collections import Counter
from statistics import median
from typing import Literal

from pydantic import model_validator

from parserx.hierarchy.legality import numbering_signature
from parserx.hierarchy.typography_titles import body_typography, native_style, title_evidence
from parserx.ir.base import IRModel
from parserx.ir.block import Block
from parserx.ir.enums import BlockKind, TaskKind
from parserx.ir.state import DocumentState
from parserx.tools.context import ToolContext, ToolOutput, output
from parserx.tools.envelope import DocText, FailureCode, ToolFailure
from parserx.workspace.queries import HIDDEN, block_unit, ordered

GLANCE = 120  # characters of a paragraph shown when skimming
FOLLOWING = 100  # characters of the text after a heading-like line (outline)
EXAMPLES = 3


class SkimRequest(IRModel):
    outline: bool = False
    page: int | None = None
    find: str | None = None
    cls: str | None = None  # a style class id from the outline
    start: str | None = None  # scroll: a block id
    after: int = 40  # scroll: this block and the ones after it
    before: int = 0  # scroll: blocks before the start
    full: bool = False  # whole paragraphs instead of their start

    @model_validator(mode="after")
    def _one_mode(self) -> "SkimRequest":
        if sum((self.outline, self.page is not None, self.find is not None, self.cls is not None)) > 1:
            raise ValueError("give at most one of outline, page, find or cls")
        if self.after < 0 or self.before < 0:
            raise ValueError("after and before count blocks: not negative")
        if self.find is not None and not _squash(self.find):
            raise ValueError("find needs a phrase")
        return self


class SkimLine(IRModel):
    id: str
    kind: BlockKind
    level: int | None
    page: int | None
    cls: str | None  # style class (see the outline); None for tables and images
    text: DocText | None
    shortened: bool
    next_text: DocText | None = None  # outline: the start of the text after this line


class StyleClass(IRModel):
    id: str
    typography: str  # font, size, weight — or the scan engine's label
    numbering: str | None  # shape of the leading number (N.N, C、, 第N章 …)
    count: int
    chars: int  # median length of the blocks' text
    roles: dict[str, int]  # current roles: text, H1, H2 …
    evidence: dict[str, int]  # blocks with each kind of title evidence (typography, layout, numbering)
    pages: str
    examples: list[DocText]


class SkimResult(IRModel):
    mode: Literal["scroll", "page", "find", "cls", "outline"]
    lines: list[SkimLine]
    classes: list[StyleClass] | None
    total_blocks: int
    before_id: str | None  # scroll back: start here with before
    after_id: str | None  # scroll on: start here with after


def run(ctx: ToolContext, req: SkimRequest) -> ToolOutput[SkimResult]:
    state = ctx.ws.load()
    shown = [b for b in ordered(state) if b.status not in HIDDEN]
    classes = _classes(state, shown)
    width = None if req.full else GLANCE

    def lines(blocks: list[Block]) -> list[SkimLine]:
        return [_line(state, b, classes.of.get(b.id), width) for b in blocks]

    before_id = after_id = None
    if req.outline:
        mode, picked = "outline", _outline_lines(state, shown, classes, width)
    elif req.page is not None:
        if all(p.n != req.page for p in state.pages):
            raise ToolFailure(FailureCode.NOT_FOUND, f"no page {req.page}", targets=[f"p{req.page}"])
        mode, picked = "page", lines([b for b in shown if block_unit(state, b) == req.page])
    elif req.find is not None:
        needle = _squash(req.find)
        mode, picked = "find", [_found(state, b, classes.of.get(b.id), needle, width)
                                for b in shown if needle in _squash(_text(b))]
    elif req.cls is not None:
        if all(c.id != req.cls for c in classes.views):
            raise ToolFailure(FailureCode.NOT_FOUND, f"no style class {req.cls}", targets=[req.cls])
        mode, picked = "cls", lines([b for b in shown if classes.of.get(b.id) == req.cls])
    else:
        at = 0
        if req.start is not None:
            at = next((i for i, b in enumerate(shown) if b.id == req.start), -1)
            if at < 0:
                raise ToolFailure(FailureCode.NOT_FOUND, f"no shown block {req.start}", targets=[req.start])
        lo, hi = max(0, at - req.before), min(len(shown), at + req.after)
        mode, picked = "scroll", lines(shown[lo:hi])
        before_id = shown[lo].id if lo > 0 else None
        after_id = shown[hi].id if hi < len(shown) else None
    return output(SkimResult(mode=mode, lines=picked, classes=classes.views if req.outline else None,
                             total_blocks=len(shown), before_id=before_id, after_id=after_id))


class _Classes:
    def __init__(self) -> None:
        self.of: dict[str, str] = {}  # block id → class id
        self.views: list[StyleClass] = []
        self.evidence: dict[str, dict] = {}  # block id → its kinds of title evidence


def _classes(state: DocumentState, shown: list[Block]) -> _Classes:
    """Group the text blocks into classes of blocks set alike: native text by font, size, weight and the shape of its
    leading number; scan text by the engine's label and the number's shape.  Ids by first appearance."""
    body = body_typography(state)
    keys: dict[tuple, list[Block]] = {}
    evidence: dict[str, dict] = {}
    for block in shown:
        if block.kind not in (BlockKind.TEXT, BlockKind.TITLE) or not _text(block).strip():
            continue
        style = native_style(block)
        text = _text(block).strip()
        numbering = numbering_signature(text)
        if style is not None:
            typography = " ".join(filter(None, (style.font, _size(style.font_size),
                                                "bold" if style.bold else "regular")))
            evidence[block.id] = title_evidence(block, style, body)
        else:
            chosen = next((o for o in block.observations if o.id == block.chosen_observation), None)
            label = chosen.label if chosen is not None and chosen.label else "text"
            typography = f"{chosen.engine if chosen is not None else 'unknown'} label {label}"
            layout = {o.label for o in block.observations if o.task == TaskKind.LAYOUT}
            evidence[block.id] = {"layout": 1} if label in ("paragraph_title", "doc_title") or \
                (layout and layout <= {"paragraph_title", "doc_title"}) else {}
            if numbering is not None:
                evidence[block.id]["numbering"] = numbering
        keys.setdefault((typography, numbering), []).append(block)
    out = _Classes()
    for i, ((typography, numbering), blocks) in enumerate(keys.items(), 1):
        cid = f"S{i}"
        roles: Counter[str] = Counter()
        kinds: Counter[str] = Counter()
        for block in blocks:
            out.of[block.id] = cid
            roles[_role(block)] += 1
            kinds.update(evidence[block.id].keys())
        pages = sorted({p for b in blocks if (p := block_unit(state, b)) is not None})
        out.views.append(StyleClass(
            id=cid, typography=typography, numbering=numbering, count=len(blocks),
            chars=int(median(len(_text(b).strip()) for b in blocks)),
            roles=dict(sorted(roles.items())), evidence=dict(sorted(kinds.items())), pages=_ranges(pages),
            examples=[DocText(doc_text=_shorten(_text(b).strip(), 60)[0]) for b in blocks[:EXAMPLES]]))
    out.evidence = evidence
    return out


def _outline_lines(state: DocumentState, shown: list[Block], classes: _Classes, width: int | None) -> list[SkimLine]:
    """Heading-like lines: titles, and one-line text numbered or laid out like a title — with what follows each."""
    evidence = classes.evidence
    out = []
    for i, block in enumerate(shown):
        text = _text(block).strip()
        heading_like = block.kind == BlockKind.TITLE or (
            block.kind == BlockKind.TEXT and text and "\n" not in text
            and set(evidence.get(block.id, ())) & {"numbering", "layout"})
        if not heading_like:
            continue
        line = _line(state, block, classes.of.get(block.id), width)
        following = next((_text(b).strip() for b in shown[i + 1:] if _text(b).strip()), "")
        if following:
            line.next_text = DocText(doc_text=_shorten(following, FOLLOWING)[0])
        out.append(line)
    return out


def _line(state: DocumentState, block: Block, cls: str | None, width: int | None) -> SkimLine:
    text, shortened = _shorten(_text(block).strip(), width)
    return SkimLine(id=block.id, kind=block.kind, level=block.level, page=block_unit(state, block), cls=cls,
                    text=DocText(doc_text=text) if text else None, shortened=shortened)


def _found(state: DocumentState, block: Block, cls: str | None, needle: str, width: int | None) -> SkimLine:
    """The block's text around the first match when the text is longer than the glance."""
    line = _line(state, block, cls, None)
    text = _text(block).strip()
    if width is None or len(text) <= width:
        return line
    positions = [i for i, c in enumerate(text) if not c.isspace()]  # where each non-space character is
    at = _squash(text).find(needle)
    start = positions[at] if 0 <= at < len(positions) else 0  # normalisation may shift it a little
    lo = max(0, start - width // 3)
    snippet = ("…" if lo else "") + text[lo:lo + width] + ("…" if lo + width < len(text) else "")
    line.text, line.shortened = DocText(doc_text=snippet), True
    return line


def _text(block: Block) -> str:
    if block.text:
        return block.text
    if block.cells is not None:  # a table: its first row
        first = sorted((c for c in block.cells.cells if c.row == 0), key=lambda c: c.col)
        return f"[table {block.cells.n_rows}×{block.cells.n_cols}] " + " | ".join(c.content for c in first)
    return ""


def _role(block: Block) -> str:
    if block.kind == BlockKind.TITLE:
        return f"H{block.level}" if block.level else "title"
    return block.kind.value


def _shorten(text: str, width: int | None) -> tuple[str, bool]:
    if width is None or len(text) <= width:
        return text, False
    return text[:width - 1] + "…", True


def _squash(text: str) -> str:
    return re.sub(r"\s+", "", unicodedata.normalize("NFKC", text)).casefold()


def _size(size: float | None) -> str | None:
    return None if size is None else f"{size:g}pt"


def _ranges(pages: list[int]) -> str:
    runs: list[list[int]] = []
    for p in pages:
        if runs and p == runs[-1][1] + 1:
            runs[-1][1] = p
        else:
            runs.append([p, p])
    return ", ".join(str(a) if a == b else f"{a}–{b}" for a, b in runs)
