"""Gamma API: market discovery and metadata.

Gamma returns several fields as JSON-encoded *strings* (outcomes,
outcomePrices, clobTokenIds) — a classic gotcha. Protocol V2 markets carry
`positionIds` as a real array instead, and `version` says which to use.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from decimal import Decimal
from typing import Any

from .config import GAMMA_URL
from .http import HttpClient
from .models import MarketMeta, Outcome


def _json_list(value: Any) -> list:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    return json.loads(value)


def parse_market(raw: dict) -> MarketMeta:
    version = raw.get("version") or "v1"
    labels = _json_list(raw.get("outcomes"))
    prices = _json_list(raw.get("outcomePrices"))
    if version == "v2":
        ids = _json_list(raw.get("positionIds"))
    else:
        ids = _json_list(raw.get("clobTokenIds"))
    if version not in ("v1", "v2"):
        raise ValueError(f"unsupported market version {version!r}")

    outcomes = tuple(
        Outcome(
            label=label,
            asset_id=str(ids[i]) if i < len(ids) else "",
            price=Decimal(str(prices[i])) if i < len(prices) else None,
        )
        for i, label in enumerate(labels)
    )
    tick = raw.get("orderPriceMinTickSize")
    return MarketMeta(
        id=str(raw["id"]),
        slug=raw.get("slug", ""),
        question=raw.get("question", ""),
        condition_id=raw.get("conditionId", ""),
        version=version,
        outcomes=outcomes,
        active=bool(raw.get("active")),
        closed=bool(raw.get("closed")),
        neg_risk=bool(raw.get("negRisk")),
        tick_size=Decimal(str(tick)) if tick is not None else None,
        end_date=raw.get("endDateIso") or raw.get("endDate"),
    )


class GammaClient:
    def __init__(self, http: HttpClient | None = None) -> None:
        self.http = http or HttpClient(GAMMA_URL)

    def market_by_slug(self, slug: str) -> MarketMeta:
        return parse_market(self.http.get_json(f"/markets/slug/{slug}"))

    def market_by_condition(self, condition_id: str) -> MarketMeta | None:
        # Gamma omits closed markets unless closed=true is passed, so ask for
        # closed first (most of a trading history has resolved), then open.
        for closed in (True, False):
            rows = self.http.get_json(
                "/markets", {"condition_ids": condition_id, "closed": closed}
            )
            if rows:
                return parse_market(rows[0])
        return None

    def iter_markets(
        self, *, closed: bool | None = False, page_size: int = 100, **filters: Any
    ) -> Iterator[MarketMeta]:
        """Offset-paginated scan of /markets."""
        offset = 0
        while True:
            rows = self.http.get_json(
                "/markets",
                {"closed": closed, "limit": page_size, "offset": offset, **filters},
            )
            for raw in rows:
                yield parse_market(raw)
            if len(rows) < page_size:
                return
            offset += page_size
