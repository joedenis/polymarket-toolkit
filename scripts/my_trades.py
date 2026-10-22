"""Analyse your own Polymarket history from your public wallet address.

    python scripts/my_trades.py 0xYourProxyWallet

Your proxy wallet is the address on your Polymarket profile page (not your
login email). No keys needed: the Data API is public.

Writes:
  reports/fills.csv          every trade
  reports/ledger.csv         one row per position, average-cost P&L
  reports/calibration.csv    entry price vs. resolution, by price bucket
  reports/summary.md         headline numbers — the cover-letter paragraph

Decide before publishing whether the CSVs go in the public repo. The wallet
is public on-chain anyway, but you choose what to put on GitHub.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from pmkit.analysis.calibration import calibration_table, edge_summary, entries
from pmkit.analysis.pnl import fills_to_frame, position_ledger, summary
from pmkit.data_api import DataClient
from pmkit.gamma import GammaClient


def resolutions_for(condition_ids, gamma: GammaClient, cache: Path) -> dict[str, float]:
    """{asset_id: payout} for every closed market among condition_ids.
    Cached to disk: resolved markets do not change."""
    known: dict[str, dict] = json.loads(cache.read_text(encoding="utf-8")) if cache.exists() else {}
    for cid in condition_ids:
        if cid in known and known[cid].get("closed"):
            continue
        m = gamma.market_by_condition(cid)
        if m is None:
            continue
        known[cid] = {
            "closed": m.closed,
            "payouts": {o.asset_id: float(o.price) for o in m.outcomes if o.price is not None},
        }
    cache.write_text(json.dumps(known, indent=1), encoding="utf-8")
    out: dict[str, float] = {}
    for v in known.values():
        if v["closed"]:
            out.update(v["payouts"])
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("wallet")
    ap.add_argument("--out", default=str(Path(__file__).resolve().parents[1] / "reports"))
    args = ap.parse_args()
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)

    fills = DataClient().fills(args.wallet)
    print(f"{len(fills)} fills")
    df = fills_to_frame(fills)
    df.to_csv(out / "fills.csv", index=False)

    res = resolutions_for(df["condition_id"].unique(), GammaClient(), out / "resolutions_cache.json")
    ledger = position_ledger(fills, res)
    ledger.to_csv(out / "ledger.csv", index=False)

    e = entries(df, res)
    cal = calibration_table(e)
    cal.to_csv(out / "calibration.csv", index=False)

    s, es = summary(ledger), edge_summary(e)
    lines = [
        "# Polymarket trading summary",
        "",
        f"- Period: {df['timestamp'].min():%b %Y} – {df['timestamp'].max():%b %Y}",
        f"- Fills: {len(df)} across {s['positions']} positions ({s['resolved_positions']} resolved)",
        f"- Capital deployed (sum of buys): ${s['capital_deployed']:,.0f}",
        f"- Realized P&L: ${s['realized_pnl']:,.0f}  (return on capital {s['return_on_capital']:.1%})",
        f"- Hit rate on resolved positions: {s['hit_rate_resolved']:.0%}",
        f"- Average edge per share at entry (outcome − price): {es.get('avg_edge_per_share', float('nan')):+.3f}",
        f"- Brier score of my entry prices: {es.get('brier_of_entry_prices', float('nan')):.3f}",
        "",
        "## Calibration by entry price",
        "",
        ("```\n" + cal.to_string(index=False, float_format=lambda x: f"{x:.3f}") + "\n```") if not cal.empty else "_no resolved entries_",
    ]
    (out / "summary.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines))


if __name__ == "__main__":
    main()
