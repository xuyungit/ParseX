"""V: a page's allocation into the workspace (experiment adapter; execution plan §7, common plan §6.2, §6.6).

The pipeline is not changed.  After ``workspace init`` has read the native PDF, each routed page's allocation
(``p0_contract``) replaces the blocks the native extraction made for that page's text lines; then the unchanged
``run_pipeline`` does what it does for every document — layout evidence, page reading, figure descriptions, titles,
paragraphs cut by a page break, lists, check — and ``export`` writes the Markdown.  The existing data model carries
it (review item IO of the audit: "P0 结果先独立存 JSON，通过后再定工作区的最小数据模型变更"):

- a text, title, list, caption, footnote or other block of the allocation → one block (TEXT, CAPTION, FOOTNOTE;
  titles are TEXT with the model's reading as a layout observation labelled ``paragraph_title``: the title step
  decides, on its two-kinds-of-evidence rule).  Its text is the parts joined as ``p0_contract.render`` joins them;
  a block made only of copied lines keeps the text layer as its engine (``native_pdf``) and the typography of the
  native block its lines came from, so the title step reads it as it reads any native paragraph;
- a formula block → a FORMULA block (LaTeX);
- a ``table`` part → the native extraction's TABLE block holding those lines, kept (with ``scripts``, cells that are
  one line get the line's script candidates in their Unicode forms); a written HTML table → a new TABLE block;
- a figure block → the native image placed there, kept; with no placed image under it (a drawn figure) → a FIGURE
  block cut from the page render;
- ``aside`` lines excluded → one EXCLUDED block per aside item; into a block → merged into it;
- written content no text line holds → a block with its own ledger item (``ocr_block``: read from the page image).

Every native text line of the page gets its new block in the ledger; the native text blocks become duplicates of
the new block that holds most of their lines (``duplicate_of``), so the accounts balance and nothing is deleted.
"""

from __future__ import annotations

import io
import re
from collections import Counter
from pathlib import Path

import pymupdf
from PIL import Image

import p0_contract as contract

from parserx.content.text import join_wrapped
from parserx.content.select import renumber
from parserx.ir import ids
from parserx.ir.anchor import AssetAnchor, PdfAnchor
from parserx.ir.block import Block
from parserx.ir.decision import Decision
from parserx.ir.enums import BlockKind, BlockStatus, DecisionStage, ObservationStatus, RelationKind, TaskKind
from parserx.ir.observation import Observation
from parserx.ir.relation import Relation
from parserx.ir.state import DocumentState, LedgerEntry
from parserx.tables.grid import TableGrid
from parserx.tools.recognize import _next_block_seq, _next_item
from parserx.workspace import Workspace
from parserx.workspace.queries import HIDDEN, block_unit

ACTOR = "program:vision_first.v"
ENGINE = "vision_allocation"
CHOICE = "vision_allocation"
_KINDS = {"title": BlockKind.TEXT, "text": BlockKind.TEXT, "list": BlockKind.TEXT, "other": BlockKind.TEXT,
          "caption": BlockKind.CAPTION, "footnote": BlockKind.FOOTNOTE, "formula": BlockKind.FORMULA}
_SUP = str.maketrans("0123456789+-=()ni", "⁰¹²³⁴⁵⁶⁷⁸⁹⁺⁻⁼⁽⁾ⁿⁱ")
_SUB = str.maketrans("0123456789+-=()aeoxhklmnpst", "₀₁₂₃₄₅₆₇₈₉₊₋₌₍₎ₐₑₒₓₕₖₗₘₙₚₛₜ")


def _union(boxes):
    return (min(b[0] for b in boxes), min(b[1] for b in boxes), max(b[2] for b in boxes), max(b[3] for b in boxes))


def _page_box(region: list[float], page: pymupdf.Page, dpi: int):
    """A region on the page render (pixels of the page as shown) → points of the unrotated page."""
    k = 72.0 / dpi
    rect = pymupdf.Rect(*(v * k for v in region)) * page.derotation_matrix
    return (round(rect.x0, 2), round(rect.y0, 2), round(rect.x1, 2), round(rect.y1, 2))


def _unicode_scripts(scripted: str) -> str:
    """``β<sub>1</sub>`` as ``β₁`` where every character has a Unicode form, else LaTeX in math."""
    def form(match: re.Match) -> str:
        kind, body = match.group(1), match.group(2)
        table = _SUP if kind == "sup" else _SUB
        converted = body.translate(table)
        if all(ch != c for ch, c in zip(converted, body)) or not body:
            return converted
        return f"${'^' if kind == 'sup' else '_'}{{{body}}}$"

    return re.sub(r"<(sup|sub)>(.*?)</\1>", form, scripted)


def apply(ws: Workspace, source: Path, n: int, page: dict, data: dict, *, model: str, dpi: int) -> dict:
    """Put *data* (a checked allocation of page *n*) into the workspace; returns counts."""
    lines = page["lines"]
    text_of = {f"L{k}": line["text"] for k, line in enumerate(lines, 1)}
    scripted_of = {f"L{k}": line.get("_scripted", line["text"]) for k, line in enumerate(lines, 1)}
    with pymupdf.open(source) as doc:
        pdf_page = doc[n - 1]
        render = pdf_page.get_pixmap(dpi=dpi).tobytes("png")
        counts: Counter = Counter()
        with ws.txn("tool:vision_first:allocate") as state:
            _apply(ws, state, n, page, data, text_of, scripted_of, pdf_page, render, dpi, model, counts)
    return dict(counts)


def _apply(ws, state: DocumentState, n: int, page: dict, data: dict, text_of, scripted_of, pdf_page, render: bytes,
           dpi: int, model: str, counts: Counter) -> None:
    items = {e.item: e for e in state.ledger}
    line_entry = {f"L{k}": items[ids.ledger_item_pdf(n, k)] for k in range(1, len(page["lines"]) + 1)}
    blocks = {b.id: b for b in state.blocks}
    owner = {name: e.block for name, e in line_entry.items()}  # native block of each line
    page_blocks = [b for b in state.blocks if block_unit(state, b) == n]
    base = min((b.order for b in page_blocks), default=len(state.blocks))
    tables = {b.id: b for b in page_blocks if b.kind == BlockKind.TABLE and b.status not in HIDDEN}
    table_lines = {tid: {name for name, bid in owner.items() if bid == tid} for tid in tables}
    figures = [b for b in page_blocks if b.kind == BlockKind.FIGURE and b.status not in HIDDEN]
    placed: list[Block] = []  # the page's blocks in the allocation's order
    line_block: dict[str, str] = {}  # line → the block that now carries it
    merged: dict[str, str] = {}
    excluded: dict[str, str] = {}
    used_figures: set[str] = set()
    decision = lambda reason, **evidence: Decision(  # noqa: E731
        stage=DecisionStage.CONTENT_SOURCE, choice=CHOICE, reason=reason, actor=ACTOR,
        evidence={"model": model, **evidence})

    def new_id() -> str:
        return ids.block_id_pdf(n, _next_block_seq(state, n))

    def box_of(names: list[str], region) -> tuple:
        boxes = [line_entry[x].source.bbox for x in names if x in line_entry]
        if region is not None:
            boxes.append(_page_box(region, pdf_page, dpi))
        return _union(boxes) if boxes else (0.0, 0.0, 1.0, 1.0)

    def add(block: Block) -> Block:
        state.blocks.append(block)
        placed.append(block)
        return block

    for item in data["blocks"]:
        parts = item["parts"]
        names_all = [x for p in parts for ref in p["lines"] for x in contract._names(ref) if x in text_of]
        region = item.get("region")
        kind = item["type"]
        if kind == "figure":
            box = box_of([], region) if region else box_of(names_all, None)
            native = next((f for f in figures if f.id not in used_figures and _overlaps(f.anchors[0].bbox, box)), None)
            if native is not None:
                used_figures.add(native.id)
                placed.append(native)
                figure_id = native.id
                counts["figure_native"] += 1
            else:
                figure_id = add(_drawn_figure(ws, state, n, new_id(), box, render, pdf_page, dpi, decision)).id
                counts["figure_drawn"] += 1
            for name in names_all:
                line_block[name] = figure_id
            _written_into(state, n, blocks, figure_id, parts)
            continue
        if kind == "table":
            for part in parts:
                refs = {x for ref in part["lines"] for x in contract._names(ref)}
                if part["kind"] == "table":
                    hits = [tid for tid, owned in table_lines.items() if refs & owned]
                    for tid in hits:
                        table = tables[tid]
                        if part.get("scripts"):
                            _script_cells(table, refs, page)
                        if table not in placed:
                            placed.append(table)
                        for name in refs:
                            line_block[name] = tid
                        counts["table_native"] += 1
                    if not hits:  # no grid there: the lines as a text block
                        block = add(_text_block(new_id(), n, BlockKind.TEXT, "\n".join(
                            text_of[x] for x in sorted(refs, key=lambda r: int(r[1:])) if x in text_of),
                            box_of(sorted(refs), None), None, decision("a table the extraction has no grid for")))
                        for name in refs:
                            line_block[name] = block.id
                        counts["table_as_text"] += 1
                elif part["kind"] == "write":
                    try:
                        grid = TableGrid.from_html(part["text"])
                    except ValueError:
                        grid = None
                    bid = new_id()
                    box = box_of(sorted(refs), region)
                    anchor = PdfAnchor(page=n, bbox=box, coord_space="page_pt")
                    obs = Observation(id=ids.observation_id(bid, "vlm", 1), engine="vlm", engine_version=model,
                                      task=TaskKind.RECOGNIZE, anchor=anchor, label=ENGINE,
                                      text=None if grid else part["text"], cells=grid, status=ObservationStatus.OK)
                    block = add(Block(id=bid, kind=BlockKind.TABLE if grid else BlockKind.TEXT, order=0,
                                      anchors=[anchor], observations=[obs], chosen_observation=obs.id,
                                      text="" if grid else part["text"], cells=grid,
                                      decisions=[decision("a table the service model wrote from the page image")]))
                    for name in refs:
                        line_block[name] = block.id
                    counts["table_written"] += 1
            continue
        # text-like and formula blocks
        pieces, copied_only = [], True
        for part in parts:
            names = [x for ref in part["lines"] for x in contract._names(ref) if x in text_of]
            if part["kind"] == "copy":
                source = scripted_of if part.get("scripts") else text_of
                pieces.append(join_wrapped([_unicode_scripts(source[x]) if part.get("scripts") else source[x]
                                            for x in names]))
            else:
                pieces.append(part["text"].strip())
                copied_only = False
        text = contract.visible("\n".join(p for p in pieces if p))
        if kind == "formula":
            body = text.strip()
            body = body[2:-2].strip() if body.startswith("$$") and body.endswith("$$") else body
            text = body
        bkind = _KINDS.get(kind, BlockKind.TEXT)
        native_style = _style_of(blocks, owner, names_all)
        marks = _marks_of(blocks, owner, names_all, text) if copied_only else []
        block = add(_text_block(new_id(), n, bkind, text, box_of(names_all, region), native_style,
                                decision(f"the page's {kind} block, as the service model allocated it",
                                         parts=len(parts), lines=len(names_all)),
                                engine="native_pdf" if copied_only and names_all else "vlm", model=model,
                                title=kind == "title",
                                layer_text="\n".join(text_of[x] for x in names_all) if names_all else None,
                                marks=marks))
        for name in names_all:
            line_block[name] = block.id
        if not names_all:  # content no text line holds: its own ledger item, read from the page image
            state.ledger.append(LedgerEntry(item=ids.ledger_item_pdf(n, _next_item(state, n)), unit="ocr_block",
                                            source=block.anchors[0], chars=len("".join(text.split())),
                                            disposition="output", block=block.id))
        counts[f"block_{kind}"] += 1

    for k, item in enumerate(data.get("aside", []), 1):
        names = [x for ref in item["lines"] for x in contract._names(ref) if x in text_of]
        if not names:
            continue
        target = None if item["to"] == contract.EXCLUDED else _placed_id(placed, data, item["to"])
        if target is None:  # excluded, or aside into a block that is not there: excluded, with the reason
            bid = new_id()
            anchor = PdfAnchor(page=n, bbox=box_of(names, None), coord_space="page_pt")
            text = "\n".join(text_of[x] for x in names)
            obs = Observation(id=ids.observation_id(bid, "native_pdf", 1), engine="native_pdf",
                              engine_version="text-layer", task=TaskKind.EXTRACT, anchor=anchor, text=text,
                              status=ObservationStatus.OK)
            state.blocks.append(Block(
                id=bid, kind=BlockKind.OTHER, order=base, status=BlockStatus.EXCLUDED, anchors=[anchor],
                observations=[obs], chosen_observation=obs.id, text=text,
                decisions=[Decision(stage=DecisionStage.EXCLUDE, choice="aside", actor=ACTOR,
                                    reason=f"the service model put these lines aside: {item['reason'][:120]}",
                                    evidence={"model": model, "lines": len(names)})]))
            for name in names:
                excluded[name] = bid
        else:
            for name in names:
                merged[name] = target
        counts["aside_lines"] += len(names)

    # the ledger: every line of the page to its new block
    for name, entry in line_entry.items():
        if name in line_block:
            entry.block, entry.disposition = line_block[name], "output"
        elif name in merged:
            entry.block, entry.disposition = merged[name], "merged"
        elif name in excluded:
            entry.block, entry.disposition = excluded[name], "excluded"
    # native text blocks become duplicates of the block that now holds most of their lines
    for bid in {owner[name] for name in line_entry if owner.get(name)}:
        native = blocks.get(bid)
        if native is None or native in placed:
            continue
        heirs = Counter(line_block.get(x) or merged.get(x) or excluded.get(x) for x, o in owner.items() if o == bid)
        heir = next((h for h, _ in heirs.most_common() if h), None)
        native.status = BlockStatus.DUPLICATE
        native.decisions.append(Decision(stage=DecisionStage.CONTENT_SOURCE, choice=CHOICE, actor=ACTOR,
                                         reason="the page is read by the service model's allocation (vision-first V)",
                                         evidence={"model": model}, refs=[heir] if heir else []))
        if heir:
            state.relations.append(Relation(id=ids.relation_id(RelationKind.DUPLICATE_OF, bid, heir),
                                            kind=RelationKind.DUPLICATE_OF, src=bid, dst=heir))
        counts["native_replaced"] += 1
    # the page's order: the allocation's; native figures it did not place keep their place after it
    for i, block in enumerate(placed + [f for f in figures if f.id not in used_figures]):
        block.order = base + i
    renumber(state)


def _placed_id(placed: list[Block], data: dict, to: str) -> str | None:
    """The workspace id of allocation block *to*."""
    order = [b["id"] for b in data["blocks"]]
    if to not in order:
        return None
    index = order.index(to)
    return placed[index].id if index < len(placed) else None


def _overlaps(a, b) -> bool:
    return min(a[2], b[2]) > max(a[0], b[0]) and min(a[3], b[3]) > max(a[1], b[1])


def _style_of(blocks: dict, owner: dict, names: list[str]):
    """The typography of the native block most of *names* came from (its text-layer observation's style)."""
    most = Counter(owner.get(x) for x in names).most_common(1)
    native = blocks.get(most[0][0]) if most else None
    if native is None:
        return None
    return next((o.style for o in native.observations if o.engine == "native_pdf" and o.style is not None), None)


def _marks_of(blocks: dict, owner: dict, names: list[str], text: str) -> list:
    """The inline marks (bold, underline) of the native blocks *names* came from, whose text the new block holds."""
    marks, seen = [], set()
    for bid in dict.fromkeys(owner.get(x) for x in names):
        native = blocks.get(bid)
        for obs in native.observations if native is not None else []:
            for mark in obs.marks:
                if (mark.kind, mark.text) not in seen and mark.text and mark.text in text:
                    seen.add((mark.kind, mark.text))
                    marks.append(mark)
    return marks


def _text_block(bid: str, n: int, kind: BlockKind, text: str, box, style, decision: Decision, *,
                engine: str = "vlm", model: str = "", title: bool = False, layer_text: str | None = None,
                marks: list | None = None) -> Block:
    """A text block; its chosen reading is the allocation's text.  A block the model wrote over text-layer lines
    also keeps the text layer's own reading of those lines — their characters and typography, facts the title step
    uses whoever rewrote the text."""
    anchor = PdfAnchor(page=n, bbox=box, coord_space="page_pt")
    obs = Observation(id=ids.observation_id(bid, engine, 1), engine=engine,
                      engine_version="text-layer" if engine == "native_pdf" else model,
                      task=TaskKind.EXTRACT if engine == "native_pdf" else TaskKind.RECOGNIZE, anchor=anchor,
                      label=ENGINE, text=text, style=style if engine == "native_pdf" else None,
                      marks=marks or [], status=ObservationStatus.OK)
    observations = [obs]
    if engine != "native_pdf" and layer_text:
        observations.append(Observation(id=ids.observation_id(bid, "native_pdf", 1), engine="native_pdf",
                                        engine_version="text-layer", task=TaskKind.EXTRACT, anchor=anchor,
                                        text=layer_text, style=style, status=ObservationStatus.OK))
    if title:  # the service model's reading of the block: one kind of evidence for the title step
        observations.append(Observation(id=ids.observation_id(bid, "vlm", 2), engine="vlm", engine_version=model,
                                        task=TaskKind.LAYOUT, anchor=anchor, label="paragraph_title",
                                        status=ObservationStatus.OK))
    return Block(id=bid, kind=kind, order=0, anchors=[anchor], observations=observations, chosen_observation=obs.id,
                 text=text, decisions=[decision])


def _drawn_figure(ws, state, n: int, bid: str, box, render: bytes, pdf_page, dpi: int, decision) -> Block:
    """A figure the page draws (no placed image): cut from the page render."""
    image = Image.open(io.BytesIO(render))
    shown = pymupdf.Rect(*box) * pdf_page.rotation_matrix
    k = dpi / 72.0
    crop_box = (max(0, int(shown.x0 * k)), max(0, int(shown.y0 * k)), min(image.width, int(round(shown.x1 * k))),
                min(image.height, int(round(shown.y1 * k))))
    region = image.crop(crop_box)
    buf = io.BytesIO()
    region.save(buf, "PNG")
    asset = ws.add_asset(buf.getvalue(), media_type="image/png", width=region.width, height=region.height,
                         role="crop", source=PdfAnchor(page=n, bbox=box, coord_space="page_pt"), dpi=float(dpi))
    if all(a.id != asset.id for a in state.assets):
        state.assets.append(asset)
    anchors = [PdfAnchor(page=n, bbox=box, coord_space="page_pt"),
               AssetAnchor(asset=asset.id, bbox=(0, 0, region.width, region.height),
                           image_size=(region.width, region.height))]
    state.ledger.append(LedgerEntry(item=ids.ledger_item_pdf(n, _next_item(state, n)), unit="pdf_image",
                                    source=anchors[0], chars=0, disposition="output", block=bid))
    return Block(id=bid, kind=BlockKind.FIGURE, order=0, anchors=anchors,
                 decisions=[decision("a figure the page draws, cut from the page render")])


def _written_into(state, n: int, blocks: dict, figure_id: str, parts: list[dict]) -> None:
    """Text the model read inside a figure: kept as its evidence, not as body text."""
    written = "\n".join(p["text"] for p in parts if p["kind"] == "write" and p["text"].strip())
    if not written:
        return
    block = next(b for b in state.blocks if b.id == figure_id)
    anchor = block.anchors[0]
    block.observations.append(Observation(
        id=ids.observation_id(figure_id, "vlm", 1 + sum(o.engine == "vlm" for o in block.observations)),
        engine="vlm", engine_version=ENGINE, task=TaskKind.RECOGNIZE, anchor=anchor, label=ENGINE,
        text=contract.visible(written), status=ObservationStatus.OK))


def _script_cells(table: Block, refs: set[str], page: dict) -> None:
    """Cells that are exactly one of *refs*' lines (spacing aside) written with that line's script candidates."""
    todo: dict[str, list[str]] = {}
    for k, line in enumerate(page["lines"], 1):
        if f"L{k}" in refs and "_scripted" in line:
            todo.setdefault("".join(line["text"].split()), []).append(_unicode_scripts(line["_scripted"]))
    if not todo or table.cells is None:
        return
    for cell in table.cells.cells:
        key = "".join(cell.content.split())
        if todo.get(key):
            cell.content = todo[key].pop(0)
