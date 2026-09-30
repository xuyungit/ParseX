"""Script candidates of the text layer (scripts/vision_first/p0_inputs.py, candidates version 2)."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts" / "vision_first"))

from p0_inputs import RawLine, script_candidates  # noqa: E402


def _line(*glyphs):
    """A text-layer line from (char, x0, x1, size, baseline), one span per glyph."""
    return RawLine([{"size": size, "font": "F", "flags": 0,
                     "chars": [{"c": c, "bbox": (x0, base - 0.8 * size, x1, base + 0.2 * size), "origin": (x0, base)}]}
                    for c, x0, x1, size, base in glyphs], (1.0, 0.0))


def _row(text, x0, size, base, width=None):
    width = width or size
    return [(c, x0 + k * width, x0 + (k + 1) * width, size, base) for k, c in enumerate(text)]


def test_a_small_raised_glyph_beside_its_base_is_a_superscript():
    found, _ = script_candidates([_line(("x", 0, 5, 10, 100), ("2", 5, 8, 6, 96))])
    assert found == [[{"base": "x", "t": "2", "kind": "sup"}]]


def test_a_smaller_line_under_a_larger_one_is_not_its_subscript():
    found, _ = script_candidates([_line(*_row("结构横断面", 0, 18, 100)), _line(*_row("Section", 5, 8.8, 110, 10))])
    assert found == [[], []]


def test_a_raised_citation_is_one_script_and_the_full_stop_after_it_is_none():
    found, kinds = script_candidates([_line(("梁", 0, 21, 21, 100), ("[", 21, 27, 12, 95), ("1", 27, 30, 6, 95),
                                            ("]", 30, 36, 12, 95), (".", 36, 39, 6, 100), ("等", 39, 60, 21, 100))])
    assert found == [[{"base": "梁", "t": "[1]", "kind": "sup"}]]
    assert kinds == [["", "sup", "sup", "sup", "", ""]]


def test_a_prime_is_a_character_unless_it_stands_in_a_script():
    alone, _ = script_candidates([_line(("z", 0, 5, 10, 100), ("′", 5, 8, 5, 96), ("E", 8, 14, 10, 100))])
    inside, _ = script_candidates([_line(("q", 0, 5, 10, 100), ("x", 5, 8, 6, 96), ("′", 8, 10, 5, 96))])
    assert alone == [[]]
    assert inside == [[{"base": "q", "t": "x′", "kind": "sup"}]]


def test_no_script_of_a_glyph_across_a_line_of_another_row():
    heading, body = _line(*_row("论", 45, 27, 98.5)), _line(*_row("将铰缝刚度均作为", 72, 20, 118))
    reference = _line(*_row("[3]李海生", 256, 18, 106.8))
    found, _ = script_candidates([heading, body, reference])
    assert found[2] == []
