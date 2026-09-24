"""``recognize``: acquire content of pages with an engine; the selection step runs at the end (Q20).

Phase 1 engines: ``paddleocr`` (whole pages, batched; the pages whose native
layer failed), ``native`` (reports the native readings, no request),
``layout`` (shadow detection, see P1-9).  ``vlm`` region transcription is a
Phase 4 engine (guide §6.4).
"""

from __future__ import annotations

from typing import Literal

import fitz

from parserx.content import scan
from parserx.content.select import integrate_scan_page, mark_scan_failed
from parserx.ir.anchor import AssetAnchor, PdfAnchor
from parserx.ir.base import IRModel
from parserx.ir.enums import BlockKind, PageStatus
from parserx.scheduling import run_ordered
from parserx.tools.context import ToolContext, ToolOutput, output, service_failure
from parserx.tools.envelope import Change, Failure, FailureCode, ToolFailure
from parserx.tools.imaging import write_once
from parserx.tools.views import ObservationView, observation_view, unresolved_items
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


class SelectionOutcome(IRModel):
    target: str
    choice: str
    adopted: bool
    reason: str


class RecognizeResult(IRModel):
    observations: list[ObservationView]  # new observations (first OBSERVATION_VIEWS)
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
    views = [observation_view(b, o) for b in state.blocks if block_unit(state, b) in pages
             for o in b.observations if o.engine in ("native_pdf", "docx")]
    return output(RecognizeResult(observations=views[:OBSERVATION_VIEWS], observations_total=len(views),
                                  pages=[r for r in page_rows(state) if r.n in pages], selections=[]))


def _paddleocr(ctx: ToolContext, req: RecognizeRequest) -> ToolOutput[RecognizeResult]:
    state = ctx.ws.load()
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
    size = ctx.config.tools.scan_batch_pages
    batches = [pages[i:i + size] for i in range(0, len(pages), size)]
    ocr = ctx.ocr()
    source = ctx.ws.source_path

    def fetch(batch: list[int]):
        with fitz.open(source) as doc:
            data = scan.batch_pdf(doc, batch)
        results = ocr.recognize_pdf(data)
        return ocr.request_key(data, "application/pdf"), results

    outcomes = run_ordered(batches, fetch, max_workers=2)
    selections: list[SelectionOutcome] = []
    new_ids: list[str] = []
    before = {p.n: p.status for p in state.pages}
    with ctx.ws.txn("tool:recognize") as state:
        sizes = {p.n: p.size_pt for p in state.pages}
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
            with fitz.open(source) as doc:
                for n, result in zip(batch, results):
                    page = result.raw["layoutParsingResults"][0]
                    pruned = page.get("prunedResult") or {}
                    has_figures = any(scan.labels.to_kind(scan.ENGINE, e.get("block_label", "")) == BlockKind.FIGURE
                                      for e in pruned.get("parsing_res_list") or [])
                    image = scan.render_page(doc, n, int(pruned.get("width") or 0)) if has_figures and \
                        pruned.get("width") else None
                    scan_result = scan.page_blocks(
                        scan.PageScan(page=n, raw=page, raw_ref=raw_ref, engine_version=ocr.model),
                        page_size=sizes[n], first_seq=_next_block_seq(state, n), first_item=_next_item(state, n),
                        page_image=image)
                    for path, data in scan_result.asset_bytes.items():
                        write_once(ctx.ws.root / path, data)
                    new_ids += integrate_scan_page(state, n, scan_result)
                    selections.append(SelectionOutcome(target=f"p{n}", choice="scan_engine", adopted=True,
                                                       reason="native layer failed its quality check"))
        new_blocks = [b for b in state.blocks if b.id in set(new_ids)]
        views = [observation_view(b, o) for b in new_blocks for o in b.observations]
        rows = [r for r in page_rows(state) if r.n in set(requested)]
        diff = [Change(target=f"p{p.n}", field="status", before=before[p.n].value, after=p.status.value)
                for p in state.pages if p.status != before[p.n]]
        unresolved = [u for u in unresolved_items(state) if u.target in {f"p{n}" for n in requested}]
    return output(RecognizeResult(observations=views[:OBSERVATION_VIEWS], observations_total=len(views), pages=rows,
                                  selections=selections), failures=failures, diff=diff, unresolved=unresolved)


def _next_block_seq(state, n: int) -> int:
    prefix = f"b-p{n:03d}-"
    return max((int(b.id[len(prefix):]) for b in state.blocks if b.id.startswith(prefix)), default=0) + 1


def _next_item(state, n: int) -> int:
    prefix = f"i-p{n:03d}-"
    return max((int(e.item[len(prefix):]) for e in state.ledger if e.item.startswith(prefix)), default=0) + 1
