"""Which documents' heading scores count (Q79)."""

import json

import pymupdf

from parserx.eval.outline import has_outline

OUTLINE = "# Report\n\n## 1 Scope\n\ntext\n\n## 2 Terms\n\ntext\n"


def _doc(tmp_path, name, expected, pages=None, meta=None):
    d = tmp_path / name
    d.mkdir()
    (d / "expected.md").write_text(expected, encoding="utf-8")
    if pages:
        pdf = pymupdf.open()
        for _ in range(pages):
            pdf.new_page()
        pdf.save(d / "input.pdf")
    if meta is not None:
        (d / "meta.json").write_text(json.dumps(meta), encoding="utf-8")
    return d


def test_a_document_with_an_outline_counts(tmp_path):
    assert has_outline(_doc(tmp_path, "report", OUTLINE, pages=5))
    assert has_outline(_doc(tmp_path, "word", OUTLINE))  # a DOCX: no page count, the annotation decides


def test_short_or_flat_documents_do_not_count(tmp_path):
    assert not has_outline(_doc(tmp_path, "two_pages", OUTLINE, pages=2))
    assert not has_outline(_doc(tmp_path, "flat", "## a\n\n## b\n\n## c\n", pages=9))  # one level
    assert not has_outline(_doc(tmp_path, "few", "# a\n\n## b\n", pages=9))  # two headings


def test_meta_json_decides_when_it_says(tmp_path):
    assert not has_outline(_doc(tmp_path, "receipt", OUTLINE, pages=3, meta={"outline": False}))
    assert has_outline(_doc(tmp_path, "letter", "# a\n", pages=1, meta={"outline": True}))
