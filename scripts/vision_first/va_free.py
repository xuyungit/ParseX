"""V-a: the service model writes the page freely (common plan §6.2, the control of complete allocation).

Same materials as the allocation (page image, text layer line by line with its facts, engine entries, detector
regions), no line references: the answer is the page's Markdown (``{"markdown": …}``).  Checked for JSON, schema,
emptiness, balanced LaTeX and unmapped code points; one retry with the problems.  Into the workspace (``apply``):
the Markdown's blocks (blank-line separated; ``#`` titles, ``$$`` formulas, HTML or pipe tables, the rest text)
replace the page's native text blocks, as V's do; each text-layer line is accounted to the new block that holds
most of its characters (there is no allocation to say which).
"""

from __future__ import annotations

import json
import re
from collections import Counter
from pathlib import Path

import pymupdf
from jsonschema import Draft202012Validator

import p0_contract as contract
import v_adapter

from parserx.content.select import renumber
from parserx.ir import ids
from parserx.ir.anchor import PdfAnchor
from parserx.ir.block import Block
from parserx.ir.decision import Decision
from parserx.ir.enums import BlockKind, BlockStatus, DecisionStage, ObservationStatus, RelationKind, TaskKind
from parserx.ir.observation import Observation
from parserx.ir.relation import Relation
from parserx.reading.compare import normalize
from parserx.tables.grid import TableGrid
from parserx.tools.recognize import _next_block_seq
from parserx.workspace.queries import HIDDEN, block_unit

SCHEMA = {"type": "object", "additionalProperties": False, "required": ["markdown"],
          "properties": {"markdown": {"type": "string"}}}
_VALIDATOR = Draft202012Validator(SCHEMA)

INSTRUCTIONS = """你在整理文档的一页，写成供大模型阅读的 Markdown。看页面图，照图写出这一页的全部内容，按阅读顺序：标题用 #（按版面估计层级），正文段落之间空一行，行内公式 $…$，行间公式 $$…$$（公式编号保留，可写 \\tag{{n}}），表格用 HTML，图写成 ![图](figure)。PDF 文字层（L1、L2……）与扫描引擎条目只作参考：字符以文字层为准，结构以图为准。照图写，不总结、不翻译、不补全；各页重复的页眉、页脚、页码不写；看不清的地方写 〔?〕。

给你的材料：页面图（宽 {width}、高 {height} 像素，下面的框都是它的像素坐标）；文字层逐行（框、字号、字体、原文，runs 是字号或基线不同的片段，odd 是映射不到可读字符的字形，scripts 是只按字号与基线推测的上下标候选）；{engine}版面检测区域（只作提示）。

只输出一个 JSON 对象：{{"markdown": "这一页的 Markdown"}}。"""


def prompt(page: dict) -> str:
    return INSTRUCTIONS.format(width=page["image"]["width"], height=page["image"]["height"],
                               engine="扫描引擎的读数条目（含公式的片段）；" if page.get("engine") else "")


def check(text: str) -> contract.Checked:
    if not (text or "").strip():
        return contract.Checked("empty", problems=["回答是空的"])
    try:
        data = contract.parse(text)
    except (json.JSONDecodeError, ValueError) as exc:
        return contract.Checked("unparseable", problems=[f"不是合法的 JSON：{exc}"])
    errors = list(_VALIDATOR.iter_errors(data))
    if errors:
        return contract.Checked("schema", data=data if isinstance(data, dict) else None,
                                problems=[f"不符合 schema：{e.message}" for e in errors[:10]])
    problems = []
    if not data["markdown"].strip():
        problems.append("markdown 是空的")
    problems += [f"markdown：{p}" for p in contract.latex_problems(data["markdown"])]
    if contract.unmapped(data["markdown"]):
        problems.append(f"写进了映射不到的字符：照图写出，看不清写 {contract.UNREADABLE}")
    return contract.Checked("invariants" if problems else "valid", data=data, problems=problems)


_TABLE = re.compile(r"^\s*(<table\b.*</table>|\|.*\|)\s*$", re.S | re.I)
_FIGURE = re.compile(r"^\s*!\[[^\]]*\]\([^)]*\)\s*$")


def blocks_of(markdown: str) -> list[tuple[str, str, int | None]]:
    """(kind, text, level) for each block of the Markdown."""
    out = []
    from parserx.eval.pages import markdown_blocks

    for chunk in markdown_blocks(markdown):
        text = chunk.strip()
        if not text:
            continue
        heading = re.match(r"^(#{1,6})\s+(.*)$", text, re.S)
        if heading:
            out.append(("title", heading.group(2).strip(), len(heading.group(1))))
        elif text.startswith("$$") and text.endswith("$$"):
            out.append(("formula", text[2:-2].strip(), None))
        elif _TABLE.match(text):
            out.append(("table", text, None))
        elif _FIGURE.match(text):
            out.append(("figure", text, None))
        else:
            out.append(("text", text, None))
    return out


def apply(ws, source: Path, n: int, page: dict, markdown: str, *, model: str) -> dict:
    lines = page["lines"]
    text_of = {f"L{k}": line["text"] for k, line in enumerate(lines, 1)}
    counts: Counter = Counter()
    with ws.txn("tool:vision_first:free") as state:
        items = {e.item: e for e in state.ledger}
        line_entry = {name: items[ids.ledger_item_pdf(n, int(name[1:]))] for name in text_of}
        blocks = {b.id: b for b in state.blocks}
        owner = {name: e.block for name, e in line_entry.items()}
        page_blocks = [b for b in state.blocks if block_unit(state, b) == n]
        base = min((b.order for b in page_blocks), default=len(state.blocks))
        figures = [b for b in page_blocks if b.kind == BlockKind.FIGURE and b.status not in HIDDEN]
        whole = _page_box(page_blocks)
        new: list[Block] = []
        for kind, text, level in blocks_of(markdown):
            if kind == "figure":
                continue  # the page's placed images stay; a drawn figure is not cut in the control
            bid = ids.block_id_pdf(n, _next_block_seq(state, n))
            anchor = PdfAnchor(page=n, bbox=whole, coord_space="page_pt")
            grid = None
            if kind == "table":
                try:
                    grid = TableGrid.from_html(text) if text.lstrip().startswith("<") else None
                except ValueError:
                    grid = None
            obs = Observation(id=ids.observation_id(bid, "vlm", 1), engine="vlm", engine_version=model,
                              task=TaskKind.RECOGNIZE, anchor=anchor, label="vision_free",
                              text=None if grid else contract.visible(text), cells=grid, status=ObservationStatus.OK)
            observations = [obs]
            if kind == "title":
                observations.append(Observation(id=ids.observation_id(bid, "vlm", 2), engine="vlm",
                                                engine_version=model, task=TaskKind.LAYOUT, anchor=anchor,
                                                label="paragraph_title", status=ObservationStatus.OK))
            block = Block(id=bid, kind=BlockKind.TABLE if grid else (BlockKind.FORMULA if kind == "formula"
                                                                        else BlockKind.TEXT),
                          order=0, anchors=[anchor], observations=observations, chosen_observation=obs.id,
                          text="" if grid else contract.visible(text), cells=grid,
                          decisions=[Decision(stage=DecisionStage.CONTENT_SOURCE, choice="vision_free", actor=v_adapter.ACTOR,
                                              reason="the page as the service model wrote it freely (V-a)",
                                              evidence={"model": model}),
                                     Decision(stage=DecisionStage.CONTENT_SOURCE, choice=v_adapter.FORMULA_DONE,
                                              actor=v_adapter.ACTOR, evidence={"by": "vision_free"},
                                              reason="the page's formulas are the service model's (V-a)")])
            state.blocks.append(block)
            new.append(block)
            counts[f"block_{kind}"] += 1
        # each line to the new block holding most of its characters
        for name, entry in line_entry.items():
            chars = Counter(normalize(text_of[name]))
            best = max(new, key=lambda b: sum((chars & Counter(normalize(b.text or _grid_text(b)))).values()),
                       default=None)
            if best is not None:
                entry.block, entry.disposition = best.id, "output"
        # line boxes and typography for the blocks that got lines (the title step's evidence, as in V)
        for block in new:
            names = [x for x, e in line_entry.items() if e.block == block.id]
            if names:
                box = v_adapter._union([line_entry[x].source.bbox for x in names])
                block.anchors = [PdfAnchor(page=n, bbox=box, coord_space="page_pt")]
                style = v_adapter._style_of(blocks, owner, names)
                if block.kind == BlockKind.TEXT:
                    block.observations.append(Observation(
                        id=ids.observation_id(block.id, "native_pdf", 1), engine="native_pdf", engine_version="text-layer",
                        task=TaskKind.EXTRACT, anchor=block.anchors[0], text="\n".join(text_of[x] for x in names),
                        style=style, status=ObservationStatus.OK))
        for bid in {o for o in owner.values() if o}:
            native = blocks.get(bid)
            if native is None or native.kind == BlockKind.FIGURE:
                continue
            heirs = Counter(line_entry[x].block for x, o in owner.items() if o == bid)
            heir = heirs.most_common(1)[0][0] if heirs else None
            native.status = BlockStatus.DUPLICATE
            if heir:
                state.relations.append(Relation(id=ids.relation_id(RelationKind.DUPLICATE_OF, bid, heir),
                                                kind=RelationKind.DUPLICATE_OF, src=bid, dst=heir))
        for i, block in enumerate(new + figures):
            block.order = base + i
        renumber(state)
    return dict(counts)


def _page_box(blocks: list[Block]):
    boxes = [b.anchors[0].bbox for b in blocks if isinstance(b.anchors[0], PdfAnchor)]
    return v_adapter._union(boxes) if boxes else (0.0, 0.0, 1.0, 1.0)


def _grid_text(block: Block) -> str:
    return " ".join(c.content for c in block.cells.cells) if block.cells is not None else ""
