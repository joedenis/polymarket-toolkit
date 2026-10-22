"""Were my entry prices good forecasts — and where was my edge?

Treat every BUY as a claim: "this outcome is worth more than p". If the market
was calibrated and I had no edge, outcomes bought at ~p should win ~p of the
time. Edge per share is (outcome - p); its share-weighted mean is my
average edge, and positive edge concentrated in a price bucket says where
my judgement beat the market.

Only BUY fills are used. A SELL is usually an exit, not an independent
forecast, and treating it as "bought the other side at 1 - p" double-counts.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

DEFAULT_BINS = (0.0, 0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9, 1.0)


def entries(fills: pd.DataFrame, resolutions: dict[str, float]) -> pd.DataFrame:
    """BUY fills on resolved assets, with column y = payout (0, 0.5 or 1)
    and edge = y - price. Unresolved assets are dropped."""
    if fills.empty:
        return fills.assign(y=pd.Series(dtype=float), edge=pd.Series(dtype=float))
    df = fills[(fills["side"] == "BUY") & fills["asset_id"].isin(resolutions)].copy()
    df["y"] = df["asset_id"].map(resolutions).astype(float)
    df["edge"] = df["y"] - df["price"]
    return df.reset_index(drop=True)


def brier_score(p, y) -> float:
    """Mean squared error of probability forecasts p against outcomes y."""
    p, y = np.asarray(p, dtype=float), np.asarray(y, dtype=float)
    if p.shape != y.shape or p.size == 0:
        raise ValueError("p and y must be non-empty and the same shape")
    return float(np.mean((p - y) ** 2))


def calibration_table(e: pd.DataFrame, bins=DEFAULT_BINS) -> pd.DataFrame:
    """Bucket entries by price. One row per non-empty bucket, columns:

      bucket      the pd.Interval (left-closed: [0.1, 0.2) ; last bin closed)
      n           number of fills
      shares      total shares
      avg_price   share-weighted mean entry price
      win_rate    share-weighted mean of y
      edge        win_rate - avg_price
      se          sqrt(win_rate * (1 - win_rate) / n)   (rough; fills in the
                  same market are not independent — say so if asked)
    """
    if e.empty:
        return pd.DataFrame(columns=["bucket", "n", "shares", "avg_price", "win_rate", "edge", "se"])
    e = e.copy()
    e["bucket"] = pd.cut(e["price"], bins=list(bins), right=False, include_lowest=True)
    # price == 1.0 falls outside right-open bins; fold it into the last bucket
    e.loc[e["price"] >= bins[-1], "bucket"] = e["bucket"].cat.categories[-1]
    e["w_price"] = e["price"] * e["shares"]
    e["w_y"] = e["y"] * e["shares"]
    g = e.groupby("bucket", observed=True)
    t = pd.DataFrame(
        {
            "n": g.size(),
            "shares": g["shares"].sum(),
            "avg_price": g["w_price"].sum() / g["shares"].sum(),
            "win_rate": g["w_y"].sum() / g["shares"].sum(),
        }
    )
    t["edge"] = t["win_rate"] - t["avg_price"]
    t["se"] = np.sqrt(t["win_rate"] * (1 - t["win_rate"]) / t["n"])
    return t.reset_index()


def edge_summary(e: pd.DataFrame) -> dict:
    if e.empty:
        return {"fills": 0}
    w = e["shares"]
    return {
        "fills": int(len(e)),
        "markets": int(e["condition_id"].nunique()) if "condition_id" in e else None,
        "avg_edge_per_share": float((e["edge"] * w).sum() / w.sum()),
        "edge_dollars_if_held": float((e["edge"] * w).sum()),
        "brier_of_entry_prices": brier_score(e["price"], e["y"]),
    }
