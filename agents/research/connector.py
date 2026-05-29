"""Bridge research forecasts to paper (simulated) trades.

This connector closes the loop:

    Market -> Memo -> Forecast -> Risk Decision -> Paper Trade -> P&L

It loads a stored forecast from the research journal, checks that the forecast
actually recommends a paper trade (or that the operator explicitly overrode it),
and then records a *simulated* trade carrying provenance back to the forecast.

It is dependency-light: it only composes the stdlib-backed
:class:`~agents.research.journal.ForecastJournal` and
:class:`~agents.research.paper_trading.PaperTradingJournal`. No ``httpx``, no
wallet, no network, no real order placement.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from agents.research.journal import ForecastJournal, ForecastRecord
from agents.research.paper_trading import (
    SOURCE_AGENT,
    SOURCE_MANUAL_OVERRIDE,
    PaperTrade,
    PaperTradeError,
    PaperTradingJournal,
)

# The forecast recommendation that authorizes a paper trade without an override.
PAPER_TRADE_RECOMMENDATION = "PAPER_TRADE"


class ForecastNotFoundError(Exception):
    """Raised when the referenced forecast id does not exist."""


class RecommendationGateError(PaperTradeError):
    """Raised when a forecast does not authorize a paper trade and no override is set."""


@dataclass
class ForecastPaperTradeResult:
    """The outcome of converting a forecast into a simulated trade."""

    forecast: ForecastRecord
    trade: PaperTrade
    overridden: bool

    @property
    def provenance(self) -> str:
        return self.trade.source


def paper_trade_from_forecast(
    forecast_id: int,
    price: float,
    size_usdc: float,
    forecast_journal: ForecastJournal,
    paper_journal: PaperTradingJournal,
    override: bool = False,
    notes: Optional[str] = None,
) -> ForecastPaperTradeResult:
    """Convert a stored forecast into a guardrail-checked simulated BUY.

    Rules:

    - The forecast must exist in the research journal.
    - The forecast's recommendation must be ``PAPER_TRADE``; otherwise an
      explicit ``override=True`` is required (recorded as a manual override so
      the provenance is honest).
    - Question, market id, outcome, and confidence are copied from the forecast
      into the paper ledger. Notes are merged (forecast notes + any new notes).
    - Risk guardrails still apply via the paper journal; a rejected trade is not
      recorded.

    Returns a :class:`ForecastPaperTradeResult` linking the forecast and trade.
    Raises :class:`ForecastNotFoundError`, :class:`RecommendationGateError`, or
    :class:`PaperTradeError` (guardrails) as appropriate.
    """
    forecast = forecast_journal.get_forecast(forecast_id)
    if forecast is None:
        raise ForecastNotFoundError(
            f"No forecast found with id {forecast_id}. "
            "Use 'list-journal' to see saved forecasts."
        )

    is_paper_rec = forecast.recommendation == PAPER_TRADE_RECOMMENDATION
    if not is_paper_rec and not override:
        raise RecommendationGateError(
            f"Forecast #{forecast_id} has recommendation "
            f"'{forecast.recommendation}', not '{PAPER_TRADE_RECOMMENDATION}'. "
            "Re-run with --override to record a manual paper trade anyway."
        )

    # Provenance: an honest label for where this trade came from.
    source = SOURCE_AGENT if is_paper_rec else SOURCE_MANUAL_OVERRIDE

    merged_notes = _merge_notes(forecast, notes, overridden=not is_paper_rec)

    trade = paper_journal.record_buy(
        market_id=forecast.market_id,
        price=price,
        size_usdc=size_usdc,
        question=forecast.question,
        outcome=forecast.outcome,
        notes=merged_notes,
        source=source,
        forecast_id=forecast.id,
        confidence=forecast.confidence,
    )

    return ForecastPaperTradeResult(
        forecast=forecast,
        trade=trade,
        overridden=not is_paper_rec,
    )


def _merge_notes(
    forecast: ForecastRecord, extra_notes: Optional[str], overridden: bool
) -> Optional[str]:
    """Combine forecast context and any operator-supplied notes."""
    parts: list[str] = []
    parts.append(f"from forecast #{forecast.id} (rec={forecast.recommendation})")
    if overridden:
        parts.append("manual override")
    if forecast.notes:
        parts.append(f"forecast notes: {forecast.notes}")
    if extra_notes:
        parts.append(extra_notes)
    return " | ".join(parts) if parts else None
