import asyncio
import json
from datetime import datetime, timezone
from decimal import Decimal as D

import pytest
import websockets

from pmkit.recorder import Recorder
from pmkit.storage import MemorySink

from .conftest import NO, YES, raw

T0 = datetime(2026, 10, 21, tzinfo=timezone.utc)


def rec(assets=(YES, NO), **kw):
    return Recorder(list(assets), MemorySink(), clock=lambda: T0, **kw)


def test_subscribe_frame():
    frame = json.loads(rec().subscribe_frame())
    assert frame["type"] == "market" and frame["assets_ids"] == [YES, NO]


def test_pong_and_garbage_ignored():
    r = rec()
    assert r.handle_message("PONG") == []
    assert r.handle_message("") == []
    assert r.handle_message("not json") == []


def test_book_array_snapshots_both_assets():
    r = rec()
    rows = r.handle_message(raw("ws_book_array.json"))
    assert len(rows) == 6  # 4 YES levels + 2 NO levels
    assert r.synced == {YES, NO}
    assert r.books[YES].best_bid() == (D("0.08"), D("33343.4"))
    assert r.books[YES].best_ask() == (D("0.09"), D("163939.58"))
    assert r.books[YES].hash == "0xabc123"
    assert {row.side for row in rows} == {"BUY", "SELL"}
    assert rows[0].exch_ts == datetime.fromtimestamp(1782753357.257, tz=timezone.utc)


def test_price_change_applied_after_snapshot():
    r = rec()
    r.handle_message(raw("ws_book_array.json"))
    rows = r.handle_message(raw("ws_price_change.json"))
    assert len(rows) == 2
    assert r.books[YES].best_bid() == (D("0.07"), D("1000"))
    assert r.books[YES].best_ask() == (D("0.085"), D("250"))
    assert rows[1].best_ask == "0.085"


def test_price_change_before_snapshot_recorded_not_applied():
    r = rec()
    rows = r.handle_message(raw("ws_price_change.json"))
    assert len(rows) == 2
    assert r.books[YES].asks == {}


def test_last_trade_row_keeps_extra_fields():
    r = rec()
    (row,) = r.handle_message(raw("ws_last_trade.json"))
    assert row.event_type == "last_trade_price"
    assert row.price == "0.09" and row.side == "SELL"
    assert row.extra["transaction_hash"] == "0xeeefff"


def test_unsubscribed_assets_ignored():
    r = rec(assets=[NO])
    rows = r.handle_message(raw("ws_book_array.json"))
    assert {row.asset_id for row in rows} == {NO}
    assert r.handle_message(raw("ws_last_trade.json")) == []


# ---- integration: a local WebSocket server standing in for Polymarket ----


async def test_run_reconnects_resubscribes_and_pings():
    received: list[str] = []
    connections = 0
    book = raw("ws_book_array.json")

    async def server(ws):
        nonlocal connections
        connections += 1
        received.append(await ws.recv())  # subscription frame
        await ws.send(book)
        if connections == 1:
            await ws.close()  # force a reconnect
            return
        async for msg in ws:  # second connection: collect heartbeats
            received.append(msg)

    async with websockets.serve(server, "127.0.0.1", 0) as srv:
        port = srv.sockets[0].getsockname()[1]
        r = Recorder(
            [YES, NO], MemorySink(), url=f"ws://127.0.0.1:{port}",
            ping_interval=0.05, max_backoff=0.1,
        )
        stop = asyncio.Event()
        task = asyncio.create_task(r.run(stop))
        for _ in range(100):
            await asyncio.sleep(0.02)
            if connections == 2 and received.count("PING") >= 2:
                break
        stop.set()
        await asyncio.wait_for(task, timeout=2)

    subs = [m for m in received if m.startswith("{")]
    assert len(subs) == 2, "must resubscribe on reconnect"
    assert received.count("PING") >= 2, "must send PING heartbeats"
    assert r.connections == 2
    assert len(r.sink.rows) == 12  # the snapshot recorded on both connections
    assert r.synced == {YES, NO}


def test_new_market_for_other_assets_ignored():
    r = rec()
    event = {
        "event_type": "new_market",
        "market": "0xother",
        "assets_ids": ["111", "222"],
        "question": "Some unrelated market?",
        "timestamp": "1782753357257",
    }
    assert r.handle_message(json.dumps(event)) == []


def test_new_market_for_our_asset_recorded():
    r = rec()
    event = {
        "event_type": "new_market",
        "market": "0xours",
        "assets_ids": [YES, "222"],
        "question": "A market we track?",
        "timestamp": "1782753357258",
    }

    rows = r.handle_message(json.dumps(event))
    assert len(rows) == 1
    assert rows[0].event_type == "new_market"
    assert YES in rows[0].extra["assets_ids"]  # the list survived in extra
    assert rows[0].extra["question"] == "A market we track?"
