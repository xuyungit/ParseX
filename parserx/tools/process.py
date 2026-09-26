"""``process``: the standard processing in one call (plan P2-5, guide §7 — the agent stays in charge).

The fixed sequence of the pipeline runtime, as a tool the agent calls first:

1. ``recognize`` (paddleocr) — pages whose native layer failed;
2. ``recognize`` (layout) — shadow detection of pages, routing of figures;
2a. the local page reading (``tools/page_reading.py``, PDF): evidence for the two-way comparison of the output
    with each page image, listed in the worklist (guide §9.5, Q56);
2b. ``recognize`` (paddleocr) on embedded images routed SCAN or MIXED (Q42): their text and tables follow them;
3. ``describe_figure`` — every shown figure without a description, in one concurrent batch, except images
   routed SCAN whose content was transcribed;
4. confirmed cross-page table continuations (``tables.merge``);
5. titles through ``apply_structure``: DOCX styles and outline levels, then titles by agreeing evidence on native
   text (``hierarchy.typography_titles``) and the scan engine's title labels, unified as one outline (a level refused only because it depends on a title of the
   other source is sent again once both are in place);
6. paragraphs cut by a page break (``content.continuation``, PDF): ``continues`` between the two parts;
7. ``check``.

It returns a compact summary and the worklist — what is left for judgment (unresolved items: pending pages, failed
blocks, uncertain tables, merge candidates, pending structure …) — so the agent need not read every page to find
the problems.  Steps already done are skipped: calling it again costs nothing.  One call, one call record: the
steps run on this call's context, so the requests are counted once.
"""

from __future__ import annotations

from collections import Counter
from pathlib import Path

from parserx.config.schema import ParserXConfig
from parserx.content.continuation import ACTOR as CONTINUATION_ACTOR, propose_continuations
from parserx.content.select import transcribed
from parserx.hierarchy.docx_styles import ACTOR as DOCX_ACTOR, propose_docx_structure
from parserx.hierarchy.engine_titles import ACTOR as ENGINE_ACTOR, REASON as ENGINE_REASON, engine_titles
from parserx.hierarchy.levels import title_changes, unify_levels
from parserx.hierarchy.typography_titles import ACTOR as TYPOGRAPHY_ACTOR, REASON as TYPOGRAPHY_REASON, typography_titles
from parserx.ir.base import IRModel
from parserx.ir.anchor import AssetAnchor
from parserx.ir.enums import BlockKind, DocumentStatus, ImageRoute, PageStatus, RelationKind
from parserx.ir.state import AccountingSummary, DocumentState
from parserx.runtimes.events import Step
from parserx.tables.merge import propose_merges
from parserx.tools import check_export, describe_figure, recognize, structure
from parserx.tools.context import ToolContext, ToolOutput, output
from parserx.tools.envelope import Failure
from parserx.tools.layout_shadow import layout_todo
from parserx.tools.formulas import formula_pages, read_formula_pages
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

    state = ctx.ws.load()
    pending = [p.n for p in state.pages if p.status == PageStatus.PENDING]
    if pending:
        ctx.report(Step("process", "recognize", total=len(pending)))
        out = recognize.run(ctx, recognize.RecognizeRequest(pages=pending, engine="paddleocr"))
        failures += out.failures
        steps.append(StepSummary(step="recognize", detail=f"scan engine on {len(pending)} pages, "
                                                          f"{len(out.failures)} failures"))

    state = ctx.ws.load()
    if ctx.config.runtime.layout_shadow:
        pages, figures = layout_todo(state)
        if pages or figures:
            ctx.report(Step("process", "layout", total=len(pages), detail={"figures": len(figures)}))
            out = recognize.run(ctx, recognize.RecognizeRequest(pages=pages, blocks=figures, engine="layout"))
            failures += out.failures
            steps.append(StepSummary(step="layout", detail=f"{len(pages)} pages, {len(figures)} figures"))

    state = ctx.ws.load()
    if ctx.config.runtime.page_reading:  # evidence for the two-way comparison with the output (guide §9.5, Q56)
        todo = reading_todo(state)
        if todo:
            ctx.report(Step("process", "reading", done=0, total=len(todo)))
            steps.append(StepSummary(step="reading", detail=f"{read_pages(ctx, todo)} pages read locally"))

    if ctx.config.runtime.formulas:  # formulas of native pages: whole pages read, passages chosen (Q70)
        pages = formula_pages(ctx.ws.load())
        if pages:
            counts, problems = read_formula_pages(ctx, pages)
            failures += problems
            steps.append(StepSummary(step="formulas", detail=f"{len(pages)} pages read; passages: " + ", ".join(
                f"{k} {v}" for k, v in sorted(counts.items()))))

    state = ctx.ws.load()
    candidates = _textual_images(ctx, state)
    if candidates:
        ctx.report(Step("process", "transcribe", total=len(candidates)))
        out = recognize.run(ctx, recognize.RecognizeRequest(blocks=candidates, engine="paddleocr"))
        failures += out.failures
        steps.append(StepSummary(step="transcribe_images",
                                 detail=f"{len(out.result.selections)} of {len(candidates)} images read"))

    if ctx.config.runtime.describe_figures and req.describe_figures:
        state = ctx.ws.load()
        read_as_text = _transcribed_scans(state)
        todo = [b.id for b in state.blocks if b.kind == BlockKind.FIGURE and b.status not in HIDDEN
                and b.semantic is None and b.id not in read_as_text]
        if todo:
            ctx.report(Step("process", "describe", total=len(todo)))
            out = describe_figure.run(ctx, describe_figure.DescribeFigureRequest(blocks=todo))
            failures += out.failures
            steps.append(StepSummary(step="describe_figure", detail=f"{len(out.result.items)} of {len(todo)} "
                                                                    "figures described"))

    state = ctx.ws.load()  # text an uncertain image shows that its description does not carry (P4-6)
    candidates = _textual_images(ctx, state, uncarried=True)
    candidates = [c for c in candidates if routes_of(state).get(c) == ImageRoute.UNCERTAIN]
    if candidates:
        ctx.report(Step("process", "transcribe", total=len(candidates)))
        out = recognize.run(ctx, recognize.RecognizeRequest(blocks=candidates, engine="paddleocr"))
        failures += out.failures
        steps.append(StepSummary(step="transcribe_uncarried",
                                 detail=f"{len(out.result.selections)} of {len(candidates)} images read"))

    ctx.report(Step("process", "structure"))
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

    ctx.report(Step("process", "check"))
    checked = check_export._checked(ctx)
    steps.append(StepSummary(step="check", detail=f"{checked.document_status.value}, exportable {checked.exportable}"))
    state = ctx.ws.load()
    return output(_summary(state, steps, checked), failures=failures, unresolved=unresolved_items(state))


def _image_asset(state: DocumentState, block) -> str | None:
    return next((a.asset for a in block.anchors if isinstance(a, AssetAnchor)), None)


def _textual_images(ctx: ToolContext, state: DocumentState, *, uncarried: bool = False) -> list[str]:
    """Shown embedded images (not crops of scanned pages) not yet transcribed (Q42): routed SCAN or MIXED; with
    *uncarried* (after the descriptions), routed UNCERTAIN when the local reading of the image has text its
    description does not carry (P4-6, conservation: the small print of a screenshotted form, an equation)."""
    routes = {r.id: r.route for r in state.images}
    assets = {a.id: a for a in state.assets}
    done = transcribed(state)
    out = []
    for block in ordered(state):
        asset = assets.get(_image_asset(state, block))
        if block.kind != BlockKind.FIGURE or block.status in HIDDEN or block.id in done or asset is None \
                or asset.role != "original":
            continue
        route = routes.get(asset.id)
        if route in (ImageRoute.SCAN, ImageRoute.MIXED) or (uncarried and route == ImageRoute.UNCERTAIN
                                                            and _text_not_carried(ctx, block, asset)):
            out.append(block.id)
    return out


def _text_not_carried(ctx: ToolContext, block, asset) -> bool:
    """Whether the local reading of an image has a line its description does not carry, measured as the page
    comparison measures an unaccounted line (``reading/compare.py``: two letters or digits, its tolerance)."""
    from rapidfuzz import fuzz

    from parserx.reading.compare import SOMEWHERE, normalize
    from parserx.reading.local import read_cached
    from parserx.render.markdown import _semantic_block

    try:
        lines = read_cached(ctx.reader(), (ctx.ws.root / asset.path).read_bytes(), ctx.cache)
    except Exception:  # noqa: BLE001 - an image the reader cannot decode (e.g. EMF) gives no evidence
        return False
    carried = normalize(_semantic_block(block))
    return any(len(text := normalize(line)) >= 2 and (not carried or fuzz.partial_ratio(text, carried) < SOMEWHERE)
               for _, line, _ in lines)


def routes_of(state: DocumentState) -> dict[str, ImageRoute]:
    """Figure block id → its image's route."""
    routes = {r.id: r.route for r in state.images}
    return {b.id: routes.get(_image_asset(state, b)) for b in state.blocks if b.kind == BlockKind.FIGURE}


def _transcribed_scans(state: DocumentState) -> set[str]:
    """Images routed SCAN whose content now follows them as text: a description would repeat it."""
    routes = {r.id: r.route for r in state.images}
    done = {r.src for r in state.relations if r.kind == RelationKind.CONTAINS}
    return {b.id for b in state.blocks if b.id in done and routes.get(_image_asset(state, b)) == ImageRoute.SCAN}


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


def docx_titles(source: Path, state: DocumentState, config: ParserXConfig) -> list[tuple[str, list[dict]]]:
    """DOCX titles: those the styles and outline levels declare first (Q24); the ones a hand-formatted document
    leaves undeclared from agreeing evidence (``hierarchy.typography_titles``, Q72), placed in the same outline.
    A block the styles already make a title or a list item keeps that."""
    declared = propose_docx_structure(state)
    taken = {c["block"] for c in declared if c["op"] == "set_role"}
    found = typography_titles(state, skip=taken, titled=any(c["op"] == "set_level" and c["level"] == 1
                                                          and "Title style" in c["reason"] for c in declared))
    if not found:
        return [(DOCX_ACTOR, declared)]
    position = {b.id: i for i, b in enumerate(ordered(state))}
    texts = {b.id: b.text for b in state.blocks}
    combined = sorted([(c["block"], texts[c["block"]], c["level"]) for c in declared if c["op"] == "set_level"]
                      + [t[:3] for t in found], key=lambda t: position[t[0]])
    fixed = {c["block"] for c in declared if c["op"] == "set_level"}  # the file's own levels stay
    return [(DOCX_ACTOR, declared),
            (TYPOGRAPHY_ACTOR, title_changes(found, unify_levels(combined, fixed=fixed), reason=TYPOGRAPHY_REASON))]


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
