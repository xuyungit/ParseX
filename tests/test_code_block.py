"""Tests for CodeBlockProcessor."""

from parserx.models.elements import Document, FontInfo, Page, PageElement
from parserx.processors.code_block import CodeBlockProcessor, is_monospace_font


# ── is_monospace_font ─────────────────────────────────────────────────


def test_monospace_detection_known_fonts():
    assert is_monospace_font("Monaco")
    assert is_monospace_font("Menlo-Regular")
    assert is_monospace_font("Courier New")
    assert is_monospace_font("Consolas")
    assert is_monospace_font("Source Code Pro")
    assert is_monospace_font("JetBrains Mono")
    assert is_monospace_font("Fira Code")
    assert is_monospace_font("Inconsolata")


def test_monospace_detection_proportional_fonts():
    assert not is_monospace_font("PingFangSC-Regular")
    assert not is_monospace_font(".SFNS-Regular_wdth_opsz1")
    assert not is_monospace_font("SimSun")
    assert not is_monospace_font("Arial")
    assert not is_monospace_font("Times New Roman")
    assert not is_monospace_font("Helvetica")
    assert not is_monospace_font("")


# ── Helpers ───────────────────────────────────────────────────────────

_BODY_FONT = FontInfo(name="PingFangSC-Regular", size=12.8)
_CODE_FONT = FontInfo(name="Monaco", size=11.2)


def _make_elem(
    content: str,
    font: FontInfo = _BODY_FONT,
    bbox: tuple[float, float, float, float] = (0, 0, 500, 20),
) -> PageElement:
    return PageElement(
        type="text", content=content, font=font, bbox=bbox, page_number=1,
    )


def _make_doc(elements: list[PageElement]) -> Document:
    doc = Document(pages=[Page(number=1, width=595, height=842, elements=elements)])
    doc.metadata.font_stats.body_font = _BODY_FONT
    return doc


# ── CodeBlockProcessor ────────────────────────────────────────────────


def test_mixed_font_tags_monospace_as_code():
    elems = [
        _make_elem("暂停ceph自平衡"),
        _make_elem("ceph osd set nobackfill", font=_CODE_FONT),
        _make_elem("停止osd容器"),
    ]
    doc = _make_doc(elems)
    CodeBlockProcessor().process(doc)

    assert not elems[0].metadata.get("code_block")
    assert elems[1].metadata.get("code_block") is True
    assert not elems[2].metadata.get("code_block")


def test_all_monospace_no_tagging():
    """If the entire document uses monospace, don't tag anything as code."""
    elems = [
        _make_elem("line one", font=_CODE_FONT),
        _make_elem("line two", font=_CODE_FONT),
    ]
    doc = _make_doc(elems)
    CodeBlockProcessor().process(doc)

    assert not elems[0].metadata.get("code_block")
    assert not elems[1].metadata.get("code_block")


def test_all_proportional_no_tagging():
    elems = [
        _make_elem("paragraph one"),
        _make_elem("paragraph two"),
    ]
    doc = _make_doc(elems)
    CodeBlockProcessor().process(doc)

    assert not elems[0].metadata.get("code_block")
    assert not elems[1].metadata.get("code_block")


def test_consecutive_code_blocks_merged():
    elems = [
        _make_elem("暂停ceph"),
        _make_elem("ceph osd set nobackfill", font=_CODE_FONT, bbox=(50, 100, 400, 120)),
        _make_elem("ceph osd set norebalance", font=_CODE_FONT, bbox=(50, 120, 400, 140)),
        _make_elem("ceph osd set norecovery", font=_CODE_FONT, bbox=(50, 140, 400, 160)),
        _make_elem("停止osd容器"),
    ]
    doc = _make_doc(elems)
    CodeBlockProcessor().process(doc)

    # Three code elements should be merged into one.
    page_elems = doc.pages[0].elements
    assert len(page_elems) == 3  # body + merged_code + body
    code_elem = page_elems[1]
    assert code_elem.metadata.get("code_block") is True
    assert "ceph osd set nobackfill" in code_elem.content
    assert "ceph osd set norebalance" in code_elem.content
    assert "ceph osd set norecovery" in code_elem.content
    # Bbox should encompass all three.
    assert code_elem.bbox[1] == 100  # min y0
    assert code_elem.bbox[3] == 160  # max y1


def test_non_consecutive_code_stays_separate():
    elems = [
        _make_elem("ceph osd set nobackfill", font=_CODE_FONT),
        _make_elem("说明文字"),
        _make_elem("docker stop ceph_osd_6", font=_CODE_FONT),
    ]
    doc = _make_doc(elems)
    CodeBlockProcessor().process(doc)

    page_elems = doc.pages[0].elements
    assert len(page_elems) == 3  # Not merged
    assert page_elems[0].metadata.get("code_block") is True
    assert not page_elems[1].metadata.get("code_block")
    assert page_elems[2].metadata.get("code_block") is True


def test_skip_render_elements_ignored():
    elems = [
        _make_elem("body text"),
        _make_elem("skipped code", font=_CODE_FONT),
    ]
    elems[1].metadata["skip_render"] = True
    doc = _make_doc(elems)
    CodeBlockProcessor().process(doc)

    # skip_render mono element should not be tagged — and since the only
    # non-skip mono element is gone, no tagging at all.
    assert not elems[1].metadata.get("code_block")


def test_disabled_config():
    from parserx.config.schema import CodeBlockConfig

    elems = [
        _make_elem("body text"),
        _make_elem("code text", font=_CODE_FONT),
    ]
    doc = _make_doc(elems)
    CodeBlockProcessor(CodeBlockConfig(enabled=False)).process(doc)
    assert not elems[1].metadata.get("code_block")


def test_numbered_list_with_inline_code_not_tagged():
    """Elements starting with numbered list patterns should not be tagged as code."""
    elems = [
        _make_elem("暂停ceph"),
        _make_elem("2. 停⽌osd容器，docker stop ceph_osd_6", font=_CODE_FONT),
        _make_elem("i. ceph osd out osd.6", font=_CODE_FONT),
        _make_elem("纯代码行", font=_CODE_FONT),  # CJK start
    ]
    doc = _make_doc(elems)
    CodeBlockProcessor().process(doc)

    assert not elems[1].metadata.get("code_block")  # "2. ..." is a list item
    assert not elems[2].metadata.get("code_block")  # "i. ..." is a sub-item
    assert not elems[3].metadata.get("code_block")  # CJK start = body text


def test_shell_comment_not_heading_after_code_block():
    """Shell comments starting with # should not become headings when inside code blocks."""
    elems = [
        _make_elem("对于识别出的新数据盘打标签"),
        _make_elem("# 参考命令如下", font=_CODE_FONT),
        _make_elem("parted /dev/sdf -s -- mklabel gpt", font=_CODE_FONT),
    ]
    doc = _make_doc(elems)
    CodeBlockProcessor().process(doc)

    page_elems = doc.pages[0].elements
    # Code elements should be merged and tagged as code_block.
    code_elem = page_elems[1]
    assert code_elem.metadata.get("code_block") is True
    assert "# 参考命令如下" in code_elem.content
    assert "parted" in code_elem.content
