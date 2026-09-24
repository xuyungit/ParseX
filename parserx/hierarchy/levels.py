"""Document-level level unification (guide §6.8: "它是几级" is decided over the whole document).

Proposed title levels are made legal before they are applied:

1. titles sharing a numbering pattern (``1.2`` / ``1.3`` → ``N.N``) take the
   level most of them have (ties: the shallower);
2. walking in reading order, a title is at most one level deeper than the
   title before it (the first title keeps its level).

Both steps only move levels toward consistency; they never add or remove a
title.
"""

from __future__ import annotations

from collections import Counter

from parserx.hierarchy.legality import numbering_signature


def unify_levels(titles: list[tuple[str, str, int]]) -> dict[str, int]:
    """``titles`` = (block id, text, proposed level) in reading order → unified level per block id."""
    by_signature: dict[str, Counter[int]] = {}
    for _, text, level in titles:
        signature = numbering_signature(text)
        if signature is not None:
            by_signature.setdefault(signature, Counter())[level] += 1
    agreed = {sig: min(counts, key=lambda lv: (-counts[lv], lv)) for sig, counts in by_signature.items()}
    unified: dict[str, int] = {}
    previous: int | None = None
    for block_id, text, level in titles:
        signature = numbering_signature(text)
        level = agreed.get(signature, level) if signature is not None else level
        if previous is not None:
            level = min(level, previous + 1)
        level = max(1, min(6, level))
        unified[block_id] = level
        previous = level
    return unified


def title_changes(titles: list[tuple[str, str, int, dict]], levels: dict[str, int], *, reason: str) -> list[dict]:
    """``set_role`` + ``set_level`` per (block id, text, proposed level, evidence), at the unified level."""
    changes: list[dict] = []
    for block_id, _text, proposed, evidence in titles:
        level = levels[block_id]
        changes.append({"op": "set_role", "block": block_id, "kind": "title", "reason": reason, "evidence": evidence})
        changes.append({"op": "set_level", "block": block_id, "level": level, "evidence": evidence,
                        "reason": reason + ("" if level == proposed else "; unified with the outline")})
    return changes
