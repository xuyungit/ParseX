"""``recognize``: acquire content of pages with an engine; the selection step runs at the end (Q20).

Phase 1 engines: ``paddleocr`` (whole pages, batched; the pages whose native
layer failed), ``native`` (reports the native readings, no request),
``layout`` (shadow detection, see P1-9).  ``vlm`` region transcription is a
Phase 4 engine (guide §6.4).

``paddleocr`` with figure ``blocks`` reads the text and tables inside those
embedded images (Q42, PDF and DOCX): images are batched one per page into a
PDF, and what is read becomes blocks right after the image.
"""

from __future__ import annotations

from typing import Literal

import pymupdf

from parserx.content import scan
from parserx.content.select import integrate_image, integrate_scan_page, mark_scan_failed, transcribed
from parserx.ir.anchor import AssetAnchor, PdfAnchor
from parserx.ir.base import IRModel
from parserx.ir.enums import BlockKind, PageStatus
from parserx.scheduling import run_ordered, spread
from parserx.tools.context import ToolContext, ToolOutput, output, service_failure
from parserx.tools.envelope import Failure, FailureCode, ToolFailure
from parserx.tools.imaging import write_once
from parserx.tools.views import ObservationView, observation_view
from parserx.workspace.queries import block_unit
from parserx.workspace.views import PageRow, page_rows

OBSERVATION_VIEWS = 50  # at most this many observation views per result (JSON length stays bounded)
_SCAN_ENGINE_PAGES = frozenset({PageStatus.PENDING, PageStatus.FAILED, PageStatus.SKIPPED})


class RegionRef(IRModel):
    anchor: PdfAnchor | AssetAnchor
    hint: BlockKind | None = None


class RecognizeRequest(IRModel):
    pages: list[int] = []
    blocks: list[str] = []
    regions: list[RegionRef] = []
    engine: Literal["paddleocr", "vlm", "native", "layout"]
    force: bool = False  # recognise again pages already recognised by this engine (still cached)
    observations: bool = False  # include the new observations (text) in the result; read gives them per page


class SelectionOutcome(IRModel):
    target: str
    choice: str
    adopted: bool
    reason: str


class RecognizeResult(IRModel):
    observations: list[ObservationView]  # new observations when asked for (first OBSERVATION_VIEWS)
    observations_total: int
    pages: list[PageRow]
    selections: list[SelectionOutcome]


def run(ctx: ToolContext, req: RecognizeRequest) -> ToolOutput[RecognizeResult]:
    if req.engine == "vlm":
        raise ToolFailure(FailureCode.INVALID_REQUEST, "engine vlm (region transcription) arrives in Phase 4")
    if req.engine == "layout":
        from parserx.tools.layout_shadow import run_layout

        return run_layout(ctx, req)
    if req.engine == "native":
        return _native(ctx, req)
    return _paddleocr(ctx, req)


def _pages(ctx: ToolContext, req: RecognizeRequest) -> list[int]:
    state = ctx.ws.load()
    if req.regions or (req.blocks and not req.pages):
        pages = sorted({block_unit(state, b) for b in state.blocks if b.id in set(req.blocks)} - {None})
        if req.regions or not pages:
            raise ToolFailure(FailureCode.INVALID_REQUEST, f"engine {req.engine} recognises whole pages; give pages")
        return pages
    known = {p.n for p in state.pages}
    unknown = [n for n in req.pages if n not in known]
    if unknown:
        raise ToolFailure(FailureCode.NOT_FOUND, f"no pages {unknown}", targets=[f"p{n}" for n in unknown])
    if not req.pages:
        raise ToolFailure(FailureCode.INVALID_REQUEST, "give pages to recognise")
    return sorted(set(req.pages))


def _native(ctx: ToolContext, req: RecognizeRequest) -> ToolOutput[RecognizeResult]:
    pages = set(_pages(ctx, req))
    state = ctx.ws.load()
    views = [observation_view(b, o, geometry=False) for b in state.blocks if block_unit(state, b) in pages
             for o in b.observations if o.engine in ("native_pdf", "docx")]
    return output(RecognizeResult(observations=views[:OBSERVATION_VIEWS] if req.observations else [],
                                  observations_total=len(views),
                                  pages=[r for r in page_rows(state) if r.n in pages], selections=[]))


def _paddleocr(ctx: ToolContext, req: RecognizeRequest) -> ToolOutput[RecognizeResult]:
    state = ctx.ws.load()
    kinds = {b.id: b.kind for b in state.blocks}
    if req.blocks and not req.pages and not req.regions and all(kinds.get(b) == BlockKind.FIGURE for b in req.blocks):
        return _transcribe_images(ctx, req)
    if state.format != "pdf":
        raise ToolFailure(FailureCode.INVALID_REQUEST, "the scan engine reads PDF pages; DOCX images come in Phase 4")
    requested = _pages(ctx, req)
    status = {p.n: p.status for p in state.pages}
    failures: list[Failure] = []
    pages = []
    for n in requested:
        recognised = any(o.engine == scan.ENGINE for b in state.blocks if block_unit(state, b) == n
                         for o in b.observations)
        if status[n] in _SCAN_ENGINE_PAGES or (req.force and recognised):
            pages.append(n)
        else:
            reason = ("already recognised by the scan engine (force to repeat)" if recognised
                      else "its native text layer passed the quality check; the selection step keeps it")
            failures.append(Failure(code=FailureCode.INVALID_REQUEST, message=f"page {n} skipped: {reason}",
                                    retryable=False, targets=[f"p{n}"]))
    if not pages:
        return output(RecognizeResult(observations=[], observations_total=0,
                                      pages=[r for r in page_rows(state) if r.n in set(requested)], selections=[]),
                      failures=failures)
    tools = ctx.config.tools
    batches = spread(pages, at_most=tools.scan_batch_pages, workers=tools.scan_concurrency)
    try:
        ocr = ctx.ocr()
    except ToolFailure as exc:  # no scan engine (--no-ocr): as if it gave no result — the fallbacks stay, listed
        with ctx.ws.txn("tool:recognize") as state:
            for n in pages:
                mark_scan_failed(state, n, exc.failure.message, skipped=False)
        failures.append(exc.failure.model_copy(update={"targets": [f"p{n}" for n in pages]}))
        state = ctx.ws.load()
        return output(RecognizeResult(observations=[], observations_total=0,
                                      pages=[r for r in page_rows(state) if r.n in set(requested)], selections=[]),
                      failures=failures)
    source = ctx.ws.source_path

    def fetch(batch: list[int]):
        with pymupdf.open(source) as doc:
            data = scan.batch_pdf(doc, batch)
        results = ocr.recognize_pdf(data)
        return ocr.request_key(data, "application/pdf"), results

    outcomes = run_ordered(batches, fetch, max_workers=tools.scan_concurrency)
    selections: list[SelectionOutcome] = []
    new_ids: list[str] = []
    with ctx.ws.txn("tool:recognize") as state:
        for outcome in outcomes:
            batch = outcome.task
            targets = [f"p{n}" for n in batch]
            if outcome.status != "ok":
                failures.append(service_failure(outcome.exception, targets))
                if outcome.status == "cache_miss":
                    continue  # offline replay without a recorded response: the pages stay pending
                for n in batch:
                    mark_scan_failed(state, n, outcome.error or outcome.status,
                                     skipped=outcome.status == "skipped_budget")
                    selections.append(SelectionOutcome(target=f"p{n}", choice="native_fallback", adopted=False,
                                                       reason=outcome.error or outcome.status))
                continue
            raw_ref, results = outcome.value
            for n, result in zip(batch, results):
                new_ids += integrate_page(ctx, state, n, result.raw["layoutParsingResults"][0], raw_ref, ocr.model)
                selections.append(SelectionOutcome(target=f"p{n}", choice="scan_engine", adopted=True,
                                                   reason="native layer failed its quality check"))
        new_blocks = [b for b in state.blocks if b.id in set(new_ids)]
        views = [observation_view(b, o, geometry=False) for b in new_blocks for o in b.observations]
        rows = [r for r in page_rows(state) if r.n in set(requested)]
    return output(RecognizeResult(observations=views[:OBSERVATION_VIEWS] if req.observations else [],
                                  observations_total=len(views), pages=rows, selections=selections),
                  failures=failures)


SCAN_ENGINE_MEDIA = frozenset({"image/png", "image/jpeg"})


def _transcribe_images(ctx: ToolContext, req: RecognizeRequest) -> ToolOutput[RecognizeResult]:
    state = ctx.ws.load()
    blocks = {b.id: b for b in state.blocks}
    assets = {a.id: a for a in state.assets}
    done = transcribed(state)  # an image with nothing to read has no contains relation: read once all the same
    failures: list[Failure] = []
    tasks = []
    for block_id in dict.fromkeys(req.blocks):
        anchor = next((a for a in blocks[block_id].anchors if isinstance(a, AssetAnchor) and a.asset in assets), None)
        problem = None
        if anchor is None:
            problem = f"{block_id} has no image"
        elif block_id in done:
            problem = f"{block_id} is already transcribed"
        elif assets[anchor.asset].media_type not in SCAN_ENGINE_MEDIA:
            problem = f"{assets[anchor.asset].media_type} images cannot be read by the scan engine"
        if problem:
            failures.append(Failure(code=FailureCode.INVALID_REQUEST, message=problem, retryable=False,
                                    targets=[block_id]))
        else:
            tasks.append((block_id, assets[anchor.asset]))
    if not tasks:
        return output(RecognizeResult(observations=[], observations_total=0, pages=[], selections=[]),
                      failures=failures)
    tools = ctx.config.tools
    batches = spread(tasks, at_most=tools.scan_batch_pages, workers=tools.scan_concurrency)
    ocr = ctx.ocr()
    outcomes = run_ordered(batches, lambda batch: read_images(ctx, ocr, batch), max_workers=tools.scan_concurrency)
    selections, views = record_images(ctx, ocr, outcomes, failures)
    return output(RecognizeResult(observations=views[:OBSERVATION_VIEWS] if req.observations else [],
                                  observations_total=len(views), pages=[], selections=selections),
                  failures=failures)


def read_images(ctx: ToolContext, ocr, batch: list[tuple]) -> tuple[str, list]:
    """One scan-engine request for the images of *batch* ((figure, asset) pairs), a page each: (raw_ref, results)."""
    data = scan.image_batch_pdf([((ctx.ws.root / a.path).read_bytes(), a.width, a.height) for _, a in batch])
    return ocr.request_key(data, "application/pdf"), ocr.recognize_pdf(data)


def record_images(ctx: ToolContext, ocr, outcomes: list, failures: list[Failure]) -> tuple[list, list]:
    """Record the readings of *outcomes* (``read_images`` per batch, in batch order) inside their images; a batch
    the service failed is added to *failures*.  (selections, observation views)."""
    selections: list[SelectionOutcome] = []
    new_ids: list[str] = []
    with ctx.ws.txn("tool:recognize:images") as state:
        for outcome in outcomes:  # batch order
            if outcome.status != "ok":
                failures.append(service_failure(outcome.exception, [b for b, _ in outcome.task]))
                continue
            raw_ref, results = outcome.value
            for (block_id, asset), result in zip(outcome.task, results):
                read = scan.image_blocks(scan.PageScan(page=0, raw=result.raw["layoutParsingResults"][0],
                                                       raw_ref=raw_ref, engine_version=ocr.model),
                                         asset, figure=block_id)
                new_ids += integrate_image(state, block_id, read)
                selections.append(SelectionOutcome(target=block_id, choice="transcribed", adopted=True,
                                                   reason=f"{len(read.blocks)} blocks read inside the image"))
        new_blocks = [b for b in state.blocks if b.id in set(new_ids)]
        views = [observation_view(b, o, geometry=False) for b in new_blocks for o in b.observations]
    return selections, views


def integrate_page(ctx: ToolContext, state, n: int, page: dict, raw_ref: str, engine_version: str) -> list[str]:
    """Take the scan engine's reading of page *n* (one ``layoutParsingResults`` entry) into *state*: its blocks,
    their images cut from the page render, and the selection step; returns the new block ids."""
    pruned = page.get("prunedResult") or {}
    # figures, and pictures inside tables or text, are cut from the page render
    has_figures = any(scan.labels.to_kind(scan.ENGINE, e.get("block_label", "")) == BlockKind.FIGURE
                      or scan.take_pictures(str(e.get("block_content") or ""))[1]
                      for e in pruned.get("parsing_res_list") or [])
    page_state = next(p for p in state.pages if p.n == n)
    with pymupdf.open(ctx.ws.source_path) as doc:
        image = scan.render_page(doc, n, int(pruned.get("width") or 0)) if has_figures and pruned.get("width") else None
    scan_result = scan.page_blocks(scan.PageScan(page=n, raw=page, raw_ref=raw_ref, engine_version=engine_version),
                                   page_size=page_state.size_pt, first_seq=_next_block_seq(state, n),
                                   first_item=_next_item(state, n), page_image=image, page=page_state)
    for path, data in scan_result.asset_bytes.items():
        write_once(ctx.ws.root / path, data)
    return integrate_scan_page(state, n, scan_result)


def scan_engine_pages(state) -> set[int]:
    """Pages a scan engine reading may replace: pending, failed or skipped, or read by the scan engine before (a
    native layer that passed its quality check stays: its text is corrected, not replaced)."""
    scanned = {block_unit(state, b) for b in state.blocks if any(o.engine == scan.ENGINE for o in b.observations)}
    return {p.n for p in state.pages if p.status in _SCAN_ENGINE_PAGES or p.n in scanned}


def _next_block_seq(state, n: int) -> int:
    prefix = f"b-p{n:03d}-"  # the four digits after it: a split's part (…-0012-s1) keeps its block's number
    return max((int(b.id[len(prefix):len(prefix) + 4]) for b in state.blocks if b.id.startswith(prefix)), default=0) + 1


def _next_item(state, n: int) -> int:
    prefix = f"i-p{n:03d}-"
    return max((int(e.item[len(prefix):]) for e in state.ledger if e.item.startswith(prefix)), default=0) + 1
