"""Local L2 order book for one outcome token.

The market WebSocket sends a full `book` snapshot on subscribe (and after
trades that sweep levels), then `price_change` deltas. In a delta, `size` is
the NEW AGGREGATE size resting at that price — not an increment — and a size
of 0 removes the level. `side` BUY means the bid side, SELL the ask side.

Everything here is Decimal: ticks go down to 0.0001, and keying a dict by a
float price is a bug waiting to happen.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal

ZERO = Decimal(0)
ONE = Decimal(1)


class BookError(Exception):
    pass


@dataclass
class OrderBook:
    asset_id: str
    bids: dict[Decimal, Decimal] = field(default_factory=dict)  # price -> size
    asks: dict[Decimal, Decimal] = field(default_factory=dict)
    last_update: datetime | None = None
    hash: str | None = None
    updates: int = 0

    # ---- mutation ---------------------------------------------------------

    def apply_snapshot(
        self,
        bids: Iterable[tuple[Decimal, Decimal]],
        asks: Iterable[tuple[Decimal, Decimal]],
        ts: datetime | None = None,
        hash: str | None = None,
    ) -> None:
        """Replace the whole book. Drop any zero-size levels."""
        self.bids = {Decimal(p): Decimal(s) for p, s in bids if Decimal(s) > ZERO}
        self.asks = {Decimal(p): Decimal(s) for p, s in asks if Decimal(s) > ZERO}
        self.last_update = ts
        self.hash = hash
        self.updates += 1

    def apply_change(
        self, side: str, price: Decimal, size: Decimal, ts: datetime | None = None
    ) -> None:
        """Set the aggregate size at one level. size == 0 deletes the level.
        side is "BUY" (bids) or "SELL" (asks); anything else is a BookError."""
        if side == "BUY":
            levels = self.bids
        elif side == "SELL":
            levels = self.asks
        else:
            raise BookError(f"unknown side {side!r}")
        price, size = Decimal(price), Decimal(size)
        if size < ZERO:
            raise BookError(f"negative size {size} at {price}")
        if size == ZERO:
            levels.pop(price, None)
        else:
            levels[price] = size
        self.last_update = ts
        self.updates += 1

    # ---- top of book ------------------------------------------------------

    def best_bid(self) -> tuple[Decimal, Decimal] | None:
        """(price, size) of the highest bid, or None if the side is empty."""
        if not self.bids:
            return None
        p = max(self.bids)
        return p, self.bids[p]

    def best_ask(self) -> tuple[Decimal, Decimal] | None:
        """(price, size) of the lowest ask, or None if the side is empty."""
        if not self.asks:
            return None
        p = min(self.asks)
        return p, self.asks[p]

    def mid(self) -> Decimal | None:
        bb, ba = self.best_bid(), self.best_ask()
        if bb is None or ba is None:
            return None
        return (bb[0] + ba[0]) / 2

    def spread(self) -> Decimal | None:
        bb, ba = self.best_bid(), self.best_ask()
        if bb is None or ba is None:
            return None
        return ba[0] - bb[0]

    def is_crossed(self) -> bool:
        s = self.spread()
        return s is not None and s <= ZERO

    def microprice(self) -> Decimal | None:
        """Size-weighted mid: leans toward the side with LESS size, because the
        thin side is the one more likely to be taken out next.

            micro = (bid * ask_size + ask * bid_size) / (bid_size + ask_size)
        """
        bb, ba = self.best_bid(), self.best_ask()
        if bb is None or ba is None:
            return None
        (b, bs), (a, as_) = bb, ba
        return (b * as_ + a * bs) / (bs + as_)

    # ---- depth ------------------------------------------------------------

    def levels(self, side: str, n: int | None = None) -> list[tuple[Decimal, Decimal]]:
        """Levels best-first: bids descending, asks ascending."""
        if side == "BUY":
            items = sorted(self.bids.items(), reverse=True)
        elif side == "SELL":
            items = sorted(self.asks.items())
        else:
            raise BookError(f"unknown side {side!r}")
        return items[:n] if n is not None else items

    def sweep(self, taker_side: str, shares: Decimal) -> tuple[Decimal, Decimal]:
        """Simulate a marketable order for `shares` against resting liquidity.

        taker_side "BUY" lifts asks (cheapest first); "SELL" hits bids
        (highest first). Returns (shares_filled, average_price). If depth runs
        out, fill what exists. If nothing fills, return (0, 0).
        """
        shares = Decimal(shares)
        if shares <= ZERO:
            raise BookError("shares must be positive")
        resting = self.levels("SELL" if taker_side == "BUY" else "BUY") if taker_side in ("BUY", "SELL") else None
        if resting is None:
            raise BookError(f"unknown side {taker_side!r}")
        remaining, notional = shares, ZERO
        for price, size in resting:
            take = min(size, remaining)
            notional += take * price
            remaining -= take
            if remaining == ZERO:
                break
        filled = shares - remaining
        if filled == ZERO:
            return ZERO, ZERO
        return filled, notional / filled


def synthetic_top(yes: OrderBook, no: OrderBook) -> tuple[Decimal | None, Decimal | None]:
    """Best YES bid/ask once the NO book is folded in.

    YES and NO pay out 1 between them, so selling NO at q is economically
    buying YES at 1 - q, and buying NO at q is selling YES at 1 - q. The
    effective YES bid is therefore max(yes_bid, 1 - no_ask) and the effective
    YES ask is min(yes_ask, 1 - no_bid). Either may be None if no quote exists.
    """
    yb, ya, nb, na = yes.best_bid(), yes.best_ask(), no.best_bid(), no.best_ask()
    bid_cands = [x for x in (yb and yb[0], na and ONE - na[0]) if x is not None]
    ask_cands = [x for x in (ya and ya[0], nb and ONE - nb[0]) if x is not None]
    return (max(bid_cands) if bid_cands else None, min(ask_cands) if ask_cands else None)
