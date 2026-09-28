"""Equation numbers: "(12)" at the right of a display formula's line is the formula's, rendered as its \\tag."""

from parserx.content.equation_numbers import number_equations, tagged
from parserx.ir.anchor import PdfAnchor
from parserx.ir.block import Block
from parserx.ir.enums import BlockKind, DocumentStatus, PageStatus
from parserx.ir.state import DocumentState, PageState
from parserx.render.markdown import render_markdown


def _block(bid, kind, text, bbox, order):
    return Block(id=bid, kind=kind, order=order, anchors=[PdfAnchor(page=1, bbox=bbox, coord_space="page_pt")],
                 text=text)


def _state(blocks):
    return DocumentState(id="d", source="x.pdf", source_sha256="0" * 64, format="pdf",
                         status=DocumentStatus.IN_PROGRESS, blocks=blocks,
                         pages=[PageState(n=1, unit="pdf_page", status=PageStatus.DONE, size_pt=(595, 842))])


def test_the_number_beside_a_formula_is_its_tag():
    state = _state([
        _block("f1", BlockKind.FORMULA, "$$ E=mc^{2} $$", (100, 100, 300, 130), 0),
        _block("n1", BlockKind.TEXT, "（12）", (480, 108, 510, 120), 1),
        _block("f2", BlockKind.FORMULA, "$$ (lz)^{-1} $$", (100, 200, 470, 230), 2),
        _block("n2", BlockKind.TEXT, "-1 (13)", (460, 205, 510, 217), 3),  # a superscript cut off with it
        _block("p", BlockKind.TEXT, "(14)", (480, 400, 510, 412), 4),  # beside no formula
        _block("t", BlockKind.TEXT, "由式（12）可知", (100, 140, 300, 152), 5),
    ])
    assert number_equations(state) == ["f1", "f2"]
    md = render_markdown(state)
    assert "$$ E=mc^{2} \\tag{12} $$" in md and "$$ (lz)^{-1} \\tag{13} $$" in md
    assert "（12）\n" not in md and "(13)" not in md and "(14)" in md and "由式（12）可知" in md


def test_a_number_across_the_page_belongs_to_the_nearest_formula():
    state = _state([
        _block("left", BlockKind.FORMULA, "$$ a=b $$", (60, 100, 250, 130), 0),
        _block("right", BlockKind.FORMULA, "$$ c=d $$", (320, 100, 520, 130), 1),
        _block("n", BlockKind.TEXT, "(3)", (525, 110, 545, 122), 2),
    ])
    assert number_equations(state) == ["right"]


def test_tagged_keeps_the_delimiters():
    assert tagged("$$\nx\n$$", "3.1") == "$$\nx \\tag{3.1} $$"
    assert tagged("x", "2") == "x \\tag{2}"
    assert tagged("$$ x \\tag{1} $$", "10") == "$$ x \\tag{10} $$"  # the reading's misread number


def test_a_formula_with_a_tag_of_words_keeps_it():
    state = _state([
        _block("f", BlockKind.FORMULA, "$$ x=1 \\tag{ 故 } $$", (100, 100, 300, 130), 0),
        _block("n", BlockKind.TEXT, "（4）", (480, 108, 510, 120), 1),
        _block("g", BlockKind.FORMULA, "$$ y=2 \\tag{5} $$", (100, 200, 300, 230), 2),
        _block("m", BlockKind.TEXT, "（5）", (480, 208, 510, 220), 3),
    ])
    assert number_equations(state) == ["g"]


def test_a_block_of_several_numbered_equations_keeps_its_numbers_apart():
    state = _state([
        _block("f", BlockKind.FORMULA, "$$ a=b \\\\ c=d $$", (100, 100, 300, 200), 0),
        _block("n1", BlockKind.TEXT, "(1)", (480, 110, 510, 122), 1),
        _block("n2", BlockKind.TEXT, "(2)", (480, 180, 510, 192), 2),
    ])
    assert number_equations(state) == []
