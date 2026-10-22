"""These pass out of the box — they cover the provided code."""

from decimal import Decimal

import httpx
import pytest

from pmkit.gamma import GammaClient, parse_market
from pmkit.http import HttpClient

from .conftest import NO, YES, load


def test_parse_v1_market_decodes_json_strings():
    m = parse_market(load("gamma_market_v1.json"))
    assert [o.label for o in m.outcomes] == ["Yes", "No"]
    assert m.outcomes[0].asset_id == YES and m.outcomes[1].asset_id == NO
    assert m.outcomes[0].price == Decimal("0.085")
    assert m.tick_size == Decimal("0.01")
    assert m.resolved_winner is None


def test_parse_v2_market_uses_position_ids():
    m = parse_market(load("gamma_market_v2.json"))
    assert m.version == "v2"
    assert m.outcomes[0].asset_id.endswith("800")
    assert m.neg_risk


def test_closed_market_winner():
    m = parse_market(load("gamma_market_closed.json"))
    assert m.resolved_winner.label == "No"
    assert m.resolved_winner.asset_id == "222"


def test_http_retries_on_429_then_succeeds():
    calls = []

    def handler(request):
        calls.append(request)
        if len(calls) < 3:
            return httpx.Response(429)
        return httpx.Response(200, json={"ok": True})

    sleeps = []
    http = HttpClient("https://x", transport=httpx.MockTransport(handler), sleep=sleeps.append)
    assert http.get_json("/a") == {"ok": True}
    assert len(calls) == 3
    assert sleeps == [0.5, 1.0]  # exponential backoff


def test_http_gives_up_after_max_retries():
    http = HttpClient(
        "https://x",
        transport=httpx.MockTransport(lambda r: httpx.Response(503)),
        max_retries=2,
        sleep=lambda s: None,
    )
    with pytest.raises(httpx.HTTPStatusError):
        http.get_json("/a")


def test_gamma_offset_pagination():
    page = [load("gamma_market_v1.json")] * 2
    seen = []

    def handler(request):
        seen.append(int(request.url.params["offset"]))
        return httpx.Response(200, json=page if len(seen) == 1 else page[:1])

    g = GammaClient(HttpClient("https://x", transport=httpx.MockTransport(handler)))
    assert len(list(g.iter_markets(page_size=2))) == 3
    assert seen == [0, 2]
