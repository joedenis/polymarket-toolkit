"""Replace the synthetic test fixtures with real captures from the live API.

    python scripts/capture_fixtures.py --slug <an-active-market-slug> --wallet 0xYourWallet

Run this first. The fixtures shipped in tests/fixtures/ were built from the
documented payload shapes (Oct 2026); if Polymarket's live output differs,
you want to find out here, with a failing test, not in an interview.

Writes tests/fixtures/live_*.json. The shipped tests keep using the synthetic
files (their asserted numbers depend on them); add your own tests against the
live_* files, or diff the two to check the shapes match.
"""

from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path

import websockets

from pmkit.config import DATA_URL, GAMMA_URL, MARKET_WS_URL
from pmkit.gamma import parse_market
from pmkit.http import HttpClient

OUT = Path(__file__).resolve().parents[1] / "tests" / "fixtures"


def save(name: str, obj) -> None:
    (OUT / name).write_text(json.dumps(obj, indent=2) + "\n")
    print("wrote", name)


async def capture_ws(asset_ids: list[str], seconds: float) -> list:
    msgs = []
    async with websockets.connect(MARKET_WS_URL, ping_interval=None) as ws:
        await ws.send(json.dumps({"assets_ids": asset_ids, "type": "market", "custom_feature_enabled": True}))
        loop = asyncio.get_running_loop()
        end = loop.time() + seconds
        while loop.time() < end:
            try:
                raw = await asyncio.wait_for(ws.recv(), timeout=max(0.1, end - loop.time()))
            except asyncio.TimeoutError:
                break
            try:
                msgs.append(json.loads(raw))
            except json.JSONDecodeError:
                msgs.append(raw)
    return msgs


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--slug", required=True)
    ap.add_argument("--wallet")
    ap.add_argument("--seconds", type=float, default=20)
    args = ap.parse_args()

    gamma = HttpClient(GAMMA_URL)
    raw_market = gamma.get_json(f"/markets/slug/{args.slug}")
    save("live_gamma_market.json", raw_market)
    m = parse_market(raw_market)

    msgs = asyncio.run(capture_ws([o.asset_id for o in m.outcomes], args.seconds))
    save("live_ws_messages.json", msgs)
    kinds = {}
    for msg in msgs:
        for ev in msg if isinstance(msg, list) else [msg]:
            if isinstance(ev, dict):
                kinds[ev.get("event_type")] = kinds.get(ev.get("event_type"), 0) + 1
    print("event types seen:", kinds)

    if args.wallet:
        data = HttpClient(DATA_URL)
        save("live_activity_page.json", data.get_json("/v2/activity", {"user": args.wallet, "limit": 5}))


if __name__ == "__main__":
    main()
