"""Glyphs the text layer maps to no readable character, read from the page image (content/glyphs.py).

Test PDFs stand for such glyphs with control characters: a written PDF keeps their codes, which the pipeline reads
as U+FFFD (private-use characters would become "·" there)."""

import pymupdf

from parserx.content import glyphs
from parserx.content.pdf_native import extract_pdf
from parserx.ir.enums import BlockKind, BlockStatus


def test_a_glyph_reads_as_the_character_between_its_conserved_neighbours():
    assert glyphs.reading_of("收稿日期：20140907", 9, "收稿日期： 2014-09-07") == "-"
    assert glyphs.reading_of("电话：02165979772，地址", 6, "noise 电话:021-65979772,地址 noise") == "-"


def test_no_reading_without_a_readable_neighbour_or_with_two_answers():
    assert glyphs.reading_of("\x01", 0, "e 1") is None  # nothing of the line to check the reading against
    assert glyphs.reading_of("ab", 1, "a-b a+b") is None
    assert glyphs.reading_of("ab", 1, "a b") is None  # read as no character


def test_a_reading_needs_two_lines_that_agree_and_none_that_disagrees():
    assert glyphs.agreed({"2014?09?07": "-", "021?6597": "-"}) == "-"
    assert glyphs.agreed({"2014?09?07": "-"}) is None
    assert glyphs.agreed({"2014?09?07": "-", "021?6597": "-", "B?": "*"}) is None


BODY = "Body text that stays, long enough that a few unreadable glyphs leave the text layer usable."


def _pdf(path, lines):
    doc = pymupdf.open()
    page = doc.new_page(width=595, height=842)
    for k, text in enumerate(lines):
        page.insert_text((72, 100 + 30 * k), text, fontsize=11)
    for k in range(6):  # body text below: the page's text layer stays usable (``assess_native_layer``)
        page.insert_text((72, 400 + 16 * k), BODY, fontsize=10)
    doc.save(path)


def test_read_glyphs_are_written_everywhere_and_recorded(tmp_path):
    _pdf(tmp_path / "g.pdf", ["Date 2014\x0309\x0307", "Phone 021\x0365979772", "Range 3\x035"])
    seen = {100: "Date 2014-09-07", 130: "Phone 021-65979772", 160: "Range 3-5"}

    def read(page, box):
        return next((t for y, t in seen.items() if box[1] - 2 <= y <= box[3] + 2), BODY)

    ext = extract_pdf(tmp_path / "g.pdf", read=read)
    texts = [b.text for b in ext.blocks if b.kind == BlockKind.TEXT]
    assert "".join(texts).count("\ufffd") == 0
    assert any("2014-09-07" in t for t in texts) and any("3-5" in t for t in texts)
    assert any(d.choice == glyphs.READ for b in ext.blocks for d in b.decisions)


def test_a_glyph_read_in_one_line_only_stays(tmp_path):
    _pdf(tmp_path / "g.pdf", ["Date 2014\x0309\x0307"])
    ext = extract_pdf(tmp_path / "g.pdf", read=lambda page, box: "Date 2014-09-07")
    assert "\ufffd" in "".join(b.text for b in ext.blocks)


def test_a_line_of_unreadable_glyphs_where_the_image_shows_no_text_is_excluded(tmp_path):
    _pdf(tmp_path / "g.pdf", ["Body text that stays", "\x04\x04\x04\x04\x04\x04"])
    ext = extract_pdf(tmp_path / "g.pdf", read=lambda page, box: BODY if box[1] < 110 or box[1] > 300 else "")
    ornament = next(b for b in ext.blocks if "\ufffd" in (b.observations[0].text or ""))
    assert ornament.status == BlockStatus.EXCLUDED
    assert all(e.disposition == "excluded" for e in ext.ledger if e.block == ornament.id)
    assert any(b.status != BlockStatus.EXCLUDED and "Body" in b.text for b in ext.blocks)


def test_a_line_of_unreadable_glyphs_in_a_picture_is_the_picture_s(tmp_path):
    _pdf(tmp_path / "g.pdf", ["Body text that stays", "\x01\x02"])
    picture = ("image", (60.0, 115.0, 300.0, 140.0))
    ext = extract_pdf(tmp_path / "g.pdf", layout=lambda page: [picture], read=lambda page, box: "e 1")
    label = next(b for b in ext.blocks if "\ufffd" in (b.observations[0].text or ""))
    assert label.status == BlockStatus.EXCLUDED


def test_a_line_of_unreadable_glyphs_the_image_shows_as_text_stays(tmp_path):
    _pdf(tmp_path / "g.pdf", ["Body text that stays", "\x05\x06\x07"])
    ext = extract_pdf(tmp_path / "g.pdf", read=lambda page, box: "some words")
    line = next(b for b in ext.blocks if "\ufffd" in (b.observations[0].text or ""))
    assert line.status != BlockStatus.EXCLUDED  # listed for the agent as unreadable characters


def test_a_line_of_unreadable_glyphs_in_a_display_formula_stays_for_the_formula_tool(tmp_path):
    _pdf(tmp_path / "g.pdf", ["Body text that stays", "\x01\x02"])
    formula = ("display_formula", (60.0, 115.0, 300.0, 140.0))
    ext = extract_pdf(tmp_path / "g.pdf", layout=lambda page: [formula], read=lambda page, box: "")
    piece = next(b for b in ext.blocks if "�" in (b.observations[0].text or ""))
    assert piece.status != BlockStatus.EXCLUDED
