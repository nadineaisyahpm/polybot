"""Deterministic local chat agent that dispatches intents to safe tools.

The agent composes the existing research and paper-trading building blocks:

- :class:`~agents.research.journal.ForecastJournal`
- :class:`~agents.research.paper_trading.PaperTradingJournal`
- :class:`~agents.research.runs.AgentRunsJournal`
- :class:`~agents.research.market_data.MarketDataClient` (only constructed for
  intents that need the network)
- :func:`~agents.research.memo.build_memo` / :func:`~agents.research.memo.render_memo`
- :func:`~agents.research.ranking.rank_markets`
- :func:`~agents.research.workflow.run_paper_agent`

Offline intents (``SHOW_FORECASTS``, ``SHOW_PORTFOLIO``, ``SHOW_HISTORY``,
``SHOW_RUNS``, ``HELP``) never import :mod:`httpx`. Network intents
(``SCAN_MARKETS``, ``RESEARCH_MARKET``, ``RUN_PAPER_AGENT``) import the network
module lazily and surface a clear error if ``httpx`` is missing or the API is
unreachable.

The chat layer **never** touches a wallet, never authenticates to trading
endpoints, and never places a real order. Every paper-trading response carries
the ``PAPER/SIMULATED`` label.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional

from agents.chat.intents import Intent, ParsedIntent, parse_intent
from agents.research.models import MarketDataError

DEFAULT_LIST_LIMIT = 10
DEFAULT_SCAN_LIMIT = 5
PAPER_LABEL = "PAPER/SIMULATED"


@dataclass
class ChatResponse:
    """One chat-agent reply.

    ``text`` is the conversational message. ``data`` carries the structured
    payload a future chat UI can render (rows, ids, etc.).
    """

    text: str
    intent: Intent
    ok: bool = True
    data: dict[str, Any] = field(default_factory=dict)


HELP_TEXT = (
    "I can help you research Polymarket safely. Everything is research-only "
    "and any trade I record is PAPER/SIMULATED — no wallet, no real orders. "
    "Try:\n"
    "  - 'show forecasts'\n"
    "  - 'show portfolio'\n"
    "  - 'show paper history'\n"
    "  - 'show agent runs'\n"
    "  - 'scan markets' (needs network)\n"
    "  - 'research market <id>' (needs network)\n"
    "  - 'run paper agent for market <id> at price <p> size <usdc>' (needs network)\n"
    "  - 'help' to see this list again\n"
    "  - 'exit' to leave the shell."
)


class ChatAgent:
    """Dispatch parsed intents to the existing safe tools.

    The agent does not open long-lived database connections. Each call to
    :meth:`handle` opens the journals it needs and closes them, so the agent
    is safe to share across the REPL and a future web UI.
    """

    def __init__(
        self,
        forecast_db_path: str,
        paper_db_path: str,
        runs_db_path: str,
        market_client_factory: Optional[Any] = None,
        timeout: Optional[float] = None,
    ) -> None:
        self.forecast_db_path = forecast_db_path
        self.paper_db_path = paper_db_path
        self.runs_db_path = runs_db_path
        self.timeout = timeout
        # ``market_client_factory`` is a zero-arg callable returning something
        # that satisfies :class:`~agents.research.workflow.MarketFetcher` and
        # also exposes ``fetch_active_markets``. Tests inject a stub here so
        # network intents can run without httpx or a live network.
        self._market_client_factory = market_client_factory

    # ------------------------------------------------------------------
    # Public entry point
    # ------------------------------------------------------------------
    def handle(self, message: str) -> ChatResponse:
        parsed = parse_intent(message)
        return self.dispatch(parsed)

    def dispatch(self, parsed: ParsedIntent) -> ChatResponse:
        if parsed.intent == Intent.HELP:
            return ChatResponse(text=HELP_TEXT, intent=Intent.HELP)
        if parsed.intent == Intent.UNKNOWN:
            return ChatResponse(
                text=(parsed.reason or "I didn't understand that.")
                + "\n\n"
                + HELP_TEXT,
                intent=Intent.UNKNOWN,
                ok=False,
            )
        if parsed.intent == Intent.SHOW_FORECASTS:
            return self._show_forecasts(parsed)
        if parsed.intent == Intent.SHOW_PORTFOLIO:
            return self._show_portfolio()
        if parsed.intent == Intent.SHOW_HISTORY:
            return self._show_history(parsed)
        if parsed.intent == Intent.SHOW_RUNS:
            return self._show_runs(parsed)
        if parsed.intent == Intent.SCAN_MARKETS:
            return self._scan_markets(parsed)
        if parsed.intent == Intent.RESEARCH_MARKET:
            return self._research_market(parsed)
        if parsed.intent == Intent.RUN_PAPER_AGENT:
            return self._run_paper_agent(parsed)
        # Unreachable given the enum coverage above.
        return ChatResponse(
            text="I don't know how to handle that yet.",
            intent=Intent.UNKNOWN,
            ok=False,
        )

    # ------------------------------------------------------------------
    # Offline intents
    # ------------------------------------------------------------------
    def _show_forecasts(self, parsed: ParsedIntent) -> ChatResponse:
        from agents.research.journal import ForecastJournal

        limit = parsed.limit or DEFAULT_LIST_LIMIT
        with ForecastJournal(self.forecast_db_path) as journal:
            records = journal.list_forecasts(limit=limit)
        if not records:
            return ChatResponse(
                text="No forecasts saved yet. Save one with the journal CLI or "
                "run the paper agent on a market.",
                intent=Intent.SHOW_FORECASTS,
                data={"forecasts": []},
            )
        lines = [f"Most recent {len(records)} forecast(s):"]
        for r in records:
            prob = _pct(r.forecast_probability)
            lines.append(
                f"  #{r.id} [{r.market_id}] {prob} "
                f"({r.recommendation}, {r.confidence})"
            )
        return ChatResponse(
            text="\n".join(lines),
            intent=Intent.SHOW_FORECASTS,
            data={"forecasts": [_forecast_summary(r) for r in records]},
        )

    def _show_portfolio(self) -> ChatResponse:
        from agents.research.paper_trading import PaperTradingJournal

        with PaperTradingJournal(self.paper_db_path) as journal:
            positions = journal.get_open_positions()
            exposure = journal.total_exposure()
            realized = journal.total_realized_pnl()
        if not positions:
            return ChatResponse(
                text=(
                    f"[{PAPER_LABEL}] No open simulated positions. "
                    "Nothing real is at risk."
                ),
                intent=Intent.SHOW_PORTFOLIO,
                data={"positions": [], "total_open_exposure": exposure},
            )
        lines = [
            f"[{PAPER_LABEL}] {len(positions)} open simulated position(s):"
        ]
        for p in positions:
            avg = _pct(p.average_price)
            lines.append(
                f"  [{p.market_id}] {p.outcome}  shares={p.shares:.4f}  "
                f"avg={avg}  exposure={_usd(p.exposure)}"
            )
        lines.append(
            f"Total exposure: {_usd(exposure)}  ·  realized P&L: {_usd(realized)}"
        )
        return ChatResponse(
            text="\n".join(lines),
            intent=Intent.SHOW_PORTFOLIO,
            data={
                "positions": [_position_summary(p) for p in positions],
                "total_open_exposure": exposure,
                "total_realized_pnl": realized,
            },
        )

    def _show_history(self, parsed: ParsedIntent) -> ChatResponse:
        from agents.research.paper_trading import PaperTradingJournal

        limit = parsed.limit or DEFAULT_LIST_LIMIT
        with PaperTradingJournal(self.paper_db_path) as journal:
            trades = journal.history(limit=limit)
        if not trades:
            return ChatResponse(
                text=f"[{PAPER_LABEL}] No simulated trades yet.",
                intent=Intent.SHOW_HISTORY,
                data={"trades": []},
            )
        lines = [f"[{PAPER_LABEL}] Most recent {len(trades)} simulated trade(s):"]
        for t in trades:
            lines.append(
                f"  #{t.id} {t.side} [{t.market_id}] price={t.price:g} "
                f"shares={t.shares:.4f} amount={_usd(t.size_usdc)} "
                f"({t.source})"
            )
        return ChatResponse(
            text="\n".join(lines),
            intent=Intent.SHOW_HISTORY,
            data={"trades": [_trade_summary(t) for t in trades]},
        )

    def _show_runs(self, parsed: ParsedIntent) -> ChatResponse:
        from agents.research.runs import AgentRunsJournal

        limit = parsed.limit or DEFAULT_LIST_LIMIT
        with AgentRunsJournal(self.runs_db_path) as journal:
            runs = journal.list_runs(limit=limit)
        if not runs:
            return ChatResponse(
                text="No agent runs recorded yet.",
                intent=Intent.SHOW_RUNS,
                data={"runs": []},
            )
        lines = [f"Most recent {len(runs)} agent run(s):"]
        for r in runs:
            forecast_part = f"forecast #{r.forecast_id}" if r.forecast_id else "no forecast"
            trade_part = (
                f"paper trade #{r.paper_trade_id}"
                if r.paper_trade_id
                else "no trade"
            )
            lines.append(
                f"  #{r.id} [{r.status}] market {r.market_id} -> "
                f"{forecast_part} -> {trade_part}"
            )
            if r.error:
                lines.append(f"     error: {r.error}")
        return ChatResponse(
            text="\n".join(lines),
            intent=Intent.SHOW_RUNS,
            data={"runs": [_run_summary(r) for r in runs]},
        )

    # ------------------------------------------------------------------
    # Network intents
    # ------------------------------------------------------------------
    def _make_market_client(self):
        """Build the market client lazily so offline intents stay httpx-free."""
        if self._market_client_factory is not None:
            return self._market_client_factory()
        try:
            from agents.research.market_data import (
                DEFAULT_TIMEOUT_SECONDS,
                MarketDataClient,
            )
        except ImportError as err:
            raise MarketDataError(
                "The 'httpx' package is required for this command. Install it "
                f"with 'pip install httpx'. Original error: {err}"
            ) from err
        timeout = self.timeout if self.timeout is not None else DEFAULT_TIMEOUT_SECONDS
        return MarketDataClient(timeout=timeout)

    def _scan_markets(self, parsed: ParsedIntent) -> ChatResponse:
        from agents.research.ranking import rank_markets

        limit = parsed.limit or DEFAULT_SCAN_LIMIT
        try:
            client = self._make_market_client()
            markets = client.fetch_active_markets(limit=max(limit * 3, limit))
        except MarketDataError as err:
            return ChatResponse(
                text=f"I couldn't reach the public market data API: {err}",
                intent=Intent.SCAN_MARKETS,
                ok=False,
            )
        ranked = rank_markets(markets, limit=limit)
        if not ranked:
            return ChatResponse(
                text="No markets passed the basic quality filters right now.",
                intent=Intent.SCAN_MARKETS,
                data={"markets": []},
            )
        lines = [f"Top {len(ranked)} active markets (public data only):"]
        for idx, item in enumerate(ranked, start=1):
            m = item.market
            lines.append(
                f"  {idx}. [{m.id}] {(m.question or '(no question)').strip()} "
                f"(score {item.score:.1f}, implied {_pct(m.implied_probability)})"
            )
        return ChatResponse(
            text="\n".join(lines),
            intent=Intent.SCAN_MARKETS,
            data={
                "markets": [
                    {
                        "market_id": item.market.id,
                        "question": item.market.question,
                        "score": item.score,
                        "implied_probability": item.market.implied_probability,
                    }
                    for item in ranked
                ]
            },
        )

    def _research_market(self, parsed: ParsedIntent) -> ChatResponse:
        from agents.research.memo import build_memo, render_memo

        if not parsed.market_id:
            return ChatResponse(
                text=parsed.reason
                or "Please tell me which market id to research.",
                intent=Intent.RESEARCH_MARKET,
                ok=False,
            )
        try:
            client = self._make_market_client()
            market = client.fetch_market(parsed.market_id)
        except MarketDataError as err:
            return ChatResponse(
                text=f"I couldn't fetch market {parsed.market_id}: {err}",
                intent=Intent.RESEARCH_MARKET,
                ok=False,
            )
        memo = build_memo(market)
        rendered = render_memo(memo)
        intro = (
            f"Here's a deterministic research memo for market {parsed.market_id}. "
            f"Recommendation: {memo.recommendation} ({memo.confidence} confidence). "
            "This is research only — no trade is placed."
        )
        return ChatResponse(
            text=intro + "\n\n" + rendered,
            intent=Intent.RESEARCH_MARKET,
            data={
                "market_id": memo.market_id,
                "recommendation": memo.recommendation,
                "confidence": memo.confidence,
                "forecast_probability": memo.forecast_probability,
            },
        )

    def _run_paper_agent(self, parsed: ParsedIntent) -> ChatResponse:
        from agents.research.journal import ForecastJournal
        from agents.research.paper_trading import PaperTradingJournal
        from agents.research.runs import AgentRunsJournal
        from agents.research.workflow import run_paper_agent

        if parsed.missing:
            return ChatResponse(
                text=parsed.reason
                or "I need a market id, price, and size to run the paper agent.",
                intent=Intent.RUN_PAPER_AGENT,
                ok=False,
                data={"missing": parsed.missing},
            )
        assert parsed.market_id is not None
        assert parsed.price is not None
        assert parsed.size is not None
        try:
            client = self._make_market_client()
        except MarketDataError as err:
            return ChatResponse(
                text=f"I couldn't start the paper agent: {err}",
                intent=Intent.RUN_PAPER_AGENT,
                ok=False,
            )
        with ForecastJournal(self.forecast_db_path) as fj, PaperTradingJournal(
            self.paper_db_path
        ) as pj, AgentRunsJournal(self.runs_db_path) as rj:
            result = run_paper_agent(
                market_id=parsed.market_id,
                price=parsed.price,
                size_usdc=parsed.size,
                market_client=client,
                forecast_journal=fj,
                paper_journal=pj,
                runs_journal=rj,
                notes="from chat agent",
            )
        return ChatResponse(
            text=_format_workflow_result(result),
            intent=Intent.RUN_PAPER_AGENT,
            ok=(result.status != "FAILED"),
            data={
                "run_id": result.run.id,
                "status": result.status,
                "forecast_id": result.forecast_id,
                "paper_trade_id": result.paper_trade_id,
                "error": result.error,
            },
        )


# ----------------------------------------------------------------------
# Formatting helpers
# ----------------------------------------------------------------------
def _pct(value: Optional[float]) -> str:
    if value is None:
        return "n/a"
    return f"{value:.0%}"


def _usd(value: Optional[float]) -> str:
    if value is None:
        return "n/a"
    return f"${value:.2f}"


def _forecast_summary(record) -> dict[str, Any]:
    return {
        "id": record.id,
        "market_id": record.market_id,
        "forecast_probability": record.forecast_probability,
        "recommendation": record.recommendation,
        "confidence": record.confidence,
    }


def _position_summary(position) -> dict[str, Any]:
    return {
        "market_id": position.market_id,
        "outcome": position.outcome,
        "shares": position.shares,
        "average_price": position.average_price,
        "exposure": position.exposure,
    }


def _trade_summary(trade) -> dict[str, Any]:
    return {
        "id": trade.id,
        "market_id": trade.market_id,
        "side": trade.side,
        "price": trade.price,
        "shares": trade.shares,
        "size_usdc": trade.size_usdc,
        "source": trade.source,
        "forecast_id": trade.forecast_id,
    }


def _run_summary(run) -> dict[str, Any]:
    return {
        "id": run.id,
        "status": run.status,
        "market_id": run.market_id,
        "forecast_id": run.forecast_id,
        "paper_trade_id": run.paper_trade_id,
        "error": run.error,
    }


def _format_workflow_result(result) -> str:
    forecast_part = (
        f"Forecast #{result.forecast_id}" if result.forecast_id else "Forecast n/a"
    )
    if result.paper_trade_id:
        trade_part = f"Paper Trade #{result.paper_trade_id} [{PAPER_LABEL}]"
    elif result.status == "SKIPPED":
        trade_part = "SKIPPED"
    else:
        trade_part = "FAILED"
    chain = (
        f"Run #{result.run.id} [{result.status}]: Market "
        f"{result.run.market_id} -> Memo -> {forecast_part} -> {trade_part}"
    )
    extras: list[str] = []
    if result.memo is not None:
        extras.append(
            f"recommendation: {result.memo.recommendation} ({result.memo.confidence})"
        )
        extras.append(f"reasoning: {result.memo.reasoning}")
    if result.skipped_reason:
        extras.append(f"skipped: {result.skipped_reason}")
    if result.error:
        extras.append(f"error: {result.error}")
    if result.paper_trade_id:
        extras.append(
            f"NOTE: paper trade is {PAPER_LABEL}. No real order placed."
        )
    if extras:
        return chain + "\n  " + "\n  ".join(extras)
    return chain
