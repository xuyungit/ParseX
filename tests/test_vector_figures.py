"""Figures drawn with vector paths on native PDF pages (no raster image): rendered as images, the text layer's
lines inside them their text."""

import pymupdf

from parserx.content.pdf_native import extract_pdf
from parserx.ir.enums import BlockKind, RelationKind
from parserx.render.markdown import render_markdown

BODY = "Body text above the figure, long enough to be a paragraph of the page and not a label of the drawing."
PICTURE = ("image", (90.0, 140.0, 330.0, 260.0))


def _page(path, *, drawn=True, raster=False):
    doc = pymupdf.open()
    page = doc.new_page(width=595, height=842)
    page.insert_text((72, 100), BODY, fontsize=10)
    if drawn:
        page.draw_rect((100, 150, 200, 200))
        page.draw_rect((220, 150, 320, 200))
        page.draw_line((200, 175), (220, 175))
    if raster:
        pix = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, 40, 40), False)
        pix.clear_with(200)
        page.insert_image((100, 150, 320, 250), pixmap=pix)
    page.insert_text((120, 180), "client", fontsize=9)
    page.insert_text((240, 180), "master", fontsize=9)
    page.insert_text((72, 320), "Text after the figure stays in the body as before.", fontsize=10)
    doc.save(path)


def test_a_drawn_picture_becomes_a_figure_holding_its_labels(tmp_path):
    _page(tmp_path / "v.pdf")
    ext = extract_pdf(tmp_path / "v.pdf", layout=lambda page: [PICTURE])
    figures = [b for b in ext.blocks if b.kind == BlockKind.FIGURE]
    assert len(figures) == 1 and any(a.role == "render" for a in ext.assets)
    held = {r.dst for r in ext.relations if r.kind == RelationKind.CONTAINS and r.src == figures[0].id}
    labels = {b.id for b in ext.blocks if b.text in ("client", "master")}
    assert labels and labels <= held
    body = [b for b in ext.blocks if b.id not in held and b.kind == BlockKind.TEXT]
    assert any("Body text" in b.text for b in body) and any("after the figure" in b.text for b in body)


def test_the_labels_render_with_their_figure_not_in_the_body(tmp_path):
    _page(tmp_path / "v.pdf")
    ext = extract_pdf(tmp_path / "v.pdf", layout=lambda page: [PICTURE])
    md = render_markdown(ext.to_state(doc_id="v", source="v.pdf", source_sha256="0"))
    image_text = md[md.index("parserx:image-text"):md.index("/parserx:image-text")]
    assert "![" in md and "client" in image_text and "master" in image_text
    assert md.count("client") == 1


def test_a_picture_region_of_text_alone_stays_text(tmp_path):
    _page(tmp_path / "v.pdf", drawn=False)
    ext = extract_pdf(tmp_path / "v.pdf", layout=lambda page: [PICTURE])
    assert not [b for b in ext.blocks if b.kind == BlockKind.FIGURE]
    assert not ext.relations


def test_a_raster_picture_is_not_rendered_again(tmp_path):
    _page(tmp_path / "v.pdf", raster=True)
    ext = extract_pdf(tmp_path / "v.pdf", layout=lambda page: [PICTURE])
    assert len([b for b in ext.blocks if b.kind == BlockKind.FIGURE]) == 1
    assert not any(a.role == "render" for a in ext.assets)


def test_small_raster_pieces_of_a_drawing_are_part_of_its_figure(tmp_path):
    doc = pymupdf.open()
    page = doc.new_page(width=595, height=842)
    page.insert_text((72, 100), BODY, fontsize=10)
    page.draw_rect((100, 150, 200, 200))
    page.draw_rect((220, 150, 320, 200))
    pix = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, 4, 4), False)
    pix.clear_with(0)
    page.insert_image((205, 173, 210, 177), pixmap=pix)  # an arrow head drawn as a tiny image
    page.insert_text((120, 180), "client", fontsize=9)
    doc.save(tmp_path / "v.pdf")
    ext = extract_pdf(tmp_path / "v.pdf", layout=lambda page: [PICTURE])
    figures = [b for b in ext.blocks if b.kind == BlockKind.FIGURE]
    assert len(figures) == 1 and any(d.choice == "vector_figure" for d in figures[0].decisions)
