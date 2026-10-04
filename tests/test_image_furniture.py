"""The running heads of a page shown as an image, read again where the scan engine left them out
(tools/image_furniture.py, Q151), and written with the image's text — offline, with a fake scan engine."""

import io

import pymupdf
import pytest
from PIL import Image

from parserx.config.schema import OCRBuilderConfig, ParserXConfig
from parserx.ir.anchor import AssetAnchor
from parserx.ir.block import Block
from parserx.ir.enums import (BlockKind, BlockStatus, ImageRoute, ObservationStatus, RelationKind, TaskKind)
from parserx.ir.observation import Observation
from parserx.ir.relation import Relation
from parserx.ir.state import ImageRecord, ReadLine
from parserx.render.markdown import render_markdown
from parserx.services.ocr import PaddleOCRService
from parserx.tools import ToolContext, image_furniture, workspace_init
from parserx.workspace import Workspace

HEADER = (100, 20, 500, 50)  # in the image's pixels
NUMBER = (400, 760, 500, 785)


def _png(width=600, height=800):
    out = io.BytesIO()
    Image.new("RGB", (width, height), "white").save(out, "PNG")
    return out.getvalue()


@pytest.fixture
def ws(tmp_path):
    doc = pymupdf.open()
    page = doc.new_page(width=595, height=842)
    page.insert_text((72, 72), "附件：检测报告", fontsize=12, fontname="china-s")
    page.insert_image(pymupdf.Rect(72, 100, 372, 500), stream=_png())
    doc.save(tmp_path / "doc.pdf")
    workspace_init(tmp_path / "doc.pdf", tmp_path / "ws", config=ParserXConfig())
    workspace = Workspace(tmp_path / "ws")
    with workspace.txn("test") as state:  # the image read as content: its body read, its running head left out
        figure = next(b for b in state.blocks if b.kind == BlockKind.FIGURE)
        anchor = next(a for a in figure.anchors if isinstance(a, AssetAnchor))
        for label, box in (("header", HEADER), ("number", NUMBER), ("text", (60, 100, 540, 700))):
            figure.observations.append(Observation(
                id=f"o-{figure.id}-{label}", engine="layout", engine_version="v", task=TaskKind.LAYOUT, label=label,
                anchor=AssetAnchor(asset=anchor.asset, bbox=box, image_size=anchor.image_size),
                status=ObservationStatus.OK))
        body = AssetAnchor(asset=anchor.asset, bbox=(60, 100, 540, 700), image_size=anchor.image_size)
        state.blocks.append(Block(id=f"{figure.id}-r001", kind=BlockKind.TEXT, order=figure.order + 1,
                                  status=BlockStatus.OK, anchors=[body], text="3 检测结果（及分析曲线）"))
        state.relations.append(Relation(id="r-contains-x", kind=RelationKind.CONTAINS, src=figure.id,
                                        dst=f"{figure.id}-r001"))
        state.images.append(ImageRecord(id=anchor.asset, route=ImageRoute.SCAN, shown=True, reading=[
            ReadLine(bbox=(110, 25, 490, 45), text="报告编号：ZJA1-X001-2023000001B", score=0.97),
            ReadLine(bbox=(405, 762, 495, 783), text="第5页共15页", score=0.95)]))
    return workspace


def _context(readings):
    class Context(ToolContext):
        def _new_ocr(self):
            service = PaddleOCRService(OCRBuilderConfig(endpoint="https://x/api/v2/ocr/jobs", token="t"))

            def run_job(file_bytes, filename, mime, job_key=None):
                with pymupdf.open(stream=file_bytes, filetype="pdf") as sub:
                    assert sub.page_count == len(readings)  # every region of the document in one request
                return {"layoutParsingResults": [
                    {"prunedResult": {"width": 400, "height": 30, "parsing_res_list": [
                        {"block_label": "text", "block_content": text, "block_bbox": [0, 0, 400, 30],
                         "block_order": 1}] if text else []}} for text in readings]}

            service._run_job = run_job
            return service
    config = ParserXConfig()
    config.cache.mode = "off"
    config.tools.scan_concurrency = 1  # one request: the answers below are the regions' in order
    return Context, config


def test_a_running_head_left_out_is_read_again_and_written_with_the_image(ws):
    assert [label for _, label, _ in image_furniture.places(ws.load())] == ["header", "number"]
    Context, config = _context(["报告编号：ZJA1-X001-2023000001B", ""])  # the page number reads as nothing
    added, failures = image_furniture.read_again(Context(ws, config))
    assert (added, failures) == (2, [])
    state = ws.load()
    kinds = {b.kind: b for b in state.blocks if b.id.split("-")[-1].startswith("f")}
    assert kinds[BlockKind.HEADER].status == BlockStatus.EXCLUDED
    assert kinds[BlockKind.PAGE_NUMBER].text == "第5页共15页"  # the local reading, where the engine read nothing
    assert kinds[BlockKind.PAGE_NUMBER].observations[0].engine == "reading"
    assert image_furniture.places(state) == []  # covered now
    md = render_markdown(state)
    assert "> <!-- 图片页眉：报告编号：ZJA1-X001-2023000001B · 图片页码：第5页共15页 -->" in md
    assert md.index("〔图片识别〕") < md.index("图片页眉") < md.index("3 检测结果")
    assert "PAGE 1 · " not in md  # not the page's own running head
    assert "> 〔图片页眉〕报告编号：ZJA1-X001-2023000001B" in render_markdown(state, page_furniture="text")
    assert "ZHA2" not in render_markdown(state, page_furniture="omit")
