"""Per-position P&L from a wallet's own fills, average-cost method.

Inputs
  fills        list[Fill], any order (sorted here by timestamp)
  resolutions  {asset_id: payout} for resolved outcomes — 1.0 for the winner,
               0.0 for the loser, 0.5 for a 50/50 resolution
  marks        optional {asset_id: price} to value still-open positions

Average cost: a BUY adds shares and cost; a SELL realizes
(sell_price - avg_cost) * shares and leaves avg_cost unchanged. On
resolution, remaining shares realize (payout - avg_cost) * shares.

Fees are ignored: Polymarket's political/macro markets were fee-free for most
of 2023-26. If you traded fee-enabled markets, extend Fill with a fee field.
"""

from __future__ import annotations

from collections.abc import Iterable

import pandas as pd

from ..models import Fill

LEDGER_COLUMNS = [
    "asset_id",
    "title",
    "outcome",
    "n_fills",
    "shares_bought",
    "shares_sold",
    "open_shares",
    "avg_cost",
    "cost_bought",
    "realized_pnl",
    "unrealized_pnl",
    "unmatched_sell_shares",
    "status",
]


def fills_to_frame(fills: Iterable[Fill]) -> pd.DataFrame:
    rows = [
        {
            "timestamp": f.timestamp,
            "asset_id": f.asset_id,
            "condition_id": f.condition_id,
            "side": f.side,
            "shares": float(f.shares),
            "price": float(f.price),
            "usdc": float(f.usdc),
            "title": f.title,
            "outcome": f.outcome,
        }
        for f in fills
    ]
    df = pd.DataFrame(rows)
    if df.empty:
        return df
    return df.sort_values("timestamp", kind="stable").reset_index(drop=True)


def position_ledger(
    fills: Iterable[Fill],
    resolutions: dict[str, float],
    marks: dict[str, float] | None = None,
) -> pd.DataFrame:
    """One row per asset_id with the columns in LEDGER_COLUMNS.

    status:
      RESOLVED  asset is in `resolutions` (open shares settled at the payout)
      CLOSED    not resolved, open_shares == 0
      OPEN      not resolved, open_shares > 0 (unrealized_pnl uses marks; 0 if
                no mark is given)

    A SELL for more shares than are held (e.g. shares that arrived via a split
    or transfer, which are not in the trade feed) closes what is held; the
    excess goes in unmatched_sell_shares and realizes nothing.
    """
    marks = marks or {}
    df = fills_to_frame(fills)
    if df.empty:
        return pd.DataFrame(columns=LEDGER_COLUMNS)

    out = []
    for asset_id, g in df.groupby("asset_id", sort=False):
        held = cost = realized = bought = sold = cost_bought = unmatched = 0.0
        for side, sh, px in zip(g["side"], g["shares"], g["price"]):
            if side == "BUY":
                held += sh
                cost += sh * px
                bought += sh
                cost_bought += sh * px
            elif side == "SELL":
                sold += sh
                matched = min(sh, held)
                unmatched += sh - matched
                if matched > 0:
                    avg = cost / held
                    realized += (px - avg) * matched
                    cost -= avg * matched
                    held -= matched
            else:
                raise ValueError(f"unknown side {side!r}")
        avg_cost = cost / held if held > 1e-12 else 0.0
        unrealized = 0.0
        if asset_id in resolutions:
            realized += (resolutions[asset_id] - avg_cost) * held
            status = "RESOLVED"
            open_shares = 0.0
        else:
            open_shares = held if held > 1e-12 else 0.0
            if open_shares and asset_id in marks:
                unrealized = (marks[asset_id] - avg_cost) * open_shares
            status = "OPEN" if open_shares else "CLOSED"
        out.append(
            {
                "asset_id": asset_id,
                "title": g["title"].iloc[0],
                "outcome": g["outcome"].iloc[0],
                "n_fills": len(g),
                "shares_bought": bought,
                "shares_sold": sold,
                "open_shares": open_shares,
                "avg_cost": avg_cost,
                "cost_bought": cost_bought,
                "realized_pnl": realized,
                "unrealized_pnl": unrealized,
                "unmatched_sell_shares": unmatched,
                "status": status,
            }
        )
    return pd.DataFrame(out, columns=LEDGER_COLUMNS)


def summary(ledger: pd.DataFrame) -> dict:
    """Headline numbers for the README / cover letter."""
    resolved = ledger[ledger["status"] == "RESOLVED"]
    return {
        "positions": int(len(ledger)),
        "resolved_positions": int(len(resolved)),
        "capital_deployed": float(ledger["cost_bought"].sum()),
        "realized_pnl": float(ledger["realized_pnl"].sum()),
        "unrealized_pnl": float(ledger["unrealized_pnl"].sum()),
        "hit_rate_resolved": float((resolved["realized_pnl"] > 0).mean()) if len(resolved) else float("nan"),
        "return_on_capital": float(ledger["realized_pnl"].sum() / ledger["cost_bought"].sum())
        if ledger["cost_bought"].sum()
        else float("nan"),
    }
