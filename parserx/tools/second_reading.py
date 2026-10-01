"""A second reading, by service models, of what the scan engine read with mathematics where no text layer checks it.

On a native page the text layer checks a formula the scan engine reads (``tools/formulas.py``, Q70).  A scanned page,
or an image the scan engine transcribed, has no text layer: what the engine read stood unchecked, and it misreads
formulas (μL as pL, a subscript 57 as 56) and the numbers beside them.  So each block of such content with
mathematics — a display formula, a paragraph with inline formulas — is read again on its own image, without the
engine's reading, by each configured reader (``tools.second_readers``, all at once).  Readings are compared by their
letters and digits (LaTeX read as its characters, case kept: J_K is not J_k).  One reader: the block is listed where
it differs.  Several: where they all differ from the engine on a same character — the engine is then the odd one of
three readings; one reader alone differing is that reader's misread (measured: luna + DeepSeek caught 8 of 9 engine
misreads with 3 false items in 40, luna alone 9 with 9; docs/v2_pipeline_scripts.md §10.4).  A listed block
(``second_reading``) is decided by the agent, who looks at the image: the readers are not a majority vote, since
models "correct" what is printed alike (No read as N₀ by every model tried).  The output does not change.
"""

from __future__ import annotations

import hashlib
import io
import unicodedata
from collections import Counter
from functools import reduce

import pymupdf

from parserx.content.latex import characters
from parserx.content.select import UNPROMPTED
from parserx.ir import ids
from parserx.ir.anchor import AssetAnchor, PdfAnchor
from parserx.ir.block import Block
from parserx.ir.decision import Decision
from parserx.ir.enums import BlockKind, DecisionStage, ObservationStatus, RelationKind, TaskKind
from parserx.ir.observation import Observation
from parserx.ir.rotation import shown
from parserx.ir.state import DocumentState
from parserx.reading.compare import has_math
from parserx.scheduling import run_ordered
from parserx.tools.context import ToolContext, service_failure
from parserx.tools.envelope import Failure, FailureCode, ToolFailure
from parserx.tools.imaging import write_once
from parserx.workspace.queries import HIDDEN, block_map, ordered, scanned_pages

ACTOR = "program:tools.second_reading"
LABEL = "second_reading"  # the observation holding a reader's reading
RECHECK = "recheck"  # one made for the agent's correction of the block's characters (``recheck``)
VERSION = "second-reading"  # its engine version: "second-reading:<reader>"
DPI = 200
MAX_TOKENS = 32768  # the answer's budget, reasoning included (DeepSeek at medium thought 8 192 away on a long block)
PROMPT = (
    "照图逐字抄写这一块的全部内容。正文照抄；数学用 LaTeX，行内用 $…$，独立成行的公式用 $$…$$，公式编号照抄。"
    "照原件写，不改写、不补全、不纠正原件的错字；看不清的字写〔?〕。图的边上可能露出相邻的内容，不要抄。只输出抄写结果。")
_KINDS = frozenset({BlockKind.TEXT, BlockKind.FORMULA})


def candidates(state: DocumentState) -> list[Block]:
    """Shown blocks the scan engine read with mathematics where no text layer checks them — on a scanned page, or
    inside an image it transcribed — not read a second time yet."""
    if state.format != "pdf":
        return []
    scanned = scanned_pages(state)
    inside = {r.dst for r in state.relations if r.kind == RelationKind.CONTAINS}
    out = []
    for block in ordered(state):
        if block.status in HIDDEN or block.kind not in _KINDS or any(o.label == LABEL for o in block.observations):
            continue
        chosen = next((o for o in block.observations if o.id == block.chosen_observation), None)
        if chosen is None or chosen.engine != "paddleocr":
            continue
        page = _page_anchor(block)
        if ((page is not None and page.page in scanned) or block.id in inside) \
                and (block.kind == BlockKind.FORMULA or has_math(block.text)):
            out.append(block)
    return out


def differences(text: str, other: str) -> tuple[Counter, Counter]:
    """The letters and digits *text* has that *other* lacks, and those *other* adds: LaTeX read as its characters,
    width and script forms folded (²: 2), case kept."""
    ours, theirs = Counter(_letters(text)), Counter(_letters(other))
    return ours - theirs, theirs - ours


def disagrees(text: str, other: str) -> tuple[Counter, Counter] | None:
    """Where the block's *text* and a second reading *other* differ (``differences``), or None where they agree — the
    whole of it, or the part the block's image shows: the engine reads a paragraph cut by a column break whole, at the
    place before the cut, and a reading of the image then holds one run of the block's characters and nothing else."""
    lacks, adds = differences(text, other)
    if not lacks and not adds:
        return None
    seen = _letters(other)
    if seen and not adds and seen in _letters(text):
        return None
    return lacks, adds


def _letters(text: str) -> str:
    return "".join(ch for ch in unicodedata.normalize("NFKC", characters(text or "")) if ch.isalnum())


def readers(ctx: ToolContext) -> tuple[list[tuple[str, object]], list[str]]:
    """The configured readers that can be asked, (name, service), and the names left out (no such entry, or not
    configured); none left: the service model alone."""
    out, missing = [], []
    for reader in ctx.config.tools.second_readers:
        try:
            service = ctx.vlm_using(reader.use, reader.reasoning_effort)
        except ToolFailure:
            service = None
        if service is None:
            missing.append(reader.use)
        else:
            out.append((reader.use, service))
    return out or [("service", ctx.vlm(ctx.config.tools.ask_reasoning_effort))], missing


def read_again(ctx: ToolContext) -> tuple[dict[str, int], list[Failure]]:
    """Read the candidates again on their images, by every reader at once; record each reading and whether the block
    is listed.  (counts, failures)"""
    state = ctx.ws.load()
    todo = candidates(state)
    if not todo:
        return {}, []
    read, missing, failures = _ask(ctx, state, todo)
    counts: Counter[str] = Counter({f"reader {name} missing": 1 for name in missing})
    with ctx.ws.txn("tool:process:second_reading") as state:
        blocks = block_map(state)
        for block_id, by_reader in read.items():
            block = blocks[block_id]
            _record(block, by_reader, LABEL)
            found = differing(block.text or "", list(by_reader.values()))
            block.decisions.append(Decision(
                stage=DecisionStage.CONTENT_SOURCE, choice=LABEL, actor=ACTOR,
                evidence={"readers": ",".join(sorted(by_reader)), "listed": found is not None,
                          "lacks": sum(found[0].values()) if found else 0,
                          "adds": sum(found[1].values()) if found else 0},
                reason=(f"read again on its image by {', '.join(sorted(by_reader))}: "
                        + ("the readings differ from the scan engine's on the same characters: listed for a look at "
                           "the image; the scan engine's reading stays" if found else
                           "no reading differs from the scan engine's, or they differ from it on nothing alike"))))
            counts["listed" if found else "agree"] += 1
    return dict(counts), failures


def recheck(ctx: ToolContext, block_ids: list[str]) -> list[Failure]:
    """Read blocks again on their images alone, as the second reading does — every reader at once, shown neither the
    draft nor the correction — for the agent's correction of their characters, which stands only where every reading
    shows it (``content/select.as_printed``).  The readings join the blocks' observations (``RECHECK``), kept whatever
    becomes of the correction; a block read so before is not read again.  No image to cut (a Word document's own
    text) or no reader: none, and the correction is judged by the block's other readings.  Failures."""
    blocks = block_map(ctx.ws.load())
    todo = [blocks[i] for i in block_ids
            if i in blocks and not any(o.label in UNPROMPTED for o in blocks[i].observations)]
    if not todo:
        return []
    try:
        read, _, failures = _ask(ctx, ctx.ws.load(), todo)
    except ToolFailure as exc:  # no service model configured
        return [exc.failure]
    if read:
        with ctx.ws.txn("tool:edit_draft:recheck") as state:
            blocks = block_map(state)
            for block_id, by_reader in read.items():
                _record(blocks[block_id], by_reader, RECHECK)
    return failures


def _ask(ctx: ToolContext, state: DocumentState, blocks: list[Block]
         ) -> tuple[dict[str, dict[str, str]], list[str], list[Failure]]:
    """Each block's image read by every reader at once: ({block: {reader: reading}}, readers missing, failures).  A
    reader that wrote nothing has not read the block: the others' readings decide alone."""
    crops = _images(ctx, state, blocks)
    if not crops:
        return {}, [], []
    asked, missing = readers(ctx)
    tasks = [(block_id, path, name, service) for block_id, path in crops for name, service in asked]
    outcomes = run_ordered(tasks, lambda t: t[3].call(
        "describe_image", t[1], PROMPT, temperature=0.0, max_tokens=MAX_TOKENS, structured_output_mode="off",
        json_schema_name="parserx_second_reading"), max_workers=ctx.config.services.vlm.max_concurrent)
    failures: list[Failure] = []
    read: dict[str, dict[str, str]] = {}
    for outcome in outcomes:
        block_id, _, name, _ = outcome.task
        if outcome.exception is not None:
            failures.append(service_failure(outcome.exception, [block_id]))
        elif str(outcome.value or "").strip():
            read.setdefault(block_id, {})[name] = str(outcome.value).strip()
        else:
            failures.append(Failure(code=FailureCode.SERVICE_ERROR, retryable=False, targets=[block_id],
                                    message=f"{name}: an empty answer to the second reading"))
    return read, missing, failures


def _images(ctx: ToolContext, state: DocumentState, blocks: list[Block]) -> list[tuple[str, object]]:
    """Each block's image, (block id, path): its place on its page, or in the image it was read in; blocks without
    one (a Word document's own text) left out."""
    pages = {p.n: p for p in state.pages}
    holder = {r.dst: r.src for r in state.relations if r.kind == RelationKind.CONTAINS}
    by_id, assets = block_map(state), {a.id: a for a in state.assets}
    crops, doc = [], None
    try:
        for block in blocks:
            anchor = _page_anchor(block)
            if anchor is not None and state.format == "pdf":  # its place on the page
                doc = doc or pymupdf.open(ctx.ws.source_path)
                clip = pymupdf.Rect(shown(pages.get(anchor.page), anchor.bbox)) + (-4, -4, 4, 4)
                png = doc[anchor.page - 1].get_pixmap(dpi=DPI, clip=clip).tobytes("png")
            else:  # read inside an image: its place in that image
                png = _crop(ctx, block, by_id.get(holder.get(block.id, "")), assets)
            if png is None:
                continue
            path = ctx.ws.root / "renders" / f"second-{block.id}-{hashlib.sha256(png).hexdigest()[:8]}.png"
            write_once(path, png)
            crops.append((block.id, path))
    finally:
        if doc is not None:
            doc.close()
    return crops


def _record(block: Block, by_reader: dict[str, str], label: str) -> None:
    for name, text in by_reader.items():
        block.observations.append(Observation(
            id=ids.observation_id(block.id, "vlm", 1 + sum(o.engine == "vlm" for o in block.observations)),
            engine="vlm", engine_version=f"{VERSION}:{name}", task=TaskKind.RECOGNIZE, anchor=block.anchors[0],
            label=label, text=text, status=ObservationStatus.OK))


def differing(text: str, readings: list[str]) -> tuple[Counter, Counter] | None:
    """Where the block's *text* and its second *readings* differ so that the block is listed, or None: every reading
    differs (``disagrees``), and with several, on a same character — the letters and digits they all lack, and those
    they all add."""
    found = [disagrees(text, r) for r in readings]
    if not found or any(f is None for f in found):
        return None
    lacks = reduce(lambda a, b: a & b, (f[0] for f in found))
    adds = reduce(lambda a, b: a & b, (f[1] for f in found))
    return (lacks, adds) if lacks or adds else None


def readings(block: Block) -> list[Observation]:
    """The block's latest second reading by each reader."""
    latest: dict[str, Observation] = {}
    for o in block.observations:
        if o.label == LABEL:
            latest[o.engine_version] = o
    return list(latest.values())


def _crop(ctx: ToolContext, block: Block, figure: Block | None, assets: dict) -> bytes | None:
    """The region of the image the scan engine read *block* in (its pixel anchor: on the image asset it names, else
    on *figure*'s), or None."""
    from PIL import Image

    chosen = [o.anchor for o in block.observations if o.id == block.chosen_observation]
    pixel = next((a for a in [*block.anchors, *chosen]
                  if getattr(a, "coord_space", None) == "image_px" and getattr(a, "image_size", None)), None)
    image = getattr(pixel, "asset", None) or (
        next((a.asset for a in figure.anchors if isinstance(a, AssetAnchor)), None) if figure else None)
    asset = assets.get(image)
    if pixel is None or asset is None:
        return None
    with Image.open(ctx.ws.root / asset.path) as img:
        kx, ky = img.width / pixel.image_size[0], img.height / pixel.image_size[1]
        x0, y0, x1, y1 = pixel.bbox
        box = (max(0, int(x0 * kx) - 4), max(0, int(y0 * ky) - 4), min(img.width, int(x1 * kx) + 4),
               min(img.height, int(y1 * ky) + 4))
        out = io.BytesIO()
        img.convert("RGB").crop(box).save(out, "PNG")
    return out.getvalue()


def _page_anchor(block: Block) -> PdfAnchor | None:
    return next((a for a in block.anchors if isinstance(a, PdfAnchor) and a.coord_space == "page_pt"), None)
