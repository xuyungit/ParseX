"""``skim``: reading the document the way a person skims it — scroll, a page, find, the outline (2026-09-26)."""

import pymupdf
import pytest

from parserx.tools import call_tool, workspace_init
from tests.test_tools_contract import _assert_contract, _config, _context

BODY = ("SENTINEL-NATIVE body text of the section, long enough to be a paragraph of its own and to be shortened "
        "when the reader only glances at it, as a person does when skimming.")


@pytest.fixture
def ws(tmp_path):
    doc = pymupdf.open()
    page = doc.new_page(width=595, height=842)
    y = 80
    for text, size, font in [("SENTINEL-NATIVE Annual Report", 18, "hebo"), ("1 Scope", 12, "hebo"),
                             (BODY, 10, "helv"), ("1.1 Terms", 12, "hebo"), (BODY, 10, "helv")]:
        page.insert_textbox(pymupdf.Rect(72, y, 523, y + 60), text, fontsize=size, fontname=font)
        y += 70
    page = doc.new_page(width=595, height=842)
    page.insert_textbox(pymupdf.Rect(72, 80, 523, 140), "2 Methods", fontsize=12, fontname="hebo")
    page.insert_textbox(pymupdf.Rect(72, 150, 523, 210), BODY, fontsize=10, fontname="helv")
    path = tmp_path / "doc.pdf"
    doc.save(path)
    envelope, code = workspace_init(path, tmp_path / "ws", config=_config())
    assert code == 0 and envelope.ok
    return tmp_path / "ws"


def _skim(ws, **request):
    env, code = call_tool("skim", ws, request, config=_config(), context_factory=_context())
    assert code == 0, env.failures
    return _assert_contract(env, "skim")["result"]


def _texts(lines):
    return [(line["text"] or {}).get("doc_text", "") for line in lines]


def test_scroll_from_the_start_and_on_from_where_it_stopped(ws):
    first = _skim(ws, after=3)
    assert first["mode"] == "scroll" and len(first["lines"]) == 3 and first["total_blocks"] == 7
    assert _texts(first["lines"])[:2] == ["SENTINEL-NATIVE Annual Report", "1 Scope"]
    body = first["lines"][2]
    assert body["shortened"] and len(body["text"]["doc_text"]) < len(BODY)  # a glance, not the whole paragraph
    assert all(line["page"] == 1 and line["cls"] for line in first["lines"])
    on = _skim(ws, start=first["after_id"], after=10)
    assert _texts(on["lines"])[0] == "1.1 Terms" and on["after_id"] is None  # the end of the document
    back = _skim(ws, start=on["lines"][0]["id"], before=2, after=0)
    assert [line["id"] for line in back["lines"]] == [line["id"] for line in first["lines"][1:]]
    full = _skim(ws, start=body["id"], after=1, full=True)
    assert [" ".join(t.split()) for t in _texts(full["lines"])] == [BODY]  # the block's text, line breaks kept
    assert not full["lines"][0]["shortened"]


def test_a_page_and_a_search(ws):
    page2 = _skim(ws, page=2)
    assert page2["mode"] == "page" and _texts(page2["lines"])[0] == "2 Methods"
    found = _skim(ws, find="1.1  terms")  # spacing and case do not matter
    assert found["mode"] == "find" and [_texts(found["lines"])] == [["1.1 Terms"]]


def test_the_outline_shows_the_documents_conventions(ws):
    outline = _skim(ws, outline=True)
    assert outline["mode"] == "outline"
    classes = {c["id"]: c for c in outline["classes"]}
    heads = [line for line in outline["lines"] if _texts([line])[0] in ("1 Scope", "1.1 Terms", "2 Methods")]
    assert len(heads) == 3  # every heading-like line of the document, with what follows it
    assert all(line["next_text"] and "body text" in line["next_text"]["doc_text"] for line in heads)
    scope = classes[heads[0]["cls"]]
    assert scope["numbering"] == "N" and scope["count"] == 2 and scope["pages"] == "1–2"  # "1 Scope", "2 Methods"
    body = next(c for c in outline["classes"] if c["count"] == 3)  # the body text: one class, never a title
    assert body["roles"] == {"text": 3} and body["chars"] > 100 and body["examples"]
    every = _skim(ws, cls=body["id"])
    assert every["mode"] == "cls" and len(every["lines"]) == 3


def test_one_mode_at_a_time(ws):
    env, code = call_tool("skim", ws, {"outline": True, "page": 1}, config=_config(), context_factory=_context())
    assert code != 0 and not env.ok
