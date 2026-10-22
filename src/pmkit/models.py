"""Plain data types shared across the package.

Prices and sizes are Decimal throughout the live path: prices sit on a tick
grid (0.01, 0.001, ...) and float rounding makes level lookups and hashing
unreliable. Analysis code converts to float once data is in pandas.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from typing import Literal

Side = Literal["BUY", "SELL"]


@dataclass(frozen=True)
class Outcome:
    label: str
    asset_id: str  # CLOB token ID (CTF/v1) or position ID (Protocol V2)
    price: Decimal | None


@dataclass(frozen=True)
class MarketMeta:
    id: str
    slug: str
    question: str
    condition_id: str
    version: str
    outcomes: tuple[Outcome, ...]
    active: bool
    closed: bool
    neg_risk: bool
    tick_size: Decimal | None
    end_date: str | None

    @property
    def resolved_winner(self) -> Outcome | None:
        """For a closed market, the outcome whose final price is 1."""
        if not self.closed:
            return None
        for o in self.outcomes:
            if o.price == Decimal(1):
                return o
        return None


@dataclass(frozen=True)
class Fill:
    """One of the wallet's own trades, from the Data API activity feed."""

    timestamp: datetime
    asset_id: str
    condition_id: str
    side: Side
    shares: Decimal
    price: Decimal
    usdc: Decimal
    title: str = ""
    outcome: str = ""
    tx_hash: str = ""


@dataclass
class TapeRow:
    """Flat record written to Parquet by the recorder: one row per level change
    or trade. Flat rows keep the schema stable and make replay trivial."""

    recv_ts: datetime
    exch_ts: datetime | None
    event_type: str
    market: str
    asset_id: str
    side: str | None = None
    price: str | None = None
    size: str | None = None
    best_bid: str | None = None
    best_ask: str | None = None
    extra: dict = field(default_factory=dict)
