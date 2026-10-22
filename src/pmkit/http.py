"""Small HTTP wrapper: JSON GET with retry on 429/5xx and exponential backoff.

Provided complete — read it, it is the pattern the other clients rely on.
"""

from __future__ import annotations

import logging
import time
from typing import Any

import httpx

log = logging.getLogger(__name__)

RETRY_STATUS = {429, 500, 502, 503, 504}


class HttpClient:
    def __init__(
        self,
        base_url: str,
        *,
        timeout: float = 10.0,
        max_retries: int = 4,
        backoff_s: float = 0.5,
        transport: httpx.BaseTransport | None = None,
        sleep=time.sleep,
    ) -> None:
        self._client = httpx.Client(base_url=base_url, timeout=timeout, transport=transport)
        self.max_retries = max_retries
        self.backoff_s = backoff_s
        self._sleep = sleep  # injectable so tests don't actually wait

    def get_json(self, path: str, params: dict[str, Any] | None = None) -> Any:
        attempt = 0
        while True:
            try:
                resp = self._client.get(path, params=_clean(params))
            except httpx.TransportError as exc:
                if attempt >= self.max_retries:
                    raise
                log.warning("transport error %s on %s, retrying", exc, path)
            else:
                if resp.status_code not in RETRY_STATUS:
                    resp.raise_for_status()
                    return resp.json()
                if attempt >= self.max_retries:
                    resp.raise_for_status()
                log.warning("HTTP %s on %s, retrying", resp.status_code, path)
            delay = self.backoff_s * (2**attempt)
            self._sleep(delay)
            attempt += 1

    def close(self) -> None:
        self._client.close()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()


def _clean(params: dict[str, Any] | None) -> dict[str, Any] | None:
    if params is None:
        return None
    out: dict[str, Any] = {}
    for k, v in params.items():
        if v is None:
            continue
        out[k] = str(v).lower() if isinstance(v, bool) else v
    return out
