"""Document-level level unification (guide §6.8: "它是几级" is decided over the whole document).

Proposed title levels are made legal before they are applied:

1. titles sharing a numbering pattern (``1.2`` / ``1.3`` → ``N.N``) take the
   level most of them have (ties: the shallower);
2. walking in reading order, a title whose dotted number extends the number of
   an earlier title (``5.1.1`` after ``5.1``) is one level below it — the
   numbering is the document's own statement of nesting (Phase 3 D4);
3. a numbering style seen for the first time right under a title of another
   style (the last numbered title before it) nests in it — one level below —
   when its proposal equals that title's proposal (the typography cannot tell
   the two apart): the order in which the document introduces its styles says
   how they nest (``一、 > （一） > 1. > （1）``), with no table of styles
   (P4-3).  A different proposal is the typography's evidence and stands; a
   number continuing the one above (``2 …`` then ``3、…``) is a sibling
   written in another style;
4. a title is at most one level deeper than the title before it (the first
   title keeps its level); a numbering pattern moved by 2, 3 or 4 stays at that
   level for the titles that follow.

The steps only move levels toward consistency; they never add or remove a
title.
"""

from __future__ import annotations

import re
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
    levels_of: dict[tuple[int, ...], int] = {}  # the level each leading number was given
    seen: set[str] = set()  # numbering styles met so far
    above: tuple[str, int, int, str] | None = None  # the last numbered title: (style, level, proposed, text)
    for block_id, text, level in titles:
        proposed = level
        signature = numbering_signature(text)
        level = agreed.get(signature, level) if signature is not None else level
        number = leading_number(text)
        parent = levels_of.get(number[:-1]) if number is not None and len(number) > 1 else None
        if parent is not None and level != parent + 1:
            level = parent + 1
            if signature is not None:
                agreed[signature] = level
        elif signature is not None and signature not in seen and above is not None and above[0] != signature \
                and proposed == above[2] and not _continues(text, above[3]):
            level = above[1] + 1  # a new list under a title of another style nests in it
            agreed[signature] = level
        if previous is not None and level > previous + 1:
            level = previous + 1
            if signature is not None:
                agreed[signature] = level  # the rest of the pattern follows (one level per pattern)
        level = max(1, min(6, level))
        unified[block_id] = level
        previous = level
        if number is not None:
            levels_of[number] = level
        if signature is not None:
            seen.add(signature)
            above = (signature, level, proposed, text)
    return unified


_FIRST_NUMERAL = re.compile(r"[(（\s]*(?:第\s*)?([0-9０-９]+|[一二三四五六七八九十百零〇两]+|[IVXLCDM]+|[ivxlcdm]+|[A-Za-z])")
_CJK_DIGITS = {c: i for i, c in enumerate("零一二三四五六七八九")} | {"〇": 0, "两": 2}
_ROMAN = {"I": 1, "V": 5, "X": 10, "L": 50, "C": 100, "D": 500, "M": 1000}


def _continues(text: str, above: str) -> bool:
    """The title's number is the one after the title above's (``2 概述`` → ``3、结构``): the same list."""
    value, previous = number_value(text), number_value(above)
    return value is not None and previous is not None and value == previous + 1


def number_value(text: str) -> int | None:
    """The value of a numbered title's first number: ``3、`` → 3, ``（十二）`` → 12, ``IV.`` → 4, ``B.`` → 2."""
    match = _FIRST_NUMERAL.match(text or "")
    if not match:
        return None
    numeral = match.group(1)
    if numeral[0] in "0123456789０１２３４５６７８９":
        return int(numeral.translate(str.maketrans("０１２３４５６７８９", "0123456789")))
    if len(numeral) == 1 and numeral.isascii() and numeral.upper() not in "IVX":
        return ord(numeral.upper()) - ord("A") + 1  # a letter sequence
    if numeral.isascii():
        values = [_ROMAN[c] for c in numeral.upper()]
        return sum(-v if i + 1 < len(values) and v < values[i + 1] else v for i, v in enumerate(values))
    total, digit = 0, 0
    for c in numeral:
        if c == "十":
            total += (digit or 1) * 10
            digit = 0
        elif c == "百":
            total += (digit or 1) * 100
            digit = 0
        else:
            digit = _CJK_DIGITS.get(c, 0)
    return total + digit


_LEADING_NUMBER = re.compile(r"\s*(\d+(?:[.．]\d+)*)(?![\d])")


def leading_number(text: str) -> tuple[int, ...] | None:
    """The leading arabic number of a title as its parts (``5.1.1 材料`` → (5, 1, 1), ``6支座`` → (6,))."""
    match = _LEADING_NUMBER.match(text or "")
    return tuple(int(p) for p in re.split(r"[.．]", match.group(1))) if match else None


def title_changes(titles: list[tuple[str, str, int, dict]], levels: dict[str, int], *, reason: str) -> list[dict]:
    """``set_role`` + ``set_level`` per (block id, text, proposed level, evidence), at the unified level."""
    changes: list[dict] = []
    for block_id, _text, proposed, evidence in titles:
        level = levels[block_id]
        changes.append({"op": "set_role", "block": block_id, "kind": "title", "reason": reason, "evidence": evidence})
        changes.append({"op": "set_level", "block": block_id, "level": level, "evidence": evidence,
                        "reason": reason + ("" if level == proposed else "; unified with the outline")})
    return changes
