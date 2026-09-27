"""``read_draft``: read the draft — the parse as it stands (Q85).  No side effects, no cost.

One ``view`` per call:

- **summary** (default): the document, its pages' status, block and issue counts, cost so far;
- **issues**: the worklist — what the program found for judgment, each item with a stable id (``dismiss`` takes
  it); by kind or page;
- **text**: the draft in reading order, the way a person skims it — from a block ``start`` with ``after`` /
  ``before`` blocks and cursors to go on, a ``page``, a phrase (``find``: Unicode-normalised, spacing and case
  ignored) or a regular expression (``pattern``) searched in the text, the table rows and the figure descriptions,
  or every block of one style class (``cls``).  Paragraphs are shortened to their start unless ``full``;
- **outline**: the document's conventions at a glance — its style classes (blocks set alike and numbered alike, with
  current roles and title evidence) and the titles and the lines numbered or laid out like one, each with the start
  of what follows; Word documents add their paragraph styles and list numbering;
- **blocks**: the named blocks in detail — every table cell, status, level, and with ``sources`` each engine's
  reading.
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
from parserx.render.markdown import semantic_block
from parserx.tools.context import ToolContext, ToolOutput, output
from parserx.tools.envelope import DocText, FailureCode, ToolFailure, Unresolved, UnresolvedKind
from parserx.tools.views import BlockView, DocInfo, ObservationView, block_view, doc_info, observation_view, \
    unresolved_items
from parserx.workspace.queries import HIDDEN, block_unit, ordered
from parserx.workspace.views import PageRow, page_rows

GLANCE = 120  # characters of a paragraph shown when skimming
FOLLOWING = 100  # characters of the text after a heading-like line (outline)
EXAMPLES = 3


class ReadDraftRequest(IRModel):
    view: Literal["summary", "issues", "text", "outline", "blocks"] = "summary"
    kinds: list[UnresolvedKind] = []  # issues: only these kinds
    page: int | None = None  # issues or text: one page
    start: str | None = None  # text: a block id to read from
    after: int = 40  # text: this block and the ones after it
    before: int = 0  # text: blocks before the start
    find: str | None = None  # text: blocks containing this phrase
    pattern: str | None = None  # text: blocks matching this regular expression
    cls: str | None = None  # text: every block of this style class (ids from the outline)
    full: bool = False  # text: whole paragraphs instead of their start
    blocks: list[str] = []  # blocks: which
    sources: bool = False  # blocks: each engine's reading of them

    @model_validator(mode="after")
    def _fits_the_view(self) -> "ReadDraftRequest":
        chosen = [n for n in ("find", "pattern", "cls") if getattr(self, n) is not None]
        if self.view == "text" and len(chosen) + (self.page is not None) > 1:
            raise ValueError("text: give at most one of page, find, pattern or cls")
        if self.view != "text" and (chosen or self.start is not None):
            raise ValueError(f"{', '.join(chosen) or 'start'} belongs to the text view")
        if self.view == "blocks" and not self.blocks:
            raise ValueError("blocks: name the blocks")
        if self.after < 0 or self.before < 0:
            raise ValueError("after and before count blocks: not negative")
        if self.find is not None and not _squash(self.find):
            raise ValueError("find needs a phrase")
        if self.pattern is not None:
            try:
                re.compile(self.pattern)
            except re.error as exc:
                raise ValueError(f"pattern is not a regular expression: {exc}") from exc
        return self


class DraftSummary(IRModel):
    doc: DocInfo
    pages: list[PageRow]
    blocks: dict[str, int]  # shown blocks by kind
    issues: dict[str, int]  # open issues by kind
    requests: dict[str, int]  # service requests made for the document so far
    usd: float | None


class DraftLine(IRModel):
    id: str
    role: str  # H1 … H6, text, list, table, figure, caption …
    page: int | None
    cls: str | None  # style class (see the outline); None for tables and images
    text: DocText | None  # shortened ones end with "…"
    row: int | None = None  # a table row that matched a search
    next: DocText | None = None  # outline: the start of the text after this line


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


class WordStyle(IRModel):
    name: str | None
    outline_level: int | None
    count: int


class WordNumbering(IRModel):
    num_id: str
    level: int
    count: int
    example: DocText | None


class ReadDraftResult(IRModel):
    view: str
    summary: DraftSummary | None = None
    issues: list[Unresolved] | None = None
    lines: list[DraftLine] | None = None
    classes: list[StyleClass] | None = None
    styles: list[WordStyle] | None = None  # outline, Word documents
    numbering: list[WordNumbering] | None = None  # outline, Word documents
    blocks: list[BlockView] | None = None
    sources: list[ObservationView] | None = None
    total_blocks: int | None = None  # text: shown blocks in the document
    before_id: str | None = None  # text: read back from here with before
    after_id: str | None = None  # text: read on from here with after


def run(ctx: ToolContext, req: ReadDraftRequest) -> ToolOutput[ReadDraftResult]:
    state = ctx.ws.load()
    if req.page is not None and all(p.n != req.page for p in state.pages):
        raise ToolFailure(FailureCode.NOT_FOUND, f"no page {req.page}", targets=[f"p{req.page}"])
    if req.view == "summary":
        return output(ReadDraftResult(view="summary", summary=_summary(state)))
    if req.view == "issues":
        return output(ReadDraftResult(view="issues", issues=_issues(state, req)))
    if req.view == "blocks":
        return output(_blocks(state, req))
    shown = [b for b in ordered(state) if b.status not in HIDDEN]
    classes = _classes(state, shown)
    if req.view == "outline":
        styles, numbering = _word_styles(state)
        return output(ReadDraftResult(view="outline", lines=_outline_lines(state, shown, classes), classes=classes.views,
                                      styles=styles, numbering=numbering, total_blocks=len(shown)))
    return output(_text(state, shown, classes, req))


# ── views ───────────────────────────────────────────────────────────────


def _summary(state: DocumentState) -> DraftSummary:
    shown = [b for b in state.blocks if b.status not in HIDDEN]
    return DraftSummary(doc=doc_info(state), pages=page_rows(state),
                        blocks=dict(sorted(Counter(b.kind.value for b in shown).items())),
                        issues=dict(sorted(Counter(u.kind.value for u in unresolved_items(state)).items())),
                        requests=dict(state.stats.requests), usd=state.stats.cost_usd)


def _issues(state: DocumentState, req: ReadDraftRequest) -> list[Unresolved]:
    items = unresolved_items(state)
    if req.kinds:
        items = [u for u in items if u.kind in set(req.kinds)]
    if req.page is not None:
        blocks = {b.id: b for b in state.blocks}
        items = [u for u in items if u.target == f"p{req.page}"
                 or (u.target in blocks and block_unit(state, blocks[u.target]) == req.page)]
    return items


def _blocks(state: DocumentState, req: ReadDraftRequest) -> ReadDraftResult:
    by_id = {b.id: b for b in state.blocks}
    unknown = [b for b in req.blocks if b not in by_id]
    if unknown:
        raise ToolFailure(FailureCode.NOT_FOUND, f"no blocks {unknown}", targets=unknown)
    chosen = [by_id[b] for b in req.blocks]
    sources = [observation_view(b, o, geometry=False) for b in chosen for o in b.observations] if req.sources else None
    return ReadDraftResult(view="blocks", blocks=[block_view(state, b, geometry=False) for b in chosen],
                           sources=sources)


def _text(state: DocumentState, shown: list[Block], classes: "_Classes", req: ReadDraftRequest) -> ReadDraftResult:
    width = None if req.full else GLANCE

    def lines(blocks: list[Block]) -> list[DraftLine]:
        return [_line(state, b, classes.of.get(b.id), width) for b in blocks]

    result = ReadDraftResult(view="text", total_blocks=len(shown))
    if req.page is not None:
        result.lines = lines([b for b in shown if block_unit(state, b) == req.page])
    elif req.find is not None or req.pattern is not None:
        result.lines = _search(state, shown, classes, req, width)
    elif req.cls is not None:
        if all(c.id != req.cls for c in classes.views):
            raise ToolFailure(FailureCode.NOT_FOUND, f"no style class {req.cls}", targets=[req.cls])
        result.lines = lines([b for b in shown if classes.of.get(b.id) == req.cls])
    else:
        at = 0
        if req.start is not None:
            at = next((i for i, b in enumerate(shown) if b.id == req.start), -1)
            if at < 0:
                raise ToolFailure(FailureCode.NOT_FOUND, f"no shown block {req.start}", targets=[req.start])
        lo, hi = max(0, at - req.before), min(len(shown), at + req.after)
        result.lines = lines(shown[lo:hi])
        result.before_id = shown[lo].id if lo > 0 else None
        result.after_id = shown[hi].id if hi < len(shown) else None
    return result


def _search(state: DocumentState, shown: list[Block], classes: "_Classes", req: ReadDraftRequest,
            width: int | None) -> list[DraftLine]:
    """Blocks whose text matches — for a table, each matching row; for a figure, its description."""
    if req.pattern is not None:
        regex = re.compile(req.pattern)
        matches = lambda text: regex.search(text) is not None  # noqa: E731
    else:
        needle = _squash(req.find)
        matches = lambda text: needle in _squash(text)  # noqa: E731
    found = []
    for block in shown:
        if block.cells is not None:
            for row, text in _rows(block):
                if matches(text):
                    found.append(DraftLine(id=block.id, role=_role(block), page=block_unit(state, block), cls=None,
                                           text=DocText(doc_text=_shorten(text, width)), row=row))
            continue
        text = _text_of(block).strip()
        if text and matches(text):
            line = _line(state, block, classes.of.get(block.id), None)
            if width is not None and len(text) > width:
                line.text = DocText(doc_text=_around(text, req, width))
            found.append(line)
    return found


# ── style classes and the outline ───────────────────────────────────────


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
    out = _Classes()
    for block in shown:
        if block.kind not in (BlockKind.TEXT, BlockKind.TITLE) or not _text_of(block).strip():
            continue
        style = native_style(block)
        text = _text_of(block).strip()
        numbering = numbering_signature(text)
        if style is not None:
            size = None if style.font_size is None else f"{style.font_size:g}pt"
            typography = " ".join(filter(None, (style.font, size, "bold" if style.bold else "regular")))
            out.evidence[block.id] = title_evidence(block, style, body)
        else:
            chosen = next((o for o in block.observations if o.id == block.chosen_observation), None)
            label = chosen.label if chosen is not None and chosen.label else "text"
            typography = f"{chosen.engine if chosen is not None else 'unknown'} label {label}"
            layout = {o.label for o in block.observations if o.task == TaskKind.LAYOUT}
            titled = label in ("paragraph_title", "doc_title") or (layout and layout <= {"paragraph_title", "doc_title"})
            out.evidence[block.id] = {"layout": label} if titled else {}
            if numbering is not None:
                out.evidence[block.id]["numbering"] = numbering
        keys.setdefault((typography, numbering), []).append(block)
    for i, ((typography, numbering), blocks) in enumerate(keys.items(), 1):
        cid = f"S{i}"
        roles: Counter[str] = Counter()
        kinds: Counter[str] = Counter()
        for block in blocks:
            out.of[block.id] = cid
            roles[_role(block)] += 1
            kinds.update(out.evidence[block.id].keys())
        pages = sorted({p for b in blocks if (p := block_unit(state, b)) is not None})
        out.views.append(StyleClass(
            id=cid, typography=typography, numbering=numbering, count=len(blocks),
            chars=int(median(len(_text_of(b).strip()) for b in blocks)),
            roles=dict(sorted(roles.items())), evidence=dict(sorted(kinds.items())), pages=_ranges(pages),
            examples=[DocText(doc_text=_shorten(_text_of(b).strip(), 60)) for b in blocks[:EXAMPLES]]))
    return out


def _outline_lines(state: DocumentState, shown: list[Block], classes: _Classes) -> list[DraftLine]:
    """Titles, and one-line text numbered or laid out like a title — with the start of what follows each.  Lines set
    apart only by their typography are not listed one by one (bold phrases and form labels are many): the class
    table shows them."""
    out = []
    for i, block in enumerate(shown):
        text = _text_of(block).strip()
        heading_like = block.kind == BlockKind.TITLE or (
            block.kind == BlockKind.TEXT and text and "\n" not in text
            and set(classes.evidence.get(block.id, ())) & {"numbering", "layout"})
        if not heading_like:
            continue
        line = _line(state, block, classes.of.get(block.id), GLANCE)
        following = next((_text_of(b).strip() for b in shown[i + 1:] if _text_of(b).strip()), "")
        if following:
            line.next = DocText(doc_text=_shorten(following, FOLLOWING))
        out.append(line)
    return out


def _word_styles(state: DocumentState) -> tuple[list[WordStyle] | None, list[WordNumbering] | None]:
    if state.format != "docx":
        return None, None
    styles: Counter[tuple[str | None, int | None]] = Counter()
    numbering: Counter[tuple[str, int]] = Counter()
    examples: dict[tuple[str, int], str] = {}
    for block in state.blocks:
        for obs in block.observations[:1]:
            if obs.style is None:
                continue
            styles[(obs.style.style_name, obs.style.outline_level)] += 1
            if obs.style.numbering is not None:
                key = (obs.style.numbering.num_id, obs.style.numbering.level)
                numbering[key] += 1
                examples.setdefault(key, (block.text or "")[:60])
    return ([WordStyle(name=n, outline_level=lv, count=c)
             for (n, lv), c in sorted(styles.items(), key=lambda kv: (-kv[1], str(kv[0])))],
            [WordNumbering(num_id=k[0], level=k[1], count=c,
                           example=DocText(doc_text=examples[k]) if examples.get(k) else None)
             for k, c in sorted(numbering.items())])


# ── lines ───────────────────────────────────────────────────────────────


def _line(state: DocumentState, block: Block, cls: str | None, width: int | None) -> DraftLine:
    text = _shorten(_text_of(block).strip(), width)
    return DraftLine(id=block.id, role=_role(block), page=block_unit(state, block), cls=cls,
                     text=DocText(doc_text=text) if text else None)


def _text_of(block: Block) -> str:
    if block.text:
        return block.text
    if block.cells is not None:  # a table: its size and first row
        return f"[table {block.cells.n_rows}×{block.cells.n_cols}] " + (_rows(block)[0][1] if block.cells.cells else "")
    if block.semantic is not None:
        return semantic_block(block)
    return ""


def _rows(block: Block) -> list[tuple[int, str]]:
    rows: dict[int, list] = {}
    for cell in block.cells.cells:
        rows.setdefault(cell.row, []).append(cell)
    return [(r, " | ".join(" ".join(c.content.split()) for c in sorted(cells, key=lambda c: c.col)))
            for r, cells in sorted(rows.items())]


def _role(block: Block) -> str:
    if block.kind == BlockKind.TITLE:
        return f"H{block.level}" if block.level else "title"
    return block.kind.value


def _shorten(text: str, width: int | None) -> str:
    return text if width is None or len(text) <= width else text[:width - 1] + "…"


def _around(text: str, req: ReadDraftRequest, width: int) -> str:
    """The text around the first match."""
    if req.pattern is not None:
        match = re.search(req.pattern, text)
        start = match.start() if match else 0
    else:
        positions = [i for i, c in enumerate(text) if not c.isspace()]  # where each non-space character is
        at = _squash(text).find(_squash(req.find))
        start = positions[at] if 0 <= at < len(positions) else 0  # normalisation may shift it a little
    lo = max(0, start - width // 3)
    return ("…" if lo else "") + text[lo:lo + width] + ("…" if lo + width < len(text) else "")


def _squash(text: str) -> str:
    return re.sub(r"\s+", "", unicodedata.normalize("NFKC", text)).casefold()


def _ranges(pages: list[int]) -> str:
    runs: list[list[int]] = []
    for p in pages:
        if runs and p == runs[-1][1] + 1:
            runs[-1][1] = p
        else:
            runs.append([p, p])
    return ", ".join(str(a) if a == b else f"{a}–{b}" for a, b in runs)
