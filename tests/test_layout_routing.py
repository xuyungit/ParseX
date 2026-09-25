"""Layout area statistics and image routing (guide §6.5), shadow run (plan P1-9) with a fake detector."""

import io

import fitz
import numpy as np
import pytest
from PIL import Image

from parserx.config.schema import RoutingConfig
from parserx.ir.enums import BlockKind, ImageRoute
from parserx.layout import labels
from parserx.layout.area import coverage
from parserx.layout.detector import Region
from parserx.routing.image import cheap_filter, route


def _r(label, box, score=0.9):
    return Region(bbox=box, label=label, score=score)


# ── Area statistics ─────────────────────────────────────────────────────


def test_union_areas_and_text_inside_figures():
    regions = [
        _r("text", (0, 0, 50, 10)), _r("text", (25, 0, 75, 10)),  # overlapping text: union 75x10
        _r("image", (0, 50, 100, 100)),                            # figure 100x50
        _r("text", (10, 60, 40, 70)),                              # label inside the figure: counts only as f
    ]
    t, f = coverage(regions, width=100, height=100, source="layout")
    assert t == pytest.approx(0.075) and f == pytest.approx(0.5)


def test_whitespace_counts_for_nothing():
    assert coverage([], width=10, height=10, source="layout") == (0.0, 0.0)


# ── Routing table ───────────────────────────────────────────────────────

CFG = RoutingConfig()


@pytest.mark.parametrize("regions, expected", [
    ([_r("text", (0, 0, 100, 70))], ImageRoute.SCAN),
    ([_r("image", (0, 0, 100, 80)), _r("figure_title", (0, 85, 100, 95))], ImageRoute.FIGURE),
    ([_r("text", (0, 0, 100, 40)), _r("image", (0, 50, 100, 90))], ImageRoute.MIXED),
    ([_r("text", (0, 0, 10, 10))], ImageRoute.UNCERTAIN),                        # both low
    ([_r("text", (0, 0, 100, 70), score=0.3)], ImageRoute.UNCERTAIN),            # all detections unsure
])
def test_route_table(regions, expected):
    scaled = [Region(bbox=tuple(v * 10 for v in r.bbox), label=r.label, score=r.score) for r in regions]
    assert route(width=1000, height=1000, pixel_std=40.0, regions=scaled, config=CFG).route == expected  # not an icon


@pytest.mark.parametrize("size, std, reason", [
    ((20, 400), 30.0, "short_side"),
    ((400, 300), 0.5, "blank"),
    ((600, 40), 30.0, "strip"),
    ((70, 84), 30.0, "trivial"),       # an icon: small area, short long edge
    ((120, 120), 30.0, None),          # 14 400 px² is above the trivial area
    ((400, 300), 30.0, None),
])
def test_cheap_filter(size, std, reason):
    assert cheap_filter(*size, pixel_std=std, config=CFG) == reason


def test_a_decorative_shape_is_decorative_only_without_text_in_it():
    # P4-4: shape alone does not decide — a one-line equation is a thin strip; text or a formula detected in it
    # keeps it (routed by its content), an image or nothing detected confirms the decorative candidate
    formula = route(width=542, height=40, pixel_std=30.0, regions=[_r("display_formula", (10, 2, 530, 38))],
                    config=CFG)
    assert formula.route == ImageRoute.SCAN and formula.evidence["decorative_shape"] == "strip"
    logo = route(width=75, height=78, pixel_std=30.0, regions=[_r("image", (0, 0, 75, 78))], config=CFG)
    assert logo.route == ImageRoute.DECORATIVE and logo.evidence["decorative"] == "trivial"
    nothing = route(width=20, height=400, pixel_std=30.0, regions=[], config=CFG)
    assert nothing.route == ImageRoute.DECORATIVE and nothing.evidence["decorative"] == "short_side"
    blank = route(width=300, height=300, pixel_std=0.1, regions=[_r("text", (0, 0, 300, 300))], config=CFG)
    assert blank.route == ImageRoute.DECORATIVE  # a blank image holds nothing, whatever a detector says


def test_route_evidence_is_flat():
    result = route(width=1000, height=1000, pixel_std=40.0, regions=[_r("text", (0, 0, 1000, 700))], config=CFG)
    assert set(result.evidence) >= {"t", "f", "regions", "width", "height"}
    assert all(isinstance(v, (int, float, str, bool)) for v in result.evidence.values())


def test_every_detector_label_is_mapped():
    detector_labels = {
        "abstract", "algorithm", "aside_text", "chart", "content", "display_formula", "doc_title", "figure_title",
        "footer", "footer_image", "footnote", "formula_number", "header", "header_image", "image",
        "inline_formula", "number", "paragraph_title", "reference", "reference_content", "seal", "table", "text",
        "vertical_text", "vision_footnote"}
    assert detector_labels <= set(labels.LAYOUT)
    assert labels.to_kind("docx", "image") == BlockKind.FIGURE and labels.to_kind("docx", "paragraph") == BlockKind.TEXT


@pytest.mark.live_layout
def test_real_detector_finds_text_on_a_rendered_page():
    from parserx.layout.detector import RapidLayoutDetector

    doc = fitz.open()
    page = doc.new_page(width=595, height=842)
    page.insert_text((72, 100), "Chapter One", fontsize=22)
    for i in range(12):
        page.insert_text((72, 150 + 20 * i), "Body text line with several words to form a paragraph.", fontsize=11)
    png = page.get_pixmap(dpi=100).tobytes("png")
    regions = RapidLayoutDetector().detect(png)
    assert any(labels.to_kind("layout", r.label) in (BlockKind.TEXT, BlockKind.TITLE) for r in regions)


def test_detector_input_decoding_is_stable():
    from parserx.layout.detector import decode

    buf = io.BytesIO()
    Image.new("RGBA", (5, 4), (1, 2, 3, 128)).save(buf, "PNG")
    array = decode(buf.getvalue())
    assert array.shape == (4, 5, 3) and array.dtype == np.uint8


# ── Shadow run through the recognize tool ───────────────────────────────


class FakeDetector:
    name = "layout"
    version = "fake-detector-1"

    def __init__(self):
        self.calls = 0

    def detect(self, png):
        self.calls += 1
        with Image.open(io.BytesIO(png)) as image:
            w, h = image.size
        if w > 500:  # a page render: one title and one text region near the top
            return [Region(bbox=(95.0, 100.0, 400.0, 130.0), label="paragraph_title", score=0.9),
                    Region(bbox=(95.0, 180.0, 700.0, 260.0), label="text", score=0.8)]
        return [Region(bbox=(0.0, 0.0, float(w), float(h)), label="image", score=0.95)]


def _shadow_setup(tmp_path):
    from parserx.config.schema import ParserXConfig
    from parserx.tools import ToolContext, workspace_init

    doc = fitz.open()
    page = doc.new_page(width=595, height=842)
    page.insert_text((72, 90), "Chapter One", fontsize=18)
    page.insert_text((72, 150), "Body text of the first page.", fontsize=11)

    def png(w, h):
        buf = io.BytesIO()
        Image.fromarray(np.random.default_rng(0).integers(0, 255, (h, w, 3), dtype=np.uint8)).save(buf, "PNG")
        return buf.getvalue()

    page.insert_image(fitz.Rect(72, 300, 100, 330), stream=png(70, 84))     # an icon
    page.insert_image(fitz.Rect(72, 400, 372, 625), stream=png(400, 300))   # a real figure
    path = tmp_path / "doc.pdf"
    doc.save(path)
    config = ParserXConfig()
    workspace_init(path, tmp_path / "ws", config=config)
    detector = FakeDetector()

    class Context(ToolContext):
        def _new_detector(self):
            return detector

    return tmp_path / "ws", config, Context, detector


def test_shadow_run_records_without_changing_the_output_except_decorative(tmp_path):
    from parserx.render import render_markdown
    from parserx.tools import call_tool
    from parserx.workspace import Workspace

    ws, config, Context, detector = _shadow_setup(tmp_path)
    before = Workspace.open(ws).load()
    figures = [b.id for b in before.blocks if b.kind == BlockKind.FIGURE]
    env, _ = call_tool("recognize", ws, {"pages": [1], "blocks": figures, "engine": "layout"}, config=config,
                       context_factory=Context)
    assert env.ok, env.failures
    state = Workspace.open(ws).load()
    records = {r.id: r for r in state.images}
    routes = {b.id: b.decisions[-1] for b in state.blocks if b.id in figures}
    icon, figure = figures
    assert routes[icon].choice == "DECORATIVE" and routes[figure].choice == "FIGURE"
    assert routes[figure].reason.startswith("shadow")
    assert next(b for b in state.blocks if b.id == icon).status == "excluded"
    assert {r.route for r in records.values()} == {ImageRoute.DECORATIVE, ImageRoute.FIGURE}
    assert sum(r.shown for r in records.values()) == 1
    # page detections are evidence on the blocks they overlap; nothing else changed
    title = next(b for b in state.blocks if b.text == "Chapter One")
    assert [o.label for o in title.observations if o.task == "layout"] == ["paragraph_title"]
    icon_line = next(line for line in render_markdown(before).splitlines() if "![" in line)
    assert render_markdown(state) == render_markdown(before).replace(icon_line + "\n\n", "")
    ledger = {e.block: e.disposition for e in state.ledger}
    assert ledger[icon] == "excluded"


def test_shadow_run_is_idempotent_and_cached(tmp_path):
    from parserx.config.schema import CacheConfig
    from parserx.tools import call_tool
    from parserx.workspace import Workspace

    ws, config, Context, detector = _shadow_setup(tmp_path)
    config.cache = CacheConfig(mode="read_write", dir=str(tmp_path / "cache"))
    figures = [b.id for b in Workspace.open(ws).load().blocks if b.kind == BlockKind.FIGURE]
    request = {"pages": [1], "blocks": figures, "engine": "layout"}
    call_tool("recognize", ws, request, config=config, context_factory=Context)
    version, calls = Workspace.open(ws).load().version, detector.calls
    env, _ = call_tool("recognize", ws, request, config=config, context_factory=Context)
    assert env.ok and Workspace.open(ws).load().version == version and detector.calls == calls
    env, _ = call_tool("recognize", ws, {**request, "force": True}, config=config, context_factory=Context)
    assert detector.calls == calls  # forced again, but the detections come from the derived cache


def test_onnxruntime_telemetry_is_off():
    # it sends usage events, and in a sandbox leaves ":memory:.ses" in the working directory (P2-4 F4)
    import os

    import parserx.layout.detector  # noqa: F401

    assert os.environ["ORT_DISABLE_TELEMETRY"] == "1"


def test_small_images_are_detected_on_a_page_and_mapped_back():
    # P4-4: a decorative-shaped image is confirmed on a blank page of the layout resolution
    from parserx.tools.layout_shadow import on_page_regions

    seen = {}

    class PageDetector:
        version = "fake-page-detector"

        def detect(self, png):
            with Image.open(io.BytesIO(png)) as page:
                seen["size"] = page.size
            return [Region(bbox=(60.0, 55.0, 590.0, 85.0), label="display_formula", score=0.8),
                    Region(bbox=(700.0, 900.0, 800.0, 1000.0), label="text", score=0.9)]  # elsewhere on the page

    buf = io.BytesIO()
    Image.new("RGB", (542, 40), "white").save(buf, "PNG")
    regions = on_page_regions(PageDetector(), buf.getvalue(), (542, 40), 100, None)
    assert seen["size"] == (827, 1169)
    assert [(r.label, r.bbox) for r in regions] == [("display_formula", (10.0, 5.0, 540.0, 35.0))]
