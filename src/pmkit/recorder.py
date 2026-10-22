"""Market WebSocket recorder.

Connects to the public market channel, keeps a local OrderBook per asset,
and writes every event to a sink as flat TapeRows.

Protocol (docs.polymarket.com, Oct 2026):
  * subscribe:  {"assets_ids": [...], "type": "market"}
  * heartbeat:  send text "PING" every 10 s; server answers "PONG"
  * messages:   a JSON object, or a JSON array of objects (the initial book
                snapshots usually arrive as an array). Each has "event_type":
                book | price_change | last_trade_price | tick_size_change
                (+ best_bid_ask | new_market | market_resolved with
                custom_feature_enabled). Timestamps are epoch-ms strings.
"""

from __future__ import annotations

import asyncio
import json
import logging
import random
from collections.abc import Callable
from datetime import datetime, timezone
from decimal import Decimal

import websockets

from .book import OrderBook
from .config import MARKET_WS_URL, WS_PING_INTERVAL_S
from .models import TapeRow

log = logging.getLogger(__name__)


def ms_to_dt(value) -> datetime | None:
    if value in (None, ""):
        return None
    return datetime.fromtimestamp(int(value) / 1000, tz=timezone.utc)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Recorder:
    def __init__(
        self,
        asset_ids: list[str],
        sink,
        *,
        url: str = MARKET_WS_URL,
        ping_interval: float = WS_PING_INTERVAL_S,
        max_backoff: float = 30.0,
        custom_features: bool = True,
        clock: Callable[[], datetime] = _utcnow,
    ) -> None:
        self.asset_ids = list(asset_ids)
        self.sink = sink
        self.url = url
        self.ping_interval = ping_interval
        self.max_backoff = max_backoff
        self.custom_features = custom_features
        self.clock = clock
        self.books: dict[str, OrderBook] = {a: OrderBook(a) for a in self.asset_ids}
        # An asset is "synced" once a book snapshot has arrived on the current
        # connection. Deltas before that are recorded but not applied.
        self.synced: set[str] = set()
        self.connections = 0

    def subscribe_frame(self) -> str:
        frame = {"assets_ids": self.asset_ids, "type": "market"}
        if self.custom_features:
            frame["custom_feature_enabled"] = True
        return json.dumps(frame)

    # ---- message handling (pure, synchronous, unit-testable) --------------

    def handle_message(self, raw: str) -> list[TapeRow]:
        """Parse one WebSocket text frame, update books, return TapeRows.

        Rules:
          * "PONG" (and any other non-JSON heartbeat text) -> no rows
          * a JSON array -> handle each element
          * book            -> snapshot the asset's book, mark it synced,
                               one row per level (side BUY/SELL)
          * price_change    -> for each entry in price_changes: apply to that
                               asset's book IF synced, one row per entry,
                               carrying best_bid/best_ask from the message
          * last_trade_price, tick_size_change, others -> one row, with the
                               leftover fields in `extra`
          * events for assets we did not subscribe to are ignored
        """
        text = raw.strip()
        if not text or text.upper() == "PONG":
            return []
        try:
            msg = json.loads(text)
        except json.JSONDecodeError:
            log.debug("non-JSON frame ignored: %r", text[:80])
            return []
        rows: list[TapeRow] = []
        for event in msg if isinstance(msg, list) else [msg]:
            rows.extend(self._handle_event(event))
        return rows

    def _handle_event(self, ev: dict) -> list[TapeRow]:


        # TODO(you): implement — see IMPLEMENTING.md and the rules in handle_message().
        #
        # Outline:
        #   recv = self.clock()                         # when WE received it
        #   etype = ev.get("event_type"); market = ev.get("market", "")
        #   exch = ms_to_dt(ev.get("timestamp"))        # exchange time, epoch-ms string
        #
        #   if etype == "book":           snapshot self.books[asset], add to self.synced,
        #                                 one TapeRow per level (side "BUY" for bids, "SELL" for asks)
        #   elif etype == "price_change": for each entry in ev["price_changes"]:
        #                                 apply to the book ONLY if the asset is synced,
        #                                 always emit a TapeRow (with best_bid / best_ask)
        #   else:                         one TapeRow; leftover fields go in `extra`
        #
        #   Ignore any asset not in self.books. Decimal for the book,
        #   the original strings for the TapeRow.

        recv = self.clock()  # when we receive
        etype = ev.get("event_type")  # this is strict.  so an error breaks quickly
        market = ev.get("market", "") # key errors can stop the recording so we must state is key does not exist.

        exch = ms_to_dt(ev.get("timestamp"))

        if etype == "book":
            asset = ev["asset_id"]
            if asset not in self.books:
                return []

            bids = [(Decimal(bid["price"]), Decimal(bid["size"])) for bid in ev.get("bids", [])]
            asks = [(Decimal(ask["price"]), Decimal(ask["size"])) for ask in ev.get("asks", [])]

            book = self.books[asset]
            book.apply_snapshot(bids, asks, ts=exch, hash=ev.get("hash"))

            self.synced.add(asset)

            ## creating the TapeRow

            rows = []
            for p, s in bids:
                rows.append(TapeRow(recv_ts=recv, exch_ts=exch, event_type=etype, market=market, asset_id=asset, side="BUY", price=str(p), size=str(s)))

            for p, s in asks:
                rows.append(TapeRow(recv_ts=recv, exch_ts=exch, event_type=etype, market=market, asset_id=asset, side ="SELL", price=str(p), size=str(s) ))

            return rows

        elif etype == "price_change":
            rows = []
            for ch in ev.get("price_changes", []):
                asset = ch["asset_id"]
                if asset not in self.books:
                    continue
                if asset in self.synced:
                    self.books[asset].apply_change(ch["side"], Decimal(ch["price"]), Decimal(ch["size"]), exch)

                rows.append(TapeRow(recv_ts=recv, exch_ts=exch, event_type=etype, market=market,
                                    asset_id=asset, side=ch["side"], price=ch["price"], size=ch["size"],
                                    best_bid=ch.get("best_bid"), best_ask=ch.get("best_ask")))

            return rows

        else:
            asset = ev.get("asset_id", "") # lenient some events like new_market dont have one

            ids = ev.get("assets_ids")
            if isinstance(ids, list):
                if not any(a in self.books for a in ids):  # True if at least one id is one of ours
                    return []

            if asset and asset not in self.books: return [] # case of an asset we dont track

            known = {"event_type", "market", "asset_id", "timestamp",
                     "side", "price", "size", "best_bid", "best_ask"}
            extra = {k: v for k, v in ev.items() if k not in known}

            row = TapeRow(recv, exch, etype, market, asset,
                    side=ev.get("side", None), price= ev.get("price", None),
                    size=ev.get("size", None), best_bid=ev.get("best_bid", None),
                    best_ask=ev.get("best_ask", None), extra=extra
                    )

            return [row]

    # ---- connection loop --------------------------------------------------

    async def run(self, stop: asyncio.Event | None = None) -> None:
        """Connect, subscribe, read forever; reconnect on any disconnect.

        Requirements:
          * send subscribe_frame() on every (re)connect
          * run a heartbeat that sends "PING" every self.ping_interval
          * on (re)connect, clear self.synced — books are stale until the
            next snapshot
          * reconnect with exponential backoff + jitter, capped at
            self.max_backoff; reset the backoff after a connection that
            delivered at least one message
          * return cleanly (and flush the sink) when `stop` is set
        """
        stop = stop or asyncio.Event()
        backoff = 1.0
        while not stop.is_set():
            got_message = False
            try:
                async with websockets.connect(self.url, ping_interval=None) as ws:
                    self.connections += 1
                    self.synced.clear()
                    await ws.send(self.subscribe_frame())
                    log.info("connected (%d), subscribed %d assets", self.connections, len(self.asset_ids))
                    hb = asyncio.create_task(self._heartbeat(ws))
                    stopper = asyncio.create_task(stop.wait())
                    try:
                        while True:
                            recv = asyncio.create_task(ws.recv())
                            done, _ = await asyncio.wait(
                                {recv, stopper}, return_when=asyncio.FIRST_COMPLETED
                            )
                            if stopper in done:
                                recv.cancel()
                                return
                            raw = recv.result()
                            got_message = True
                            if isinstance(raw, bytes):
                                raw = raw.decode()
                            rows = self.handle_message(raw)
                            if rows:
                                self.sink.write(rows)
                    finally:
                        hb.cancel()
                        stopper.cancel()
            except (websockets.ConnectionClosed, OSError) as exc:
                log.warning("disconnected: %s", exc)
            finally:
                if stop.is_set():
                    self.sink.flush()
            if stop.is_set():
                break
            if got_message:
                backoff = 1.0
            delay = min(self.max_backoff, backoff) * (0.5 + random.random() / 2)
            log.info("reconnecting in %.1fs", delay)
            try:
                await asyncio.wait_for(stop.wait(), timeout=delay)
            except asyncio.TimeoutError:
                pass
            backoff = min(self.max_backoff, backoff * 2)
        self.sink.flush()

    async def _heartbeat(self, ws) -> None:
        while True:
            await asyncio.sleep(self.ping_interval)
            await ws.send("PING")
