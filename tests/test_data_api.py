from decimal import Decimal

import httpx

from pmkit.data_api import DataClient
from pmkit.http import HttpClient

from .conftest import YES, load


def mock_client(pages):
    requests = []

    def handler(request):
        requests.append(request)
        cursor = request.url.params.get("cursor")
        return httpx.Response(200, json=pages[1] if cursor == "CURSOR_2" else pages[0])

    http = HttpClient("https://data", transport=httpx.MockTransport(handler))
    return DataClient(http), requests


def test_iter_activity_follows_cursor_and_keeps_filters():
    client, reqs = mock_client([load("activity_page1.json"), load("activity_page2.json")])
    rows = list(client.iter_activity("0xabc", types=["TRADE"]))
    assert len(rows) == 3
    assert len(reqs) == 2
    assert "cursor" not in reqs[0].url.params
    assert reqs[1].url.params["cursor"] == "CURSOR_2"
    for r in reqs:  # filters re-sent on every page
        assert r.url.path == "/v2/activity"
        assert r.url.params["user"] == "0xabc"
        assert r.url.params["type"] == "TRADE"


def test_iter_activity_stops_without_cursor():
    page = load("activity_page1.json")
    page["pagination"] = {"has_more": True, "next_cursor": None}
    client, reqs = mock_client([page, page])
    assert len(list(client.iter_activity("0xabc"))) == 2
    assert len(reqs) == 1


def test_fills_parsed_and_sorted():
    client, _ = mock_client([load("activity_page2.json"), load("activity_page1.json")])
    # page order deliberately reversed relative to time: page2 served first,
    # but it has has_more=False so only one page is read -> one fill
    fills = client.fills("0xabc")
    assert len(fills) == 1 and fills[0].side == "SELL"
    client, _ = mock_client([load("activity_page1.json"), load("activity_page2.json")])
    fills = client.fills("0xabc")
    assert [f.side for f in fills] == ["BUY", "BUY", "SELL"]
    assert fills[0].asset_id == YES
    assert fills[0].price == Decimal("0.4")
