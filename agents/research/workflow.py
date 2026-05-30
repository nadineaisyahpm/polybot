"""Deterministic single-agent research workflow.

The chain is:

    fetch market -> build memo -> save forecast
                 -> evaluate / attempt paper trade -> record run outcome

Every invocation produces exactly one :class:`~agents.research.runs.AgentRun`
row regardless of outcome (COMPLETED / SKIPPED / FAILED). This is the layer a
future chat agent would call as a tool. It composes the existing pieces:

- :class:`~agents.research.market_data.MarketDataClient` (public Gamma API)
- :func:`~agents.research.memo.build_memo`
- :class:`~agents.research.journal.ForecastJournal`
- :func:`~agents.research.connector.paper_trade_from_forecast`
- :class:`~agents.research.paper_trading.PaperTradingJournal`
- :class:`~agents.research.runs.AgentRunsJournal`

It is research-only: no wallet, no private key, no authenticated trading
endpoints, no real orders. A "PAPER_TRADE" outcome means a simulated entry in
the local paper-trade ledger, nothing more.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional, Protocol

from agents.research.connector import (
    ForecastNotFoundError,
    RecommendationGateError,
    paper_trade_from_forecast,
)
from agents.research.journal import ForecastJournal, ForecastValidationError
from agents.research.memo import Memo, build_memo
from agents.research.models import MarketDataError, MarketSnapshot
from agents.research.paper_trading import PaperTradeError, PaperTradingJournal
from agents.research.runs import (
    STATUS_COMPLETED,
    STATUS_FAILED,
    STATUS_SKIPPED,
    AgentRun,
    AgentRunsJournal,
)

PAPER_TRADE_RECOMMENDATION = "PAPER_TRADE"


class MarketFetcher(Protocol):
    """Minimal interface the workflow needs from a market client."""

    def fetch_market(self, market_id: str) -> MarketSnapshot:  # pragma: no cover
        ...


@dataclass
class WorkflowResult:
    """Outcome of a single run of :func:`run_paper_agent`."""

    run: AgentRun
    status: str
    memo: Optional[Memo] = None
    forecast_id: Optional[int] = None
    paper_trade_id: Optional[int] = None
    error: Optional[str] = None
    skipped_reason: Optional[str] = None

    @property
    def is_paper_trade(self) -> bool:
        return self.paper_trade_id is not None


def _memo_to_payload(memo: Memo) -> dict[str, Any]:
    """Serialize a memo into a JSON-safe dict for the runs table."""
    return {
        "market_id": memo.market_id,
        "question": memo.question,
        "outcome": memo.outcome,
        "implied_probability": memo.implied_probability,
        "forecast_probability": memo.forecast_probability,
        "confidence": memo.confidence,
        "recommendation": memo.recommendation,
        "reasoning": memo.reasoning,
        "evidence_for": memo.evidence_for,
        "evidence_against": memo.evidence_against,
        "unknowns": memo.unknowns,
    }


def run_paper_agent(
    market_id: str,
    price: float,
    size_usdc: float,
    market_client: MarketFetcher,
    forecast_journal: ForecastJournal,
    paper_journal: PaperTradingJournal,
    runs_journal: AgentRunsJournal,
    notes: Optional[str] = None,
) -> WorkflowResult:
    """Run the full deterministic research workflow for a single market.

    The workflow always records exactly one ``agent_runs`` row. Behaviour:

    - On any error (network, validation, unexpected exception) the run is
      marked ``FAILED`` with a useful error string. The exception is **not**
      re-raised; the caller inspects ``WorkflowResult.error`` instead.
    - If the memo recommendation is ``WATCH`` or ``NO_TRADE``, no trade is
      attempted; the forecast is saved (when there is a usable forecast
      probability) and the run is marked ``SKIPPED`` with a reason.
    - If the memo recommendation is ``PAPER_TRADE`` the workflow saves the
      forecast and attempts a guardrail-checked simulated paper trade. A
      successful trade marks the run ``COMPLETED``; a guardrail rejection
      marks it ``FAILED``.
    """
    run = runs_journal.start_run(
        market_id=market_id,
        price=price,
        size_usdc=size_usdc,
        notes=notes,
    )

    # ------------------------------------------------------------------
    # Step 1: fetch market
    # ------------------------------------------------------------------
    try:
        market = market_client.fetch_market(str(market_id))
    except MarketDataError as err:
        final = runs_journal.fail_run(run.id, error=f"MarketDataError: {err}")
        return WorkflowResult(run=final, status=STATUS_FAILED, error=str(err))
    except Exception as err:  # noqa: BLE001 - workflow must always finalize the run
        final = runs_journal.fail_run(
            run.id, error=f"{type(err).__name__}: {err}"
        )
        return WorkflowResult(run=final, status=STATUS_FAILED, error=str(err))

    # ------------------------------------------------------------------
    # Step 2: build memo
    # ------------------------------------------------------------------
    try:
        memo = build_memo(market)
    except Exception as err:  # noqa: BLE001
        final = runs_journal.fail_run(
            run.id, error=f"MemoError: {type(err).__name__}: {err}"
        )
        return WorkflowResult(run=final, status=STATUS_FAILED, error=str(err))

    memo_payload = _memo_to_payload(memo)

    # ------------------------------------------------------------------
    # Step 3: save forecast (only if memo produced a usable probability)
    # ------------------------------------------------------------------
    forecast_id: Optional[int] = None
    if memo.forecast_probability is not None:
        try:
            forecast = forecast_journal.add_forecast(
                market_id=memo.market_id,
                forecast_probability=memo.forecast_probability,
                question=memo.question,
                outcome=memo.outcome,
                market_probability=memo.implied_probability,
                confidence=memo.confidence,
                recommendation=memo.recommendation,
                notes=f"agent run #{run.id}: {memo.reasoning}",
            )
            forecast_id = forecast.id
        except ForecastValidationError as err:
            final = runs_journal.fail_run(
                run.id,
                error=f"ForecastValidationError: {err}",
                memo=memo_payload,
                recommendation=memo.recommendation,
            )
            return WorkflowResult(
                run=final,
                status=STATUS_FAILED,
                memo=memo,
                error=str(err),
            )

    # ------------------------------------------------------------------
    # Step 4: gate on the recommendation
    # ------------------------------------------------------------------
    if memo.recommendation != PAPER_TRADE_RECOMMENDATION:
        reason = (
            f"memo recommendation is {memo.recommendation!r}; no paper trade."
        )
        final = runs_journal.skip_run(
            run.id,
            reason=reason,
            forecast_id=forecast_id,
            memo=memo_payload,
            recommendation=memo.recommendation,
        )
        return WorkflowResult(
            run=final,
            status=STATUS_SKIPPED,
            memo=memo,
            forecast_id=forecast_id,
            skipped_reason=reason,
        )

    if forecast_id is None:
        # Defensive: PAPER_TRADE recommendation without a forecast probability
        # should not happen given the memo logic, but we guard anyway.
        reason = "memo recommended PAPER_TRADE but no forecast probability was available."
        final = runs_journal.skip_run(
            run.id,
            reason=reason,
            memo=memo_payload,
            recommendation=memo.recommendation,
        )
        return WorkflowResult(
            run=final,
            status=STATUS_SKIPPED,
            memo=memo,
            skipped_reason=reason,
        )

    # ------------------------------------------------------------------
    # Step 5: attempt the guardrail-checked simulated paper trade
    # ------------------------------------------------------------------
    try:
        result = paper_trade_from_forecast(
            forecast_id=forecast_id,
            price=price,
            size_usdc=size_usdc,
            forecast_journal=forecast_journal,
            paper_journal=paper_journal,
        )
    except (PaperTradeError, RecommendationGateError) as err:
        reasons = getattr(err, "reasons", []) or []
        risk_payload = {"rejected": True, "reasons": list(reasons)}
        final = runs_journal.fail_run(
            run.id,
            error=f"PaperTradeError: {err}",
            forecast_id=forecast_id,
            memo=memo_payload,
            risk=risk_payload,
            recommendation=memo.recommendation,
        )
        return WorkflowResult(
            run=final,
            status=STATUS_FAILED,
            memo=memo,
            forecast_id=forecast_id,
            error=str(err),
        )
    except ForecastNotFoundError as err:
        final = runs_journal.fail_run(
            run.id,
            error=f"ForecastNotFoundError: {err}",
            memo=memo_payload,
            recommendation=memo.recommendation,
        )
        return WorkflowResult(
            run=final,
            status=STATUS_FAILED,
            memo=memo,
            forecast_id=forecast_id,
            error=str(err),
        )

    risk_payload = {
        "rejected": False,
        "price": price,
        "size_usdc": size_usdc,
        "source": result.trade.source,
    }
    final = runs_journal.complete_run(
        run.id,
        forecast_id=forecast_id,
        paper_trade_id=result.trade.id,
        memo=memo_payload,
        risk=risk_payload,
        recommendation=memo.recommendation,
    )
    return WorkflowResult(
        run=final,
        status=STATUS_COMPLETED,
        memo=memo,
        forecast_id=forecast_id,
        paper_trade_id=result.trade.id,
    )
