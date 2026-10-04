"""An image read the way up it reads best (reading/local.read_upright, Q150), with a reader that reads only text
that stands the right way up — offline."""

import io

from PIL import Image

from parserx.reading.local import read_upright, upright

TEXT = "编制单位：华通智能装备股份有限公司　2025年度　所有者权益变动表"


def _png(width, height, mark):
    """A blank image with a dark corner: where the corner is tells which way up the image stands."""
    image = Image.new("RGB", (width, height), "white")
    x, y = {"top-left": (0, 0), "bottom-right": (width - 4, height - 4)}[mark]
    image.paste((0, 0, 0), (x, y, x + 4, y + 4))
    out = io.BytesIO()
    image.save(out, "PNG")
    return out.getvalue()


class Reader:
    """Reads TEXT cleanly only from a wide image with its dark corner top left; otherwise what a turned page gives."""

    name, version = "reading", "upright-test-1"

    def __init__(self):
        self.reads = 0

    def read(self, png):
        self.reads += 1
        image = Image.open(io.BytesIO(png)).convert("RGB")
        upright = image.getpixel((1, 1)) == (0, 0, 0)
        if image.width < image.height:  # turned a quarter: the lines stand on end
            return [((10, 10, 20, 90), "口一", 0.6), ((30, 10, 40, 90), "二十", 0.6)]
        if not upright:  # upside down: confident-looking garbage
            return [((10, 10, 190, 20), "280999 2299039", 0.7)]
        return [((10, 10, 190, 20), TEXT, 0.98)]


def test_an_upright_image_is_read_once():
    reader = Reader()
    assert [t for _, t, _ in read_upright(reader, _png(200, 60, "top-left"), None)] == [TEXT] and reader.reads == 1


def test_an_image_turned_a_quarter_is_read_the_right_way():
    tall = Image.open(io.BytesIO(_png(200, 60, "top-left"))).rotate(90, expand=True)
    out = io.BytesIO()
    tall.save(out, "PNG")
    assert [t for _, t, _ in read_upright(Reader(), out.getvalue(), None)] == [TEXT]


def test_an_unsure_reading_is_tried_upside_down():
    assert [t for _, t, _ in read_upright(Reader(), _png(200, 60, "bottom-right"), None)] == [TEXT]
