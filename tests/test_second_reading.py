"""A second reading, by the service model, of what the scan engine read with mathematics where no text layer checks
it (tools/second_reading.py): blocks the two readings differ on are listed for the agent; the output stays."""

import io

import pymupdf
from PIL import Image

from parserx.tools import call_tool, second_reading, workspace_init
from parserx.tools.envelope import UnresolvedKind
from parserx.tools.views import unresolved_items
from parserx.workspace import Workspace
from tests.test_tools_contract import _config, _context


def test_readings_are_compared_by_their_letters_and_digits_case_kept():
    assert second_reading.differences(r"加入 5 $\mu$L 水", "加入 5 pL 水") == ({"μ": 1}, {"p": 1})
    assert second_reading.differences(r"$J_{K}^{T}$", r"$J_k^T$") == ({"K": 1}, {"k": 1})
    assert second_reading.differences(r"$x^{2}$ 与 $\mathrm{CDCl_{3}}$", "x² 与 CDCl$_{3}$") == ({}, {})


def test_a_reading_of_part_of_the_block_agrees_where_it_reads():
    # the engine reads a paragraph cut by a column break whole, at the place before the cut: the block's image shows
    # only the first part, and a reading of it holds a run of the block's characters, nothing else
    whole = r"为了确保两跨连续梁桥的安全运行，需要开发可靠的损伤定位技术。目前主要有两大类损伤定位方法 $^{[1]}$"
    assert second_reading.disagrees(whole, "为了确保两跨连续梁桥的安全运行，需要开发") is None
    assert second_reading.disagrees(whole, "为了确保两跨连续梁桥的安全运行，需要升发") is not None


def test_several_readings_list_a_block_only_where_they_all_differ_on_a_same_character():
    engine = r"Add 5 $\mu$L to $C_{57}H_{36}$."
    both = second_reading.differing(engine, [r"Add 5 $\mu$L to $C_{56}H_{36}$.", r"Add 5 μL to C$_{56}$H$_{36}$."])
    assert both == ({"7": 1}, {"6": 1})
    assert second_reading.differing(engine, [r"Add 5 $\mu$L to $C_{56}H_{36}$.", engine]) is None  # one alone
    assert second_reading.differing(engine, [r"Add 5 pL to $C_{57}H_{36}$.", r"Add 5 $\mu$L to $C_{56}H_{36}$."]) is None
    assert second_reading.differing(engine, [r"Add 5 $\mu$L to $C_{56}H_{36}$."]) == ({"7": 1}, {"6": 1})


def _scanned_pdf(tmp_path):
    buf = io.BytesIO()
    Image.new("RGB", (300, 424), (235, 235, 235)).save(buf, "PNG")
    doc = pymupdf.open()
    page = doc.new_page(width=595, height=842)
    page.insert_image(page.rect, stream=buf.getvalue())
    path = tmp_path / "s.pdf"
    doc.save(path)
    return path


def _entry(label, text, box):
    return {"block_label": label, "block_content": text, "block_bbox": list(box)}


def _run(tmp_path, second, readers=None):
    entries = [_entry("text", "A sentence of prose without any mathematics in it at all.", (72, 100, 500, 114)),
               _entry("text", r"Add 5 $\mu$L of the solution to $C_{57}H_{36}$.", (72, 140, 500, 154)),
               _entry("display_formula", r"E = m c^{2}", (200, 200, 400, 230))]
    base = _context(page=lambda: {"prunedResult": {"width": 595, "height": 842, "parsing_res_list": entries}})
    calls = []

    class Reader:  # answers by the crop (the calls run concurrently): the paragraph's is twice the formula's width
        def __init__(self, model):
            self.model = model

        def describe_image(self, image_path, prompt, **kw):
            calls.append(self.model)
            with Image.open(image_path) as crop:
                kind = "paragraph" if crop.width > 300 * second_reading.DPI / 72 else "formula"
                return second(kind) if readers is None else second(kind, self.model)

    class Context(base):
        def _new_vlm(self, cfg):
            return Reader(cfg.model)

    config = _config()
    config.runtime.describe_figures = False
    if readers is not None:
        from parserx.config.schema import ModelProfile, Reader as ReaderConfig

        config.models = {name: ModelProfile(endpoint=f"https://{name}.test/v1", api_key="test", model=name)
                         for name in readers}
        config.tools.second_readers = [ReaderConfig(use=name) for name in readers]
    workspace_init(_scanned_pdf(tmp_path), tmp_path / "ws", config=config)
    envelope, _ = call_tool("run_pipeline", tmp_path / "ws", {}, config=config, context_factory=Context)
    assert envelope.ok, envelope.failures
    _run.failures = envelope.failures
    return Workspace.open(tmp_path / "ws").load(), calls


def test_blocks_with_mathematics_on_a_scanned_page_are_read_again(tmp_path):
    answers = {"paragraph": r"Add 5 $\mu$L of the solution to $C_{57}H_{36}$.", "formula": r"$$E = mc^2$$"}
    state, calls = _run(tmp_path, lambda k: answers[k])
    assert len(calls) == 2  # the prose without mathematics is not read again
    assert not [u for u in unresolved_items(state) if u.kind == UnresolvedKind.SECOND_READING]


def test_a_block_the_readings_differ_on_is_listed_and_stays(tmp_path):
    answers = {"paragraph": r"Add 5 $\mu$L of the solution to $C_{56}H_{36}$.", "formula": r"$$E = mc^2$$"}
    state, _ = _run(tmp_path, lambda k: answers[k])
    items = [u for u in unresolved_items(state) if u.kind == UnresolvedKind.SECOND_READING]
    assert len(items) == 1 and "7×1" in items[0].detail and "6×1" in items[0].detail
    block = next(b for b in state.blocks if b.id == items[0].target)
    assert "C_{57}" in block.text  # the scan engine's reading stays: the image decides


def test_a_block_corrected_to_agree_is_no_longer_listed(tmp_path):
    answers = {"paragraph": r"Add 5 $\mu$L of the solution to $C_{56}H_{36}$.", "formula": r"$$E = mc^2$$"}
    state, _ = _run(tmp_path, lambda k: answers[k])
    block = next(b for b in state.blocks if "C_{57}" in (b.text or ""))
    block.text = block.text.replace("C_{57}", "C_{56}")
    assert not [u for u in unresolved_items(state) if u.kind == UnresolvedKind.SECOND_READING]


def test_a_block_read_inside_an_image_is_cut_from_that_image(tmp_path):
    from types import SimpleNamespace

    from parserx.ir.anchor import AssetAnchor
    from parserx.ir.asset import Asset
    from parserx.ir.block import Block
    from parserx.ir.enums import BlockKind, ObservationStatus, TaskKind
    from parserx.ir.observation import Observation

    picture = Image.new("RGB", (400, 200), (255, 255, 255))
    picture.paste((0, 0, 0), (100, 50, 300, 100))  # what the engine read sits here
    buf = io.BytesIO()
    picture.save(buf, "PNG")
    asset = Asset.from_bytes(buf.getvalue(), media_type="image/png", width=400, height=200, role="original")
    (tmp_path / asset.path).parent.mkdir(parents=True, exist_ok=True)
    (tmp_path / asset.path).write_bytes(buf.getvalue())
    figure = Block(id="f", kind=BlockKind.FIGURE, order=0, anchors=[AssetAnchor(asset=asset.id, bbox=(0, 0, 400, 200),
                                                                                image_size=(400, 200))])
    pixel = AssetAnchor(asset=asset.id, bbox=(50, 25, 150, 50), image_size=(200, 100))  # at half the file's scale
    obs = Observation(id="o", engine="paddleocr", engine_version="v", task=TaskKind.RECOGNIZE, anchor=pixel,
                      text="$x^2$", status=ObservationStatus.OK)
    block = Block(id="b", kind=BlockKind.TEXT, order=1, anchors=[pixel], observations=[obs], chosen_observation="o",
                  text="$x^2$")
    png = second_reading._crop(SimpleNamespace(ws=SimpleNamespace(root=tmp_path)), block, figure, {asset.id: asset})
    with Image.open(io.BytesIO(png)) as crop:
        assert crop.size == (208, 58) and crop.getpixel((104, 29)) == (0, 0, 0)


def test_two_readers_are_asked_at_once_and_list_what_they_both_read_otherwise(tmp_path):
    right, wrong = r"Add 5 $\mu$L of the solution to $C_{57}H_{36}$.", r"Add 5 $\mu$L of the solution to $C_{56}H_{36}$."
    answers = {("paragraph", "a"): wrong, ("paragraph", "b"): wrong, ("formula", "a"): r"$$E = mc^3$$",
               ("formula", "b"): r"$$E = mc^2$$"}  # only reader a reads the formula otherwise: its own misread
    state, calls = _run(tmp_path, lambda kind, model: answers[(kind, model)], readers=["a", "b"])
    assert sorted(calls) == ["a", "a", "b", "b"]
    items = [u for u in unresolved_items(state) if u.kind == UnresolvedKind.SECOND_READING]
    assert len(items) == 1 and len(items[0].quotes) == 2 and "7×1" in items[0].detail
    block = next(b for b in state.blocks if b.id == items[0].target)
    assert "C_{57}" in block.text and right == block.text.strip()


def test_a_reader_that_writes_nothing_is_a_failure_and_the_other_reads_alone(tmp_path):
    wrong = r"Add 5 $\mu$L of the solution to $C_{56}H_{36}$."
    answers = {("paragraph", "a"): wrong, ("paragraph", "b"): "", ("formula", "a"): r"$$E = mc^2$$",
               ("formula", "b"): r"$$E = mc^2$$"}
    state, _ = _run(tmp_path, lambda kind, model: answers[(kind, model)], readers=["a", "b"])
    assert any("b: an empty answer" in f.message for f in _run.failures)
    items = [u for u in unresolved_items(state) if u.kind == UnresolvedKind.SECOND_READING]
    assert len(items) == 1 and len(items[0].quotes) == 1  # reader a's reading, judged alone
