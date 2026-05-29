"""Transparent, explainable market ranking.

The ranking here is intentionally simple and auditable. Each market gets a
numeric score plus a list of human-readable reason strings explaining how that
score was reached. A beginner should be able to read the reasons and understand
exactly why one market ranked above another.

Markets that are closed, archived, or inactive are excluded outright (when those
fields are present), in keeping with the public-data, beginner-safe posture of
this tool.

Note on ``restricted``: on Polymarket this flag means the market is
*trading*-restricted in some jurisdictions (e.g. the US). It is not a
market-quality signal, and empirically almost every active market carries it.
Because this tool is **read-only research and never trades**, restricted markets
are included by default (with a transparent note). Callers who specifically want
the stricter behavior can pass ``exclude_restricted=True``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional

from agents.research.models import MarketSnapshot


@dataclass
class RankedMarket:
    """A market paired with its computed score and the reasons behind it."""

    market: MarketSnapshot
    score: float
    reasons: list[str] = field(default_factory=list)


def _is_eligible(
    market: MarketSnapshot, exclude_restricted: bool = False
) -> tuple[bool, Optional[str]]:
    """Return ``(eligible, reason_if_excluded)`` for hard inclusion filters.

    Only excludes when a disqualifying field is explicitly present. Absent
    fields are treated as "unknown" and do not exclude the market.

    ``restricted`` is only a disqualifier when ``exclude_restricted`` is set,
    because it reflects trading jurisdiction limits, not research suitability.
    """
    if market.active is False:
        return False, "excluded: market is not active"
    if market.closed is True:
        return False, "excluded: market is closed"
    if market.archived is True:
        return False, "excluded: market is archived"
    if exclude_restricted and market.restricted is True:
        return False, "excluded: market is restricted (exclude_restricted=True)"
    return True, None


def _parse_end_date(value: Optional[str]) -> Optional[datetime]:
    """Parse an ISO-8601 end date into a timezone-aware datetime, if possible."""
    if not value:
        return None
    text = value.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def score_market(
    market: MarketSnapshot,
    now: Optional[datetime] = None,
    exclude_restricted: bool = False,
) -> RankedMarket:
    """Score a single market and explain the score.

    The score starts at zero and accrues small, transparent bonuses for quality
    signals (a real question, defined outcomes, usable prices, liquidity, volume,
    a tight spread, and a future end date). Each contribution is recorded as a
    reason string so the ranking is never a black box.
    """
    now = now or datetime.now(timezone.utc)
    reasons: list[str] = []
    score = 0.0

    eligible, exclusion_reason = _is_eligible(
        market, exclude_restricted=exclude_restricted
    )
    if not eligible:
        return RankedMarket(market=market, score=float("-inf"), reasons=[exclusion_reason])

    # Transparency: note (but do not penalize) trading-restricted markets.
    if market.restricted is True:
        reasons.append("note: trading-restricted in some regions (research only)")

    # Non-empty question.
    if market.question and market.question.strip():
        score += 1.0
        reasons.append("+1.0 has a non-empty question")
    else:
        reasons.append("+0.0 missing question text")

    # Non-empty outcomes.
    if market.outcomes:
        score += 1.0
        reasons.append(f"+1.0 has {len(market.outcomes)} defined outcomes")
    else:
        reasons.append("+0.0 no defined outcomes")

    # Non-empty, plausible outcome prices.
    if market.outcome_prices:
        valid_prices = all(0.0 <= p <= 1.0 for p in market.outcome_prices)
        if valid_prices:
            score += 1.0
            reasons.append("+1.0 has usable outcome prices")
        else:
            reasons.append("+0.0 outcome prices look out of range")
    else:
        reasons.append("+0.0 no outcome prices")

    # Liquidity bonus (log-ish via thresholds to keep things readable).
    if market.liquidity is not None:
        if market.liquidity >= 100_000:
            score += 2.0
            reasons.append(f"+2.0 strong liquidity (~{market.liquidity:,.0f})")
        elif market.liquidity >= 10_000:
            score += 1.0
            reasons.append(f"+1.0 moderate liquidity (~{market.liquidity:,.0f})")
        elif market.liquidity > 0:
            score += 0.5
            reasons.append(f"+0.5 some liquidity (~{market.liquidity:,.0f})")
        else:
            reasons.append("+0.0 no liquidity reported")

    # Volume bonus.
    if market.volume is not None:
        if market.volume >= 100_000:
            score += 2.0
            reasons.append(f"+2.0 strong volume (~{market.volume:,.0f})")
        elif market.volume >= 10_000:
            score += 1.0
            reasons.append(f"+1.0 moderate volume (~{market.volume:,.0f})")
        elif market.volume > 0:
            score += 0.5
            reasons.append(f"+0.5 some volume (~{market.volume:,.0f})")
        else:
            reasons.append("+0.0 no volume reported")

    # Spread bonus: tighter is better (lower spread => more bonus).
    if market.spread is not None:
        if market.spread <= 0.02:
            score += 1.0
            reasons.append(f"+1.0 tight spread ({market.spread:.3f})")
        elif market.spread <= 0.05:
            score += 0.5
            reasons.append(f"+0.5 moderate spread ({market.spread:.3f})")
        else:
            reasons.append(f"+0.0 wide spread ({market.spread:.3f})")

    # Future end date bonus.
    end_dt = _parse_end_date(market.end_date)
    if end_dt is not None:
        if end_dt > now:
            score += 1.0
            reasons.append(f"+1.0 resolves in the future ({market.end_date})")
        else:
            reasons.append(f"+0.0 end date is in the past ({market.end_date})")

    return RankedMarket(market=market, score=score, reasons=reasons)


def rank_markets(
    markets: list[MarketSnapshot],
    limit: Optional[int] = None,
    now: Optional[datetime] = None,
    exclude_restricted: bool = False,
) -> list[RankedMarket]:
    """Score and sort markets best-first, dropping excluded markets.

    Ties are broken by market id so the ordering is deterministic and stable.
    """
    now = now or datetime.now(timezone.utc)
    scored = [
        score_market(market, now=now, exclude_restricted=exclude_restricted)
        for market in markets
    ]
    eligible = [rm for rm in scored if rm.score != float("-inf")]
    eligible.sort(key=lambda rm: (-rm.score, rm.market.id))
    if limit is not None:
        return eligible[:limit]
    return eligible
