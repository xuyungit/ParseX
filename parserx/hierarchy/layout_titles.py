"""Title candidates on native pages from two independent signals (Phase 3 D4, guide §6.8): a worklist signal.

v1's detection on native text finds titles by size and weight; a heading set in another face at the body's size
(``技术领域`` in a heavier face, not flagged bold) escapes it.  The local layout detector reads the page image and
labels such a region a section title.  Neither signal alone decides: a native text block is proposed as a title
when the detector labels its region ``paragraph_title`` *and* its typography is set apart from the document's body
text — another face, a larger size, or bold where the body is not.  A ``doc_title`` region is the document's own
title, which the outline leaves as a paragraph.  Only one-line blocks with text are proposed: a block that runs
on into body text is not a title as a whole.  Text inside a picture or formula region of the page reading is the
picture's.  The proposal is the label's rank plus one level per extra part of a dotted number, as for the scan
engine's labels (``engine_titles``).

Measured on the corpus (2026-09-25), applying these as titles found the patent's section headings and a report's
headings but also gave wrong levels (a new numbering style under a table-framed section) and cover lines: two
documents better, four slightly worse.  So they are listed as ``title_candidate`` for the agent to confirm, with a
level, through ``apply_structure``; the fixed pipeline's outline does not change.
"""

from __future__ import annotations

from collections import Counter

from parserx.hierarchy.engine_titles import _DOTTED
from parserx.hierarchy.legality import numbering_signature
from parserx.ir.anchor import PdfAnchor
from parserx.ir.block import Block
from parserx.ir.enums import BlockKind, TaskKind
from parserx.ir.state import DocumentState
from parserx.layout import labels
from parserx.workspace.queries import HIDDEN, ordered

ACTOR = "program:hierarchy.layout_titles"
REASON = "layout detector section title on text set apart from the body"
_LABEL = "paragraph_title"


def layout_titles(state: DocumentState) -> list[tuple[str, str, int, dict]]:
    """(block id, text, proposed level, evidence) for native text blocks both signals mark as titles."""
    body = _body_style(state)
    if body is None:
        return []
    pictures = {r.n: r.not_prose for r in state.readings}
    rank = labels.title_rank("layout", _LABEL)
    out: list[tuple[str, str, int, dict]] = []
    for block in ordered(state):
        if block.kind != BlockKind.TEXT or block.status in HIDDEN or not isinstance(block.anchors[0], PdfAnchor):
            continue
        text = (block.text or "").strip()
        style = _native_style(block)
        if not text or "\n" in text or style is None:
            continue
        if not any(o.task == TaskKind.LAYOUT and o.label == _LABEL for o in block.observations):
            continue
        anchor = block.anchors[0]
        centre = ((anchor.bbox[0] + anchor.bbox[2]) / 2, (anchor.bbox[1] + anchor.bbox[3]) / 2)
        if any(b[0] <= centre[0] <= b[2] and b[1] <= centre[1] <= b[3] for b in pictures.get(anchor.page, [])):
            continue
        apart = _set_apart(style, body)
        if not apart:
            continue
        signature = numbering_signature(text)
        depth = signature.count(".") if signature and _DOTTED.fullmatch(signature) else 0
        out.append((block.id, text, min(6, rank + depth),
                    {"label": _LABEL, "rank": rank, "numbering_depth": depth, "set_apart": ", ".join(apart)}))
    return out


def _native_style(block: Block):
    chosen = next((o for o in block.observations if o.id == block.chosen_observation), None)
    return chosen.style if chosen is not None and chosen.engine == "native_pdf" else None


def _body_style(state: DocumentState) -> tuple[str | None, float | None, bool] | None:
    """The typography most of the document's native text is set in (by characters)."""
    weights: Counter[tuple[str | None, float | None, bool]] = Counter()
    for block in state.blocks:
        style = _native_style(block)
        if block.kind == BlockKind.TEXT and style is not None:
            weights[(style.font, style.font_size, bool(style.bold))] += len("".join((block.text or "").split()))
    return weights.most_common(1)[0][0] if weights else None


def _set_apart(style, body) -> list[str]:
    font, size, bold = body
    apart = []
    if style.font and font and style.font != font:
        apart.append(f"font {style.font} (body {font})")
    if style.font_size and size and style.font_size > size:
        apart.append(f"size {style.font_size} (body {size})")
    if style.bold and not bold:
        apart.append("bold (body regular)")
    return apart
