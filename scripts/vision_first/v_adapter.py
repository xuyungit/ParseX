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
- a ``table`` part → the native extraction's TABLE block holding those lines, kept (cells that are one line get the
  line's script candidates in their Unicode forms, contract v5; a ``cell`` part of the block writes one cell from the
  image, contract v6); a written HTML table → a new TABLE block;
- copied lines take their script candidates (contract v5); a written part that leaves out scripts the candidates of
  its lines have keeps the lines as the candidates write them as a candidate reading — a review item (Q143 ③);
- a formula whose LaTeX neither request gave → the engine reading whose numbers agree, else the lines (degraded);
- a figure block → the native image placed there, kept; with no placed image under it (a drawn figure) → a FIGURE
  block cut from the page render;
- ``aside`` lines excluded → one EXCLUDED block per aside item, except lines of a block the native extraction
  already marked as page furniture (running head, foot, page number): that block stays as it is, so its text goes
  into the page's marker like any other furniture (user 2026-09-30, plan B); into a block → merged into it;
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
import v_formulas

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
CANDIDATE_LABEL = "formula_reading"  # tools/formulas.CANDIDATE: a reading not adopted, listed for the agent
FORMULA_DONE = "formula_page"  # tools/formulas.DONE: a page whose formulas are decided
ENGINE = "vision_allocation"
CHOICE = "vision_allocation"
_KINDS = {"title": BlockKind.TEXT, "text": BlockKind.TEXT, "list": BlockKind.TEXT, "other": BlockKind.TEXT,
          "caption": BlockKind.CAPTION, "footnote": BlockKind.FOOTNOTE, "formula": BlockKind.FORMULA}
_SUP = str.maketrans("0123456789+-=()ni", "⁰¹²³⁴⁵⁶⁷⁸⁹⁺⁻⁼⁽⁾ⁿⁱ")
_SUB = str.maketrans("0123456789+-=()aeoxhklmnpst", "₀₁₂₃₄₅₆₇₈₉₊₋₌₍₎ₐₑₒₓₕₖₗₘₙₚₛₜ")


def learn_glyphs(pages: list[tuple[dict, dict]]) -> dict[str, str]:
    """Readings of the text layer's unmapped glyphs (private use, U+FFFD) from the document's allocations: where the
    service model rewrote a line holding one, the character it wrote in its place (the line and the writing aligned
    on their characters).  A glyph is read only when at least two rewrites agree and none disagrees; the rest keep
    the visible mark 〔?〕 (round 1: a hyphen the model wrote as "-" in 53 rewritten lines stayed 〔?〕 in 9 copied
    ones)."""
    from difflib import SequenceMatcher

    votes: dict[str, Counter] = {}
    for page, data in pages:
        lines = page["lines"]
        for block in data["blocks"]:
            if block["type"] == "formula":  # written as LaTeX: no character to align with
                continue
            for part in block["parts"]:
                if part["kind"] != "write" or not part["lines"] or not part["text"].strip():
                    continue
                names = [x for ref in part["lines"] for x in contract._names(ref) if 1 <= int(x[1:]) <= len(lines)]
                src = "".join("".join(lines[int(x[1:]) - 1]["text"].split()) for x in names)
                if not contract.unmapped(src):
                    continue
                dst = "".join(part["text"].split())
                for op, i0, i1, j0, j1 in SequenceMatcher(None, src, dst, autojunk=False).get_opcodes():
                    written = dst[j0:j1]
                    if op == "replace" and i1 - i0 == 1 and contract.unmapped(src[i0]) and 1 <= len(written) <= 2 \
                            and not contract.unmapped(written) and not set(written) & set("$\\{}〔〕?"):
                        votes.setdefault(src[i0], Counter())[written] += 1
    return {glyph: c.most_common(1)[0][0] for glyph, c in votes.items() if len(c) == 1 and sum(c.values()) >= 2}


def _joined(texts: list[str], style) -> str:
    """Copied lines as one block's text: wrapped prose joined; lines set in a monospaced face (code, a console's
    table) keep their line breaks."""
    return "\n".join(texts) if style is not None and style.monospace else join_wrapped(texts)


def _outside_formula(names: list[str], text_of: dict, latex: str, beside: frozenset = frozenset()) -> list[str]:
    """Lines of a formula block that are not the formula's (a paragraph number such as "[0020]" set beside it): counted
    over the block, a line whose letters and digits the LaTeX does not hold at all; and a line *beside* the
    detector's formula region (in its band, not on it) whose characters the LaTeX does not hold in one run — an
    equation number does (``\\tag{7}``), a paragraph number sharing digits with the formula does not."""
    import unicodedata

    from parserx.content.latex import characters

    def alnum(text: str) -> str:
        return "".join(ch for ch in unicodedata.normalize("NFKC", text) if ch.isalnum())

    held = alnum(characters(latex))
    lines = {x: Counter(alnum(text_of[x])) for x in names}
    surplus = sum(lines.values(), Counter()) - Counter(held)
    return [x for x in names if lines[x] and (not (lines[x] - surplus)
                                              or (x in beside and alnum(text_of[x]) not in held))]


def _formula_box(box, page: dict, pdf_page, dpi: int) -> tuple:
    """A written formula's area on the page: its lines, and the detector's formula region beside them (a formula set
    as an image beside its text-layer lines, ``_beside``)."""
    return _union([box] + _beside(box, page, pdf_page, dpi))


def _beside(box, page: dict, pdf_page, dpi: int) -> list[tuple]:
    """The detector's formula region beside *box* (``v_formulas.beside``), in points of the page."""
    return v_formulas.beside(box, [_page_box(r["box"], pdf_page, dpi) for r in page.get("regions") or []
                                   if r["label"] in v_formulas.FORMULA_LABELS])


def _set_apart(line, regions: list) -> bool:
    """A line in the band of one of the formula *regions* and wholly to its left or right (a paragraph number in the
    margin, an equation number) — not a line of the formula the region happens not to cover (a matrix's
    subscripts)."""
    return any(min(line[3], r[3]) - max(line[1], r[1]) > 0.5 * min(line[3] - line[1], r[3] - r[1]) for r in regions) \
        and not any(min(line[2], r[2]) > max(line[0], r[0]) for r in regions)


def _image_of(figure, formula) -> bool:
    """Whether a placed image is a picture of the formula at *formula*: at least half its height in the formula's
    band, and across overlapping it or beside it (a gap no wider than the formula's height: its number set beside
    the image, a detector's region narrower than the image)."""
    w = min(figure[2], formula[2]) - max(figure[0], formula[0])
    h = min(figure[3], formula[3]) - max(figure[1], formula[1])
    return h >= 0.5 * (figure[3] - figure[1]) > 0 and w >= -(formula[3] - formula[1])


def _runs(numbers: list[int]) -> list[list[int]]:
    runs: list[list[int]] = []
    for k in sorted(numbers):
        if runs and runs[-1][-1] == k - 1:
            runs[-1].append(k)
        else:
            runs.append([k])
    return runs


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


def apply(ws: Workspace, source: Path, n: int, page: dict, data: dict, *, model: str, dpi: int,
          defaults: bool = True, repeated: set[str] | None = None, glyphs: dict[str, str] | None = None) -> dict:
    """*repeated*: the furniture keys of the document's other pages (``p0_score.furniture_key``): with the defaults,
    a line put aside as excluded that no other page repeats stays in the output (information first) as a review
    item."""
    """Put *data* (a checked allocation of page *n*) into the workspace; returns counts.  *glyphs*: readings of the
    text layer's unmapped glyphs the service model gave where it rewrote their lines (``learn_glyphs``), used where
    their lines are copied."""
    lines = page["lines"]
    table = str.maketrans(glyphs or {})
    text_of = {f"L{k}": line["text"].translate(table) for k, line in enumerate(lines, 1)}
    scripted_of = {f"L{k}": line.get("_scripted", line["text"]).translate(table) for k, line in enumerate(lines, 1)}
    with pymupdf.open(source) as doc:
        pdf_page = doc[n - 1]
        render = pdf_page.get_pixmap(dpi=dpi).tobytes("png")
        counts: Counter = Counter()
        with ws.txn("tool:vision_first:allocate") as state:
            _apply(ws, state, n, page, data, text_of, scripted_of, pdf_page, render, dpi, model, counts, defaults,
                   repeated, table)
    # the transaction is claimed by a call record, as a tool's is: the workspace's integrity check (guide §7.3) then
    # tells a later change outside the tools (by the agent) from this step of the experiment
    ws.log_call({"tool": "vision_first_allocate", "request": {"page": n, "model": model},
                 "note": "experiment adapter (scripts/vision_first/v_adapter.py), before the agent"})
    return dict(counts)


def _apply(ws, state: DocumentState, n: int, page: dict, data: dict, text_of, scripted_of, pdf_page, render: bytes,
           dpi: int, model: str, counts: Counter, use_defaults: bool = True, repeated: set[str] | None = None,
           glyph_table: dict | None = None) -> None:
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
    formula_boxes: list[tuple[Block, tuple]] = []  # written formula blocks and the page area they cover
    line_block: dict[str, str] = {}  # line → the block that now carries it
    merged: dict[str, str] = {}
    excluded: dict[str, str] = {}
    used_figures: set[str] = set()
    decision = lambda reason, **evidence: Decision(  # noqa: E731
        stage=DecisionStage.CONTENT_SOURCE, choice=CHOICE, reason=reason, actor=ACTOR,
        evidence={"model": model, **evidence})
    # the page is decided: the pipeline's formula step (Q70) leaves it alone (it takes a page whose allocation failed)
    done = Decision(stage=DecisionStage.CONTENT_SOURCE, choice=FORMULA_DONE, actor=ACTOR, evidence={"by": CHOICE},
                    reason="the page's formulas are the service model's allocation (vision-first V)")

    def new_id() -> str:
        return ids.block_id_pdf(n, _next_block_seq(state, n))

    def box_of(names: list[str], region) -> tuple:
        boxes = [line_entry[x].source.bbox for x in names if x in line_entry]
        if region is not None:
            boxes.append(_page_box(region, pdf_page, dpi))
        return _union(boxes) if boxes else (0.0, 0.0, 1.0, 1.0)

    def add(block: Block) -> Block:
        block.decisions.append(done)
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
            written_cells = contract.cells_of(item, page)  # table index → (row, column) → text
            block_of_table = {i: t["block"] for i, t in enumerate(page.get("tables") or [])}
            for part in parts:
                refs = {x for ref in part["lines"] for x in contract._names(ref)}
                if part["kind"] == "table":
                    hits = [tid for tid, owned in table_lines.items() if refs & owned]
                    for tid in hits:
                        table = tables[tid]
                        _script_cells(table, refs, page)
                        index = next((i for i, b in block_of_table.items() if b == tid), None)
                        for cell in table.cells.cells if table.cells is not None else []:
                            text = written_cells.get(index, {}).get((cell.row, cell.col))
                            if text is not None:
                                cell.content = _unicode_scripts(text)
                                counts["table_cells_written"] += 1
                        for cell in table.cells.cells if table.cells is not None else []:  # a glyph read elsewhere:
                            cell.content = contract.visible(cell.content.translate(glyph_table or {}))
                        if table not in placed:
                            placed.append(table)
                        for name in refs:
                            line_block[name] = tid
                        counts["table_native"] += 1
                    if not hits:  # no grid there: the lines as a text block (a console's table set in code: code)
                        rows = sorted((x for x in refs if x in text_of), key=lambda r: int(r[1:]))
                        style = _style_of(blocks, owner, rows)
                        block = add(_text_block(new_id(), n, BlockKind.TEXT, "\n".join(text_of[x] for x in rows),
                            box_of(rows, None), style if style is not None and style.monospace else None,
                            decision("a table the extraction has no grid for"),
                            engine="native_pdf" if style is not None and style.monospace else "vlm", model=model))
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
                    if not refs:  # content no text line holds: its own ledger item, read from the page image
                        state.ledger.append(LedgerEntry(item=ids.ledger_item_pdf(n, _next_item(state, n)),
                                                        unit="ocr_block", source=anchor, chars=0, disposition="output",
                                                        block=block.id))
                    counts["table_written"] += 1
                elif part["kind"] == "copy":  # lines beside the table in its block (a paragraph number): their own
                    rows = sorted((x for x in refs if x in text_of), key=lambda r: int(r[1:]))
                    if rows:
                        style = _style_of(blocks, owner, rows)
                        block = add(_text_block(new_id(), n, BlockKind.TEXT, contract.visible(_joined(
                            [_unicode_scripts(scripted_of[x]) for x in rows], style)), box_of(rows, None), style,
                            decision("lines the service model put in a table block, outside its grid"),
                            engine="native_pdf", model=model))
                        for name in rows:
                            line_block[name] = block.id
                        counts["table_block_lines"] += 1
            continue
        # text-like and formula blocks
        pieces, copied_only, candidates, degraded, defaults = [], True, [], False, Counter()
        for part in parts:
            names = [x for ref in part["lines"] for x in contract._names(ref) if x in text_of]
            if part["kind"] == "copy":  # with the script candidates (contract v5); code keeps its lines
                pieces.append(_joined([_unicode_scripts(scripted_of[x]) for x in names], _style_of(blocks, owner, names)))
                continue
            written = part["text"].strip()
            original = "\n".join(text_of[x] for x in names)
            if kind != "formula" and names and _scripts_left_out(written, [page["lines"][int(x[1:]) - 1] for x in names]):
                candidates.append(join_wrapped([_unicode_scripts(scripted_of[x]) for x in names]))
                counts["scripts_left_out"] += 1
            if kind == "formula" and not written:  # neither request gave LaTeX: the engine, else the lines
                options = [("engine", t) for t in _engine_versions(page, names)] if names else []
                best = min(options, key=lambda o: _number_mismatch(o[1], original)) if options else ("text layer", original)
                pieces.append(best[1])
                candidates += [t for _w, t in options if t != best[1]]
                copied_only = False
                degraded = degraded or best[0] == "text layer" or _number_mismatch(best[1], original) > 0
                defaults["formula_engine" if best[0] == "engine" else "formula_text_layer"] += 1
                continue
            if not names or not use_defaults or not _numbers_differ(written, original):
                pieces.append(written)
                copied_only = False
            elif kind != "formula":  # prose: the text layer's lines stay; the written text waits for review
                pieces.append(join_wrapped([text_of[x] for x in names]))
                candidates.append(written)
                defaults["text_layer_kept"] += 1
            else:  # a formula: the engine reading whose numbers agree, else the version that disagrees least
                options = [("service model", written)] + [("engine", t) for t in _engine_versions(page, names)]
                best = min(options, key=lambda o: _number_mismatch(o[1], original))
                pieces.append(best[1])
                candidates += [t for _w, t in options if t != best[1]]
                copied_only = False
                degraded = degraded or _number_mismatch(best[1], original) > 0
                defaults["formula_engine" if best[0] == "engine" else "formula_kept"] += 1
                defaults["formula_degraded"] += int(_number_mismatch(best[1], original) > 0)
        counts.update(defaults)
        text = contract.visible("\n".join(p for p in pieces if p))
        if kind == "formula":
            body = text.strip()
            body = body[2:-2].strip() if body.startswith("$$") and body.endswith("$$") else body
            text = body
        place = None
        if kind == "formula" and names_all:
            regions = _beside(box_of(names_all, None), page, pdf_page, dpi)
            outside = _outside_formula(names_all, text_of, text, frozenset(
                x for x in names_all if x in line_entry and _set_apart(line_entry[x].source.bbox, regions)))
            if outside:
                if len(outside) == len(names_all) and region is None:  # the formula is the image beside its lines
                    lines_box = box_of(names_all, None)
                    place = _union(_beside(lines_box, page, pdf_page, dpi) or [lines_box])
                number = add(_text_block(new_id(), n, BlockKind.TEXT, contract.visible(join_wrapped(
                    [text_of[x] for x in outside])), box_of(outside, None), _style_of(blocks, owner, outside),
                    decision("lines the formula does not hold (a paragraph number beside it): their own block"),
                    engine="native_pdf", model=model))
                for name in outside:
                    line_block[name] = number.id
                names_all = [x for x in names_all if x not in outside]
                counts["formula_lines_apart"] += len(outside)
        bkind = _KINDS.get(kind, BlockKind.TEXT)
        native_style = _style_of(blocks, owner, names_all)
        marks = _marks_of(blocks, owner, names_all, text) if copied_only else []
        block = add(_text_block(new_id(), n, bkind, text, place or box_of(names_all, region), native_style,
                                decision(f"the page's {kind} block, as the service model allocated it",
                                         parts=len(parts), lines=len(names_all)),
                                engine="native_pdf" if copied_only and names_all else "vlm", model=model,
                                title=kind == "title",
                                layer_text="\n".join(text_of[x] for x in names_all) if names_all else None,
                                marks=marks))
        for candidate in candidates:  # a review item for the agent (the formula step's candidate kind)
            block.observations.append(Observation(
                id=ids.observation_id(block.id, "vlm", 10 + len(block.observations)), engine="vlm",
                engine_version=model, task=TaskKind.RECOGNIZE, anchor=block.anchors[0], label=CANDIDATE_LABEL,
                text=contract.visible(candidate), status=ObservationStatus.OK))
        if degraded:
            block.status = BlockStatus.DEGRADED
        if defaults:
            block.decisions.append(decision("conservative default without the agent (common plan §3.4): "
                                            + ", ".join(f"{k} {v}" for k, v in sorted(defaults.items())),
                                            **{k: v for k, v in defaults.items()}))
        if kind == "formula":
            formula_boxes.append((block, _formula_box(block.anchors[0].bbox, page, pdf_page, dpi)))
        for name in names_all:
            line_block[name] = block.id
        if not names_all:  # content no text line holds: its own ledger item, read from the page image
            state.ledger.append(LedgerEntry(item=ids.ledger_item_pdf(n, _next_item(state, n)), unit="ocr_block",
                                            source=block.anchors[0], chars=len("".join(text.split())),
                                            disposition="output", block=block.id))
        counts[f"block_{kind}"] += 1

    furniture = {bid for bid, b in blocks.items() if b.kind in (BlockKind.HEADER, BlockKind.FOOTER, BlockKind.PAGE_NUMBER)
                 and b.status == BlockStatus.EXCLUDED}
    kept_furniture: set[str] = set()
    for k, item in enumerate(data.get("aside", []), 1):
        names = [x for ref in item["lines"] for x in contract._names(ref) if x in text_of]
        if item["to"] == contract.EXCLUDED:  # the native extraction's furniture stays as it is (plan B)
            kept_furniture |= {owner[x] for x in names if owner.get(x) in furniture}
            names = [x for x in names if owner.get(x) not in furniture]
        if use_defaults and repeated is not None and item["to"] == contract.EXCLUDED:
            from p0_score import furniture_key

            kept = [x for x in names if furniture_key(text_of[x]) and furniture_key(text_of[x]) not in repeated]
            if kept:  # not page furniture by the document's own evidence: output, and listed for review
                kept = sorted(kept, key=lambda x: (page["lines"][int(x[1:]) - 1]["box"][1] // 4,
                                                  page["lines"][int(x[1:]) - 1]["box"][0]))
                block = _text_block(new_id(), n, BlockKind.TEXT, contract.visible(join_wrapped(
                    [text_of[x] for x in kept])), box_of(kept, None), _style_of(blocks, owner, kept),
                    decision("put aside as excluded, but no other page repeats it: kept (conservative default)",
                             lines=len(kept)), engine="native_pdf", model=model)
                block.decisions.append(done)
                block.observations.append(Observation(
                    id=ids.observation_id(block.id, "vlm", 10), engine="vlm", engine_version=model,
                    task=TaskKind.RECOGNIZE, anchor=block.anchors[0], label=CANDIDATE_LABEL,
                    text=f"（服务模型把这几行排除了：{item['reason'][:80]}）", status=ObservationStatus.OK))
                state.blocks.append(block)
                placed.append(block)
                for name in kept:
                    line_block[name] = block.id
                counts["aside_kept"] += len(kept)
                names = [x for x in names if x not in kept]
        if not names:
            continue
        target = None if item["to"] == contract.EXCLUDED else _placed_id(placed, data, item["to"])
        if target is None:  # excluded, or aside into a block that is not there: excluded, with the reason
            # in the order the page shows them (the PDF may hold a page number "81" as the lines "1", "8")
            names = sorted(names, key=lambda x: (page["lines"][int(x[1:]) - 1]["box"][1] // 4,
                                                 page["lines"][int(x[1:]) - 1]["box"][0]))
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

    # a placed image inside a formula the allocation wrote is that formula (a formula set as an image): withdrawn
    for figure in figures:
        if figure.id in used_figures or figure.status in HIDDEN or not isinstance(figure.anchors[0], PdfAnchor):
            continue
        holder = next((f for f, box in formula_boxes if _image_of(figure.anchors[0].bbox, box)), None)
        if holder is None:
            continue
        figure.status = BlockStatus.DUPLICATE
        figure.decisions.append(Decision(stage=DecisionStage.CONTENT_SOURCE, choice=CHOICE, actor=ACTOR, refs=[holder.id],
                                         reason="an image of the formula the service model wrote (the formula block)",
                                         evidence={"model": model}))
        state.relations.append(Relation(id=ids.relation_id(RelationKind.DUPLICATE_OF, figure.id, holder.id),
                                        kind=RelationKind.DUPLICATE_OF, src=figure.id, dst=holder.id))
        for entry in state.ledger:
            if entry.block == figure.id:
                entry.block, entry.disposition = holder.id, "output"
        used_figures.add(figure.id)
        counts["formula_images_withdrawn"] += 1
    # every line has a block: one the allocation left without (an adapter gap) is copied back at its place
    unplaced = [x for x in line_entry if x not in line_block and x not in merged and x not in excluded
                and owner.get(x) not in kept_furniture]
    for run in _runs([int(x[1:]) for x in unplaced]):
        rows = [f"L{k}" for k in run]
        before = max((k for k in range(1, run[0]) if f"L{k}" in line_block), default=None)
        block = _text_block(new_id(), n, BlockKind.TEXT, contract.visible(join_wrapped([text_of[x] for x in rows])),
                            box_of(rows, None), _style_of(blocks, owner, rows),
                            decision("lines the allocation left without a block: copied back at their place"),
                            engine="native_pdf", model=model)
        block.decisions.append(done)
        state.blocks.append(block)
        holder = next((b for b in placed if b.id == line_block.get(f"L{before}")), None) if before else None
        placed.insert(placed.index(holder) + 1 if holder in placed else 0, block)
        for name in rows:
            line_block[name] = block.id
        counts["lines_copied_back"] += len(rows)
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
        if native is None or native in placed or bid in kept_furniture:
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


_DECIMAL = re.compile(r"(\d)\s*([.．])\s*(\d)")  # "0. 60": the text layer spaces a decimal point
_TAG = re.compile(r"<[^<>]+>")
_SPACED = re.compile(r"(\\(?!begin|end)[a-zA-Z]+)(?![a-zA-Z])")


_HTML_SCRIPT = re.compile(r"<(?:sup|sub)>(.*?)</(?:sup|sub)>")
_UNICODE_SCRIPTS = re.compile("[" + "".join(sorted(set("⁰¹²³⁴⁵⁶⁷⁸⁹⁺⁻⁼⁽⁾ⁿⁱ₀₁₂₃₄₅₆₇₈₉₊₋₌₍₎ₐₑₒₓₕₖₗₘₙₚₛₜ"))) + "]+")


def _scripts_left_out(written: str, lines: list[dict]) -> bool:
    """Characters the script candidates of *lines* hold that the written text holds in no script (HTML, LaTeX or
    Unicode; compared as characters — a candidate split over two text-layer lines is one script in the writing —
    NFKC, primes as one mark)."""
    import unicodedata

    from parserx.content.latex import characters

    def chars(text: str) -> Counter:
        shown = characters(text.replace("\\prime", "′"))
        return Counter(unicodedata.normalize("NFKC", shown).replace("'", "′").replace(" ", ""))

    wanted = sum((chars(c["t"]) for line in lines for c in line.get("scripts", ())), Counter())
    if not wanted:
        return False
    scripts = [m.group(1) for m in _HTML_SCRIPT.finditer(written)] + _latex_scripts(written)
    found = sum((chars(t) for t in scripts), Counter())
    found += sum((chars(m.group(0)) for m in _UNICODE_SCRIPTS.finditer(written)), Counter())
    return bool(wanted - found)


def _latex_scripts(text: str) -> list[str]:
    """The contents of the outermost ``_`` / ``^`` scripts of LaTeX: a brace group, a command, or one character."""
    out, i = [], 0
    while i < len(text):
        if text[i] == "\\":
            i += 2
            continue
        if text[i] not in "_^" or i + 1 >= len(text):
            i += 1
            continue
        j = i + 1
        if text[j] == "{":
            depth, k = 0, j
            while k < len(text):
                depth += {"{": 1, "}": -1}.get(text[k], 0)
                k += 2 if text[k] == "\\" else 1
                if depth == 0:
                    break
            out.append(text[j + 1:k - 1])
            i = k
        elif text[j] == "\\":
            m = re.match(r"\\[A-Za-z]+", text[j:])
            out.append(m.group(0) if m else text[j:j + 2])
            i = j + (len(m.group(0)) if m else 2)
        else:
            out.append(text[j])
            i = j + 1
    return out


def _numbers(text: str) -> Counter:
    """Digit runs as the characters show them (LaTeX markup and HTML tags aside, NFKC)."""
    import unicodedata

    from parserx.content.latex import characters

    shown = characters(_SPACED.sub(r"\1 ", _TAG.sub(" ", text)))
    shown = _DECIMAL.sub(r"\1.\3", unicodedata.normalize("NFKC", shown))
    return Counter(re.findall(r"\d+(?:\.\d+)?", shown))


def _number_mismatch(text: str, original: str) -> int:
    a, b = _numbers(text), _numbers(original)
    return sum(((a - b) + (b - a)).values())


def _numbers_differ(text: str, original: str) -> bool:
    return _number_mismatch(text, original) > 0


def _engine_versions(page: dict, names: list[str]) -> list[str]:
    """The engine's formula readings over these lines (with the equation number it reads beside them)."""
    boxes = [page["lines"][int(x[1:]) - 1]["box"] for x in names]
    area = _union(boxes)
    over = [e for e in page.get("engine") or [] if _overlaps(e["box"], area)]
    formulas = [e["text"].strip() for e in over if e["label"] in ("display_formula", "formula")]
    numbers = [e["text"].strip().strip("()（）") for e in over if e["label"] == "formula_number"]
    out = []
    for text in formulas:
        body = text[2:-2].strip() if text.startswith("$$") and text.endswith("$$") else text
        out.append(body + (f" \\tag{{{numbers[0]}}}" if numbers and "\\tag" not in body else ""))
    if len(formulas) > 1:
        out.append(" \\\\ ".join(o for o in out))
    return out


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
    # the crop is cut from the page render, which the workspace keeps too (an asset's provenance, ir/asset.py)
    whole = pymupdf.Rect(pdf_page.rect) * pdf_page.derotation_matrix
    page_render = ws.add_asset(render, media_type="image/png", width=image.width, height=image.height, role="render",
                               source=PdfAnchor(page=n, bbox=(round(whole.x0, 2), round(whole.y0, 2),
                                                              round(whole.x1, 2), round(whole.y1, 2)),
                                                coord_space="page_pt"), dpi=float(dpi))
    asset = ws.add_asset(buf.getvalue(), media_type="image/png", width=region.width, height=region.height,
                         role="crop", derived_from=page_render.id,
                         transform=(1.0, 0.0, 0.0, 1.0, float(crop_box[0]), float(crop_box[1])),
                         source=PdfAnchor(page=n, bbox=box, coord_space="page_pt"))
    for new in (page_render, asset):
        if all(a.id != new.id for a in state.assets):
            state.assets.append(new)
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
