from decimal import Decimal as D

import pytest

from pmkit.book import BookError, OrderBook, synthetic_top


def make_book():
    b = OrderBook("YES")
    b.apply_snapshot(
        bids=[(D("0.40"), D("100")), (D("0.39"), D("300")), (D("0.38"), D("0"))],
        asks=[(D("0.42"), D("50")), (D("0.43"), D("200"))],
    )
    return b


def test_snapshot_drops_zero_levels_and_replaces():
    b = make_book()
    assert D("0.38") not in b.bids
    b.apply_snapshot([(D("0.10"), D("1"))], [])
    assert b.bids == {D("0.10"): D("1")} and b.asks == {}


def test_top_of_book():
    b = make_book()
    assert b.best_bid() == (D("0.40"), D("100"))
    assert b.best_ask() == (D("0.42"), D("50"))
    assert b.mid() == D("0.41")
    assert b.spread() == D("0.02")


def test_empty_sides_return_none():
    b = OrderBook("X")
    assert b.best_bid() is None and b.best_ask() is None
    assert b.mid() is None and b.microprice() is None


def test_change_sets_aggregate_not_increment():
    b = make_book()
    b.apply_change("BUY", D("0.40"), D("150"))
    assert b.bids[D("0.40")] == D("150")


def test_change_zero_removes_level_and_new_best_emerges():
    b = make_book()
    b.apply_change("BUY", D("0.40"), D("0"))
    assert b.best_bid() == (D("0.39"), D("300"))
    b.apply_change("SELL", D("0.41"), D("10"))  # improves the ask
    assert b.best_ask() == (D("0.41"), D("10"))


def test_change_zero_on_missing_level_is_harmless():
    b = make_book()
    b.apply_change("SELL", D("0.99"), D("0"))
    assert D("0.99") not in b.asks


def test_bad_side_and_negative_size_raise():
    b = make_book()
    with pytest.raises(BookError):
        b.apply_change("BID", D("0.4"), D("1"))
    with pytest.raises(BookError):
        b.apply_change("BUY", D("0.4"), D("-1"))


def test_microprice_leans_to_thin_side():
    b = make_book()
    # bid 0.40 x100, ask 0.42 x50: ask is thinner -> micro above mid
    assert b.microprice() == (D("0.40") * 50 + D("0.42") * 100) / 150
    assert b.microprice() > b.mid()


def test_sweep_walks_levels():
    b = make_book()
    filled, avg = b.sweep("BUY", D("100"))
    assert filled == D("100")
    assert avg == (D("50") * D("0.42") + D("50") * D("0.43")) / 100


def test_sweep_partial_when_depth_runs_out():
    b = make_book()
    filled, avg = b.sweep("SELL", D("1000"))
    assert filled == D("400")
    assert avg == (D("100") * D("0.40") + D("300") * D("0.39")) / 400


def test_sweep_empty_side():
    assert OrderBook("X").sweep("BUY", D("5")) == (D(0), D(0))


def test_synthetic_top_folds_in_complement():
    yes, no = OrderBook("Y"), OrderBook("N")
    yes.apply_snapshot([(D("0.40"), D("1"))], [(D("0.45"), D("1"))])
    no.apply_snapshot([(D("0.57"), D("1"))], [(D("0.58"), D("1"))])
    # NO bid 0.57 -> YES ask 0.43 (better than 0.45); NO ask 0.58 -> YES bid 0.42
    assert synthetic_top(yes, no) == (D("0.42"), D("0.43"))


def test_synthetic_top_with_one_sided_books():
    yes, no = OrderBook("Y"), OrderBook("N")
    no.apply_snapshot([(D("0.70"), D("1"))], [])
    assert synthetic_top(yes, no) == (None, D("0.30"))
