# polymarket-toolkit

Market-data tooling for Polymarket's central limit order book: a WebSocket tape
recorder that maintains a local L2 book per outcome token, Parquet storage for
replay, and an analysis of my own trading history (P&L and calibration of entry
prices against resolutions).

I traded political, current-affairs and Fed markets on Polymarket by hand from
2024 to 2026, mostly taking prices. I built this to see what the order book
actually looks like tick by tick, and to measure where my edge came from.

Built against Polymarket's post-April-2026 stack: CLOB V2, Data API v2 (cursor
pagination), and the market WebSocket channel.

## What's here

See [ARCHITECTURE.md](ARCHITECTURE.md) for the data flow and how to extend it.

| Module | What it does |
| --- | --- |
| `pmkit.book` | L2 order book: snapshot + delta application, best bid/ask, microprice, sweep cost for a marketable order, and a YES book folded with its NO complement |
| `pmkit.recorder` | Async WebSocket client: subscribe, application-level heartbeat, reconnect with jittered backoff, resync on snapshot |
| `pmkit.storage` | Buffered Parquet sink partitioned by date, and a loader for replay |
| `pmkit.gamma` | Market discovery; decodes Gamma's JSON-in-a-string fields and V1/V2 outcome IDs |
| `pmkit.data_api` | A wallet's activity and price history, cursor-paginated |
| `pmkit.analysis.pnl` | Average-cost position ledger, settled at resolution |
| `pmkit.analysis.calibration` | Entry price vs. outcome by bucket, Brier score, edge per share |

## Quick start

```bash
pip install -e ".[dev]"
pytest

# record the book for a market (Ctrl-C to stop; writes data/tape/date=.../*.parquet)
python scripts/record.py --slug <market-slug>

# analyse a wallet's history (public data, no keys)
python scripts/my_trades.py 0x<proxy-wallet>
```

## Design notes

- **Decimal, not float, on the live path.** Prices sit on a tick grid down to
  0.0001 and are used as dict keys; floats make level lookup unreliable.
- **Deltas carry aggregate size.** A `price_change` sets the total resting at a
  level (0 deletes it); it is not an increment.
- **No deltas applied before a snapshot.** After every (re)connect a book is
  marked unsynced until its `book` message arrives; deltas in between are still
  written to the tape but not applied.
- **YES and NO are one market.** Selling NO at q is buying YES at 1 − q, so the
  tradeable YES top of book is `max(yes_bid, 1 − no_ask)` / `min(yes_ask, 1 − no_bid)`.
- **Flat tape rows.** One row per level change or trade keeps the Parquet schema
  stable and makes replay a straight pass over the file.

## Observations from live recording

- A single resting order updates both books: buying NO at 0.189 appears as
  selling YES at 0.811, same size, same instant.
- After a trade sweeps a level, Polymarket resends full book snapshots. In one
  session they arrived ~21 ms *before* the trade message, so replay must follow
  receive order, not exchange timestamps.
- With extra event types enabled, the feed carries `new_market` events for every
  market on the platform (~40% of rows in one session). The recorder now drops
  these unless they concern a subscribed asset.


## Next

- Kalshi client and a cross-venue matcher for markets listed on both
- Replay engine that rebuilds books from the tape and checks them against the
  recorded `best_bid`/`best_ask`
- Quoting simulator with inventory skew, run against recorded books