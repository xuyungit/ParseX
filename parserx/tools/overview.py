"""``overview``: the document at a glance (no side effects)."""

from __future__ import annotations

from collections import Counter

from parserx.ir.base import IRModel
from parserx.ir.enums import BlockKind, BlockStatus
from parserx.tools.context import ToolContext, ToolOutput, output
from parserx.tools.envelope import DocText, UnresolvedKind
from parserx.tools.views import PREVIEW, DocInfo, OutlineNode, doc_info, outline_nodes, unresolved_counts
from parserx.workspace.views import PageRow, page_rows


class OverviewRequest(IRModel):
    pass


class StyleSummary(IRModel):
    style_name: str | None
    outline_level: int | None
    count: int


class NumberingSummary(IRModel):
    num_id: str
    level: int
    count: int
    example: DocText | None


class OverviewResult(IRModel):
    doc: DocInfo
    native_chars: int
    images: int
    tables: int
    styles: list[StyleSummary] | None  # DOCX: paragraph styles with their outline level
    numbering: list[NumberingSummary] | None  # DOCX: list numbering in use
    pages: list[PageRow]
    blocks_by_kind: dict[BlockKind, int]
    blocks_by_status: dict[BlockStatus, int]
    outline: list[OutlineNode]
    unresolved: dict[UnresolvedKind, int]


def run(ctx: ToolContext, req: OverviewRequest) -> ToolOutput[OverviewResult]:
    state = ctx.ws.load()
    rows = page_rows(state)
    styles = numbering = None
    if state.format == "docx":
        style_counts: Counter[tuple[str | None, int | None]] = Counter()
        num_counts: Counter[tuple[str, int]] = Counter()
        examples: dict[tuple[str, int], str] = {}
        for block in state.blocks:
            for obs in block.observations[:1]:
                if obs.style is None:
                    continue
                style_counts[(obs.style.style_name, obs.style.outline_level)] += 1
                if obs.style.numbering is not None:
                    key = (obs.style.numbering.num_id, obs.style.numbering.level)
                    num_counts[key] += 1
                    examples.setdefault(key, block.text[:PREVIEW])
        styles = [StyleSummary(style_name=name, outline_level=level, count=n)
                  for (name, level), n in sorted(style_counts.items(), key=lambda kv: (-kv[1], str(kv[0])))]
        numbering = [NumberingSummary(num_id=num, level=level, count=n,
                                      example=DocText(doc_text=examples[(num, level)]) if examples.get((num, level))
                                      else None)
                     for (num, level), n in sorted(num_counts.items())]
    result = OverviewResult(
        doc=doc_info(state), native_chars=sum(r.native_chars for r in rows),
        images=sum(1 for b in state.blocks if b.kind == BlockKind.FIGURE),
        tables=sum(1 for b in state.blocks if b.kind == BlockKind.TABLE),
        styles=styles, numbering=numbering, pages=rows,
        blocks_by_kind=dict(sorted(Counter(b.kind for b in state.blocks).items())),
        blocks_by_status=dict(sorted(Counter(b.status for b in state.blocks).items())),
        outline=outline_nodes(state), unresolved=unresolved_counts(state),
    )
    return output(result)
