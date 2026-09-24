"""Titles the scan engine labelled (guide §6.8, Q33): the label is evidence for a level.

The scan engine marks regions ``doc_title`` / ``paragraph_title``; they become
TITLE blocks without a level.  Titles read inside an embedded image (Q42) are
the image's own layout and get no proposal: they stay level-less (rendered as
paragraphs) unless the structure step decides otherwise.  The proposal is the label's rank
(``layout/labels.py``), one level deeper for each extra part of a dotted
number (``1.2`` → rank + 1).  Document-level unification with the other titles
(``hierarchy/levels.py``) then makes the outline legal.
"""

from __future__ import annotations

import re

from parserx.hierarchy.legality import numbering_signature
from parserx.ir.anchor import AssetAnchor
from parserx.ir.enums import BlockKind
from parserx.ir.state import DocumentState
from parserx.layout import labels
from parserx.workspace.queries import HIDDEN, ordered

ACTOR = "program:hierarchy.engine_titles"
REASON = "scan engine title label"
_DOTTED = re.compile(r"N(?:\.N)+")


def engine_titles(state: DocumentState) -> list[tuple[str, str, int, dict]]:
    """(block id, text, proposed level, evidence) for level-less titles whose content came with a title label."""
    out: list[tuple[str, str, int, dict]] = []
    for block in ordered(state):
        if block.kind != BlockKind.TITLE or block.level is not None or block.status in HIDDEN:
            continue
        if isinstance(block.anchors[0], AssetAnchor):
            continue  # read inside an embedded image: the image's own layout, not the document outline (Q42)
        chosen = next((o for o in block.observations if o.id == block.chosen_observation), None)
        rank = labels.title_rank(chosen.engine, chosen.label) if chosen is not None and chosen.label else None
        if rank is None:
            continue
        signature = numbering_signature(block.text)
        depth = signature.count(".") if rank > 1 and signature and _DOTTED.fullmatch(signature) else 0
        level = min(6, rank + depth)
        out.append((block.id, block.text, level, {"label": chosen.label, "rank": rank, "numbering_depth": depth}))
    return out
