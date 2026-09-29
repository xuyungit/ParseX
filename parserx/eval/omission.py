"""Lost and added content (metric version 2.5; R1 and R5 of the vision-first review).

char_f1 says how many characters differ, not whether a whole passage is gone.  Here a character of the annotation
is *found* when some run of ``ANCHOR_LEN`` characters containing it also occurs in the output (whitespace dropped,
NFKC, as for char_f1; position is the order metric's concern; the text's two ends count as matching boundaries).
An isolated character error leaves only that character unfound — its neighbours are found through runs on either
side; two errors closer than a run leave the stretch between them unfound.  A stretch of ``LOST_RUN`` or more
unfound characters needs at least three errors in it, so it is content the output does not have in a readable form:
a *lost run* (a dropped sentence or line, or text garbled throughout).  A block of the annotation (text between
blank lines) none of whose runs occurs in the output is a *lost block*.  The same read the other way gives *added
runs*: output with no counterpart in the annotation (invented, or garbled).
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from parserx.eval.formulas import characters, one_prime
from parserx.eval.normalize import canonicalize, char_sequence
from parserx.eval.order import ANCHOR_LEN

_BLANK_LINE_RE = re.compile(r"\n\s*\n")
_MARKUP_RE = re.compile(r"<[^>\n]*>|[${}^_&*\\]")
LOST_RUN = 2 * ANCHOR_LEN  # two errors leave at most ANCHOR_LEN + 1 unfound characters in a row
_EDGE = "\0" * (ANCHOR_LEN - 1)


@dataclass
class OmissionMetrics:
    lost_blocks: int = 0  # annotation blocks none of whose runs occurs in the output
    blocks: int = 0  # annotation blocks long enough to judge (≥ ANCHOR_LEN characters)
    lost_runs: int = 0
    lost_chars: int = 0
    added_runs: int = 0
    added_chars: int = 0


def compute_omission(output: str, expected: str) -> OmissionMetrics:
    exp_text = canonicalize(expected).text
    out_seq = _EDGE + folded(canonicalize(output).text) + _EDGE
    exp_seq = _EDGE + folded(exp_text) + _EDGE
    out_grams, exp_grams = grams(out_seq), grams(exp_seq)
    lost_runs, lost_chars = _unfound(exp_seq, out_grams)
    added_runs, added_chars = _unfound(out_seq, exp_grams)
    blocks = [seq for seq in (folded(b) for b in _BLANK_LINE_RE.split(exp_text)) if len(seq) >= ANCHOR_LEN]
    lost_blocks = sum(not (grams(b) & out_grams) for b in blocks)
    return OmissionMetrics(lost_blocks=lost_blocks, blocks=len(blocks), lost_runs=lost_runs, lost_chars=lost_chars,
                           added_runs=added_runs, added_chars=added_chars)


def folded(text: str) -> str:
    """The characters a reader sees, in one notation: LaTeX commands as their letters (``\\alpha`` is α), primes as
    ``'`` (``x^{\\prime}`` is ``x'``), markup (HTML tags, ``$ { } ^ _ & * \\``) dropped, sub- and superscripts as plain
    characters (NFKC)."""
    return char_sequence(_MARKUP_RE.sub("", characters(one_prime(text))))


def grams(seq: str) -> set[str]:
    return {seq[i:i + ANCHOR_LEN] for i in range(len(seq) - ANCHOR_LEN + 1)}


def _unfound(seq: str, other: set[str]) -> tuple[int, int]:
    """Stretches of ≥ LOST_RUN characters of *seq* (edges included) in no run that *other* has: (count, chars)."""
    found = [False] * len(seq)
    for i in range(len(seq) - ANCHOR_LEN + 1):
        if seq[i:i + ANCHOR_LEN] in other:
            found[i:i + ANCHOR_LEN] = [True] * ANCHOR_LEN
    runs = chars = length = 0
    for hit in found + [True]:
        if not hit:
            length += 1
            continue
        if length >= LOST_RUN:
            runs, chars = runs + 1, chars + length
        length = 0
    return runs, chars
