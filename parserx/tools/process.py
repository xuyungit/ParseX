"""``run_pipeline``: the standard processing in one call — the first draft (plan P2-5, Q85).

The fixed sequence of the pipeline runtime; the program runs it before the agent (it is not the agent's tool):

1. ``recognize`` (paddleocr) — pages whose native layer failed;
2. ``recognize`` (layout) — shadow detection of pages, routing of figures;
2a. the local page reading (``tools/page_reading.py``, PDF): evidence for the two-way comparison of the output
    with each page image, listed in the worklist (guide §9.5, Q56);
3. ``describe_figure`` — every shown figure without a description, in one concurrent batch: its type says what the
   image is for (IO6);
3a. ``recognize`` (paddleocr) on the embedded images whose words are their content (Q42, IO6): an invoice, a
   certificate, a page, a table, a formula — their text and tables follow them.  A picture (a screenshot, a chart,
   a diagram, a photo) is shown with its note and not transcribed: the image is where its words are.  An image
   without a description (the service model off or failing) is transcribed by its route, SCAN or MIXED, as before;
3b. ``second_reading``: what the scan engine read with mathematics where no text layer checks it (scanned pages,
    transcribed images) read again by the service model; blocks the two readings differ on are listed;
4. confirmed cross-page table continuations (``tables.merge``);
5. titles through ``apply_structure``: DOCX styles and outline levels, then titles by agreeing evidence on native
   text (``hierarchy.typography_titles``) and the scan engine's title labels, unified as one outline (a level refused only because it depends on a title of the
   other source is sent again once both are in place);
6. paragraphs cut by a page break (``content.continuation``, PDF): the two parts joined;
6a. line breaks of scanned text the engine read as one run, put back from the local reading (P4);
6b. paragraphs the page marks with a bullet become list items (R1);
6c. text the local page reading sees where the output has nothing at all (``reading.compare.missed_lines``, Q133):
    added at its place and listed for review, not lost;
6d. those lines read again by the scan engine, one request for the document (``tools.added_text``);
7. ``check``.

It returns a compact summary and the worklist — what is left for judgment (unresolved items: pending pages, failed
blocks, uncertain tables, merge candidates, pending structure …).  Steps already done are skipped: calling it again
costs nothing.  One call, one call record: the
steps run on this call's context, so the requests are counted once.
"""

from __future__ import annotations

from collections import Counter
from pathlib import Path

from parserx.config.schema import ParserXConfig
from parserx.content.continuation import ACTOR as CONTINUATION_ACTOR, propose_continuations
from parserx.content.equation_numbers import number_equations
from parserx.content.furniture import mark_scan_furniture
from parserx.content.line_breaks import restore_line_breaks
from parserx.content.lists import mark_list_items
from parserx.content.select import transcribed
from parserx.hierarchy.docx_styles import ACTOR as DOCX_ACTOR, propose_docx_structure
from parserx.hierarchy.engine_titles import ACTOR as ENGINE_ACTOR, REASON as ENGINE_REASON, engine_titles
from parserx.hierarchy.levels import title_changes, unify_levels
from parserx.hierarchy.typography_titles import ACTOR as TYPOGRAPHY_ACTOR, REASON as TYPOGRAPHY_REASON, typography_titles
from parserx.ir.base import IRModel
from parserx.ir.anchor import AssetAnchor
from parserx.ir.enums import BlockKind, DocumentStatus, ImageRoute, PageStatus
from parserx.ir.state import AccountingSummary, DocumentState, ReadLine
from parserx.runtimes.events import Step
from parserx.scheduling.timing import StepClock
from parserx.tables.frames import split_frames
from parserx.tables.merge import propose_merges
from parserx.tools import (added_text, describe_figure, image_chain, image_furniture, recognize, second_reading,
                           structure)
from parserx.tools.submit import checked as check_accounts
from parserx.tools.context import ToolContext, ToolOutput, output
from parserx.tools.edit import add_missed_text
from parserx.tools.envelope import Failure, FailureCode, ToolFailure
from parserx.tools.layout_shadow import layout_todo
from parserx.tools.formulas import pages_to_read, read_formula_pages
from parserx.tools.page_reading import read_pages, reading_todo
from parserx.tools.views import unresolved_items
from parserx.workspace.queries import HIDDEN, ordered

MERGE_ACTOR = "program:tables.merge"
WORKLIST_MAX = 200


class ProcessRequest(IRModel):
    describe_figures: bool = True  # also subject to config runtime.describe_figures


class StepSummary(IRModel):
    step: str
    detail: str


class WorkItem(IRModel):
    target: str
    kind: str
    detail: str


class CheckBrief(IRModel):
    exportable: bool
    document_status: DocumentStatus
    accounting: AccountingSummary


class ProcessResult(IRModel):
    steps: list[StepSummary]
    pages: dict[str, int]  # page status → count
    blocks_by_kind: dict[str, int]
    figures: dict[str, int]  # described / failed / not_described (shown figures only; zero counts omitted)
    titles: dict[str, int]  # with_level / pending
    check: CheckBrief
    worklist: list[WorkItem]  # at most WORKLIST_MAX, in page / block order
    worklist_total: int


def run(ctx: ToolContext, req: ProcessRequest) -> ToolOutput[ProcessResult]:
    steps: list[StepSummary] = []
    failures: list[Failure] = []

    clock = StepClock(ctx.meter)  # where the time goes (speed plan §2)
    clock.start("recognize")
    state = ctx.ws.load()
    pending = [p.n for p in state.pages if p.status == PageStatus.PENDING]
    if pending:
        ctx.report(Step("process", "recognize", total=len(pending)))
        out = recognize.run(ctx, recognize.RecognizeRequest(pages=pending, engine="paddleocr"))
        failures += out.failures
        steps.append(StepSummary(step="recognize", detail=f"scan engine on {len(pending)} pages, "
                                                          f"{len(out.failures)} failures"))

    clock.start("scan_furniture")
    with ctx.ws.txn("tool:process:scan_furniture") as state:  # a scanning app's mark on every page (P3)
        furniture = mark_scan_furniture(state)
    if furniture:
        steps.append(StepSummary(step="scan_furniture", detail=f"{len(furniture)} repeated margin blocks excluded"))

    clock.start("layout")  # the pages; the figures go with their images below
    state = ctx.ws.load()
    if ctx.config.runtime.layout_shadow:
        pages, _ = layout_todo(state)
        if pages:
            ctx.report(Step("process", "layout", total=len(pages), detail={"figures": 0}))
            out = recognize.run(ctx, recognize.RecognizeRequest(pages=pages, engine="layout"))
            failures += out.failures
            steps.append(StepSummary(step="layout", detail=f"{len(pages)} pages"))

    if ctx.ws.load().format == "pdf":  # figures routed before the formula step: it reads the formula regions
        clock.start("figures")          # found inside them; they are described after it (P3)
        if todo := image_chain.figures_todo(ctx, ctx.ws.load(), describe=False):
            counts, problems = image_chain.run(ctx, todo, describe=False)
            failures += problems
            steps.append(StepSummary(step="figures", detail=f"{len(todo)} figures: {counts['turned']} turned "
                                                            f"upright, {counts['routed']} routed"))

    clock.start("reading")
    state = ctx.ws.load()
    if ctx.config.runtime.page_reading:  # evidence for the two-way comparison with the output (guide §9.5, Q56)
        todo = reading_todo(state)
        if todo:
            ctx.report(Step("process", "reading", done=0, total=len(todo)))
            steps.append(StepSummary(step="reading", detail=f"{read_pages(ctx, todo)} pages read locally"))

    clock.start("formulas")
    if ctx.config.runtime.formulas:  # formulas of native pages: whole pages read, passages chosen (Q70)
        pages = pages_to_read(ctx.ws.load())
        if pages:
            counts, problems = _unless_unconfigured(failures, lambda: read_formula_pages(ctx, pages), ({}, []))
            failures += problems
            steps.append(StepSummary(step="formulas", detail=f"{len(pages)} pages read; passages: " + ", ".join(
                f"{k} {v}" for k, v in sorted(counts.items()))))

    clock.start("equation_numbers")
    with ctx.ws.txn("tool:process:equation_numbers") as state:  # "(12)" at the right of a formula's line
        numbered = number_equations(state)
    if numbered:
        steps.append(StepSummary(step="equation_numbers", detail=f"{len(numbered)} formulas numbered"))

    # Embedded figures: turned upright, routed and described, each as soon as the step before is done (P3; a PDF's
    # were turned and routed above).  A request budget is spent on descriptions in block order: then they are asked
    # one step after the other.
    clock.start("images")
    describe = ctx.config.runtime.describe_figures and req.describe_figures
    in_chain = describe and ctx.gateway.budget.left()["requests"].get("vlm") is None
    if todo := image_chain.figures_todo(ctx, ctx.ws.load(), describe=in_chain):
        counts, problems = image_chain.run(ctx, todo, describe=in_chain)
        failures += problems
        steps.append(StepSummary(step="images", detail=f"{len(todo)} figures: {counts['turned']} turned upright, "
                                                       f"{counts['routed']} routed, {counts['described']} described, "
                                                       f"{counts['read']} read by the scan engine"))
    if describe and not in_chain:
        state = ctx.ws.load()
        todo = [b.id for b in state.blocks if b.kind == BlockKind.FIGURE and b.status not in HIDDEN
                and b.semantic is None]
        if todo:
            ctx.report(Step("process", "describe", total=len(todo)))
            out = _unless_unconfigured(failures, lambda: describe_figure.run(
                ctx, describe_figure.DescribeFigureRequest(blocks=todo)), _NOTHING_DESCRIBED)
            failures += out.failures
            steps.append(StepSummary(step="describe_figure", detail=f"{len(out.result.items)} of {len(todo)} "
                                                                    "figures described"))

    clock.start("transcribe")
    candidates = _textual_images(ctx.ws.load())
    if candidates:
        ctx.report(Step("process", "transcribe", total=len(candidates)))
        out = _unless_unconfigured(failures, lambda: recognize.run(
            ctx, recognize.RecognizeRequest(blocks=candidates, engine="paddleocr")), _NOTHING_READ)
        failures += out.failures
        steps.append(StepSummary(step="transcribe_images",
                                 detail=f"{len(out.result.selections)} of {len(candidates)} images read"))
    clock.start("image_reading")
    if _read_content_images(ctx):
        steps.append(StepSummary(step="image_reading", detail="content images read locally to check their text"))
    clock.start("image_furniture")
    if image_furniture.places(ctx.ws.load()):  # the running heads of pages shown as images, left out (Q151)
        ctx.report(Step("process", "image_furniture"))
        found, problems = image_furniture.read_again(ctx)
        failures += problems
        if found:
            steps.append(StepSummary(step="image_furniture", detail=f"{found} running heads, feet or page numbers "
                                                                     "of images read again"))
    clock.start("second_reading")
    rereads = second_reading.candidates(ctx.ws.load()) if ctx.config.runtime.second_reading else []
    if rereads:  # what the scan engine read with mathematics where no text layer checks it
        ctx.report(Step("process", "second_reading", total=len(rereads)))
        counts, problems = _unless_unconfigured(failures, lambda: second_reading.read_again(ctx), ({}, []))
        failures += problems
        if counts:
            steps.append(StepSummary(step="second_reading", detail="scanned blocks with mathematics read again: "
                                     + ", ".join(f"{k} {v}" for k, v in sorted(counts.items()))))

    clock.start("structure")
    ctx.report(Step("process", "structure"))
    if any(b.kind == BlockKind.TABLE for b in ctx.ws.load().blocks):
        with ctx.ws.txn("tool:process:frames") as state:
            frames = split_frames(state)  # several tables in one frame, before continuations are looked for
        if frames:
            steps.append(StepSummary(step="split_frames", detail=f"{len(frames)} frames holding several tables"))
    state = ctx.ws.load()
    merges = propose_merges(state)
    changes: list[tuple[str, list[dict]]] = [(MERGE_ACTOR, merges)] if merges else []
    applied = _apply(ctx, changes, failures)
    state = ctx.ws.load()
    if state.format == "docx":
        titles = docx_titles(ctx.ws.source_path, state, ctx.config)
    else:
        titles = pdf_titles(ctx.ws.source_path, state, ctx.config)
    applied += _apply(ctx, titles, failures, retry_level_skips=True)
    continuations = propose_continuations(ctx.ws.load())  # after the titles: a title ends a paragraph
    applied += _apply(ctx, [(CONTINUATION_ACTOR, continuations)], failures)
    if applied:
        steps.append(StepSummary(step="structure", detail=f"{applied} changes accepted"
                                                          + (f", {len(merges)} table continuations" if merges else "")
                                                          + (f", {len(continuations)} paragraph continuations"
                                                             if continuations else "")))

    clock.start("line_breaks")
    if ctx.ws.load().readings:  # scanned lines the page ends on purpose, read by the engine as one run (P4)
        with ctx.ws.txn("tool:process:line_breaks") as state:
            broken = restore_line_breaks(state)
        if broken:
            steps.append(StepSummary(step="line_breaks", detail=f"line breaks put back in {len(broken)} blocks"))
    clock.start("lists")
    with ctx.ws.txn("tool:process:lists") as state:  # paragraphs the page marks with a bullet (R1)
        items = mark_list_items(state, ctx.ws.source_path)
    if items:
        steps.append(StepSummary(step="lists", detail=f"{len(items)} bulleted list items"))
    clock.start("missed_text")
    if ctx.ws.load().readings:  # text the page shows where the output has nothing: added, not lost (Q133)
        with ctx.ws.txn("tool:process:missed_text") as state:
            added = add_missed_text(state)
        if added:
            steps.append(StepSummary(step="missed_text", detail=f"{len(added)} lines the local reading sees added"))
    clock.start("added_text")
    if added_text.places(ctx.ws.load()):  # those lines read again by the scan engine: scripts, word spaces
        ctx.report(Step("process", "added_text"))
        reread, problems = added_text.read_again(ctx)
        failures += problems
        if reread:
            steps.append(StepSummary(step="added_text", detail=f"{reread} added lines read again by the scan engine"))

    clock.start("check")
    ctx.report(Step("process", "check"))
    checked = check_accounts(ctx)
    steps.append(StepSummary(step="check", detail=f"{checked.document_status.value}, exportable {checked.exportable}"))
    clock.stop()
    with ctx.ws.txn("tool:process:timing") as state:
        state.stats.steps = [*state.stats.steps, *clock.steps]
    state = ctx.ws.load()
    return output(_summary(state, steps, checked), failures=failures)


def _image_asset(state: DocumentState, block) -> str | None:
    return next((a.asset for a in block.anchors if isinstance(a, AssetAnchor)), None)


def _read_content_images(ctx: ToolContext) -> int:
    """The local reading of each image transcribed as content, kept on its record: its text is checked against it
    before the image is left out of the Markdown (IO6-5).  An image the reader cannot decode is not read."""
    from parserx.reading.local import read_cached

    state = ctx.ws.load()
    done = transcribed(state)
    records = {r.id: r for r in state.images}
    assets = {a.id: a for a in state.assets}
    todo: list[str] = []
    for block in ordered(state):
        asset = _image_asset(state, block)
        record = records.get(asset)
        if block.id not in done or getattr(block.semantic, "type", None) != "content" or record is None \
                or record.reading is not None or asset not in assets or asset in todo:
            continue
        todo.append(asset)
    readings: dict[str, list[ReadLine]] = {}
    for outcome in ctx.map_local(lambda asset: read_cached(ctx.reader(), (ctx.ws.root / assets[asset].path)
                                                            .read_bytes(), ctx.cache), todo):
        if outcome.status == "ok":  # no reading: the image stays shown above its text
            readings[outcome.task] = [ReadLine(bbox=box, text=text, score=score) for box, text, score in outcome.value]
    if readings:
        with ctx.ws.txn("tool:process:image_reading") as state:
            for record in state.images:
                if record.id in readings:
                    record.reading = readings[record.id]
    return len(readings)


def _textual_images(state: DocumentState) -> list[str]:
    """Shown embedded images (not crops of scanned pages) not yet transcribed whose words are their content: those
    described as content; without a description, those routed SCAN or MIXED (IO6)."""
    routes = {r.id: r.route for r in state.images}
    assets = {a.id: a for a in state.assets}
    done = transcribed(state)
    out = []
    for block in ordered(state):
        asset = assets.get(_image_asset(state, block))
        if block.kind != BlockKind.FIGURE or block.status in HIDDEN or block.id in done or asset is None \
                or asset.role != "original":
            continue
        if block.semantic is None:
            wanted = routes.get(asset.id) in (ImageRoute.SCAN, ImageRoute.MIXED)
        else:
            wanted = getattr(block.semantic, "type", None) == "content"
        if wanted:
            out.append(block.id)
    return out


def _apply(ctx: ToolContext, batches: list[tuple[str, list[dict]]], failures: list[Failure], *,
           retry_level_skips: bool = False) -> int:
    """Apply structure changes per actor; with *retry_level_skips*, levels refused only as a skip are sent again
    once every batch is in (a title may depend on one of the other source)."""
    accepted = 0
    held: list[tuple[str, list[dict]]] = []
    for actor, changes in batches:
        if not changes:
            continue
        out = structure.run(ctx, structure.ApplyStructureRequest.model_validate({"changes": changes, "actor": actor}))
        failures += out.failures
        accepted += len(out.result.accepted)
        held.append((actor, [changes[r.index] for r in out.result.rejected if r.rule == "level_skip"]))
    if retry_level_skips:
        for actor, changes in held:
            if changes:
                out = structure.run(ctx, structure.ApplyStructureRequest.model_validate(
                    {"changes": changes, "actor": actor}))
                accepted += len(out.result.accepted)
    return accepted


_NOTHING_READ = output(recognize.RecognizeResult(observations=[], observations_total=0, pages=[], selections=[]))
_NOTHING_DESCRIBED = output(describe_figure.DescribeFigureResult(type=None, semantic=None, items=[]))


def _unless_unconfigured(failures: list[Failure], step, nothing):
    """Run an optional step; a service that is not configured (``--no-ocr``, ``--no-vlm``) skips it, listed as a
    failure, instead of failing the whole processing."""
    try:
        return step()
    except ToolFailure as exc:
        if exc.failure.code != FailureCode.SERVICE_ERROR:
            raise
        failures.append(exc.failure)
        return nothing


def docx_titles(source: Path, state: DocumentState, config: ParserXConfig) -> list[tuple[str, list[dict]]]:
    """DOCX titles: those the styles and outline levels declare first (Q24); the ones a hand-formatted document
    leaves undeclared from agreeing evidence (``hierarchy.typography_titles``, Q72), placed in the same outline.
    A block the styles already make a title or a list item keeps that."""
    declared = propose_docx_structure(state)
    taken = {c["block"] for c in declared}
    levels = {c["block"]: int(c["role"][1]) for c in declared if c["role"].startswith("H")}  # the file's own levels
    found = typography_titles(state, skip=taken, titled=any(levels.get(c["block"]) == 1 and "Title style" in c["reason"]
                                                          for c in declared))
    if not found:
        return [(DOCX_ACTOR, declared)]
    position = {b.id: i for i, b in enumerate(ordered(state))}
    texts = {b.id: b.text for b in state.blocks}
    combined = sorted([(b, texts[b], level) for b, level in levels.items()] + [t[:3] for t in found],
                      key=lambda t: position[t[0]])
    return [(DOCX_ACTOR, declared),
            (TYPOGRAPHY_ACTOR, title_changes(found, unify_levels(combined, fixed=set(levels)),
                                             reason=TYPOGRAPHY_REASON))]


def pdf_titles(source: Path, state: DocumentState, config: ParserXConfig) -> list[tuple[str, list[dict]]]:
    """PDF titles from two sources, unified as one outline: agreeing evidence on native text
    (``hierarchy.typography_titles``, Q72) and the scan engine's labels."""
    scanned = engine_titles(state)
    # the scan engine's labels rank sections below a document title: native titles use the same scale
    native = typography_titles(state, titled=any(t[3].get("label") == "doc_title" for t in scanned),
                               others={t[0] for t in scanned})
    position = {b.id: i for i, b in enumerate(ordered(state))}
    combined = sorted(native + scanned, key=lambda t: position[t[0]])
    levels = unify_levels([t[:3] for t in combined])
    return [(TYPOGRAPHY_ACTOR, title_changes(native, levels, reason=TYPOGRAPHY_REASON)),
            (ENGINE_ACTOR, title_changes(scanned, levels, reason=ENGINE_REASON))]


def _summary(state: DocumentState, steps: list[StepSummary], checked) -> ProcessResult:
    shown = [b for b in state.blocks if b.status not in HIDDEN]
    figures = Counter()
    for block in shown:
        if block.kind == BlockKind.FIGURE:
            failed = any(o.task.value == "describe" and o.status.value == "failed" for o in block.observations)
            figures["described" if block.semantic is not None else "failed" if failed else "not_described"] += 1
    titles = Counter("with_level" if b.level is not None else "pending" for b in shown if b.kind == BlockKind.TITLE)
    work = unresolved_items(state)
    return ProcessResult(
        steps=steps, pages=dict(sorted(Counter(p.status.value for p in state.pages).items())),
        blocks_by_kind=dict(sorted(Counter(b.kind.value for b in shown).items())),
        figures=dict(sorted(figures.items())), titles=dict(sorted(titles.items())),
        check=CheckBrief(exportable=checked.exportable, document_status=checked.document_status,
                         accounting=checked.accounting),
        worklist=[WorkItem(target=u.target, kind=u.kind.value, detail=u.detail) for u in work[:WORKLIST_MAX]],
        worklist_total=len(work),
    )
