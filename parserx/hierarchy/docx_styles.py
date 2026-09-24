"""Deterministic structure for DOCX from styles and outline levels (guide §6.8, Q24).

Evidence, read from ``TextStyle``:

- outline level (direct ``w:outlineLvl`` or inherited along ``basedOn``) or a
  heading style name (``heading N`` / ``标题 N``) → a title at that depth;
- the ``Title`` style is the document title: level 1, and every other title
  one level deeper when a document title exists (outline level 9 means body
  text, so a Title paragraph carrying it is still the document title);
- a numbered paragraph without heading evidence is a list item (numbering
  levels are list levels, not heading levels).

Levels are then unified (``unify_levels``).  Phase 4 lets a model question
this path when the document-level check finds inconsistencies.
"""

from __future__ import annotations

import re

from parserx.hierarchy.levels import unify_levels
from parserx.ir.enums import BlockKind
from parserx.ir.state import DocumentState
from parserx.workspace.queries import HIDDEN, ordered

ACTOR = "pipeline:docx_styles"
_HEADING_NAME = re.compile(r"^(?:heading|标题)\s*(\d)$", re.IGNORECASE)
_TITLE_NAMES = frozenset({"title", "标题"})
_BODY_OUTLINE = 9


def _style(block):
    return block.observations[0].style if block.observations else None


def propose_docx_structure(state: DocumentState) -> list[dict]:
    blocks = [b for b in ordered(state) if b.kind == BlockKind.TEXT and b.status not in HIDDEN and _style(b)]
    is_title = {b.id: (_style(b).style_name or "").strip().lower() in _TITLE_NAMES for b in blocks}
    shift = 1 if any(is_title.values()) else 0
    titles: list[tuple[str, str, int]] = []
    evidence: dict[str, dict] = {}
    lists: list[str] = []
    for block in blocks:
        style = _style(block)
        name = (style.style_name or "").strip()
        if is_title[block.id]:
            level, why = 1, "Title style: the document title"
        else:
            match = _HEADING_NAME.match(name)
            if style.outline_level is not None and style.outline_level < _BODY_OUTLINE:
                level, why = style.outline_level + 1 + shift, f"outline level {style.outline_level}"
            elif match:
                level, why = int(match.group(1)) + shift, f"heading style {name!r}"
            else:
                if style.numbering is not None and style.numbering.text:
                    lists.append(block.id)
                continue
            if shift:
                why += "; one level below the document title"
        titles.append((block.id, block.text, level))
        evidence[block.id] = {"style": name, "outline_level": style.outline_level if style.outline_level is not None
                              else -1, "reason": why}
    levels = unify_levels(titles)
    changes: list[dict] = []
    for block_id, _text, proposed in titles:
        ev = evidence[block_id]
        flat = {"style": ev["style"], "outline_level": ev["outline_level"], "proposed_level": proposed}
        changes.append({"op": "set_role", "block": block_id, "kind": "title", "reason": ev["reason"], "evidence": flat})
        changes.append({"op": "set_level", "block": block_id, "level": levels[block_id],
                        "reason": ev["reason"] + ("" if levels[block_id] == proposed else "; unified with the outline"),
                        "evidence": flat})
    for block_id in lists:
        changes.append({"op": "set_role", "block": block_id, "kind": "list",
                        "reason": "numbered paragraph without heading style or outline level", "evidence": {}})
    return changes
