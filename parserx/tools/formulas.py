"""Formulas of native PDF pages (Q70, version 2 — 2026-09-26): whole pages read, passages chosen one by one.

The text layer of typeset mathematics holds the right characters without their structure: sub- and superscripts
flattened (N_o → "No"), fractions and matrices in fragments.  Measured on the corpus (eval report
2026-09-26_q70_formula_experiment): reading the whole page with the scan engine recovers most inline math
(paper_chn01: 101 of 124 inline formulas, against 14 in the text layer) and reads display formulas at least as
well as crops of them (crops cut formulas the detector splits); taken as a whole the page reading loses prose the
text layer has, so it is adopted passage by passage.

1. **Pages**: native pages where the local layout detector marks a display or inline formula.  Pages whose
   formulas are plain text need no reading; DOCX formulas are OMML, already LaTeX (Q9); scanned pages are read
   whole anyway.
2. **Reading**: those pages, batched, through the scan engine (the scanned-page path, ``scan.page_blocks``).
3. **Passages**: the native text blocks and the reading's text and formula blocks over the same place form
   passages (a block belongs to a passage when its centre lies in a block of the other reading).  The engine reads
   a paragraph cut by a column break whole, at the place before the cut: the native block after the cut joins the
   passage when the reading carries its text.  Only passages where the reading has mathematics are considered:
   prose stays the text layer's.
4. **Choice**, per passage (the selection step's rule: two readings, conservation):
   - the reading carries every letter and digit of the text layer (a character neither the reading nor the
     local page reading sees — a mis-mapped glyph — does not count; LaTeX command names are not characters) →
     the reading's blocks replace the native ones (``duplicate_of``; their text stays in the sidecar);
   - otherwise an **editor** — the VLM, shown the passage's image and both readings, characters from the text
     layer and structure from the reading, and told which letters and digits the two do not share (the reading
     takes a superscript l for 1) — writes one version; adopted when it conserves the characters;
   - otherwise the text layer stays, both readings are kept as evidence, and the passage is a review item
     (``formula_candidate``) for the agent, the final editor in the hybrid runtime.
"""

from __future__ import annotations

import re
from collections import Counter

import pymupdf
from rapidfuzz import fuzz

from parserx.content import scan
from parserx.content.text import join_wrapped
from parserx.content.select import ACTOR as SELECT_ACTOR, renumber
from parserx.ir import ids
from parserx.ir.anchor import PdfAnchor
from parserx.ir.block import Block
from parserx.ir.decision import Decision
from parserx.ir.enums import BlockKind, BlockStatus, DecisionStage, ObservationStatus, PageStatus, RelationKind, TaskKind
from parserx.ir.observation import Observation
from parserx.ir.relation import Relation
from parserx.ir.rotation import shown
from parserx.ir.state import DocumentState, LedgerEntry
from parserx.reading.compare import normalize, text_at
from parserx.runtimes.events import Step
from parserx.scheduling import run_ordered
from parserx.tools.context import ToolContext, service_failure
from parserx.tools.envelope import Failure
from parserx.tools.imaging import write_once
from parserx.tools.recognize import _next_block_seq, _next_item
from parserx.workspace.queries import HIDDEN, block_unit

LABELS = frozenset({"display_formula", "inline_formula"})  # detector labels that make a page worth reading
DONE = "formula_page"  # decision choice: the passage was decided (adopted or kept)
CANDIDATE = "formula_reading"  # observation label: the reading (or the editor's version) not adopted
PAD = 2.0  # pt: a block's centre may lie this far outside the other block (box rounding, measurement tolerance)
EDITOR_DPI = 200
EDITOR_ROUNDS = 2  # the editor's tries per passage (the service model does not always follow the differences)
DIFFERENCES = 12  # characters the editor is told the two readings disagree on, the most frequent first
CARRIED = 8  # letters and digits a native block needs before a reading may carry it (a shorter one matches by chance)
CARRIED_MATCH = 90  # rapidfuzz partial ratio of that block's text inside the reading's
_PASSAGE_KINDS = frozenset({BlockKind.TEXT, BlockKind.FORMULA})
_EDITOR_PROMPT = (
    "你是编辑。图中是文档的一段。给你两份读数：A 是 PDF 文字层（字符准确，但公式的上下标、分式等结构丢失，个别字形可能是乱码），"
    "B 是 OCR（有 LaTeX 结构，个别字符可能认错、可能漏掉公式编号）。请以图为准，输出这段的最终文字：正文照抄，公式用 LaTeX（行内 "
    "$…$，行间 $$…$$），字符以 A 为准、结构以 B 为准，公式编号保留。图的边上可能露出相邻的内容，只输出 A 这一段。"
    "只输出结果，不加解释。")


def formula_pages(state: DocumentState) -> list[int]:
    """Native PDF pages with a detected formula, not decided yet."""
    if state.format != "pdf":
        return []
    native_pages = {p.n for p in state.pages if p.status == PageStatus.DONE}
    marked: set[int] = set()
    done: set[int] = set()
    scanned: set[int] = set()
    for block in state.blocks:
        n = block_unit(state, block)
        if any(o.task == TaskKind.LAYOUT and o.label in LABELS for o in block.observations):
            marked.add(n)
        if any(d.choice == DONE for d in block.decisions):
            done.add(n)
        if block.status not in HIDDEN and block.kind != BlockKind.FIGURE and isinstance(block.anchors[0], PdfAnchor) \
                and any(o.engine == "paddleocr" and o.task == TaskKind.RECOGNIZE for o in block.observations):
            scanned.add(n)  # read by the scan engine already (a scanned page)
    return sorted(n for n in marked & native_pages if n not in done and n not in scanned)


def read_formula_pages(ctx: ToolContext, pages: list[int]) -> tuple[dict[str, int], list[Failure]]:
    """Read *pages* whole and decide their passages; (counts by outcome, failures)."""
    counts: Counter[str] = Counter()
    if not pages:
        return dict(counts), []
    ctx.report(Step("process", "formulas", total=len(pages)))
    ocr = ctx.ocr()
    size = ctx.config.tools.scan_batch_pages
    batches = [pages[i:i + size] for i in range(0, len(pages), size)]
    source = ctx.ws.source_path

    def fetch(batch):
        with pymupdf.open(source) as doc:
            data = scan.batch_pdf(doc, batch)
        return ocr.request_key(data, "application/pdf"), ocr.recognize_pdf(data)

    failures: list[Failure] = []
    readings: dict[int, tuple[str, dict]] = {}
    for outcome in run_ordered(batches, fetch, max_workers=2):
        if outcome.status != "ok":
            failures.append(service_failure(outcome.exception, [f"p{n}" for n in outcome.task]))
            continue
        raw_ref, results = outcome.value
        for n, result in zip(outcome.task, results):
            readings[n] = (raw_ref, result.raw["layoutParsingResults"][0])

    state = ctx.ws.load()
    blocks = {b.id: b for b in state.blocks}
    page_of = {p.n: p for p in state.pages}
    plans = []  # (page, native ids, reading blocks)
    for n, (raw_ref, raw) in sorted(readings.items()):
        result = scan.page_blocks(scan.PageScan(page=n, raw=raw, raw_ref=raw_ref, engine_version=ocr.model),
                                  page_size=page_of[n].size_pt, first_seq=1, first_item=1, page=page_of[n])
        plans += [(n, natives, reading) for natives, reading in _passages(state, n, result.blocks)]

    # the editor, for passages whose reading loses characters of the text layer (concurrently)
    need_editor = []
    for index, (n, natives, reading) in enumerate(plans):
        native_text, reading_text = _texts(blocks, natives, reading)
        if not _lost(state, n, natives, blocks, native_text, reading_text):
            continue
        box = _union([blocks[b].anchors[0].bbox for b in natives] + [b.anchors[0].bbox for b in reading])
        with pymupdf.open(source) as doc:
            png = doc[n - 1].get_pixmap(dpi=EDITOR_DPI, clip=pymupdf.Rect(shown(page_of[n], box)) + (-4, -4, 4, 4)
                                        ).tobytes("png")
        path = ctx.ws.root / "renders" / f"formula-{natives[0]}.png"
        write_once(path, png)
        need_editor.append((index, path, native_text, reading_text))
    edited: dict[int, str] = {}
    vlm = ctx.vlm(ctx.config.tools.ask_reasoning_effort) if need_editor else None
    for _ in range(EDITOR_ROUNDS):  # a version that still loses characters is the next round's B
        outcomes = run_ordered(need_editor, lambda t: vlm.call(
            "describe_image", t[1], _EDITOR_PROMPT, context=f"A：\n{t[2]}\n\nB：\n{t[3]}{_differences(t[2], t[3])}",
            temperature=0.0, max_tokens=4096, structured_output_mode="off", json_schema_name="parserx_formula_editor"),
            max_workers=ctx.config.services.vlm.max_concurrent)
        again = []
        for outcome in outcomes:
            index, path, native_text, _ = outcome.task
            n, natives, _ = plans[index]
            if outcome.exception is not None:
                failures.append(service_failure(outcome.exception, [natives[0]]))
            elif str(outcome.value or "").strip():
                edited[index] = str(outcome.value).strip()
                if _lost(state, n, natives, blocks, native_text, edited[index]):
                    again.append((index, path, native_text, edited[index]))
        need_editor = again

    with ctx.ws.txn("tool:process:formulas") as state:
        blocks = {b.id: b for b in state.blocks}
        for index, (n, natives, reading) in enumerate(plans):
            native_text, reading_text = _texts(blocks, natives, reading)
            if not _lost(state, n, natives, blocks, native_text, reading_text):
                _adopt(state, n, [blocks[b] for b in natives], reading, how="reading")
                counts["reading"] += 1
            elif index in edited and not _lost(state, n, natives, blocks, native_text, edited[index]):
                _adopt(state, n, [blocks[b] for b in natives],
                       [_edited_block(n, [blocks[b] for b in natives], reading, edited[index])], how="editor")
                counts["editor"] += 1
            else:
                _keep([blocks[b] for b in natives], edited.get(index) or reading_text)
                counts["kept"] += 1
        renumber(state)
    return dict(counts), failures


def _texts(blocks: dict, natives: list[str], reading: list[Block]) -> tuple[str, str]:
    return "\n".join(blocks[b].text or "" for b in natives), "\n".join(b.text or "" for b in reading)


def _passages(state: DocumentState, n: int, reading: list[Block]) -> list[tuple[list[str], list[Block]]]:
    """Groups of (native block ids, reading blocks) over the same place, where the reading has mathematics."""
    natives = [b for b in state.blocks if b.status not in HIDDEN and block_unit(state, b) == n
               and b.kind in _PASSAGE_KINDS and isinstance(b.anchors[0], PdfAnchor)
               and b.anchors[0].coord_space == "page_pt" and not any(d.choice == DONE for d in b.decisions)]
    others = [b for b in state.blocks if b.status not in HIDDEN and block_unit(state, b) == n
              and b.kind not in _PASSAGE_KINDS and isinstance(b.anchors[0], PdfAnchor)
              and b.anchors[0].coord_space == "page_pt"]
    reading = [b for b in reading if b.kind in _PASSAGE_KINDS and b.status not in HIDDEN
               and not any(_touch(b.anchors[0].bbox, o.anchors[0].bbox) for o in others)]  # not over a title or table
    parent = list(range(len(natives) + len(reading)))

    def find(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    for i, a in enumerate(natives):
        for j, b in enumerate(reading):
            if _touch(a.anchors[0].bbox, b.anchors[0].bbox):
                parent[find(i)] = find(len(natives) + j)
    groups: dict[int, tuple[list[str], list[Block]]] = {}
    for i, a in enumerate(natives):
        groups.setdefault(find(i), ([], []))[0].append(a.id)
    for j, b in enumerate(reading):
        groups.setdefault(find(len(natives) + j), ([], []))[1].append(b)
    passages = [(native_ids, blocks) for native_ids, blocks in groups.values()
                if native_ids and blocks and any(b.kind == BlockKind.FORMULA or "$" in (b.text or "") for b in blocks)]
    return _continued(sorted(natives, key=lambda b: b.order), passages)


def _continued(natives: list[Block], passages: list[tuple[list[str], list[Block]]]
               ) -> list[tuple[list[str], list[Block]]]:
    """*passages* with the native blocks their reading carries beyond its place: the engine reads a paragraph cut
    by a column break whole, in the block before the cut, so the native block just after it (or just before it)
    lies under no reading block although its text is there.  Such a block joins the passage when every letter and
    digit it has is in the reading's surplus over the passage's text layer, in the same order."""
    grouped = {native_id for native_ids, _ in passages for native_id in native_ids}
    for i, native in enumerate(natives):
        if native.id in grouped:
            continue
        for neighbour in (natives[i - 1] if i else None, natives[i + 1] if i + 1 < len(natives) else None):
            passage = next((p for p in passages if neighbour is not None and neighbour.id in p[0]), None)
            if passage is not None and _carries(passage, natives, native):
                passage[0].append(native.id)
                grouped.add(native.id)
                break
    return passages


def _carries(passage: tuple[list[str], list[Block]], natives: list[Block], native: Block) -> bool:
    own = normalize(native.text or "")
    if len(own) < CARRIED:
        return False
    reading = normalize(_symbols("\n".join(b.text or "" for b in passage[1])))
    layer = normalize("\n".join(b.text or "" for b in natives if b.id in passage[0]))
    surplus = Counter(reading) - Counter(layer)
    return not Counter(own) - surplus and fuzz.partial_ratio(own, reading) >= CARRIED_MATCH


def _lost(state: DocumentState, n: int, natives: list[str], blocks: dict, native_text: str, other: str) -> bool:
    """Whether *other* lacks letters or digits of the text layer: a Latin or Greek letter or a digit always counts
    (the text layer holds them exactly; both readings may take a superscript l for 1); another character counts
    when the local page reading also sees it — one no one else sees is a mis-mapped glyph (an overline drawn with
    a CJK character)."""
    missing = Counter(normalize(native_text)) - Counter(normalize(_symbols(other)))
    if not missing:
        return False
    if any(_EXACT.fullmatch(ch) for ch in missing):
        return True
    seen = text_at(state, n, _union([blocks[b].anchors[0].bbox for b in natives]))
    if seen is None:
        return True
    local = Counter(normalize(seen))
    return any(local[ch] > 0 for ch in missing)  # a character no one else sees is a mis-mapped glyph


def _paragraphs(natives: list[Block], reading: list[Block]) -> list[Block]:
    """The reading's text items joined back into the text layer's paragraphs: consecutive text items inside the same
    native block are one paragraph (the engine sometimes returns a paragraph line by line); formulas stay apart."""
    def host(block: Block) -> str | None:
        inside = [b.id for b in natives if _centre_in(block.anchors[0].bbox, b.anchors[0].bbox)]
        return inside[0] if len(inside) == 1 else None

    out: list[Block] = []
    previous: str | None = None
    for block in reading:
        here = host(block)
        if out and block.kind == BlockKind.TEXT and out[-1].kind == BlockKind.TEXT and here is not None \
                and here == previous:
            last = out[-1]
            text = join_wrapped([last.text or "", block.text or ""])
            box = _union([last.anchors[0].bbox, block.anchors[0].bbox])
            anchor = last.anchors[0].model_copy(update={"bbox": box})
            observations = [o.model_copy(update={"text": text, "anchor": anchor}) for o in last.observations[:1]]
            out[-1] = last.model_copy(update={"text": text, "anchors": [anchor], "observations": observations})
        else:
            out.append(block)
        previous = here
    return out


def _adopt(state: DocumentState, n: int, natives: list[Block], reading: list[Block], *, how: str) -> None:
    order = min(b.order for b in natives)
    reading = _paragraphs(natives, reading)
    new: list[Block] = []
    for block in reading:  # the first native's place; renumber keeps them in id (reading) order there
        block_id = ids.block_id_pdf(n, _next_block_seq(state, n))
        observations = [o.model_copy(update={"id": ids.observation_id(block_id, o.engine, k)})
                        for k, o in enumerate(block.observations[:1], 1)]
        block = block.model_copy(update={
            "id": block_id, "order": order, "status": BlockStatus.OK, "observations": observations,
            "chosen_observation": observations[0].id if observations else None,
            "decisions": [Decision(stage=DecisionStage.CONTENT_SOURCE, choice=DONE, actor=SELECT_ACTOR,
                                   refs=[b.id for b in natives], evidence={"by": how},
                                   reason="formulas as LaTeX: the page reading (or its editor's version) carries every "
                                          "letter and digit of the text layer here (Q70)")]})
        state.blocks.append(block)
        new.append(block)
        state.ledger.append(LedgerEntry(item=ids.ledger_item_pdf(n, _next_item(state, n)), unit="ocr_block",
                                        source=block.anchors[0], chars=len("".join((block.text or "").split())),
                                        disposition="output", block=block_id))
    replaced = {b.id for b in natives}
    for native in natives:
        native.status = BlockStatus.DUPLICATE
        state.relations.append(Relation(id=ids.relation_id(RelationKind.DUPLICATE_OF, native.id, new[0].id),
                                        kind=RelationKind.DUPLICATE_OF, src=native.id, dst=new[0].id))
        native.decisions.append(Decision(stage=DecisionStage.CONTENT_SOURCE, choice=DONE, actor=SELECT_ACTOR,
                                         refs=[b.id for b in new], evidence={"by": how},
                                         reason="the passage is now read with its formulas as LaTeX (Q70)"))
    for entry in state.ledger:
        if entry.block in replaced:
            entry.disposition = "duplicate"


def _edited_block(n: int, natives: list[Block], reading: list[Block], text: str) -> Block:
    box = _union([b.anchors[0].bbox for b in natives] + [b.anchors[0].bbox for b in reading])
    anchor = PdfAnchor(page=n, bbox=box, coord_space="page_pt")
    display = re.fullmatch(r"\$\$(.+)\$\$", text.strip(), flags=re.S)
    obs = Observation(id="o-editor", engine="vlm", engine_version="formula-editor", task=TaskKind.CORRECT,
                      anchor=anchor, text=text, label=CANDIDATE, status=ObservationStatus.OK)
    return Block(id="b-editor", kind=BlockKind.FORMULA if display else BlockKind.TEXT, order=0, anchors=[anchor],
                 observations=[obs], chosen_observation=obs.id, text=display.group(1).strip() if display else text)


def _keep(natives: list[Block], candidate: str) -> None:
    first = natives[0]
    first.observations.append(Observation(
        id=ids.observation_id(first.id, "paddleocr", 1 + sum(o.engine == "paddleocr" for o in first.observations)),
        engine="paddleocr", engine_version="page-reading", task=TaskKind.RECOGNIZE, label=CANDIDATE,
        anchor=first.anchors[0], text=candidate, status=ObservationStatus.OK))
    for native in natives:
        native.decisions.append(Decision(stage=DecisionStage.CONTENT_SOURCE, choice=DONE, actor=SELECT_ACTOR,
                                         evidence={"by": "kept"}, refs=[first.id],
                                         reason="the page reading and its editor's version lose characters the "
                                                "text layer has; the text layer stays, the reading is evidence (Q70)"))


def pending_candidates(state: DocumentState) -> list[tuple[str, str]]:
    """(block id, candidate text) of passages kept with a formula reading not adopted: review items."""
    out = []
    for block in state.blocks:
        if block.status in HIDDEN:
            continue
        candidate = next((o for o in reversed(block.observations) if o.label == CANDIDATE), None)
        if candidate is not None and block.chosen_observation != candidate.id:
            out.append((block.id, candidate.text or ""))
    return out


_LETTERS = {name: chr(code) for name, code in (
    ("alpha", 0x3B1), ("beta", 0x3B2), ("gamma", 0x3B3), ("delta", 0x3B4), ("epsilon", 0x3B5), ("varepsilon", 0x3B5),
    ("zeta", 0x3B6), ("eta", 0x3B7), ("theta", 0x3B8), ("vartheta", 0x3D1), ("iota", 0x3B9), ("kappa", 0x3BA),
    ("lambda", 0x3BB), ("mu", 0x3BC), ("nu", 0x3BD), ("xi", 0x3BE), ("pi", 0x3C0), ("rho", 0x3C1), ("sigma", 0x3C3),
    ("tau", 0x3C4), ("upsilon", 0x3C5), ("phi", 0x3C6), ("varphi", 0x3C6), ("chi", 0x3C7), ("psi", 0x3C8),
    ("omega", 0x3C9), ("Gamma", 0x393), ("Delta", 0x394), ("Theta", 0x398), ("Lambda", 0x39B), ("Xi", 0x39E),
    ("Pi", 0x3A0), ("Sigma", 0x3A3), ("Phi", 0x3A6), ("Psi", 0x3A8), ("Omega", 0x3A9), ("Upsilon", 0x3A5),
    ("varpi", 0x3D6), ("varrho", 0x3F1), ("varsigma", 0x3C2),
    # letter-like symbols (ℓ is the letter l, NFKC)
    ("ell", 0x2113), ("imath", 0x131), ("jmath", 0x237), ("hbar", 0x127), ("aleph", 0x2135), ("Re", 0x211C),
    ("Im", 0x2111))}
_COMMAND = re.compile(r"\\([A-Za-z]+)")
_EXACT = re.compile(r"[a-z0-9\u03b1-\u03c9]")  # normalized: lower case Latin, digits, Greek
_ENVIRONMENT = re.compile(r"\\begin\{(?:array|tabular)\}\{[^{}]*\}|\\(?:begin|end)\{[^{}]*\}")


def _symbols(latex: str) -> str:
    """LaTeX's characters, for comparing them with the text layer's: letter commands (Greek, ``\\ell`` …) as their
    letters (LaTeX's own definitions); other command names, environments and an array's column spec are markup, not characters
    (``\\left`` carries no l) — the arguments stay."""
    return _COMMAND.sub(lambda m: _LETTERS.get(m.group(1), ""), _ENVIRONMENT.sub(" ", latex))


def _differences(native_text: str, other: str) -> str:
    """The letters and digits the two readings do not share, told to the editor: it checks each against the image
    (the page reading takes a superscript l for 1)."""
    ours, theirs = Counter(normalize(native_text)), Counter(normalize(_symbols(other)))
    lacks, adds = ours - theirs, theirs - ours
    if not lacks:
        return ""
    listed = lambda c: "、".join(f"{ch}×{k}" for ch, k in c.most_common(DIFFERENCES)) or "无"
    return (f"\n\nB 与 A 的字母和数字不一致：A 有、B 没有的是 {listed(lacks)}；B 有、A 没有的是 {listed(adds)}。"
            "请看图逐个核对这些字符，按图改正。")


def _touch(a, b) -> bool:
    """One box's centre lies in the other (with the measurement tolerance)."""
    return _centre_in(a, b) or _centre_in(b, a)


def _centre_in(inner, outer) -> bool:
    cx, cy = (inner[0] + inner[2]) / 2, (inner[1] + inner[3]) / 2
    return outer[0] - PAD <= cx <= outer[2] + PAD and outer[1] - PAD <= cy <= outer[3] + PAD


def _union(boxes):
    return (min(b[0] for b in boxes), min(b[1] for b in boxes), max(b[2] for b in boxes), max(b[3] for b in boxes))
