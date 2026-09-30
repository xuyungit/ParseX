"""V: a scanned page's allocation into the workspace (experiment adapter; docs/v2_vision_first_scanned.md §2, §7).

After ``recognize`` has read the page, its scan-engine blocks are the units the service model allocated
(``s0_inputs.workspace_units``: ``K1`` … is a workspace block).  The allocation changes only what the model changed;
then the unchanged ``run_pipeline`` does the rest of its steps and ``export`` writes the Markdown:

- a block that copies one engine block → that block, kept as it is (its reading, label and title evidence);
- several engine blocks copied into one paragraph → the first takes the joined text, the others become duplicates
  of it (``duplicate_of``; their ledger items move to it), as the native adapter does with native blocks;
- a write → the first engine block it replaces takes the written text as its chosen reading (the engine's reading
  stays as an observation; a formula block becomes FORMULA), the others duplicates of it;
- a ``table`` part → the engine's table block, kept; its ``cell`` parts write cells; a written HTML table → the
  block's written grid;
- a figure → the engine's image blocks, kept (a figure the engine did not cut: cut from the page render); text
  written inside it → its evidence;
- information first (as the native adapter's defaults): an engine text block the model put into a figure, set aside
  into a block, or excluded although the engine did not take it for page furniture and no other page repeats it,
  stays in the output where it was, the model's judgement recorded;
- written content no engine block holds → a block with its own ledger item (``ocr_block``).

Signals, not reverts (user 2026-09-30): a write whose new numbers, or new letters and digits, neither reading holds
— the engine's reading of the page, the local reading within the replaced blocks' boxes — keeps the written text
and lists the engine's reading for the agent (``reading_disagreement``); so does a write that drops engine text the page's
output no longer holds.  Notation (spacing, LaTeX markup, script form, full or half width) is not compared.
"""

from __future__ import annotations

import difflib
import re
import unicodedata
from collections import Counter
from pathlib import Path

import pymupdf

import p0_contract as contract
import v_adapter
from p0_score import _shown, furniture_key, numbers

from parserx.content.furniture import _copies_follow
from parserx.content.select import renumber
from parserx.content.text import join_wrapped
from parserx.ir import ids
from parserx.ir.anchor import PdfAnchor
from parserx.ir.block import Block
from parserx.ir.decision import Decision
from parserx.ir.enums import BlockKind, BlockStatus, DecisionStage, ObservationStatus, RelationKind, TaskKind
from parserx.ir.observation import Observation
from parserx.ir.relation import Relation
from parserx.ir.state import DocumentState, LedgerEntry
from parserx.tables.grid import TableGrid
from parserx.tools.recognize import _next_block_seq, _next_item
from parserx.tools.views import REWRITE_CANDIDATE
from parserx.workspace import Workspace
from parserx.workspace.queries import block_unit

ACTOR = "program:vision_first.v_scan"
CHOICE = v_adapter.CHOICE
ENGINE = v_adapter.ENGINE
FIGURE_KINDS = frozenset({BlockKind.FIGURE})
FURNITURE = frozenset({BlockKind.HEADER, BlockKind.FOOTER, BlockKind.PAGE_NUMBER})


def apply(ws: Workspace, source: Path, n: int, page: dict, data: dict, *, model: str, dpi: int) -> dict:
    """Put *data* (a checked allocation of scanned page *n*) into the workspace; returns counts."""
    with pymupdf.open(source) as doc:
        pdf_page = doc[n - 1]
        render = pdf_page.get_pixmap(dpi=dpi).tobytes("png")
        counts: Counter = Counter()
        with ws.txn("tool:vision_first:allocate_scan") as state:
            _apply(ws, state, n, page, data, pdf_page, render, dpi, model, counts)
    ws.log_call({"tool": "vision_first_allocate", "request": {"page": n, "model": model, "units": "scan engine blocks"},
                 "note": "experiment adapter (scripts/vision_first/s_adapter.py), before the agent"})
    return dict(counts)


def norm(text: str) -> str:
    """Letters and digits as characters show them: notation (LaTeX markup, script form, width, spacing) aside."""
    return re.sub(r"[\s$\\{}^_]", "", unicodedata.normalize("NFKC", _shown(text or ""))).lower()


def _overlaps(a, b) -> bool:
    return a[0] < b[2] and b[0] < a[2] and a[1] < b[3] and b[1] < a[3]


def local_in(page: dict, boxes: list) -> str:
    return "\n".join(line["text"] for line in page.get("local") or [] if any(_overlaps(line["box"], b) for b in boxes))


def disagreements(written: str, original: str, local: str, engine_page: str, output: str) -> dict[str, list[str]]:
    """What a write changed that no reading holds: numbers new to the replaced blocks that neither the engine's page
    reading nor the local reading within them has; changed letters and digits (with two characters of context) the
    same; engine text it dropped that the page's output does not hold."""
    have = numbers(local) + numbers(engine_page)
    out = {"numbers": sorted(n for n in numbers(written) - numbers(original) if not have[n]), "text": [], "lost": []}
    a, b = norm(original), norm(written)
    local_n, engine_n, output_n = norm(local), norm(engine_page), norm(output)
    for op, a0, a1, b0, b1 in difflib.SequenceMatcher(None, a, b, autojunk=False).get_opcodes():
        if op == "equal":
            continue
        if b1 > b0:
            segment = b[max(0, b0 - 2):b1 + 2]
            if segment not in local_n and segment not in engine_n:
                out["text"].append(segment)
        if a1 > a0:
            gone = a[a0:a1]
            if len(gone) >= 2 and gone not in output_n:
                out["lost"].append(gone)
    return out


def _apply(ws, state: DocumentState, n: int, page: dict, data: dict, pdf_page, render: bytes, dpi: int,
           model: str, counts: Counter) -> None:
    units = {u["id"]: u for u in page["lines"]}
    by_id = {b.id: b for b in state.blocks}
    block_of = {uid: by_id[u["_block"]] for uid, u in units.items() if u["_block"] in by_id}
    entry_of = {e.block: e for e in state.ledger if e.block in {b.id for b in block_of.values()}}
    page_blocks = [b for b in state.blocks if block_unit(state, b) == n]
    base = min((b.order for b in page_blocks), default=len(state.blocks))
    others = set().union(*({furniture_key(b.text or "")} for b in state.blocks
                           if block_unit(state, b) not in (n, None) and b.text))
    engine_page = "\n".join(u["text"] for u in units.values())
    output = contract.render(data, page)
    placed: list[Block] = []
    handled: set[str] = set()
    excluded: list[str] = []

    def decision(reason: str, **evidence) -> Decision:
        return Decision(stage=DecisionStage.CONTENT_SOURCE, choice=CHOICE, reason=reason, actor=ACTOR,
                        evidence={"model": model, **evidence})

    def names(part: dict) -> list[str]:
        return [x for ref in part["lines"] for x in contract._names(ref, "K") if x in block_of]

    def place(block: Block) -> None:
        if block not in placed:
            placed.append(block)

    def absorb(heir: Block, uids: list[str], why: str) -> None:
        """The other engine blocks of a part: duplicates of *heir*, their ledger items moved to it."""
        for uid in uids:
            block = block_of[uid]
            handled.add(uid)
            if block is heir:
                continue
            block.status = BlockStatus.DUPLICATE
            block.decisions.append(decision(why, heir=heir.id))
            state.relations.append(Relation(id=ids.relation_id(RelationKind.DUPLICATE_OF, block.id, heir.id),
                                            kind=RelationKind.DUPLICATE_OF, src=block.id, dst=heir.id))
            if block.id in entry_of:
                entry_of[block.id].block, entry_of[block.id].disposition = heir.id, "output"
            counts["engine_blocks_joined"] += 1

    def keep_as_body(uid: str, why: str) -> None:
        """Information first: an engine text block the model did not put in the body stays where it was."""
        block = block_of[uid]
        handled.add(uid)
        block.decisions.append(decision(f"kept in the output (information first): {why}"))
        place(block)
        counts["kept_as_body"] += 1

    def reading(block: Block, text: str | None, cells: TableGrid | None, label: str) -> Observation:
        obs = Observation(id=ids.observation_id(block.id, "vlm", 1 + sum(o.engine == "vlm" for o in block.observations)),
                          engine="vlm", engine_version=model, task=TaskKind.RECOGNIZE, anchor=block.anchors[0],
                          label=label, text=text, cells=cells, status=ObservationStatus.OK)
        block.observations.append(obs)
        block.chosen_observation = obs.id
        return obs

    for item in data["blocks"]:
        kind, parts, region = item["type"], item["parts"], item.get("region")
        if kind == "figure":
            images = [x for p in parts if p["kind"] == "copy" for x in names(p) if block_of[x].kind in FIGURE_KINDS]
            texts = [x for p in parts if p["kind"] in ("copy", "write") for x in names(p) if x not in images]
            if images:
                for uid in images:
                    handled.add(uid)
                    place(block_of[uid])
                figure_id = block_of[images[0]].id
                counts["figure_engine"] += 1
            elif region:
                figure = v_adapter._drawn_figure(ws, state, n, ids.block_id_pdf(n, _next_block_seq(state, n)),
                                                 v_adapter._page_box(region, pdf_page, dpi), render, pdf_page, dpi,
                                                 decision)
                state.blocks.append(figure)
                place(figure)
                figure_id = figure.id
                counts["figure_drawn"] += 1
            else:
                figure_id = None
            if figure_id:
                v_adapter._written_into(state, n, by_id | {b.id: b for b in placed}, figure_id, parts)
            for uid in texts:
                keep_as_body(uid, f"the service model read it as text inside figure {item['id']}")
            continue
        if kind == "table":
            written_cells = contract.cells_of(item, page)
            for part in parts:
                uids = names(part)
                if part["kind"] == "table":
                    for uid in uids:
                        table = block_of[uid]
                        handled.add(uid)
                        index = next((i for i, t in enumerate(page.get("tables") or []) if t["block"] == uid), None)
                        for cell in table.cells.cells if table.cells is not None else []:
                            text = written_cells.get(index, {}).get((cell.row, cell.col))
                            if text is not None:
                                cell.content = v_adapter._unicode_scripts(text)
                                counts["table_cells_written"] += 1
                        if written_cells.get(index):
                            table.decisions.append(decision("cells written from the page image",
                                                            cells=len(written_cells[index])))
                        place(table)
                        counts["table_engine"] += 1
                elif part["kind"] == "write" and uids:
                    heir = block_of[uids[0]]
                    try:
                        grid = TableGrid.from_html(part["text"])
                    except ValueError:
                        grid = None
                    reading(heir, None if grid else part["text"], grid, ENGINE)
                    heir.kind, heir.cells, heir.text = (BlockKind.TABLE, grid, "") if grid else (heir.kind, None, part["text"])
                    heir.decisions.append(decision("a table the service model wrote from the page image"))
                    absorb(heir, uids, "a table the service model wrote whole")
                    place(heir)
                    counts["table_written"] += 1
                elif part["kind"] == "copy":
                    for uid in uids:
                        handled.add(uid)
                        place(block_of[uid])
            continue
        # text-like and formula blocks
        uids_all = [x for p in parts for x in names(p)]
        copied_only = all(p["kind"] == "copy" for p in parts)
        if copied_only and len(uids_all) == 1:  # the engine block as it is
            handled.add(uids_all[0])
            place(block_of[uids_all[0]])
            counts["engine_kept"] += 1
            continue
        pieces = []
        for part in parts:
            if part["kind"] == "copy":
                pieces.append(join_texts([units[x]["text"] for x in names(part)]))
            elif part["kind"] == "write":
                pieces.append(part["text"].strip())
        text = contract.visible("\n".join(p for p in pieces if p))
        if kind == "formula":
            body = text.strip()
            text = body[2:-2].strip() if body.startswith("$$") and body.endswith("$$") else body
        heirs = [x for x in uids_all if block_of[x].kind not in FIGURE_KINDS]  # an image keeps its crop
        for uid in uids_all:
            if uid not in heirs:
                handled.add(uid)
                place(block_of[uid])
        if heirs:
            heir = block_of[heirs[0]]
            engine_read = next((o for o in heir.observations if o.id == heir.chosen_observation), None)
            # a title keeps its label, so the title step reads the rewritten title as it read the engine's
            reading(heir, text, None, engine_read.label if heir.kind == BlockKind.TITLE and engine_read else ENGINE)
            heir.text = text
            if heir.kind == BlockKind.TABLE:  # the model wrote the table's place as text
                heir.kind, heir.cells = BlockKind.TEXT, None
            if kind == "formula":
                heir.kind = BlockKind.FORMULA
            heir.decisions.append(decision(f"the page's {kind} block, as the service model allocated it",
                                           parts=len(parts), engine_blocks=len(uids_all)))
            absorb(heir, heirs, "joined into one block by the service model's allocation")
            place(heir)
            counts["engine_rewritten" if not copied_only else "engine_joined"] += 1
            for part in parts:  # signals, not reverts
                uids = names(part)
                if part["kind"] != "write" or not uids:
                    continue
                original = "\n".join(units[x]["text"] for x in uids)
                boxes = [units[x]["box"] for x in uids]
                found = disagreements(part["text"], original, local_in(page, boxes), engine_page, output)
                if any(found.values()):
                    heir.observations.append(Observation(
                        id=ids.observation_id(heir.id, "vlm", 10 + len(heir.observations)), engine="vlm",
                        engine_version=model, task=TaskKind.RECOGNIZE, anchor=heir.anchors[0],
                        label=REWRITE_CANDIDATE, text=contract.visible(original), status=ObservationStatus.OK))
                    heir.decisions.append(decision(
                        "the written text differs from the engine's reading where no reading holds it: the engine's "
                        "reading listed for review", **{k: " · ".join(v[:20]) for k, v in found.items() if v}))
                    counts.update({f"signal_{k}": len(v) for k, v in found.items() if v})
                    counts["signalled_writes"] += 1
        else:  # content no engine block holds: its own block and ledger item, read from the page image
            box = v_adapter._page_box(region, pdf_page, dpi) if region else (0.0, 0.0, 1.0, 1.0)
            block = v_adapter._text_block(ids.block_id_pdf(n, _next_block_seq(state, n)), n,
                                          v_adapter._KINDS.get(kind, BlockKind.TEXT), text, box, None,
                                          decision("content the scan engine did not read, written from the image"),
                                          engine="vlm", model=model, title=kind == "title")
            state.blocks.append(block)
            state.ledger.append(LedgerEntry(item=ids.ledger_item_pdf(n, _next_item(state, n)), unit="ocr_block",
                                            source=block.anchors[0], chars=len("".join(text.split())),
                                            disposition="output", block=block.id))
            place(block)
            counts["written_new"] += 1

    for item in data.get("aside", []):
        for ref in item["lines"]:
            for uid in contract._names(ref, "K"):
                if uid not in block_of or uid in handled:
                    continue
                block = block_of[uid]
                if item["to"] != contract.EXCLUDED:
                    keep_as_body(uid, f"the service model put it into block {item['to']}: {item['reason'][:80]}")
                    continue
                handled.add(uid)
                if block.status == BlockStatus.EXCLUDED or block.kind in FURNITURE:
                    counts["furniture_engine"] += 1
                    continue
                key = furniture_key(block.text or "")
                if block.kind in FIGURE_KINDS or (key and key in others):
                    block.status = BlockStatus.EXCLUDED
                    if block.id in entry_of:
                        entry_of[block.id].disposition = "excluded"
                    excluded.append(block.id)
                    block.decisions.append(Decision(stage=DecisionStage.EXCLUDE, choice="aside", actor=ACTOR,
                                                    reason=f"the service model put it aside: {item['reason'][:120]}",
                                                    evidence={"model": model}))
                    counts["excluded"] += 1
                else:
                    keep_as_body(uid, f"the service model excluded it ({item['reason'][:80]}), but the engine did not "
                                      "take it for page furniture and no other page repeats it")
    # a text layer's copy of what was set aside (a scanning app's mark) follows it, as the pipeline's furniture step does
    counts["copies_excluded"] += len(_copies_follow(state, excluded)) if excluded else 0
    # the page's order: the allocation's; engine blocks it did not place keep their relative order after it
    rest = sorted((b for b in page_blocks if b not in placed), key=lambda b: (b.order, b.id))
    for i, block in enumerate(placed + rest):
        block.order = base + i
    renumber(state)


def join_texts(texts: list[str]) -> str:
    return join_wrapped([t for t in texts if t])
