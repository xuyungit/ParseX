"""TEMPORARY (Q24, removed in Phase 4): PDF titles from v1's heading detection.

Runs v1's service-free steps — PDF provider, metadata builder, reading order,
header/footer, code block, chapter processor with the LLM fallback off — and
proposes its headings as structure changes on the native-text v2 blocks whose
text equals the heading (whitespace ignored).  Headings v1 found inside a larger v2 block
are not applied: structure changes never split text.  Every change carries
actor ``adapter:v1`` and goes through ``apply_structure`` and its legality
checks like any other.
"""

from __future__ import annotations

import logging
import re
from pathlib import Path

from parserx.config.schema import ParserXConfig
from parserx.content.pdf_native import ENGINE as NATIVE_ENGINE
from parserx.hierarchy.levels import title_changes, unify_levels
from parserx.ir.anchor import PdfAnchor
from parserx.ir.state import DocumentState
from parserx.workspace.queries import HIDDEN, block_unit, ordered

ACTOR = "adapter:v1"
REASON = "v1 heading detection (temporary adapter, Q24)"
log = logging.getLogger(__name__)


def v1_headings(pdf_path: Path, config: ParserXConfig) -> list[tuple[int, int, str]]:
    """(page, level, text) of v1's headings, computed without any service."""
    from parserx.builders.metadata import MetadataBuilder
    from parserx.builders.reading_order import ReadingOrderBuilder
    from parserx.processors.chapter import ChapterProcessor
    from parserx.processors.code_block import CodeBlockProcessor
    from parserx.processors.header_footer import HeaderFooterProcessor
    from parserx.providers.pdf import PDFProvider

    chapter = config.processors.chapter.model_copy(update={"llm_fallback": False})
    doc = PDFProvider().extract(pdf_path)
    doc = MetadataBuilder(config.builders.metadata).build(doc)
    doc = ReadingOrderBuilder(config.processors.reading_order).build(doc)
    doc = HeaderFooterProcessor(config=config.processors.header_footer,
                                metadata_config=config.builders.metadata).process(doc)
    doc = CodeBlockProcessor(config.processors.code_block).process(doc)
    doc = ChapterProcessor(chapter, llm_service=None).process(doc)
    return [(e.page_number, int(e.metadata["heading_level"]), e.content)
            for page in doc.pages for e in page.elements if e.metadata.get("heading_level")]


def _norm(text: str) -> str:
    return re.sub(r"\s+", "", text)


def _native(block) -> bool:
    chosen = next((o for o in block.observations if o.id == block.chosen_observation), None)
    return chosen is not None and chosen.engine == NATIVE_ENGINE


def matched_titles(pdf_path: Path, state: DocumentState, config: ParserXConfig) -> list[tuple[str, str, int, dict]]:
    """(block id, text, v1 level, evidence) for native blocks whose text equals one of v1's headings on that page.

    Only native-text blocks: v1's evidence (font sizes of the text layer) says nothing about scan-engine blocks,
    whose titles come from the engine's labels (``hierarchy/engine_titles.py``).
    """
    headings = v1_headings(pdf_path, config)
    candidates = [b for b in ordered(state) if b.status not in HIDDEN and isinstance(b.anchors[0], PdfAnchor)
                  and _native(b)]
    used: set[str] = set()
    matched: dict[str, int] = {}
    for page, level, text in headings:
        block = next((b for b in candidates if b.id not in used and block_unit(state, b) == page
                      and _norm(b.text) == _norm(text) and _norm(text)), None)
        if block is None:
            log.info("adapter:v1 heading on page %d has no block of the same text: %r", page, text[:40])
            continue
        used.add(block.id)
        matched[block.id] = level
    return [(b.id, b.text, matched[b.id], {"v1_level": matched[b.id]}) for b in candidates if b.id in matched]


def propose_structure(pdf_path: Path, state: DocumentState, config: ParserXConfig) -> list[dict]:
    titles = matched_titles(pdf_path, state, config)
    return title_changes(titles, unify_levels([t[:3] for t in titles]), reason=REASON)
