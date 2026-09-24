"""Compact per-page rows shared by the overview and check tools."""

from __future__ import annotations

from typing import Literal

from parserx.ir.anchor import DocxAnchor, PdfAnchor
from parserx.ir.base import IRModel
from parserx.ir.enums import BlockStatus, PageStatus
from parserx.ir.state import DocumentState
from parserx.workspace.queries import block_unit

_TEXT_UNITS = frozenset({"native_line", "docx_paragraph"})


class PageRow(IRModel):
    n: int
    unit: Literal["pdf_page", "docx_segment"]
    status: PageStatus
    native_chars: int  # characters of the native text layer / OOXML paragraphs on this page
    blocks: int
    unresolved: int  # failed or degraded blocks


def page_rows(state: DocumentState) -> list[PageRow]:
    blocks: dict[int, int] = {}
    unresolved: dict[int, int] = {}
    for block in state.blocks:
        unit = block_unit(state, block)
        if unit is None:
            continue
        blocks[unit] = blocks.get(unit, 0) + 1
        if block.status in (BlockStatus.FAILED, BlockStatus.DEGRADED):
            unresolved[unit] = unresolved.get(unit, 0) + 1
    chars: dict[int, int] = {}
    for entry in state.ledger:
        source = entry.source
        unit = source.page if isinstance(source, PdfAnchor) else source.segment if isinstance(source, DocxAnchor) else None
        if unit is not None and entry.unit in _TEXT_UNITS:
            chars[unit] = chars.get(unit, 0) + entry.chars
    return [PageRow(n=p.n, unit=p.unit, status=p.status, native_chars=chars.get(p.n, 0), blocks=blocks.get(p.n, 0),
                    unresolved=unresolved.get(p.n, 0)) for p in state.pages]
