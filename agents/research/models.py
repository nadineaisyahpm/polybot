"""Pure, dependency-light data models for research.

This module deliberately imports nothing beyond the Python standard library. In
particular it does **not** import ``httpx``. The ranking, memo, and journal
layers depend only on this module, so they can run on a plain interpreter with
no third-party packages installed. The network layer
(:mod:`agents.research.market_data`) builds on top of these models and is the
only place that requires ``httpx``.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Optional


class MarketDataError(Exception):
    """Raised when public market data cannot be fetched or parsed."""


@dataclass
class MarketSnapshot:
    """A normalized, read-only view of a single public market.

    Only fields useful for research are kept. Everything is optional because the
    public API is inconsistent about which keys it returns per market.
    """

    id: str
    question: Optional[str] = None
    description: Optional[str] = None
    slug: Optional[str] = None
    resolution_source: Optional[str] = None
    end_date: Optional[str] = None
    outcomes: list[str] = field(default_factory=list)
    outcome_prices: list[float] = field(default_factory=list)
    liquidity: Optional[float] = None
    volume: Optional[float] = None
    volume_24hr: Optional[float] = None
    spread: Optional[float] = None
    active: Optional[bool] = None
    closed: Optional[bool] = None
    archived: Optional[bool] = None
    restricted: Optional[bool] = None
    accepting_orders: Optional[bool] = None
    raw: dict[str, Any] = field(default_factory=dict, repr=False)

    @property
    def implied_probability(self) -> Optional[float]:
        """Best-effort implied probability of the first outcome (usually ``Yes``).

        Returns ``None`` when no usable outcome price is available.
        """
        if not self.outcome_prices:
            return None
        return self.outcome_prices[0]

    def primary_outcome(self) -> str:
        """Return the first outcome label, defaulting to ``Yes``."""
        if self.outcomes:
            return self.outcomes[0]
        return "Yes"


def _coerce_float(value: Any) -> Optional[float]:
    """Convert API values (often strings) to float, or ``None`` on failure."""
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _coerce_bool(value: Any) -> Optional[bool]:
    """Convert assorted truthy/falsey API representations to bool or ``None``."""
    if isinstance(value, bool):
        return value
    if value is None:
        return None
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in {"true", "1", "yes"}:
            return True
        if lowered in {"false", "0", "no"}:
            return False
    if isinstance(value, (int, float)):
        return bool(value)
    return None


def _parse_string_list(value: Any) -> list[str]:
    """Parse Gamma's stringified JSON lists (e.g. outcomes) into a list of str."""
    if value is None:
        return []
    if isinstance(value, list):
        return [str(item) for item in value]
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return []
        try:
            parsed = json.loads(text)
        except json.JSONDecodeError:
            return []
        if isinstance(parsed, list):
            return [str(item) for item in parsed]
    return []


def _parse_float_list(value: Any) -> list[float]:
    """Parse Gamma's stringified JSON price lists into a list of float."""
    floats: list[float] = []
    for item in _parse_string_list(value):
        coerced = _coerce_float(item)
        if coerced is not None:
            floats.append(coerced)
    return floats


def normalize_market(raw: dict[str, Any]) -> MarketSnapshot:
    """Turn a raw Gamma market dict into a :class:`MarketSnapshot`.

    This is deliberately tolerant: unknown or missing fields become ``None`` or
    empty collections rather than raising, because the public API shape varies.
    """
    if not isinstance(raw, dict):
        raise MarketDataError(f"Expected a market object, got {type(raw).__name__}")

    market_id = raw.get("id")
    if market_id is None:
        raise MarketDataError("Market is missing an 'id' field")

    return MarketSnapshot(
        id=str(market_id),
        question=raw.get("question"),
        description=raw.get("description"),
        slug=raw.get("slug"),
        resolution_source=raw.get("resolutionSource"),
        end_date=raw.get("endDate") or raw.get("endDateIso"),
        outcomes=_parse_string_list(raw.get("outcomes")),
        outcome_prices=_parse_float_list(raw.get("outcomePrices")),
        liquidity=_coerce_float(raw.get("liquidity") or raw.get("liquidityNum")),
        volume=_coerce_float(raw.get("volume") or raw.get("volumeNum")),
        volume_24hr=_coerce_float(raw.get("volume24hr")),
        spread=_coerce_float(raw.get("spread")),
        active=_coerce_bool(raw.get("active")),
        closed=_coerce_bool(raw.get("closed")),
        archived=_coerce_bool(raw.get("archived")),
        restricted=_coerce_bool(raw.get("restricted")),
        accepting_orders=_coerce_bool(raw.get("acceptingOrders")),
        raw=raw,
    )
