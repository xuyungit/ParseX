"""Embedded figures from their image to their description, each as soon as the step before it is done (speed plan P3).

Before, each step went over every figure before the next one began: turned upright (local reading), routed (pixels
and the layout detector), described (the service model).  Here a figure's image is turned and routed on a local
thread and its description asked as soon as that is done, while the other figures are still being read: the local
work and the waiting on the service overlap.

Nothing is recorded while the figures are on their way.  When all are through, the results are recorded step by
step in the figures' order by the functions the steps use on their own (``upright.record``,
``layout_shadow.record_figures``, ``describe_figure.record_descriptions``); every request is made from what the
steps one after another would have seen, so the workspace ends as it would have after them (the 31 documents
replayed give the same requests and outputs).
"""

from __future__ import annotations

import queue
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass

from parserx.ir.anchor import AssetAnchor
from parserx.ir.enums import BlockKind, ImageRoute
from parserx.runtimes.events import Step
from parserx.tools import describe_figure, upright
from parserx.tools.context import ToolContext, service_failure
from parserx.tools.envelope import Failure, FailureCode, ToolFailure
from parserx.tools.layout_shadow import figure_route, record_figures
from parserx.workspace.queries import HIDDEN, ordered


@dataclass
class _Figure:
    block: str
    way: tuple | None = None  # upright.look's result
    anchor: AssetAnchor | None = None  # the figure's image after the turn
    route: tuple | None = None  # figure_route's result
    task: object = None  # the description request, once asked
    answer: object = None  # (task, value) of the answer


def figures_todo(ctx: ToolContext, state, *, describe: bool) -> list[str]:
    """Figures the chain has work for: an image to turn upright, to route, or (*describe*) to describe."""
    turn = set(upright.todo(state)) if ctx.config.runtime.upright_images else set()
    routed = {r.id for r in state.images}
    out = []
    for block in ordered(state):
        if block.kind != BlockKind.FIGURE or block.status in HIDDEN:
            continue
        unrouted = ctx.config.runtime.layout_shadow and any(
            isinstance(a, AssetAnchor) and a.asset not in routed for a in block.anchors)
        if block.id in turn or unrouted or (describe and block.semantic is None):
            out.append(block.id)
    return out


def run(ctx: ToolContext, blocks_todo: list[str], *, describe: bool) -> tuple[dict[str, int], list[Failure]]:
    """Turn, route and describe *blocks_todo* (figures, in reading order); (counts by step, failures)."""
    state = ctx.ws.load()
    blocks = {b.id: b for b in state.blocks}
    assets = {a.id: a for a in state.assets}
    routed = {r.id for r in state.images}
    turn = set(upright.todo(state)) if ctx.config.runtime.upright_images else set()
    figures = {fid: _Figure(fid) for fid in blocks_todo}
    failures: list[Failure] = []
    describer = None
    if describe:
        try:
            describer = describe_figure.Describer(ctx)
        except ToolFailure as exc:  # the service is not there: the figures go undescribed, listed (as the step)
            if exc.failure.code != FailureCode.SERVICE_ERROR:
                raise
            failures.append(exc.failure)

    def prepare(figure: _Figure) -> _Figure:  # a local thread: the turn, the route, the request with its hint
        block = blocks[figure.block]
        if figure.block in turn:
            figure.way = upright.look(ctx, block, assets)
        new = figure.way[3] if figure.way else None
        figure.anchor = upright.upright_anchor(new) if new is not None else next(
            (a for a in block.anchors if isinstance(a, AssetAnchor) and a.asset in assets), None)
        asset = new or (assets[figure.anchor.asset] if figure.anchor is not None else None)
        if ctx.config.runtime.layout_shadow and asset is not None and asset.id not in routed:
            figure.route = figure_route(ctx, figure.block, figure.anchor, asset)
        decorative = figure.route is not None and figure.route[2] is not None \
            and figure.route[2].route == ImageRoute.DECORATIVE
        if describer is not None and block.semantic is None and not decorative:
            known = {**assets, **({new.id: new} if new is not None else {})}
            made = describer.task(state, figure.block, blocks, known, anchor=figure.anchor)
            figure.task = made if isinstance(made, Failure) else describer.hinted(made)
        return figure

    events: queue.Queue = queue.Queue()
    total, finished = len(figures), 0
    ctx.report(Step("process", "images", done=0, total=total))
    with ThreadPoolExecutor(max_workers=max(1, ctx.config.tools.local_workers)) as local, \
            ThreadPoolExecutor(max_workers=max(1, ctx.config.services.vlm.max_concurrent)) as remote:
        for figure in figures.values():
            local.submit(prepare, figure).add_done_callback(lambda f, fig=figure: events.put(("prepared", fig, f)))
        waiting = total
        while waiting:
            kind, figure, future = events.get()
            waiting -= 1
            if (problem := future.exception()) is not None and kind == "asked":
                failures.append(service_failure(problem, [figure.block]))
            elif problem is not None:
                failures.append(Failure(code=FailureCode.INTERNAL_ERROR, retryable=False, targets=[figure.block],
                                        message=f"{type(problem).__name__}: {problem}"))
            elif kind == "prepared" and isinstance(figure.task, Failure):
                failures.append(figure.task)
            elif kind == "prepared" and figure.task is not None:
                waiting += 1  # its description is asked now, while other figures are still read
                remote.submit(describer.ask, figure.task, hint=False).add_done_callback(
                    lambda f, fig=figure: events.put(("asked", fig, f)))
                continue
            elif kind == "asked":
                figure.answer = future.result()
            finished += 1
            ctx.report(Step("process", "images", done=finished, total=total))

    # Recorded step by step in the figures' order, as the steps one after another would have.
    ordered_figures = [figures[fid] for fid in blocks_todo]
    turned = upright.record(ctx, {f.block: f.way for f in ordered_figures if f.way is not None})
    routes = [f.route for f in ordered_figures if f.route is not None]
    if routes:
        version = ctx.detector().version
        with ctx.ws.txn("tool:recognize:layout") as state:
            record_figures(state, routes, version)
            state.engines["layout"] = version
    described = []
    for figure in ordered_figures:
        if figure.answer is not None:
            described.append(describer.described(*figure.answer))
    if described:
        describe_figure.record_descriptions(ctx, described, describer.prompt_hash, failures)
    return {"turned": turned, "routed": len(routes), "described": len(described)}, failures
