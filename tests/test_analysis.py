from datetime import datetime, timedelta, timezone
from decimal import Decimal as D

import numpy as np
import pandas as pd
import pytest

from pmkit.analysis.calibration import brier_score, calibration_table, edge_summary, entries
from pmkit.analysis.pnl import LEDGER_COLUMNS, fills_to_frame, position_ledger, summary
from pmkit.models import Fill

T0 = datetime(2024, 1, 1, tzinfo=timezone.utc)


def f(i, asset, side, shares, price, cond="c1"):
    return Fill(
        timestamp=T0 + timedelta(minutes=i), asset_id=asset, condition_id=cond,
        side=side, shares=D(str(shares)), price=D(str(price)),
        usdc=D(str(shares)) * D(str(price)), title=f"T-{asset}", outcome="Yes",
    )


def row(ledger, asset):
    return ledger.set_index("asset_id").loc[asset]


# ---------------- P&L ----------------


def test_average_cost_partial_sell_then_resolution():
    fills = [f(0, "A", "BUY", 100, 0.40), f(1, "A", "BUY", 50, 0.46), f(2, "A", "SELL", 60, 0.50)]
    led = position_ledger(fills, resolutions={"A": 1.0})
    r = row(led, "A")
    avg = (100 * 0.40 + 50 * 0.46) / 150  # 0.42
    assert r["avg_cost"] == pytest.approx(avg)
    # 60 sold at 0.50 -> 4.80; 90 settled at 1.0 -> 52.20
    assert r["realized_pnl"] == pytest.approx(60 * (0.50 - avg) + 90 * (1 - avg))
    assert r["status"] == "RESOLVED" and r["open_shares"] == 0
    assert r["cost_bought"] == pytest.approx(63.0)


def test_losing_resolution():
    led = position_ledger([f(0, "B", "BUY", 200, 0.25)], resolutions={"B": 0.0})
    assert row(led, "B")["realized_pnl"] == pytest.approx(-50.0)


def test_open_position_marked():
    led = position_ledger([f(0, "C", "BUY", 10, 0.30)], resolutions={}, marks={"C": 0.45})
    r = row(led, "C")
    assert r["status"] == "OPEN"
    assert r["unrealized_pnl"] == pytest.approx(1.5)
    assert r["realized_pnl"] == 0


def test_round_trip_closed_without_resolution():
    led = position_ledger([f(0, "D", "BUY", 10, 0.30), f(1, "D", "SELL", 10, 0.35)], {})
    r = row(led, "D")
    assert r["status"] == "CLOSED"
    assert r["realized_pnl"] == pytest.approx(0.5)


def test_oversell_is_flagged_not_realized():
    led = position_ledger([f(0, "E", "BUY", 10, 0.50), f(1, "E", "SELL", 15, 0.60)], {})
    r = row(led, "E")
    assert r["unmatched_sell_shares"] == pytest.approx(5)
    assert r["realized_pnl"] == pytest.approx(1.0)


def test_fills_sorted_before_processing():
    # sell listed first but happened last
    fills = [f(2, "F", "SELL", 10, 0.60), f(0, "F", "BUY", 10, 0.50)]
    r = row(position_ledger(fills, {}), "F")
    assert r["realized_pnl"] == pytest.approx(1.0) and r["unmatched_sell_shares"] == 0


def test_ledger_shape_and_summary():
    led = position_ledger([f(0, "A", "BUY", 100, 0.4), f(1, "B", "BUY", 100, 0.7)], {"A": 1.0, "B": 0.0})
    assert list(led.columns) == LEDGER_COLUMNS
    s = summary(led)
    assert s["realized_pnl"] == pytest.approx(60 - 70)
    assert s["hit_rate_resolved"] == 0.5


def test_empty_ledger():
    assert list(position_ledger([], {}).columns) == LEDGER_COLUMNS


# ---------------- calibration ----------------


def test_brier():
    assert brier_score([1.0, 0.0], [1, 0]) == 0.0
    assert brier_score([0.5, 0.5], [1, 0]) == pytest.approx(0.25)
    with pytest.raises(ValueError):
        brier_score([], [])


def test_entries_keeps_resolved_buys_only():
    df = fills_to_frame(
        [f(0, "A", "BUY", 10, 0.3), f(1, "A", "SELL", 10, 0.4), f(2, "Z", "BUY", 5, 0.5)]
    )
    e = entries(df, {"A": 1.0})
    assert len(e) == 1
    assert e.loc[0, "y"] == 1.0 and e.loc[0, "edge"] == pytest.approx(0.7)


def test_calibration_table_share_weighted():
    df = fills_to_frame(
        [
            f(0, "A", "BUY", 100, 0.32, "c1"),  # wins
            f(1, "B", "BUY", 300, 0.36, "c2"),  # loses
            f(2, "C", "BUY", 10, 0.85, "c3"),  # wins
            f(3, "D", "BUY", 10, 1.00, "c4"),  # price exactly 1 -> top bucket
        ]
    )
    e = entries(df, {"A": 1, "B": 0, "C": 1, "D": 1})
    t = calibration_table(e)
    assert list(t.columns) == ["bucket", "n", "shares", "avg_price", "win_rate", "edge", "se"]
    assert len(t) == 3  # [0.3,0.4), [0.8,0.9), and the 1.0 fill folded into [0.9,1.0)
    top = t[t["bucket"].apply(lambda iv: iv.left) == 0.9].iloc[0]
    assert top["n"] == 1 and top["avg_price"] == pytest.approx(1.0)
    b30 = t[t["bucket"].apply(lambda iv: iv.left) == 0.3].iloc[0]
    assert b30["n"] == 2 and b30["shares"] == 400
    assert b30["avg_price"] == pytest.approx((100 * 0.32 + 300 * 0.36) / 400)
    assert b30["win_rate"] == pytest.approx(0.25)
    assert b30["edge"] == pytest.approx(0.25 - b30["avg_price"])
    assert b30["se"] == pytest.approx(np.sqrt(0.25 * 0.75 / 2))


def test_edge_summary():
    df = fills_to_frame([f(0, "A", "BUY", 100, 0.4), f(1, "B", "BUY", 100, 0.7, "c2")])
    s = edge_summary(entries(df, {"A": 1, "B": 0}))
    assert s["avg_edge_per_share"] == pytest.approx(((0.6) + (-0.7)) / 2)
    assert s["markets"] == 2
