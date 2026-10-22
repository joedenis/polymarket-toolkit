"""Data API v2: a wallet's own activity, and price history.

v2 responses wrap rows as {"data": [...], "pagination": {"has_more": bool,
"next_cursor": str}}. Follow next_cursor via the `cursor` query param and
re-send every other filter with it — the cursor does not carry filters.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import datetime, timezone
from decimal import Decimal

from .config import DATA_URL
from .http import HttpClient
from .models import Fill


def _ts(value) -> datetime:
    """Data API v2 REST timestamps are epoch seconds."""
    return datetime.fromtimestamp(int(value), tz=timezone.utc)


def parse_fill(row: dict) -> Fill:
    return Fill(
        timestamp=_ts(row["timestamp"]),
        asset_id=str(row["token_id"]),
        condition_id=row.get("condition_id", ""),
        side=row["side"],
        shares=Decimal(str(row["size"])),
        price=Decimal(str(row["price"])),
        usdc=Decimal(str(row["usdc_size"])),
        title=row.get("title", ""),
        outcome=row.get("outcome", ""),
        tx_hash=row.get("transaction_hash", ""),
    )


class DataClient:
    def __init__(self, http: HttpClient | None = None) -> None:
        self.http = http or HttpClient(DATA_URL)

    def iter_activity(
            self, user: str, *, types: list[str] | None = None, page_size: int = 1000
    ) -> Iterator[dict]:
        """Yield every raw activity row for `user`, across all pages."""
        params = {
            "user": user,
            "limit": page_size,  # API maximum is 1000
            "type": ",".join(types) if types else None,
            "start": 1,  # full history; default is 3 years back
        }
        cursor: str | None = None
        while True:
            body = self.http.get_json("/v2/activity", {**params, "cursor": cursor})
            yield from body.get("data", [])
            page = body.get("pagination") or {}
            cursor = page.get("next_cursor")
            if not page.get("has_more") or not cursor:
                return

    def fills(self, user: str) -> list[Fill]:
        """The wallet's trades, oldest first."""
        rows = self.iter_activity(user, types=["TRADE"])
        return sorted((parse_fill(r) for r in rows), key=lambda f: f.timestamp)

    def price_history(
        self, asset_id: str, *, interval: str = "max", bucket_seconds: int | None = None
    ) -> list[tuple[datetime, Decimal]]:
        out: list[tuple[datetime, Decimal]] = []
        params = {"token_id": asset_id, "interval": interval, "bucket_seconds": bucket_seconds}
        cursor = None
        while True:
            body = self.http.get_json("/v2/prices-history", {**params, "cursor": cursor})
            out += [(_ts(p["timestamp"]), Decimal(str(p["price"]))) for p in body["data"]]
            page = body.get("pagination") or {}
            cursor = page.get("next_cursor")
            if not page.get("has_more") or not cursor:
                return out
