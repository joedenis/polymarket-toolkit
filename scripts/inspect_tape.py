"""Quick look at what the recorder wrote to data/tape."""

from pathlib import Path

import pandas as pd

from pmkit.storage import load_tape

pd.set_option("display.width", 200)
pd.set_option("display.max_columns", 20)

TAPE = Path(__file__).resolve().parents[1] / "data" / "tape"

df = load_tape(TAPE)
if df.empty:
    raise SystemExit(f"no tape found under {TAPE}")

print(f"{len(df)} rows, {df['recv_ts'].min()} → {df['recv_ts'].max()}\n")

print("== rows by event type")
print(df["event_type"].value_counts(), "\n")

print("== trades")
trades = df[df["event_type"] == "last_trade_price"]
print(trades[["recv_ts", "asset_id", "side", "price", "size"]] if len(trades) else "none", "\n")

print("== last 10 price changes")
pc = df[df["event_type"] == "price_change"]
print(pc[["recv_ts", "side", "price", "size", "best_bid", "best_ask"]].tail(10))

print(df[df["event_type"] == "book"].groupby(["recv_ts", "asset_id"]).size())