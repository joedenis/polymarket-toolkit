# Architecture

How data moves through `pmkit`, what each module is for, and how to extend it.

## Data flow

```
                      Polymarket
   ┌──────────────┬──────────────────┬──────────────────────────┐
   │ Gamma REST   │ Data API v2 REST │ Market WebSocket         │
   │ market info  │ wallet activity, │ book / price_change /    │
   │              │ price history    │ last_trade_price ...     │
   └──────┬───────┴────────┬─────────┴────────────┬─────────────┘
          │                │                      │
     gamma.py         data_api.py            recorder.py
          │  (both use http.py:               │  handle_message()
          │   GET + retry/backoff)             │    └─ _handle_event()
          ▼                ▼                      ▼
     MarketMeta        Fill, prices        OrderBook (book.py)   TapeRow
     Outcome                                 one per asset        │
          │                │                      │               ▼
          │                ▼                      │          storage.py
          │        analysis/pnl.py                │          ParquetSink
          └──────► analysis/calibration.py        │          data/tape/date=.../*.parquet
                           │                      │               │
                           ▼                      ▼               ▼
                  scripts/my_trades.py     scripts/record.py   load_tape() → pandas
                  reports/summary.md       live top-of-book      (replay, research)
```

Rule of the layering: a module only imports from modules below it. `book.py`
knows nothing about WebSockets; `recorder.py` knows nothing about Parquet (it
writes to any object with `write`/`flush`). That is what lets the same
`OrderBook` serve live data, tape replay, and a future simulator.

## Modules

### `config.py`
Base URLs and the heartbeat interval. All overridable by environment variable
(`PM_GAMMA_URL`, …) so tests or a proxy can redirect traffic.

### `models.py`
Plain dataclasses shared everywhere:
- `Outcome` — label ("Yes"/"No"), `asset_id`, current price.
- `MarketMeta` — one market: question, slug, `condition_id`, outcomes, status,
  tick size. `resolved_winner` finds the outcome priced at 1 after close.
- `Fill` — one of your trades.
- `TapeRow` — one flat record of something seen on the WebSocket.

**IDs you'll meet:** a *market* has a `condition_id`; each of its outcomes has
an `asset_id` (called `token_id` on V1 markets, `position_id` on V2). Order
books, prices and the WebSocket all work per **asset_id**, so one binary
market = two books.

### `http.py`
`HttpClient.get_json(path, params)`. Retries 429 and 5xx with exponential
backoff (0.5 s, 1 s, 2 s, …), drops `None` params, lower-cases booleans. The
`sleep` and `transport` arguments exist so tests can run without waiting or
touching the network (see `tests/test_gamma_and_http.py`).

### `gamma.py` — market discovery
`GammaClient.market_by_slug`, `market_by_condition`, `iter_markets`.
`parse_market` handles Gamma's quirk of returning lists as JSON-encoded
strings (`"[\"Yes\", \"No\"]"`) and picks `clobTokenIds` or `positionIds` by
the market's `version`.

### `data_api.py` — wallet history and prices
`DataClient.iter_activity` (all pages), `fills` (trades as `Fill`, oldest
first), `price_history`. Data API v2 pages with a `next_cursor`; filters must
be re-sent with each page because the cursor doesn't carry them.

### `book.py` — the order book
`OrderBook` holds `bids` and `asks` as `{price: size}` dicts of `Decimal`.
- `apply_snapshot` replaces everything; `apply_change` sets one level
  (size 0 deletes). Sizes in updates are **totals at that price**, not
  increments.
- Reads: `best_bid`, `best_ask`, `mid`, `spread`, `microprice`, `levels`,
  `sweep` (average price to take N shares).
- `synthetic_top(yes, no)` combines a YES book with its NO book: selling NO at
  q is buying YES at 1 − q.

### `recorder.py` — live market data
`Recorder(asset_ids, sink).run(stop)`:
1. connect to the market WebSocket, send the subscription frame
2. start a heartbeat task sending `"PING"` every 10 s
3. for each frame: `handle_message` → `_handle_event` per event, which updates
   `self.books` and returns `TapeRow`s → `sink.write(rows)`
4. on disconnect: back off (1 s, 2 s, 4 s … capped, with jitter), reconnect,
   resubscribe. `self.synced` is cleared on every connect, so deltas are not
   applied to a book until its fresh `book` snapshot arrives.

`handle_message` and `_handle_event` are synchronous and pure apart from book
updates, so they're tested by feeding JSON strings in — no network needed.

### `storage.py`
`ParquetSink` buffers rows, flushes every 5,000 rows or 30 s into
`<root>/date=YYYY-MM-DD/part-<ms>.parquet`. `load_tape(root)` reads it all back
sorted by receive time. `MemorySink` is the in-memory stand-in for tests.

### `analysis/pnl.py`
`position_ledger(fills, resolutions, marks)` — average-cost P&L per asset.
Buys add to cost; sells realize `(price − avg_cost) × shares`; at resolution
the remainder settles at the payout (1, 0, or 0.5). `summary()` gives headline
numbers.

### `analysis/calibration.py`
`entries()` keeps your BUYs on resolved markets and adds `y` (payout) and
`edge = y − price`. `calibration_table()` buckets by entry price;
`brier_score()` and `edge_summary()` score how good your prices were.

### `scripts/`
- `record.py --slug <slug>` — run the recorder, print top of book every 5 s.
- `my_trades.py <wallet>` — fills → ledger → calibration → `reports/summary.md`.
- `capture_fixtures.py` — save real API responses to compare with the test
  fixtures.

## Tests

`pytest` runs everything offline. HTTP is faked with `httpx.MockTransport`;
the WebSocket loop is tested against a real local server
(`websockets.serve` on 127.0.0.1) that drops the first connection to force a
reconnect. Fixtures in `tests/fixtures/` follow the documented payload shapes.

## Extending it

**A new read endpoint** — add a method to the right client, call
`self.http.get_json`, parse into a model. E.g. a CLOB client:

```python
class ClobClient:
    def __init__(self, http=None):
        self.http = http or HttpClient(CLOB_URL)

    def book(self, asset_id: str) -> OrderBook:
        raw = self.http.get_json("/book", {"token_id": asset_id})
        b = OrderBook(asset_id)
        b.apply_snapshot(
            [(Decimal(l["price"]), Decimal(l["size"])) for l in raw["bids"]],
            [(Decimal(l["price"]), Decimal(l["size"])) for l in raw["asks"]],
            hash=raw.get("hash"),
        )
        return b
```

Add a fixture of the real response and a test with `MockTransport`.

**React to live data** — subclass `Recorder` or pass a sink that does more
than store: anything with `write(rows)` / `flush()` / `close()` works. A
strategy can read `recorder.books[asset_id]` on each write.

**Replay** — `load_tape()`, then feed rows back into fresh `OrderBook`s
(`book` rows → snapshot, `price_change` rows → `apply_change`) and compare
your rebuilt best bid/ask against the `best_bid`/`best_ask` columns.

**Trading** — use Polymarket's official SDK (`pip install polymarket-client`)
for order signing and auth; don't hand-roll it. Wrap it behind your own small
interface (`place_order`, `cancel`, `open_orders`) so strategies never import
the SDK directly.
