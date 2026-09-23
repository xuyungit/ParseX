"""DOCX reader (guide §6.9, plan R1/R3, Q26) on synthetic documents: python-docx plus hand-written XML."""

import io

import pytest
from docx import Document
from docx.enum.section import WD_SECTION
from docx.oxml import parse_xml
from PIL import Image

from parserx.content.docx import extract_docx
from parserx.ir.anchor import AssetAnchor, DocxAnchor
from parserx.ir.enums import BlockKind, BlockStatus

W = 'xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"'


def _add(body, fragment: str) -> None:
    """Insert a paragraph where python-docx would: before the body-level sectPr."""
    body[-1].addprevious(parse_xml(fragment))


def _png() -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (40, 30), (10, 120, 200)).save(buf, "PNG")
    return buf.getvalue()


@pytest.fixture
def docx_path(tmp_path):
    doc = Document()
    doc.add_paragraph("Annual Report", style="Title")
    doc.add_paragraph("Overview", style="Heading 1")
    body = doc.element.body
    # tracked changes: final view keeps insertions and drops deletions
    _add(body, (
        f'<w:p {W}><w:r><w:t xml:space="preserve">Revenue was </w:t></w:r>'
        '<w:del w:id="1" w:author="a"><w:r><w:delText>90</w:delText></w:r></w:del>'
        '<w:ins w:id="2" w:author="a"><w:r><w:t>100</w:t></w:r></w:ins>'
        '<w:r><w:t xml:space="preserve"> million.</w:t></w:r></w:p>'))
    _add(body, (
        f'<w:p {W}><w:moveFrom w:id="3" w:author="a"><w:r><w:t>Moved away.</w:t></w:r></w:moveFrom>'
        '<w:r><w:t>Stays.</w:t></w:r></w:p>'))
    # a complex field: only its result text is content
    _add(body, (
        f'<w:p {W}><w:r><w:t xml:space="preserve">See page </w:t></w:r>'
        '<w:r><w:fldChar w:fldCharType="begin"/></w:r><w:r><w:instrText> PAGEREF _Toc1 </w:instrText></w:r>'
        '<w:r><w:fldChar w:fldCharType="separate"/></w:r><w:r><w:t>7</w:t></w:r>'
        '<w:r><w:fldChar w:fldCharType="end"/></w:r>'
        '<w:fldSimple w:instr=" DATE "><w:r><w:t xml:space="preserve"> (2026)</w:t></w:r></w:fldSimple></w:p>'))
    # a direct outline level on a plain paragraph
    _add(body, (f'<w:p {W}><w:pPr><w:outlineLvl w:val="1"/></w:pPr><w:r><w:t>Direct level</w:t></w:r></w:p>'))
    table = doc.add_table(rows=3, cols=3)
    table.cell(0, 0).merge(table.cell(0, 1)).text = "Merged header"
    table.cell(0, 2).text = "C"
    table.cell(1, 0).merge(table.cell(2, 0)).text = "Tall"
    for r, c, v in [(1, 1, "10"), (1, 2, "11"), (2, 1, "20"), (2, 2, "21")]:
        table.cell(r, c).text = v
    doc.add_page_break()
    doc.add_paragraph("After the page break")
    image_stream = io.BytesIO(_png())
    doc.add_picture(image_stream)
    doc.add_section(WD_SECTION.CONTINUOUS)
    doc.add_paragraph("New section")
    # a textbox (unsupported in Phase 1): its text must be accounted, not silently lost
    _add(body, (
        f'<w:p {W} xmlns:wps="http://schemas.microsoft.com/office/word/2010/wordprocessingShape" '
        'xmlns:mc="http://schemas.openxmlformats.org/markup-compatibility/2006" '
        'xmlns:v="urn:schemas-microsoft-com:vml"><w:r><mc:AlternateContent><mc:Choice Requires="wps">'
        '<w:drawing><wps:txbx><w:txbxContent><w:p><w:r><w:t>Callout text</w:t></w:r></w:p></w:txbxContent>'
        '</wps:txbx></w:drawing></mc:Choice><mc:Fallback><w:pict><v:textbox><w:txbxContent><w:p><w:r>'
        '<w:t>Callout text</w:t></w:r></w:p></w:txbxContent></v:textbox></w:pict></mc:Fallback>'
        '</mc:AlternateContent></w:r></w:p>'))
    path = tmp_path / "doc.docx"
    doc.save(path)
    return path


def _text_blocks(ext):
    return [b for b in ext.blocks if b.kind in (BlockKind.TEXT, BlockKind.TITLE) and b.status == BlockStatus.OK]


def test_final_view_of_tracked_changes(docx_path):
    ext = extract_docx(docx_path)
    texts = [b.text for b in _text_blocks(ext)]
    assert "Revenue was 100 million." in texts and "Stays." in texts
    deleted = [b for b in ext.blocks if b.status == BlockStatus.EXCLUDED]
    assert sorted(b.text for b in deleted) == ["90", "Moved away."]
    assert all(b.decisions[-1].stage == "exclude" and b.decisions[-1].choice == "revision_deleted" for b in deleted)
    ledger = {e.block: e for e in ext.ledger}
    assert all(ledger[b.id].unit == "docx_deleted" and ledger[b.id].disposition == "excluded" for b in deleted)
    assert any("tracked changes" in w for w in ext.warnings)


def test_fields_keep_only_their_results(docx_path):
    texts = [b.text for b in _text_blocks(extract_docx(docx_path))]
    assert "See page 7 (2026)" in texts
    assert not any("PAGEREF" in t or "DATE" in t for t in texts)


def test_style_and_outline_evidence(docx_path):
    blocks = {b.text: b for b in _text_blocks(extract_docx(docx_path))}
    title = blocks["Annual Report"].observations[0].style
    heading = blocks["Overview"].observations[0].style
    direct = blocks["Direct level"].observations[0].style
    assert title.style_name == "Title"
    assert heading.style_name == "heading 1" and heading.outline_level == 0  # built-in names are stored lowercase
    assert direct.outline_level == 1 and direct.style_name == "Normal"
    assert blocks["Overview"].kind == BlockKind.TEXT  # roles are the structure step's job


def test_table_spans(docx_path):
    table = next(b for b in extract_docx(docx_path).blocks if b.kind == BlockKind.TABLE)
    grid = table.cells
    assert (grid.n_rows, grid.n_cols) == (3, 3)
    head = grid.slot(0, 1)
    assert head.content == "Merged header" and head.colspan == 2
    tall = grid.slot(2, 0)
    assert tall.content == "Tall" and tall.rowspan == 2
    assert grid.slot(2, 2).content == "21"


def test_segments_follow_page_and_section_breaks(docx_path):
    ext = extract_docx(docx_path)
    seg = {b.text: b.anchors[0].segment for b in _text_blocks(ext)}
    assert seg["Overview"] == 1 and seg["After the page break"] == 2 and seg["New section"] == 3
    assert [(p.n, p.unit, p.starts_with) for p in ext.pages] == [
        (1, "docx_segment", None), (2, "docx_segment", "page_break"), (3, "docx_segment", "section_break")]


def test_images_become_assets(docx_path):
    ext = extract_docx(docx_path)
    figure = next(b for b in ext.blocks if b.kind == BlockKind.FIGURE)
    asset_anchor = next(a for a in figure.anchors if isinstance(a, AssetAnchor))
    asset = next(a for a in ext.assets if a.id == asset_anchor.asset)
    assert (asset.width, asset.height) == (40, 30) and asset.source.part == "word/document.xml"
    assert figure.anchors[0].segment == 2


def test_unsupported_textbox_is_accounted_once(docx_path):
    ext = extract_docx(docx_path)
    failed = [b for b in ext.blocks if b.status == BlockStatus.FAILED]
    assert [b.text for b in failed] == ["Callout text"]  # the VML fallback is not read twice
    entry = next(e for e in ext.ledger if e.block == failed[0].id)
    assert entry.unit == "docx_unsupported" and entry.disposition == "failed" and entry.chars == 11
    assert any("textbox" in w for w in ext.warnings)


def test_ledger_and_anchors(docx_path):
    ext = extract_docx(docx_path)
    assert all(e.disposition is not None and e.block for e in ext.ledger)
    assert [e.item for e in ext.ledger] == [f"i-d{i:05d}" for i in range(1, len(ext.ledger) + 1)]
    anchor = _text_blocks(ext)[0].anchors[0]
    assert isinstance(anchor, DocxAnchor) and anchor.part == "word/document.xml"
    assert anchor.node_path == "/w:body/w:p[1]"
    assert [b.id for b in ext.blocks] == [f"b-d{i:05d}" for i in range(1, len(ext.blocks) + 1)]


def test_deleted_paragraph_mark_merges_paragraphs(tmp_path):
    doc = Document()
    body = doc.element.body
    _add(body, (
        f'<w:p {W}><w:pPr><w:rPr><w:del w:id="9" w:author="a"/></w:rPr></w:pPr>'
        '<w:r><w:t xml:space="preserve">First half, </w:t></w:r></w:p>'))
    _add(body, (f'<w:p {W}><w:r><w:t>second half.</w:t></w:r></w:p>'))
    path = tmp_path / "merge.docx"
    doc.save(path)
    ext = extract_docx(path)
    assert [b.text for b in _text_blocks(ext)] == ["First half, second half."]
    merged = [e for e in ext.ledger if e.disposition == "merged"]
    assert len(merged) == 1 and merged[0].block == _text_blocks(ext)[0].id


def test_list_numbering_is_rendered(tmp_path):
    doc = Document()
    for text in ("alpha", "beta"):
        doc.add_paragraph(text, style="List Number")
    path = tmp_path / "list.docx"
    doc.save(path)
    blocks = _text_blocks(extract_docx(path))
    assert [b.text for b in blocks] == ["1. alpha", "2. beta"]
    numbering = blocks[1].observations[0].style.numbering
    assert numbering.level == 0 and numbering.text == "2."


def test_extraction_is_deterministic(docx_path):
    a, b = extract_docx(docx_path), extract_docx(docx_path)
    assert [x.model_dump() for x in a.blocks] == [x.model_dump() for x in b.blocks] and a.ledger == b.ledger
