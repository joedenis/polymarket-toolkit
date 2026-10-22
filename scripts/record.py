"""Record the live order book tape for one or more markets.

    python scripts/record.py --slug fed-decision-in-december --out data/tape
    python scripts/record.py --asset <token_id> --asset <token_id> --out data/tape

Ctrl-C stops cleanly and flushes to Parquet. Prints a top-of-book line every
few seconds so you can see the local book tracking the exchange.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import signal

from pmkit.book import synthetic_top
from pmkit.gamma import GammaClient
from pmkit.recorder import Recorder
from pmkit.storage import ParquetSink


async def status_loop(rec: Recorder, pairs: list[tuple[str, str, str]], every: float) -> None:
    while True:
        await asyncio.sleep(every)
        for label, yes, no in pairs:
            yb, nb = rec.books[yes], rec.books[no]
            bid, ask = synthetic_top(yb, nb)
            print(
                f"{label[:48]:48}  yes {fmt(yb.best_bid())}/{fmt(yb.best_ask())}"
                f"  synthetic {bid}/{ask}  micro {yb.microprice() and round(yb.microprice(), 4)}"
                f"  upd {yb.updates}"
            )


def fmt(level):
    return "-" if level is None else f"{level[0]}x{level[1]:.0f}"


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--slug", action="append", default=[], help="Gamma market slug (repeatable)")
    ap.add_argument("--asset", action="append", default=[], help="raw asset/token id (repeatable)")
    ap.add_argument("--out", default="data/tape")
    ap.add_argument("--status-every", type=float, default=5.0)
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    assets: list[str] = list(args.asset)
    pairs: list[tuple[str, str, str]] = []
    if args.slug:
        gamma = GammaClient()
        for slug in args.slug:
            m = gamma.market_by_slug(slug)
            ids = [o.asset_id for o in m.outcomes]
            assets += ids
            if len(ids) == 2:
                pairs.append((m.question, ids[0], ids[1]))
    if not assets:
        ap.error("give at least one --slug or --asset")

    sink = ParquetSink(args.out)
    rec = Recorder(assets, sink)
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, stop.set)  # Mac / Linux
        except NotImplementedError:  # Windows
            signal.signal(sig, lambda *_: loop.call_soon_threadsafe(stop.set))

    status = asyncio.create_task(status_loop(rec, pairs, args.status_every)) if pairs else None
    try:
        await rec.run(stop)
    finally:
        if status:
            status.cancel()
        sink.close()
        print(f"tape written under {args.out}")


if __name__ == "__main__":
    asyncio.run(main())
