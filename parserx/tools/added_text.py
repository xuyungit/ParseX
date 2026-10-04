"""Text the program added from the local page reading (Q133), read again by the scan engine (docs/v2_ocr_engines.md §9).

``tools.edit.add_missed_text`` adds the lines the local reading sees where the output has nothing.  The local
recognizer reads lines, not documents: it writes no superscripts or subscripts and often drops the spaces between
words ("⁴Rule 823 … occurs on Monday" comes back as "4Rule 823 … occursonMonday").  Such lines are frequent when
the scan engine leaves whole regions out (GLM-OCR returns no footnote or aside regions), so they are read once more
by the scan engine:

- lines in one footnote region of the layout detector (in whatever order its columns interleave them), or set one
  under another in one column (a gap under one and a half line heights, overlapping horizontally), are one place;
- every place of the document is cropped from its page render, and all of them go to the scan engine in one request
  (one page per crop, as embedded images go);
- a place's reading is adopted only where it reads the same characters as the local reading (letters and digits,
  rapidfuzz ratio ``SOMEWHERE`` or more: a crop that took in a neighbouring line reads more and is not adopted):
  the place's first block takes it, the others are merged into it.  Otherwise the local reading stays.

One request per document that has such lines, none otherwise.
"""

from __future__ import annotations

from parserx.content import scan
from parserx.ir import ids
from parserx.ir.anchor import PdfAnchor
from parserx.ir.enums import BlockKind, BlockStatus, DecisionStage, ObservationStatus, TaskKind
from parserx.ir.decision import Decision
from parserx.ir.observation import Observation
from parserx.ir.state import DocumentState
from rapidfuzz import fuzz

from parserx.reading.compare import SOMEWHERE, added_from_reading, normalize
from parserx.tools.context import ToolContext, service_failure
from parserx.tools.envelope import Failure, ToolFailure
from parserx.tools.imaging import region_crop
from parserx.workspace.queries import HIDDEN, ordered

ACTOR = "program:added_text"
CHOICE = "read_again"
_GAP = 1.5  # line heights between two lines of one place
_TEXT_KINDS = frozenset({BlockKind.TEXT, BlockKind.FOOTNOTE, BlockKind.TITLE, BlockKind.CAPTION})


def places(state: DocumentState) -> list[list]:
    """The shown added blocks not read again yet, grouped into places (lists of blocks, in reading order)."""
    readings = {r.n: r for r in state.readings}
    regions: dict[tuple, list] = {}  # (page, footnote region) → blocks
    loose: list[tuple[int, list]] = []  # (page, blocks) set one under another
    for block in ordered(state):
        anchor = block.anchors[0] if block.anchors else None
        if (block.status in HIDDEN or block.kind not in _TEXT_KINDS or not isinstance(anchor, PdfAnchor)
                or not added_from_reading(block) or any(d.choice == CHOICE for d in block.decisions)):
            continue
        region = _footnote_region(readings.get(anchor.page), anchor.bbox)
        if region is not None:
            regions.setdefault((anchor.page, region), []).append(block)
            continue
        near = next((g for page, g in loose if page == anchor.page
                     and any(_adjacent(b.anchors[0].bbox, anchor.bbox) for b in g)), None)
        if near is not None:
            near.append(block)
        else:
            loose.append((anchor.page, [block]))
    return sorted([*regions.values(), *(g for _, g in loose)], key=lambda g: g[0].order)


def _footnote_region(reading, bbox) -> tuple | None:
    if reading is None:
        return None
    x, y = (bbox[0] + bbox[2]) / 2, (bbox[1] + bbox[3]) / 2
    return next((tuple(r.bbox) for r in reading.roles if r.label in ("footnote", "vision_footnote")
                 and r.bbox[0] <= x <= r.bbox[2] and r.bbox[1] <= y <= r.bbox[3]), None)


def _adjacent(a, b) -> bool:
    """*b* is a line just above or below *a* in the same column."""
    height = max(a[3] - a[1], b[3] - b[1], 1.0)
    gap = max(b[1] - a[3], a[1] - b[3])
    return gap <= _GAP * height and b[0] < a[2] and b[2] > a[0]


def read_again(ctx: ToolContext) -> tuple[int, list[Failure]]:
    """Read the added places again with the scan engine and adopt agreeing readings; (blocks adopted, failures)."""
    state = ctx.ws.load()
    if state.format != "pdf":
        return 0, []
    groups = places(state)
    if not groups:
        return 0, []
    pages = {p.n: p for p in state.pages}
    cfg = ctx.config.tools
    crops = []
    for blocks in groups:
        boxes = [b.anchors[0].bbox for b in blocks]
        box = (min(b[0] for b in boxes), min(b[1] for b in boxes), max(b[2] for b in boxes), max(b[3] for b in boxes))
        crop, png, *_ = region_crop(ctx.ws.source_path, pages[blocks[0].anchors[0].page], box, cfg.read_dpi,
                                    cfg.crop_pad_pt)
        crops.append((png, crop.width, crop.height, box))
    if ctx.config.builders.ocr.engine == "none":
        return 0, []
    try:
        ocr = ctx.ocr()
    except ToolFailure as exc:  # not configured: the added lines stay as the local reading wrote them
        return 0, [exc.failure]
    data = scan.image_batch_pdf([(png, w, h) for png, w, h, _ in crops])
    try:
        raw_ref, results = ocr.request_key(data, "application/pdf"), ocr.recognize_pdf(data)
    except Exception as exc:  # noqa: BLE001 - the added lines stay as the local reading wrote them
        return 0, [service_failure(exc, [b.id for blocks in groups for b in blocks])]
    adopted = 0
    with ctx.ws.txn("tool:process:added_text") as state:
        by_id = {b.id: b for b in state.blocks}
        for blocks, (_, _, _, box), result in zip(groups, crops, results):
            text = scan_reading(result.raw["layoutParsingResults"][0])
            local = normalize(" ".join(b.text or "" for b in blocks))
            read = normalize(text)
            if not read or fuzz.ratio(local, read) < SOMEWHERE:  # the same characters, about as many
                for other in blocks:  # read once: the local reading stays
                    by_id[other.id].decisions.append(Decision(
                        stage=DecisionStage.CONTENT_SOURCE, choice=CHOICE, actor=ACTOR, refs=[],
                        reason="read again by the scan engine; its reading does not agree with the local reading, "
                               "which stays", evidence={"adopted": False, "reading": text[:200]}))
                continue
            head = by_id[blocks[0].id]
            obs = Observation(id=ids.observation_id(head.id, scan.ENGINE, 1 + sum(o.engine == scan.ENGINE
                                                                                    for o in head.observations)),
                              engine=scan.ENGINE, engine_version=ocr.model, task=TaskKind.RECOGNIZE,
                              anchor=PdfAnchor(page=head.anchors[0].page, bbox=box, coord_space="page_pt"),
                              raw_ref=raw_ref, text=text, status=ObservationStatus.OK)
            head.observations.append(obs)
            head.chosen_observation, head.text = obs.id, text
            head.anchors[0] = PdfAnchor(page=head.anchors[0].page, bbox=box, coord_space="page_pt")
            head.decisions.append(Decision(
                stage=DecisionStage.CONTENT_SOURCE, choice=CHOICE, actor=ACTOR, refs=[obs.id],
                reason="text added from the local page reading, read again by the scan engine, which writes scripts "
                       "and word spaces the local recognizer drops; the readings agree",
                evidence={"lines": len(blocks)}))
            for other in blocks[1:]:
                block = by_id[other.id]
                block.status = BlockStatus.MERGED
                block.decisions.append(Decision(stage=DecisionStage.CONTENT_SOURCE, choice=CHOICE, actor=ACTOR,
                                                refs=[head.id], reason=f"read again as one place with {head.id}",
                                                evidence={"place": head.id}))
                for entry in state.ledger:
                    if entry.block == block.id:
                        entry.disposition, entry.block = "merged", head.id
            adopted += len(blocks)
    return adopted, []


def scan_reading(page: dict) -> str:
    """The scan engine's text of a crop: its text regions in its order, one per line."""
    entries = (page.get("prunedResult") or {}).get("parsing_res_list") or []
    boxes = [scan.entry_bbox(e) for e in entries]
    parts = []
    for index in scan.scan_order(boxes, [e.get("block_order") for e in entries]):
        entry = entries[index]
        if scan.labels.to_kind(scan.ENGINE, str(entry.get("block_label", ""))) in (BlockKind.FIGURE, BlockKind.TABLE):
            continue
        text = scan.engine_text(scan.take_pictures(str(entry.get("block_content") or ""))[0]).strip()
        if text:
            parts.append(text)
    return "\n".join(parts)
