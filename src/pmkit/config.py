"""Endpoints. Polymarket moved to CLOB V2 on 2026-04-28 and to Data API v2;
these are the post-migration hosts. Override via environment for testing."""

import os

GAMMA_URL = os.getenv("PM_GAMMA_URL", "https://gamma-api.polymarket.com")
CLOB_URL = os.getenv("PM_CLOB_URL", "https://clob.polymarket.com")
DATA_URL = os.getenv("PM_DATA_URL", "https://data-api.polymarket.com")
MARKET_WS_URL = os.getenv(
    "PM_MARKET_WS_URL", "wss://ws-subscriptions-clob.polymarket.com/ws/market"
)

# The market WebSocket expects an application-level "PING" text frame every 10s.
WS_PING_INTERVAL_S = 10.0
