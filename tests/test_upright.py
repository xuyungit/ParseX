"""Embedded images turned upright before they are read (tools/upright.py, Q150): by their text, or — too bare of
text — as Word shows them; offline, with a reader that reads only text standing the right way up."""

import io

import pytest
from docx import Document
from PIL import Image

from parserx.config.schema import ParserXConfig
from parserx.content.docx import _shown_turn
from parserx.ir.anchor import AssetAnchor
from parserx.ir.rotation import transform_box
from parserx.render.markdown import render_markdown
from parserx.tools import ToolContext, upright, workspace_init
from parserx.workspace import Workspace

TEXT = "编制单位：华通智能装备股份有限公司　2025年度　所有者权益变动表"
A = "http://schemas.openxmlformats.org/drawingml/2006/main"


def _image(width, height, mark=None) -> bytes:
    """A blank image; *mark*: a dark corner top left, which tells which way up the image stands."""
    image = Image.new("RGB", (width, height), "white")
    if mark:
        image.paste((0, 0, 0), (0, 0, 4, 4))
    out = io.BytesIO()
    image.save(out, "PNG")
    return out.getvalue()


def _turned(data: bytes, ccw: int) -> bytes:
    out = io.BytesIO()
    Image.open(io.BytesIO(data)).rotate(ccw, expand=True).save(out, "PNG")
    return out.getvalue()


class Reader:
    """Reads TEXT in a level line from a wide image whose dark corner is top left; a quarter off, the line stands
    on end; anything else reads as nothing."""

    name, version = "reading", "upright-step-1"

    def read(self, png):
        image = Image.open(io.BytesIO(png)).convert("RGB")
        w, h = image.size
        dark = [(x, y) for x, y in ((1, 1), (w - 2, 1), (1, h - 2), (w - 2, h - 2)) if image.getpixel((x, y)) == (0, 0, 0)]
        if not dark:
            return []
        if w > h and dark == [(1, 1)]:
            return [((10, 10, w - 10, 30), TEXT, 0.98)]
        if w < h:  # the recognizer turns a line standing on end and still reads it
            return [((10, 10, 30, h - 10), TEXT, 0.97)]
        return [((10, 10, w - 10, 30), "280999 2299039", 0.6)]


def _docx(tmp_path, data, rot=None):
    doc = Document()
    doc.add_paragraph("正文")
    doc.add_picture(io.BytesIO(data))
    if rot is not None:
        for xfrm in doc.inline_shapes[0]._inline.iter(f"{{{A}}}xfrm"):
            xfrm.set("rot", str(rot))
    path = tmp_path / "doc.docx"
    doc.save(path)
    return path


def _run(tmp_path, data, rot=None):
    workspace_init(_docx(tmp_path, data, rot), tmp_path / "ws", config=ParserXConfig())
    ws = Workspace(tmp_path / "ws")

    class Context(ToolContext):
        def _new_reader(self):
            return Reader()

    config = ParserXConfig()
    config.cache.mode = "off"
    todo = upright.todo(ws.load())
    turned = upright.turn_upright(Context(ws, config), todo)
    state = ws.load()
    figure = next(b for b in state.blocks if b.id in todo)
    anchor = next(a for a in figure.anchors if isinstance(a, AssetAnchor))
    assets = {a.id: a for a in state.assets}
    return turned, figure, assets[anchor.asset], assets, state


def test_an_image_turned_a_quarter_is_turned_upright_and_linked(tmp_path):
    stored = _turned(_image(300, 80, mark=True), 90)  # upright it is 300 × 80; stored turned a quarter
    turned, figure, asset, assets, state = _run(tmp_path, stored)
    assert turned == 1 and (asset.width, asset.height) == (300, 80) and asset.role == "original"
    original = assets[asset.derived_from]
    assert (original.width, original.height) == (80, 300)
    assert transform_box(asset.transform, (0, 0, 300, 80)) == (0, 0, 80, 300)  # back to the stored pixels
    decision = next(d for d in figure.decisions if d.choice == upright.CHOICE)
    assert decision.evidence["turn"] == 90 and decision.evidence["by"] == "reading"
    assert f"images/{asset.path.split('/')[-1]}" in render_markdown(state)  # the Markdown links the upright image
    assert upright.todo(state) == []  # looked at once


def test_an_upright_image_stays_as_it_is(tmp_path):
    turned, figure, asset, _, _ = _run(tmp_path, _image(300, 80, mark=True))
    assert turned == 0 and asset.derived_from is None
    assert next(d for d in figure.decisions if d.choice == upright.CHOICE).evidence["turn"] == 0


def test_a_photo_is_shown_the_way_word_shows_it(tmp_path):
    turned, figure, asset, _, _ = _run(tmp_path, _image(60, 40), rot=5400000)  # no text: Word's quarter turn
    assert turned == 1 and (asset.width, asset.height) == (40, 60)
    assert next(d for d in figure.decisions if d.choice == upright.CHOICE).evidence["by"] == "document"


def test_word_turning_a_level_table_to_fit_the_page_is_not_followed(tmp_path):
    turned, _, asset, _, _ = _run(tmp_path, _image(300, 80, mark=True), rot=16200000)
    assert turned == 0 and asset.derived_from is None  # its text reads level as stored: the text decides


@pytest.mark.parametrize("attrs, turn", [({"rot": "5400000"}, 90), ({"rot": "-5400000"}, 270),
                                         ({"rot": "0", "flipH": "1", "flipV": "1"}, 180), ({"rot": "600000"}, 0),
                                         ({"flipH": "1"}, 0), ({}, 0)])
def test_words_turn_of_a_picture(attrs, turn):
    from lxml import etree

    node = etree.fromstring(f'<w:drawing xmlns:w="w" xmlns:a="{A}"><a:xfrm/></w:drawing>')
    for key, value in attrs.items():
        node.find(f"{{{A}}}xfrm").set(key, value)
    assert _shown_turn(node) == turn


def _pdf_with(tmp_path, data, rotate):
    import pymupdf

    doc = pymupdf.open()
    page = doc.new_page(width=595, height=842)
    page.insert_text((72, 72), "A page with an image placed on it", fontsize=11)
    width, height = Image.open(io.BytesIO(data)).size
    w, h = (width, height) if rotate in (0, 180) else (height, width)
    page.insert_image(pymupdf.Rect(100, 200, 100 + w, 200 + h), stream=data, rotate=rotate)
    path = tmp_path / "doc.pdf"
    doc.save(path)
    return path


def test_a_pdf_page_shows_a_photo_turned_and_so_does_the_output(tmp_path):
    # no text to read the way up from: the image's placement on the page decides (pymupdf rotate=90: a quarter
    # turn counter-clockwise, 270 clockwise)
    from parserx.tools.source import _place_image

    workspace_init(_pdf_with(tmp_path, _image(120, 60), 90), tmp_path / "ws", config=ParserXConfig())
    ws = Workspace(tmp_path / "ws")
    figure = next(b for b in ws.load().blocks if b.kind.value == "figure")
    assert next(d.evidence["shown_turn"] for d in figure.decisions if "shown_turn" in d.evidence) == 270

    class Context(ToolContext):
        def _new_reader(self):
            return Reader()

    config = ParserXConfig()
    config.cache.mode = "off"
    ctx = Context(ws, config)
    assert upright.turn_upright(ctx, upright.todo(ws.load())) == 1
    state = ws.load()
    image, problem = _place_image(ctx, state, block=figure.id, page=None, whole_page=False)
    assert problem is None and (image.width, image.height) == (60, 120)  # upright as the page shows it
    # its pixels still lead to the image's box on the page
    box = transform_box(image.transform, (0, 0, image.width, image.height))
    assert tuple(round(v) for v in box) == (100, 200, 160, 320)
