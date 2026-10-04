"""The running heads, feet and page numbers of a page shown as an image, read again where the scan engine left
them out (Q151).

A bid document holds whole pages of test reports and financial statements as images, and their running heads carry
what a reader needs — "报告编号：ZJA1-X001-2023000001B", the company, "第 5 页 共 15 页".  GLM-OCR returns no header,
footer or page-number regions (docs/v2_ocr_engines.md §4.10), so they were lost; PaddleOCR-VL reads them and they
are kept as page furniture.  Here, for an image whose text was read (transcribed), each region the layout detector
calls header, footer or page number that no block read inside the image covers is cut from the image and read again
by the scan engine — alone in a crop it is read as text — in batches of ``BATCH`` crops; where the engine reads
nothing, the local reading of the image inside the region is taken.  The text becomes an excluded block of that
kind inside the image, as the scan engine's own furniture is, and the Markdown writes it with the image's text, in
a comment by default (``output.page_furniture``: ``<!-- 图片页眉：… · 图片页码：… -->``).
"""

from __future__ import annotations

from parserx.content import scan
from parserx.ir import ids
from parserx.ir.anchor import AssetAnchor
from parserx.ir.block import Block
from parserx.ir.decision import Decision
from parserx.ir.enums import BlockStatus, DecisionStage, ObservationStatus, RelationKind, TaskKind
from parserx.ir.observation import Observation
from parserx.ir.relation import Relation
from parserx.ir.state import DocumentState, LedgerEntry
from parserx.layout import labels
from parserx.reading.compare import _ACCOUNTS, _centre, _inside, _overlap, read_inside
from parserx.scheduling import run_ordered, spread
from parserx.tools.added_text import scan_reading
from parserx.tools.context import ToolContext, service_failure
from parserx.tools.envelope import Failure, ToolFailure
from parserx.tools.imaging import image_crop

ACTOR = "program:image_furniture"
BATCH = 50  # crops in one scan-engine request
PAD = 6.0  # pixels around a region


def places(state: DocumentState) -> list[tuple[str, str, tuple]]:
    """(figure, layout label, region in the image's pixels) of the furniture regions no block read in it covers."""
    blocks = {b.id: b for b in state.blocks}
    out = []
    for figure_id in read_inside(state):
        figure = blocks[figure_id]
        asset = next((a.asset for a in figure.anchors if isinstance(a, AssetAnchor)), None)
        held = [a.bbox for r in state.relations if r.kind == RelationKind.CONTAINS and r.src == figure_id
                and r.dst in blocks and blocks[r.dst].status in _ACCOUNTS
                for a in blocks[r.dst].anchors if isinstance(a, AssetAnchor) and a.asset == asset]
        for obs in figure.observations:
            if obs.task != TaskKind.LAYOUT or labels.to_kind("layout", obs.label or "") not in labels.FURNITURE \
                    or not isinstance(obs.anchor, AssetAnchor) or obs.anchor.asset != asset:
                continue
            region = tuple(obs.anchor.bbox)
            if not any(_overlap(region, box) and (_inside(_centre(region), box) or _inside(_centre(box), region))
                       for box in held):
                out.append((figure_id, obs.label, region))
    return out


def read_again(ctx: ToolContext) -> tuple[int, list[Failure]]:
    """Read the uncovered furniture regions of images again; (blocks added, failures)."""
    state = ctx.ws.load()
    found = places(state)
    if not found or ctx.config.builders.ocr.engine == "none":
        return 0, []
    try:
        ocr = ctx.ocr()
    except ToolFailure as exc:  # not configured: the regions stay unread
        return 0, [exc.failure]
    blocks = {b.id: b for b in state.blocks}
    assets = {a.id: a for a in state.assets}
    readings = {r.id: r.reading or [] for r in state.images}
    crops = []
    for figure_id, label, region in found:
        asset = assets[next(a.asset for a in blocks[figure_id].anchors if isinstance(a, AssetAnchor))]
        crop, data = image_crop(asset, (ctx.ws.root / asset.path).read_bytes(), region, PAD)
        crops.append((data, crop.width, crop.height))
    texts: list[tuple[str, str | None]] = []  # (text, raw_ref) per place; raw_ref None: the local reading
    failures: list[Failure] = []
    tools = ctx.config.tools

    def fetch(batch):
        data = scan.image_batch_pdf([crops[i] for i in batch])
        return ocr.request_key(data, "application/pdf"), ocr.recognize_pdf(data)

    batches = spread(list(range(len(crops))), at_most=BATCH, workers=tools.scan_concurrency)
    for outcome in run_ordered(batches, fetch, max_workers=tools.scan_concurrency):  # in batch order
        if outcome.status == "ok":
            raw_ref, results = outcome.value
        else:  # these regions fall back to the local reading
            failures.append(service_failure(outcome.exception, [found[i][0] for i in outcome.task]))
            raw_ref, results = None, [None] * len(outcome.task)
        for result in results:
            texts.append((scan_reading(result.raw["layoutParsingResults"][0]) if result is not None else "", raw_ref))
    added = 0
    with ctx.ws.txn("tool:process:image_furniture") as state:
        by_id = {b.id: b for b in state.blocks}
        for n, ((figure_id, label, region), (text, raw_ref)) in enumerate(zip(found, texts), 1):
            figure = by_id[figure_id]
            asset = next(a for a in figure.anchors if isinstance(a, AssetAnchor))
            engine, version, ref = scan.ENGINE, ocr.model, raw_ref
            if not text.strip():  # the scan engine read nothing: the local reading of the region
                text = " ".join(ln.text for ln in readings.get(asset.asset, []) if _inside(_centre(ln.bbox), region))
                engine, version, ref = "reading", state.engines.get("reading", "local"), None
            if not text.strip():
                continue
            kind = labels.to_kind("layout", label)
            block_id = f"{figure_id}-f{_next(state, figure_id):03d}"
            anchor = AssetAnchor(asset=asset.asset, bbox=region, image_size=asset.image_size)
            obs = Observation(id=ids.observation_id(block_id, engine, 1), engine=engine, engine_version=version,
                              task=TaskKind.RECOGNIZE, anchor=anchor, raw_ref=ref, label=label, text=text,
                              status=ObservationStatus.OK)
            for block in state.blocks:  # right after the image, like the text read inside it
                if block.order > figure.order:
                    block.order += 1
            state.blocks.append(Block(
                id=block_id, kind=kind, order=figure.order + 1, status=BlockStatus.EXCLUDED, anchors=[anchor],
                observations=[obs], chosen_observation=obs.id, text=text, decisions=[
                    Decision(stage=DecisionStage.CONTENT_SOURCE, choice="image_furniture", actor=ACTOR, refs=[obs.id],
                             reason=f"the image's {label} region, which the scan engine left out, read again "
                                    f"({'by the scan engine' if engine == scan.ENGINE else 'locally'})",
                             evidence={"label": label}),
                    Decision(stage=DecisionStage.EXCLUDE, choice=kind.value, actor=ACTOR, refs=[obs.id],
                             reason=f"page furniture of the image: layout label {label!r}; written with its text",
                             evidence={"label": label})]))
            state.relations.append(Relation(id=ids.relation_id(RelationKind.CONTAINS, figure_id, block_id),
                                            kind=RelationKind.CONTAINS, src=figure_id, dst=block_id))
            state.ledger.append(LedgerEntry(item=f"i-{block_id}", unit="ocr_block", source=anchor,
                                            chars=len("".join(text.split())), disposition="excluded", block=block_id))
            added += 1
    return added, failures


def _next(state: DocumentState, figure_id: str) -> int:
    prefix = f"{figure_id}-f"
    return 1 + max((int(b.id[len(prefix):]) for b in state.blocks
                    if b.id.startswith(prefix) and b.id[len(prefix):].isdigit()), default=0)
