"""Display formulas of native PDF pages as LaTeX (Q70, 2026-09-26): a step of ``process``.

The text layer of a typeset formula holds the right characters in fragments (a fraction's numerator and
denominator, sub- and superscripts as separate lines or blocks), without its structure.  Where the local layout
detector marks a ``display_formula`` region, the region's image goes to the scan engine (PaddleOCR-VL, all
regions of a document in one batched job) for LaTeX.

The engine was chosen by measurement (2026-09-26, 24 formula regions of the corpus with an annotated formula,
notation normalized): scan engine 90.6 similarity to the annotation, VLM on the image 86.2, VLM on the image with
the text layer's characters 87.5 — the general VLM misreads glyphs (z′ → ζ′, l → L) that the document-trained
engine reads right.

Acceptance (the selection step's rules, guide §3.3): two independent readings must agree —
- the scan engine also calls the region a formula;
- its letters and digits and the text layer's share most adjacent pairs both ways (``reading/compare.NEAR``);
- every visible native block touching the region lies in it (a replacement never splits a block's text).
Then one FORMULA block holds the LaTeX and the native fragments become ``duplicate`` (``duplicate_of`` the
formula; their text stays in the sidecar).  Otherwise the text layer stays and the engine's reading is kept on
the first fragment as evidence.
"""

from __future__ import annotations

import re
from collections import Counter

import fitz

from parserx.content import scan
from parserx.content.select import ACTOR as SELECT_ACTOR, renumber
from parserx.ir import ids
from parserx.ir.anchor import PdfAnchor
from parserx.ir.block import Block
from parserx.ir.decision import Decision
from parserx.ir.enums import BlockKind, BlockStatus, DecisionStage, ObservationStatus, RelationKind, TaskKind
from parserx.ir.observation import Observation
from parserx.ir.relation import Relation
from parserx.ir.state import DocumentState, LedgerEntry
from parserx.reading.compare import NEAR, normalize, pairs_share
from parserx.runtimes.events import Step
from parserx.tools.context import ToolContext, service_failure
from parserx.tools.recognize import _next_block_seq, _next_item
from parserx.tools.envelope import Failure
from parserx.workspace.queries import HIDDEN, block_unit

LABEL = "display_formula"
_FORMULA_LABELS = frozenset({"display_formula", "formula", "inline_formula", "formula_number"})
PAD = 2.0  # pt around a region: the detector's box sits tight on the glyphs (measurement tolerance)
DPI = 200


def formula_regions(state: DocumentState) -> list[tuple[int, tuple[float, float, float, float], list[str]]]:
    """(page, region in page points, native blocks in it) for display-formula regions not yet read."""
    if state.format != "pdf":
        return []
    out = []
    seen: set[tuple[int, tuple]] = set()
    for block in state.blocks:
        for obs in block.observations:
            if obs.task != TaskKind.LAYOUT or obs.label != LABEL or not isinstance(obs.anchor, PdfAnchor):
                continue
            n = obs.anchor.page
            k = obs.anchor.transform[0] if obs.anchor.transform else 1.0
            box = tuple(round(v * k, 1) for v in obs.anchor.bbox)
            box = (box[0] - PAD, box[1] - PAD, box[2] + PAD, box[3] + PAD)
            if (n, box) in seen:
                continue
            seen.add((n, box))
            touching = [b for b in state.blocks if b.status not in HIDDEN and block_unit(state, b) == n
                        and isinstance(b.anchors[0], PdfAnchor) and b.anchors[0].coord_space == "page_pt"
                        and _overlap(b.anchors[0].bbox, box)]
            if not touching or any(b.kind not in (BlockKind.TEXT, BlockKind.FORMULA) for b in touching):
                continue  # nothing to replace, or a table / figure / title shares the place
            if any(_is_read(b) for b in touching) or not all(_inside(b.anchors[0].bbox, box) for b in touching):
                continue  # read before, or a block reaches beyond the formula (a replacement never splits text)
            out.append((n, box, [b.id for b in sorted(touching, key=lambda b: b.order)]))
    return sorted(out, key=lambda r: (r[0], r[1][1], r[1][0]))


def read_formulas(ctx: ToolContext, regions) -> tuple[int, list[Failure]]:
    """Read the regions with the scan engine and adopt what the readings agree on; (adopted, failures)."""
    if not regions:
        return 0, []
    ctx.report(Step("process", "formulas", total=len(regions)))
    crops = []
    with fitz.open(ctx.ws.source_path) as doc:
        for n, box, _ in regions:
            page = doc[n - 1]
            pix = page.get_pixmap(dpi=DPI, clip=fitz.Rect(box) * page.rotation_matrix)
            crops.append((pix.tobytes("png"), pix.width, pix.height))
    ocr = ctx.ocr()
    data = scan.image_batch_pdf(crops)
    try:
        results = ocr.recognize_pdf(data)
    except Exception as exc:  # noqa: BLE001 - the text layer stays; the failure is reported
        return 0, [service_failure(exc, [f"p{n}" for n, _, _ in regions])]
    raw_ref = ocr.request_key(data, "application/pdf")
    adopted = 0
    with ctx.ws.txn("tool:process:formulas") as state:
        blocks = {b.id: b for b in state.blocks}
        for (n, box, ids_in), result in zip(regions, results):
            entries = result.raw["layoutParsingResults"][0].get("prunedResult", {}).get("parsing_res_list", [])
            latex = " ".join(str(e.get("block_content", "")).strip() for e in entries
                             if e.get("block_label") in _FORMULA_LABELS).replace("$$", "").strip()
            native = [blocks[i] for i in ids_in]
            reading = Observation(
                id=ids.observation_id(native[0].id, "paddleocr", 1 + sum(o.engine == "paddleocr"
                                                                         for o in native[0].observations)),
                engine="paddleocr", engine_version=ocr.model, task=TaskKind.RECOGNIZE, label=LABEL,
                anchor=PdfAnchor(page=n, bbox=box, coord_space="page_pt"), text=latex or None, raw_ref=raw_ref,
                status=ObservationStatus.OK)
            if _agree(latex, " ".join(b.text or "" for b in native), entries):
                _adopt(state, n, box, native, reading, latex)
                adopted += 1
            else:
                native[0].observations.append(reading)  # evidence only: the text layer stays
                native[0].decisions.append(Decision(
                    stage=DecisionStage.CONTENT_SOURCE, choice="native", actor=SELECT_ACTOR,
                    reason="formula region read by the scan engine, but the two readings do not agree; the text "
                           "layer is kept", evidence={"region": LABEL}))
        renumber(state)
    return adopted, []


def _agree(latex: str, native: str, entries: list[dict]) -> bool:
    """The engine calls it a formula, the two readings share their characters' order (``NEAR`` both ways), and
    nothing of the text layer is lost (conservation): every letter and digit of the replaced text is in the LaTeX
    — a paragraph number or an equation number beside the formula keeps the text layer."""
    if not latex or not any(e.get("block_label") in ("display_formula", "formula") for e in entries):
        return False
    a, b = normalize(_symbols(latex)), normalize(native)
    if not (a and b) or pairs_share(a, b) < NEAR or pairs_share(b, a) < NEAR:
        return False
    return not (Counter(b) - Counter(a))


_GREEK = {name: chr(code) for name, code in (
    ("alpha", 0x3B1), ("beta", 0x3B2), ("gamma", 0x3B3), ("delta", 0x3B4), ("epsilon", 0x3B5), ("varepsilon", 0x3B5),
    ("zeta", 0x3B6), ("eta", 0x3B7), ("theta", 0x3B8), ("vartheta", 0x3D1), ("iota", 0x3B9), ("kappa", 0x3BA),
    ("lambda", 0x3BB), ("mu", 0x3BC), ("nu", 0x3BD), ("xi", 0x3BE), ("pi", 0x3C0), ("rho", 0x3C1), ("sigma", 0x3C3),
    ("tau", 0x3C4), ("upsilon", 0x3C5), ("phi", 0x3C6), ("varphi", 0x3C6), ("chi", 0x3C7), ("psi", 0x3C8),
    ("omega", 0x3C9), ("Gamma", 0x393), ("Delta", 0x394), ("Theta", 0x398), ("Lambda", 0x39B), ("Xi", 0x39E),
    ("Pi", 0x3A0), ("Sigma", 0x3A3), ("Phi", 0x3A6), ("Psi", 0x3A8), ("Omega", 0x3A9))}
_COMMAND = re.compile(r"\\([A-Za-z]+)")


def _symbols(latex: str) -> str:
    """LaTeX with Greek-letter commands as their letters (LaTeX's own definitions), for comparing characters."""
    return _COMMAND.sub(lambda m: _GREEK.get(m.group(1), m.group(0)), latex)


def _adopt(state: DocumentState, n: int, box, native: list[Block], reading: Observation, latex: str) -> None:
    block_id = ids.block_id_pdf(n, _next_block_seq(state, n))
    reading = reading.model_copy(update={"id": ids.observation_id(block_id, "paddleocr", 1)})
    anchor = PdfAnchor(page=n, bbox=box, coord_space="page_pt")
    formula = Block(id=block_id, kind=BlockKind.FORMULA, order=native[0].order, anchors=[anchor],
                    observations=[reading], chosen_observation=reading.id, text=latex, decisions=[Decision(
                        stage=DecisionStage.CONTENT_SOURCE, choice="scan_engine", actor=SELECT_ACTOR,
                        reason="display formula: the scan engine's LaTeX, consistent with the text layer's "
                               "characters (Q70)", evidence={"fragments": len(native)}, refs=[b.id for b in native])])
    replaced = {b.id for b in native}
    for block in native:
        block.status = BlockStatus.DUPLICATE
        state.relations.append(Relation(id=ids.relation_id(RelationKind.DUPLICATE_OF, block.id, block_id),
                                        kind=RelationKind.DUPLICATE_OF, src=block.id, dst=block_id))
        block.decisions.append(Decision(stage=DecisionStage.CONTENT_SOURCE, choice="scan_engine", actor=SELECT_ACTOR,
                                        refs=[block_id], evidence={},
                                        reason="a fragment of a display formula now read as LaTeX"))
    for entry in state.ledger:
        if entry.block in replaced:
            entry.disposition = "duplicate"
    state.ledger.append(LedgerEntry(item=ids.ledger_item_pdf(n, _next_item(state, n)), unit="ocr_block", source=anchor,
                                    chars=len("".join(latex.split())), disposition="output", block=block_id))
    state.blocks.append(formula)


def _is_read(block: Block) -> bool:
    return any(o.engine == "paddleocr" and o.label == LABEL for o in block.observations)


def _overlap(a, b) -> bool:
    return a[0] < b[2] and b[0] < a[2] and a[1] < b[3] and b[1] < a[3]


def _inside(a, b) -> bool:
    cx, cy = (a[0] + a[2]) / 2, (a[1] + a[3]) / 2
    return b[0] <= cx <= b[2] and b[1] <= cy <= b[3]
