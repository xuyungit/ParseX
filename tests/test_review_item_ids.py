"""Review items have an id each (Q164 item 2): items on one block that quote nothing — four table totals that fail —
are told apart by their detail, and closing one leaves the others open."""

from parserx.ir.state import ClosedItem
from parserx.tools.envelope import Unresolved, UnresolvedKind
from parserx.tools.views import is_closed


def _item(detail, quotes=()):
    return Unresolved(target="b-1", kind=UnresolvedKind.TABLE_ARITHMETIC, detail=detail,
                      quotes=[{"doc_text": q} for q in quotes])


def test_items_quoting_nothing_are_told_apart_by_their_detail_and_closed_one_by_one():
    row15, row35 = _item("row 15: col 5 has 50"), _item("row 35: col 6 has 91")
    assert row15.id != row35.id
    closed = [ClosedItem(target="b-1", kind="table_arithmetic", detail=row15.detail, reason="r", actor="a",
                         image="e-1")]
    assert is_closed(row15, closed) and not is_closed(row35, closed)


def test_an_item_with_quotes_is_named_by_them_and_an_old_closing_without_detail_still_holds():
    assert _item("a", ["x"]).id == _item("b", ["x"]).id  # what it quotes names it, as before
    old = [ClosedItem(target="b-1", kind="table_arithmetic", reason="r", actor="a", image="e-1")]
    assert is_closed(_item("row 15"), old) and is_closed(_item("row 35"), old)
