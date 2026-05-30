#!/usr/bin/env python3
"""Public-data-only Polymarket research CLI (beginner-safe, no live trading).

This CLI fetches public market data, ranks markets transparently, prints
deterministic research memos, and journals forecasts to local SQLite. It never
reads a wallet/private key, never authenticates to trading endpoints, and never
places a trade.

Commands:

    python scripts/python/research_cli.py scan --limit 10
    python scripts/python/research_cli.py research --market-id <id>
    python scripts/python/research_cli.py journal --market-id <id> --probability 0.57 --notes "..."
    python scripts/python/research_cli.py list-journal
    python scripts/python/research_cli.py paper-buy --market-id <id> --price 0.55 --size 10
    python scripts/python/research_cli.py paper-sell --market-id <id> --price 0.60 --shares 5
    python scripts/python/research_cli.py paper-from-forecast --forecast-id <id> --price 0.57 --size 10
    python scripts/python/research_cli.py paper-portfolio
    python scripts/python/research_cli.py paper-history

The paper-* commands record SIMULATED trades only. They never place a real
order, never touch a wallet/private key, and enforce conservative risk limits
before recording. All simulated activity is labeled PAPER/SIMULATED in output.

Uses the standard-library ``argparse`` (no extra CLI dependency) so it runs on a
plain Python install.
"""

from __future__ import annotations

import argparse
import os
import sys

# Ensure the repo root is importable when run directly as a script.
_REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from agents.research.journal import (  # noqa: E402
    DEFAULT_DB_PATH,
    ForecastJournal,
    ForecastValidationError,
)
from agents.research.models import MarketDataError  # noqa: E402
from agents.research.memo import build_memo, render_memo  # noqa: E402
from agents.research.ranking import rank_markets  # noqa: E402
from agents.research.paper_trading import (  # noqa: E402
    PAPER_LABEL,
    DEFAULT_DB_PATH as PAPER_DB_PATH,
    PaperTradeError,
    PaperTradingJournal,
)
from agents.research.connector import (  # noqa: E402
    ForecastNotFoundError,
    RecommendationGateError,
    paper_trade_from_forecast,
)
from agents.research.risk import DEFAULT_LIMITS  # noqa: E402
from agents.research.runs import (  # noqa: E402
    DEFAULT_DB_PATH as RUNS_DB_PATH,
    AgentRunsJournal,
    STATUS_COMPLETED,
    STATUS_FAILED,
    STATUS_SKIPPED,
)
from agents.research.workflow import run_paper_agent  # noqa: E402

# NOTE: ``agents.research.market_data`` (the only httpx-dependent module) is
# imported lazily inside the commands that actually hit the network, so the
# offline commands (journal, list-journal, and all paper-* commands) run
# without httpx installed.


def _format_probability(value) -> str:
    if value is None:
        return "n/a"
    return f"{value:.0%}"


def _make_market_client(timeout: float | None = None):
    """Import and construct the httpx-backed client on demand.

    Importing here (rather than at module top level) keeps the offline commands
    usable when ``httpx`` is not installed. If it is missing, we surface a clear,
    actionable error instead of an ImportError traceback.
    """
    try:
        from agents.research.market_data import (
            DEFAULT_TIMEOUT_SECONDS,
            MarketDataClient,
        )
    except ImportError as err:
        raise MarketDataError(
            "The 'httpx' package is required for fetching public market data. "
            "Install dependencies first (see README: pip install httpx). "
            f"Original import error: {err}"
        ) from err
    return MarketDataClient(
        timeout=timeout if timeout is not None else DEFAULT_TIMEOUT_SECONDS
    )


def cmd_scan(args: argparse.Namespace) -> int:
    """Fetch and rank active public markets."""
    try:
        client = _make_market_client(timeout=args.timeout)
        # Fetch a wider pool than requested so ranking has candidates to filter.
        pool_size = max(args.limit * 3, args.limit)
        markets = client.fetch_active_markets(limit=pool_size)
    except MarketDataError as err:
        print(f"Error: {err}", file=sys.stderr)
        return 1

    if not markets:
        print("No active markets were returned by the public API.")
        return 0

    ranked = rank_markets(
        markets, limit=args.limit, exclude_restricted=args.exclude_restricted
    )
    if not ranked:
        print("No markets passed the basic quality filters.")
        return 0

    print(f"Top {len(ranked)} active markets (public data only, no trading):\n")
    for position, item in enumerate(ranked, start=1):
        market = item.market
        question = (market.question or "(no question)").strip()
        print(f"{position}. [{market.id}] {question}")
        print(f"   score: {item.score:.1f}")
        print(f"   implied probability: {_format_probability(market.implied_probability)}")
        if args.reasons:
            for reason in item.reasons:
                print(f"     - {reason}")
        print()
    return 0


def cmd_research(args: argparse.Namespace) -> int:
    """Print a deterministic research memo for one market."""
    try:
        client = _make_market_client(timeout=args.timeout)
        market = client.fetch_market(args.market_id)
    except MarketDataError as err:
        print(f"Error: {err}", file=sys.stderr)
        return 1

    memo = build_memo(market)
    print(render_memo(memo))
    return 0


def cmd_journal(args: argparse.Namespace) -> int:
    """Save a forecast to the local SQLite journal."""
    question = args.question
    market_probability = args.market_probability

    # Best-effort enrichment from public data; failure here is non-fatal because
    # journaling must work offline too (and without httpx installed).
    if question is None or market_probability is None:
        try:
            market = _make_market_client(timeout=10.0).fetch_market(args.market_id)
            if question is None:
                question = market.question
            if market_probability is None:
                market_probability = market.implied_probability
        except MarketDataError as err:
            print(
                f"Note: could not fetch public market metadata ({err}). "
                "Saving forecast without it.",
                file=sys.stderr,
            )

    try:
        with ForecastJournal(args.db_path) as journal:
            record = journal.add_forecast(
                market_id=args.market_id,
                forecast_probability=args.probability,
                question=question,
                outcome=args.outcome,
                market_probability=market_probability,
                confidence=args.confidence,
                recommendation=args.recommendation,
                notes=args.notes,
            )
    except ForecastValidationError as err:
        print(f"Error: {err}", file=sys.stderr)
        return 1

    print(f"Saved forecast #{record.id} for market {record.market_id}.")
    print(f"  outcome: {record.outcome}")
    print(f"  forecast probability: {_format_probability(record.forecast_probability)}")
    print(f"  market probability: {_format_probability(record.market_probability)}")
    print(f"  confidence: {record.confidence}")
    print(f"  recommendation: {record.recommendation}")
    if record.notes:
        print(f"  notes: {record.notes}")
    return 0


def cmd_list_journal(args: argparse.Namespace) -> int:
    """List saved forecasts, most recent first."""
    with ForecastJournal(args.db_path) as journal:
        records = journal.list_forecasts(limit=args.limit)

    if not records:
        print("No forecasts saved yet. Use the 'journal' command to add one.")
        return 0

    print(f"Saved forecasts ({len(records)}):\n")
    for record in records:
        print(f"#{record.id}  [{record.market_id}]  {record.created_at}")
        if record.question:
            print(f"   question: {record.question}")
        print(
            f"   forecast: {_format_probability(record.forecast_probability)} "
            f"(market: {_format_probability(record.market_probability)}) "
            f"on outcome '{record.outcome}'"
        )
        print(f"   confidence: {record.confidence}  recommendation: {record.recommendation}")
        if record.notes:
            print(f"   notes: {record.notes}")
        print()
    return 0


def _format_usdc(value) -> str:
    if value is None:
        return "n/a"
    return f"{value:.2f} USDC"


def _format_source(trade) -> str:
    """Human-readable provenance label for a paper trade."""
    labels = {
        "manual": "manual paper trade",
        "agent_recommended": "agent-recommended paper trade",
        "manual_override": "manual override (forecast did not recommend PAPER_TRADE)",
    }
    label = labels.get(trade.source, trade.source)
    if trade.forecast_id is not None:
        return f"{label} (forecast #{trade.forecast_id})"
    return label


def _print_risk_limits() -> None:
    print(f"  risk limits ({PAPER_LABEL}):")
    for line in DEFAULT_LIMITS.describe():
        print(f"    - {line}")


def cmd_paper_buy(args: argparse.Namespace) -> int:
    """Record a simulated BUY after enforcing risk guardrails."""
    try:
        with PaperTradingJournal(args.db_path) as journal:
            trade = journal.record_buy(
                market_id=args.market_id,
                price=args.price,
                size_usdc=args.size,
                question=args.question,
                outcome=args.outcome,
                notes=args.notes,
            )
            total_exposure = journal.total_exposure()
    except PaperTradeError as err:
        print(f"[{PAPER_LABEL}] {err}", file=sys.stderr)
        for reason in err.reasons:
            print(f"  - {reason}", file=sys.stderr)
        _print_risk_limits()
        return 1

    print(f"[{PAPER_LABEL}] Recorded simulated BUY #{trade.id}. No real order placed.")
    print(f"  market: {trade.market_id}  outcome: {trade.outcome}")
    print(f"  price: {trade.price:g}  shares: {trade.shares:.4f}")
    print(f"  cost: {_format_usdc(trade.size_usdc)}")
    print(f"  total open exposure now: {_format_usdc(total_exposure)}")
    if trade.notes:
        print(f"  notes: {trade.notes}")
    return 0


def cmd_paper_sell(args: argparse.Namespace) -> int:
    """Record a simulated SELL after enforcing risk guardrails."""
    try:
        with PaperTradingJournal(args.db_path) as journal:
            trade = journal.record_sell(
                market_id=args.market_id,
                price=args.price,
                shares=args.shares,
                outcome=args.outcome,
                question=args.question,
                notes=args.notes,
            )
            total_exposure = journal.total_exposure()
    except PaperTradeError as err:
        print(f"[{PAPER_LABEL}] {err}", file=sys.stderr)
        for reason in err.reasons:
            print(f"  - {reason}", file=sys.stderr)
        return 1

    print(f"[{PAPER_LABEL}] Recorded simulated SELL #{trade.id}. No real order placed.")
    print(f"  market: {trade.market_id}  outcome: {trade.outcome}")
    print(f"  price: {trade.price:g}  shares: {trade.shares:.4f}")
    print(f"  proceeds: {_format_usdc(trade.size_usdc)}")
    print(f"  realized P&L on this sell: {_format_usdc(trade.realized_pnl)}")
    print(f"  total open exposure now: {_format_usdc(total_exposure)}")
    if trade.notes:
        print(f"  notes: {trade.notes}")
    return 0


def cmd_paper_portfolio(args: argparse.Namespace) -> int:
    """Show open simulated positions and aggregate exposure."""
    with PaperTradingJournal(args.db_path) as journal:
        positions = journal.get_open_positions()
        total_exposure = journal.total_exposure()
        realized = journal.total_realized_pnl()

    print(f"[{PAPER_LABEL}] Paper portfolio (simulated, no real funds):\n")
    if not positions:
        print("  No open simulated positions. Use 'paper-buy' to open one.")
    else:
        for pos in positions:
            print(f"  [{pos.market_id}] outcome '{pos.outcome}'")
            if pos.question:
                print(f"     question: {pos.question}")
            print(
                f"     shares: {pos.shares:.4f}  "
                f"avg price: {pos.average_price:g}  "
                f"exposure: {_format_usdc(pos.exposure)}"
            )
        print()
    print(f"  total open exposure: {_format_usdc(total_exposure)}")
    print(f"  total realized P&L: {_format_usdc(realized)}")
    _print_risk_limits()
    return 0


def cmd_paper_history(args: argparse.Namespace) -> int:
    """Show the simulated trade ledger, most recent first."""
    with PaperTradingJournal(args.db_path) as journal:
        trades = journal.history(limit=args.limit)

    if not trades:
        print(
            f"[{PAPER_LABEL}] No simulated trades yet. "
            "Use 'paper-buy' to record one."
        )
        return 0

    print(f"[{PAPER_LABEL}] Simulated trade history ({len(trades)}):\n")
    for trade in trades:
        tag = "SIMULATED" if trade.simulated else "??"
        print(
            f"#{trade.id}  [{tag}] {trade.side}  [{trade.market_id}] "
            f"outcome '{trade.outcome}'  {trade.created_at}"
        )
        print(
            f"   price: {trade.price:g}  shares: {trade.shares:.4f}  "
            f"amount: {_format_usdc(trade.size_usdc)}"
        )
        print(f"   source: {_format_source(trade)}")
        if trade.side.upper() == "SELL":
            print(f"   realized P&L: {_format_usdc(trade.realized_pnl)}")
        if trade.notes:
            print(f"   notes: {trade.notes}")
        print()
    return 0


def cmd_paper_from_forecast(args: argparse.Namespace) -> int:
    """Convert a stored forecast into a guardrail-checked simulated BUY."""
    try:
        with ForecastJournal(args.forecast_db_path) as fj, PaperTradingJournal(
            args.db_path
        ) as pj:
            result = paper_trade_from_forecast(
                forecast_id=args.forecast_id,
                price=args.price,
                size_usdc=args.size,
                forecast_journal=fj,
                paper_journal=pj,
                override=args.override,
                notes=args.notes,
            )
            total_exposure = pj.total_exposure()
    except ForecastNotFoundError as err:
        print(f"Error: {err}", file=sys.stderr)
        return 1
    except RecommendationGateError as err:
        print(f"[{PAPER_LABEL}] {err}", file=sys.stderr)
        return 1
    except PaperTradeError as err:
        print(f"[{PAPER_LABEL}] {err}", file=sys.stderr)
        for reason in err.reasons:
            print(f"  - {reason}", file=sys.stderr)
        _print_risk_limits()
        return 1

    trade = result.trade
    forecast = result.forecast
    print(
        f"[{PAPER_LABEL}] Recorded simulated BUY #{trade.id} "
        f"from forecast #{forecast.id}. No real order placed."
    )
    if result.overridden:
        print(
            "  NOTE: forecast recommendation was "
            f"'{forecast.recommendation}', recorded as a MANUAL OVERRIDE."
        )
    print(f"  chain: Forecast #{forecast.id} -> Risk OK -> Paper Trade #{trade.id}")
    print(f"  market: {trade.market_id}  outcome: {trade.outcome}")
    print(f"  question: {trade.question or '(none)'}")
    print(f"  price: {trade.price:g}  shares: {trade.shares:.4f}")
    print(f"  cost: {_format_usdc(trade.size_usdc)}")
    print(f"  source: {_format_source(trade)}")
    print(f"  confidence (from forecast): {trade.confidence or 'n/a'}")
    print(f"  total open exposure now: {_format_usdc(total_exposure)}")
    if trade.notes:
        print(f"  notes: {trade.notes}")
    return 0


def cmd_run_paper_agent(args: argparse.Namespace) -> int:
    """Run the deterministic single-agent workflow end-to-end."""
    try:
        client = _make_market_client(timeout=args.timeout)
    except MarketDataError as err:
        print(f"Error: {err}", file=sys.stderr)
        return 1

    with ForecastJournal(args.forecast_db_path) as fj, PaperTradingJournal(
        args.db_path
    ) as pj, AgentRunsJournal(args.runs_db_path) as rj:
        result = run_paper_agent(
            market_id=args.market_id,
            price=args.price,
            size_usdc=args.size,
            market_client=client,
            forecast_journal=fj,
            paper_journal=pj,
            runs_journal=rj,
            notes=args.notes,
        )

    market_id = result.run.market_id
    forecast_part = (
        f"Forecast #{result.forecast_id}" if result.forecast_id else "Forecast n/a"
    )
    risk_part = (
        "Risk OK"
        if result.status == STATUS_COMPLETED
        else ("Risk REJECTED" if result.status == STATUS_FAILED else "Risk n/a")
    )
    if result.paper_trade_id:
        trade_part = f"Paper Trade #{result.paper_trade_id} [{PAPER_LABEL}]"
    elif result.status == STATUS_SKIPPED:
        trade_part = "SKIPPED"
    else:
        trade_part = "FAILED"

    print(
        f"Run #{result.run.id} [{result.status}]: Market {market_id} -> Memo -> "
        f"{forecast_part} -> {risk_part} -> {trade_part}"
    )

    if result.memo is not None:
        print(f"  recommendation: {result.memo.recommendation}")
        print(f"  reasoning: {result.memo.reasoning}")
    if result.skipped_reason:
        print(f"  skipped: {result.skipped_reason}")
    if result.error:
        print(f"  error: {result.error}", file=sys.stderr)
    if result.is_paper_trade:
        print(
            f"  NOTE: paper trade is {PAPER_LABEL}. "
            "No real order placed, no wallet touched."
        )

    if result.status == STATUS_FAILED:
        return 1
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="research_cli",
        description=(
            "Public-data-only Polymarket research CLI. Reads public market data, "
            "ranks markets, writes research memos, and journals forecasts. "
            "Never trades, never touches a wallet or private key."
        ),
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    # scan
    scan_parser = subparsers.add_parser(
        "scan", help="List and rank active public markets."
    )
    scan_parser.add_argument(
        "--limit", type=int, default=10, help="Number of markets to display."
    )
    scan_parser.add_argument(
        "--reasons",
        action="store_true",
        help="Show the scoring reasons for each market.",
    )
    scan_parser.add_argument(
        "--exclude-restricted",
        action="store_true",
        help=(
            "Exclude markets flagged trading-restricted in some regions. "
            "Off by default since this is read-only research (restricted is a "
            "trading-jurisdiction flag, not a quality signal)."
        ),
    )
    scan_parser.add_argument(
        "--timeout",
        type=float,
        default=None,
        help="HTTP timeout in seconds for the public API call (default: 20).",
    )
    scan_parser.set_defaults(func=cmd_scan)

    # research
    research_parser = subparsers.add_parser(
        "research", help="Print a research memo for one market."
    )
    research_parser.add_argument(
        "--market-id", required=True, help="The public market id to research."
    )
    research_parser.add_argument(
        "--timeout",
        type=float,
        default=None,
        help="HTTP timeout in seconds for the public API call (default: 20).",
    )
    research_parser.set_defaults(func=cmd_research)

    # journal
    journal_parser = subparsers.add_parser(
        "journal", help="Save a forecast to the local SQLite journal."
    )
    journal_parser.add_argument("--market-id", required=True, help="The market id.")
    journal_parser.add_argument(
        "--probability",
        type=float,
        required=True,
        help="Your forecast probability for the outcome (0..1).",
    )
    journal_parser.add_argument(
        "--outcome", default="Yes", help="The outcome you are forecasting (default: Yes)."
    )
    journal_parser.add_argument(
        "--question", default=None, help="Optional question text override."
    )
    journal_parser.add_argument(
        "--market-probability",
        type=float,
        default=None,
        help="Optional market implied probability (0..1). Fetched if omitted.",
    )
    journal_parser.add_argument(
        "--confidence",
        choices=["low", "medium", "high"],
        default="low",
        help="Your confidence level (default: low).",
    )
    journal_parser.add_argument(
        "--recommendation",
        choices=["NO_TRADE", "WATCH", "PAPER_TRADE"],
        default="NO_TRADE",
        help="Recommendation label (default: NO_TRADE).",
    )
    journal_parser.add_argument(
        "--notes", default=None, help="Free-text notes for this forecast."
    )
    journal_parser.add_argument(
        "--db-path", default=DEFAULT_DB_PATH, help="SQLite database path."
    )
    journal_parser.set_defaults(func=cmd_journal)

    # list-journal
    list_parser = subparsers.add_parser(
        "list-journal", help="List saved forecasts."
    )
    list_parser.add_argument(
        "--limit", type=int, default=None, help="Max number of forecasts to show."
    )
    list_parser.add_argument(
        "--db-path", default=DEFAULT_DB_PATH, help="SQLite database path."
    )
    list_parser.set_defaults(func=cmd_list_journal)

    # paper-buy
    paper_buy_parser = subparsers.add_parser(
        "paper-buy",
        help="Record a SIMULATED buy (no real order, risk-checked).",
    )
    paper_buy_parser.add_argument("--market-id", required=True, help="The market id.")
    paper_buy_parser.add_argument(
        "--price",
        type=float,
        required=True,
        help="Entry price per share (0.01..0.99).",
    )
    paper_buy_parser.add_argument(
        "--size",
        type=float,
        required=True,
        help="USDC notional to commit (simulated). Default max is 10 USDC.",
    )
    paper_buy_parser.add_argument(
        "--outcome", default="Yes", help="Outcome to buy (default: Yes)."
    )
    paper_buy_parser.add_argument(
        "--question", default=None, help="Optional question text for context."
    )
    paper_buy_parser.add_argument(
        "--notes", default=None, help="Free-text notes for this simulated trade."
    )
    paper_buy_parser.add_argument(
        "--db-path", default=PAPER_DB_PATH, help="Paper-trading SQLite database path."
    )
    paper_buy_parser.set_defaults(func=cmd_paper_buy)

    # paper-sell
    paper_sell_parser = subparsers.add_parser(
        "paper-sell",
        help="Record a SIMULATED sell (no real order, risk-checked).",
    )
    paper_sell_parser.add_argument("--market-id", required=True, help="The market id.")
    paper_sell_parser.add_argument(
        "--price",
        type=float,
        required=True,
        help="Exit price per share (0.01..0.99).",
    )
    paper_sell_parser.add_argument(
        "--shares",
        type=float,
        required=True,
        help="Number of shares to sell (cannot exceed shares held).",
    )
    paper_sell_parser.add_argument(
        "--outcome", default="Yes", help="Outcome to sell (default: Yes)."
    )
    paper_sell_parser.add_argument(
        "--question", default=None, help="Optional question text for context."
    )
    paper_sell_parser.add_argument(
        "--notes", default=None, help="Free-text notes for this simulated trade."
    )
    paper_sell_parser.add_argument(
        "--db-path", default=PAPER_DB_PATH, help="Paper-trading SQLite database path."
    )
    paper_sell_parser.set_defaults(func=cmd_paper_sell)

    # paper-portfolio
    paper_portfolio_parser = subparsers.add_parser(
        "paper-portfolio",
        help="Show open SIMULATED positions and exposure.",
    )
    paper_portfolio_parser.add_argument(
        "--db-path", default=PAPER_DB_PATH, help="Paper-trading SQLite database path."
    )
    paper_portfolio_parser.set_defaults(func=cmd_paper_portfolio)

    # paper-history
    paper_history_parser = subparsers.add_parser(
        "paper-history",
        help="Show the SIMULATED trade ledger.",
    )
    paper_history_parser.add_argument(
        "--limit", type=int, default=None, help="Max number of trades to show."
    )
    paper_history_parser.add_argument(
        "--db-path", default=PAPER_DB_PATH, help="Paper-trading SQLite database path."
    )
    paper_history_parser.set_defaults(func=cmd_paper_history)

    # paper-from-forecast (the research -> risk -> paper-trade bridge)
    paper_ff_parser = subparsers.add_parser(
        "paper-from-forecast",
        help="Record a SIMULATED buy from a saved forecast (requires PAPER_TRADE).",
    )
    paper_ff_parser.add_argument(
        "--forecast-id",
        type=int,
        required=True,
        help="Id of a forecast saved via the 'journal' command.",
    )
    paper_ff_parser.add_argument(
        "--price",
        type=float,
        required=True,
        help="Entry price per share (0.01..0.99).",
    )
    paper_ff_parser.add_argument(
        "--size",
        type=float,
        required=True,
        help="USDC notional to commit (simulated). Default max is 10 USDC.",
    )
    paper_ff_parser.add_argument(
        "--override",
        action="store_true",
        help=(
            "Record the paper trade even if the forecast recommendation is not "
            "PAPER_TRADE. Logged as a manual override."
        ),
    )
    paper_ff_parser.add_argument(
        "--notes", default=None, help="Additional notes (merged with forecast notes)."
    )
    paper_ff_parser.add_argument(
        "--db-path", default=PAPER_DB_PATH, help="Paper-trading SQLite database path."
    )
    paper_ff_parser.add_argument(
        "--forecast-db-path",
        default=DEFAULT_DB_PATH,
        help="Research forecast SQLite database path (source of the forecast).",
    )
    paper_ff_parser.set_defaults(func=cmd_paper_from_forecast)

    # run-paper-agent (the full deterministic workflow)
    run_parser = subparsers.add_parser(
        "run-paper-agent",
        help=(
            "Run the deterministic research workflow: fetch market -> memo -> "
            "forecast -> risk -> SIMULATED paper trade -> recorded run."
        ),
    )
    run_parser.add_argument("--market-id", required=True, help="Public market id.")
    run_parser.add_argument(
        "--price",
        type=float,
        required=True,
        help="Entry price per share (0.01..0.99) if a paper trade is attempted.",
    )
    run_parser.add_argument(
        "--size",
        type=float,
        required=True,
        help="USDC notional to commit (simulated) if a paper trade is attempted.",
    )
    run_parser.add_argument(
        "--notes", default=None, help="Optional notes recorded on the agent run."
    )
    run_parser.add_argument(
        "--db-path", default=PAPER_DB_PATH, help="Paper-trading SQLite database path."
    )
    run_parser.add_argument(
        "--forecast-db-path",
        default=DEFAULT_DB_PATH,
        help="Research forecast SQLite database path.",
    )
    run_parser.add_argument(
        "--runs-db-path",
        default=RUNS_DB_PATH,
        help="Agent-runs SQLite database path.",
    )
    run_parser.add_argument(
        "--timeout",
        type=float,
        default=None,
        help="HTTP timeout in seconds for the public API call (default: 20).",
    )
    run_parser.set_defaults(func=cmd_run_paper_agent)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
