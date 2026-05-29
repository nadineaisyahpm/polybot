"""Fetch public Polymarket Gamma market data.

This module talks only to the public, read-only Gamma REST API. It never
authenticates, signs, or touches a wallet/private key, and it never reaches the
CLOB trading endpoints.

This is the **only** module in :mod:`agents.research` that requires ``httpx``.
The pure data models and parsing helpers live in :mod:`agents.research.models`
so that the ranking, memo, and journal layers can run on a plain interpreter
without any third-party dependency installed. The models are re-exported here
for convenience and backward compatibility.
"""

from __future__ import annotations

import json
from typing import Optional

import httpx

from agents.research.models import (
    MarketDataError,
    MarketSnapshot,
    normalize_market,
)

# Re-exported so callers can keep importing these from market_data.
__all__ = [
    "MarketDataClient",
    "MarketDataError",
    "MarketSnapshot",
    "normalize_market",
    "GAMMA_BASE_URL",
    "GAMMA_MARKETS_ENDPOINT",
]

GAMMA_BASE_URL = "https://gamma-api.polymarket.com"
GAMMA_MARKETS_ENDPOINT = GAMMA_BASE_URL + "/markets"
DEFAULT_TIMEOUT_SECONDS = 20.0


class MarketDataClient:
    """Read-only client for the public Gamma market data API.

    No authentication, no wallet, no order placement. Every method here performs
    HTTP GET requests against public endpoints only.
    """

    def __init__(
        self,
        base_url: str = GAMMA_BASE_URL,
        timeout: float = DEFAULT_TIMEOUT_SECONDS,
        transport: Optional[httpx.BaseTransport] = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.markets_endpoint = self.base_url + "/markets"
        self._timeout = timeout
        # ``transport`` is injectable so tests can mock HTTP without network access.
        self._transport = transport

    def _client(self) -> httpx.Client:
        return httpx.Client(timeout=self._timeout, transport=self._transport)

    def fetch_active_markets(
        self, limit: int = 20, offset: int = 0
    ) -> list[MarketSnapshot]:
        """Fetch active, non-closed, non-archived markets from the public API.

        Raises :class:`MarketDataError` with a clear message on network errors,
        non-200 responses, or unparseable payloads so the CLI can fail gracefully.
        """
        if limit <= 0:
            return []

        params = {
            "active": "true",
            "closed": "false",
            "archived": "false",
            "limit": limit,
            "offset": offset,
        }

        try:
            with self._client() as client:
                response = client.get(self.markets_endpoint, params=params)
        except httpx.HTTPError as err:
            raise MarketDataError(
                f"Failed to reach public market data API: {err}"
            ) from err

        if response.status_code != 200:
            raise MarketDataError(
                "Public market data API returned HTTP "
                f"{response.status_code}. Please try again later."
            )

        try:
            data = response.json()
        except (json.JSONDecodeError, ValueError) as err:
            raise MarketDataError(
                "Public market data API returned an unparseable response."
            ) from err

        if not isinstance(data, list):
            raise MarketDataError(
                "Unexpected response shape from public market data API "
                "(expected a list of markets)."
            )

        snapshots: list[MarketSnapshot] = []
        for raw_market in data:
            try:
                snapshots.append(normalize_market(raw_market))
            except MarketDataError:
                # Skip individual malformed records rather than failing the batch.
                continue
        return snapshots

    def fetch_market(self, market_id: str) -> MarketSnapshot:
        """Fetch a single public market by id.

        Raises :class:`MarketDataError` on network errors, a non-200 response
        (including 404 for unknown ids), or an unparseable payload.
        """
        url = f"{self.markets_endpoint}/{market_id}"
        try:
            with self._client() as client:
                response = client.get(url)
        except httpx.HTTPError as err:
            raise MarketDataError(
                f"Failed to reach public market data API: {err}"
            ) from err

        if response.status_code == 404:
            raise MarketDataError(f"No market found with id '{market_id}'.")
        if response.status_code != 200:
            raise MarketDataError(
                "Public market data API returned HTTP "
                f"{response.status_code} for market '{market_id}'."
            )

        try:
            data = response.json()
        except (json.JSONDecodeError, ValueError) as err:
            raise MarketDataError(
                "Public market data API returned an unparseable response."
            ) from err

        # The single-market endpoint may return a dict or a single-item list.
        if isinstance(data, list):
            if not data:
                raise MarketDataError(f"No market found with id '{market_id}'.")
            data = data[0]

        return normalize_market(data)
