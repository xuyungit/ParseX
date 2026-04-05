"""Shared text normalization and comparison helpers."""

from __future__ import annotations

import re

_CHUNK_SIZE = 5000  # Max chars per Levenshtein call (keeps O(n^2) tractable)


def normalize_for_comparison(text: str) -> str:
    """Normalize text for fair comparison: collapse whitespace, strip markup."""
    text = re.sub(r"^#{1,6}\s+", "", text, flags=re.MULTILINE)
    text = re.sub(r"<!-- PAGE \d+ -->", "", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def compute_edit_distance(output: str, expected: str) -> float:
    """Normalized edit distance between output and expected text."""
    out_norm = normalize_for_comparison(output)
    exp_norm = normalize_for_comparison(expected)

    if not exp_norm and not out_norm:
        return 0.0
    if not exp_norm or not out_norm:
        return 1.0

    if len(out_norm) <= _CHUNK_SIZE and len(exp_norm) <= _CHUNK_SIZE:
        distance = _levenshtein(out_norm, exp_norm)
        return distance / max(len(out_norm), len(exp_norm))

    return _chunked_edit_distance(out_norm, exp_norm)


def _chunked_edit_distance(a: str, b: str) -> float:
    """Split *a* and *b* into aligned chunks and average their distances."""
    n_chunks = max(
        (max(len(a), len(b)) + _CHUNK_SIZE - 1) // _CHUNK_SIZE,
        1,
    )
    a_step = max(len(a) // n_chunks, 1)
    b_step = max(len(b) // n_chunks, 1)

    total_dist = 0
    total_max = 0
    for i in range(n_chunks):
        a_chunk = a[i * a_step : (i + 1) * a_step] if i < n_chunks - 1 else a[i * a_step :]
        b_chunk = b[i * b_step : (i + 1) * b_step] if i < n_chunks - 1 else b[i * b_step :]
        if not a_chunk and not b_chunk:
            continue
        distance = _levenshtein(a_chunk, b_chunk)
        total_dist += distance
        total_max += max(len(a_chunk), len(b_chunk))

    return total_dist / max(total_max, 1)


def _levenshtein(s1: str, s2: str) -> int:
    """Compute Levenshtein edit distance between two strings."""
    if len(s1) < len(s2):
        return _levenshtein(s2, s1)
    if len(s2) == 0:
        return len(s1)

    prev_row = list(range(len(s2) + 1))
    for i, c1 in enumerate(s1):
        curr_row = [i + 1]
        for j, c2 in enumerate(s2):
            cost = 0 if c1 == c2 else 1
            curr_row.append(min(
                curr_row[j] + 1,
                prev_row[j + 1] + 1,
                prev_row[j] + cost,
            ))
        prev_row = curr_row

    return prev_row[-1]
