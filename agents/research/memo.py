"""Deterministic, plain-English research memos.

This module builds a structured research memo from public market metadata alone.
It does not require an LLM and never places trades. The recommendation logic is
deliberately conservative: it defaults to ``NO_TRADE`` and only relaxes to
``WATCH`` or ``PAPER_TRADE`` when there is enough structured evidence (defined
outcomes, usable prices, and meaningful liquidity/volume).

If an LLM is wired in later, its output must be treated as untrusted and parsed
strictly. For the first milestone everything here is deterministic so the same
market always yields the same memo.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from agents.research.models import MarketSnapshot

# Valid enumerations, kept in one place so the journal and memo agree.
CONFIDENCE_LEVELS = ("low", "medium", "high")
RECOMMENDATIONS = ("NO_TRADE", "WATCH", "PAPER_TRADE")

# Evidence thresholds. These are intentionally modest and explained in the memo.
_LIQUIDITY_WATCH = 10_000.0
_LIQUIDITY_PAPER = 50_000.0
_VOLUME_WATCH = 10_000.0
_VOLUME_PAPER = 50_000.0


@dataclass
class Memo:
    """Structured research memo for a single market."""

    market_id: str
    question: str
    description: str
    implied_probability: Optional[float]
    outcome: str
    evidence_for: list[str] = field(default_factory=list)
    evidence_against: list[str] = field(default_factory=list)
    unknowns: list[str] = field(default_factory=list)
    forecast_probability: Optional[float] = None
    confidence: str = "low"
    recommendation: str = "NO_TRADE"
    reasoning: str = ""


def _has_usable_prices(market: MarketSnapshot) -> bool:
    prices = market.outcome_prices
    return bool(prices) and all(0.0 <= p <= 1.0 for p in prices)


def _assess_recommendation(
    market: MarketSnapshot,
) -> tuple[str, str, str]:
    """Decide ``(recommendation, confidence, reasoning)`` conservatively.

    The default is always ``NO_TRADE``. We only step up when structured evidence
    is present. This is the core risk-control rule of the milestone.
    """
    # Hard requirements before any non-NO_TRADE recommendation.
    if not (market.question and market.question.strip()):
        return (
            "NO_TRADE",
            "low",
            "No question text is available, so there is nothing concrete to forecast.",
        )
    if not market.outcomes:
        return (
            "NO_TRADE",
            "low",
            "Outcomes are undefined, so the market cannot be evaluated yet.",
        )
    if not _has_usable_prices(market):
        return (
            "NO_TRADE",
            "low",
            "Outcome prices are missing or out of range, so the implied "
            "probability is not trustworthy.",
        )

    liquidity = market.liquidity or 0.0
    volume = market.volume or 0.0

    if liquidity >= _LIQUIDITY_PAPER and volume >= _VOLUME_PAPER:
        return (
            "PAPER_TRADE",
            "medium",
            "Outcomes and prices are well defined and both liquidity and volume "
            "are healthy, so this is a reasonable candidate for a paper (practice) "
            "trade only. No real money is involved.",
        )

    if liquidity >= _LIQUIDITY_WATCH or volume >= _VOLUME_WATCH:
        return (
            "WATCH",
            "low",
            "The market has structure and some activity, but not enough depth to "
            "act on. Add it to a watchlist and revisit as more data arrives.",
        )

    return (
        "NO_TRADE",
        "low",
        "The market is too thin (low liquidity and volume) to support a "
        "confident view. Defaulting to NO_TRADE.",
    )


def _gather_evidence(
    market: MarketSnapshot,
) -> tuple[list[str], list[str], list[str]]:
    """Collect plain-English evidence buckets from public metadata."""
    evidence_for: list[str] = []
    evidence_against: list[str] = []
    unknowns: list[str] = []

    if market.question and market.question.strip():
        evidence_for.append("The market has a clearly stated question.")
    else:
        evidence_against.append("The market is missing a question.")

    if market.outcomes:
        evidence_for.append(
            "Outcomes are defined: " + ", ".join(market.outcomes) + "."
        )
    else:
        evidence_against.append("No outcomes are defined.")

    if _has_usable_prices(market):
        prob = market.implied_probability
        evidence_for.append(
            f"Outcome prices are present (implied probability ~{prob:.0%})."
        )
    else:
        evidence_against.append("Outcome prices are missing or out of range.")

    if market.liquidity is not None:
        if market.liquidity >= _LIQUIDITY_WATCH:
            evidence_for.append(f"Liquidity is meaningful (~{market.liquidity:,.0f}).")
        else:
            evidence_against.append(
                f"Liquidity is low (~{market.liquidity:,.0f})."
            )
    else:
        unknowns.append("Liquidity is not reported.")

    if market.volume is not None:
        if market.volume >= _VOLUME_WATCH:
            evidence_for.append(f"Trading volume is meaningful (~{market.volume:,.0f}).")
        else:
            evidence_against.append(f"Trading volume is low (~{market.volume:,.0f}).")
    else:
        unknowns.append("Trading volume is not reported.")

    if market.spread is not None:
        if market.spread <= 0.05:
            evidence_for.append(f"Spread is reasonably tight ({market.spread:.3f}).")
        else:
            evidence_against.append(f"Spread is wide ({market.spread:.3f}).")
    else:
        unknowns.append("Spread is not reported.")

    if market.end_date:
        unknowns.append(f"Resolution timing depends on the end date ({market.end_date}).")
    else:
        unknowns.append("No end date is reported, so resolution timing is unclear.")

    if not market.resolution_source:
        unknowns.append("No explicit resolution source is provided.")

    if market.restricted is True:
        unknowns.append(
            "Market is trading-restricted in some regions (e.g. the US). "
            "This is research only; no trade is placed."
        )

    return evidence_for, evidence_against, unknowns


def build_memo(market: MarketSnapshot) -> Memo:
    """Build a deterministic :class:`Memo` from a market snapshot.

    The forecast probability mirrors the market's implied probability when one is
    available. This keeps the first milestone honest: without external evidence
    we anchor on the market rather than inventing an edge.
    """
    evidence_for, evidence_against, unknowns = _gather_evidence(market)
    recommendation, confidence, reasoning = _assess_recommendation(market)

    forecast_probability = (
        market.implied_probability if _has_usable_prices(market) else None
    )

    return Memo(
        market_id=market.id,
        question=(market.question or "").strip() or "(no question provided)",
        description=(market.description or "").strip()
        or "(no description provided)",
        implied_probability=market.implied_probability,
        outcome=market.primary_outcome(),
        evidence_for=evidence_for,
        evidence_against=evidence_against,
        unknowns=unknowns,
        forecast_probability=forecast_probability,
        confidence=confidence,
        recommendation=recommendation,
        reasoning=reasoning,
    )


def _format_probability(value: Optional[float]) -> str:
    if value is None:
        return "unknown"
    return f"{value:.0%}"


def _format_bullets(items: list[str]) -> str:
    if not items:
        return "- (none identified)"
    return "\n".join(f"- {item}" for item in items)


def render_memo(memo: Memo) -> str:
    """Render a :class:`Memo` as the plain-text format from the brief."""
    lines = [
        f"Market: {memo.question}",
        f"Market ID: {memo.market_id}",
        f"Resolution / Description: {memo.description}",
        f"Current Implied Probability: {_format_probability(memo.implied_probability)}",
        "",
        "Evidence For:",
        _format_bullets(memo.evidence_for),
        "",
        "Evidence Against:",
        _format_bullets(memo.evidence_against),
        "",
        "Unknowns:",
        _format_bullets(memo.unknowns),
        "",
        "Forecast:",
        f"- Probability: {_format_probability(memo.forecast_probability)}",
        f"- Confidence: {memo.confidence}",
        f"- Recommendation: {memo.recommendation}",
        "",
        "Reasoning:",
        memo.reasoning,
    ]
    return "\n".join(lines)
