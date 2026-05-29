"""Public-data-only research tooling for Polymarket markets.

This package powers a beginner-safe, research-only workflow:

- :mod:`agents.research.models` holds dependency-light data models (no ``httpx``).
- :mod:`agents.research.market_data` fetches public Gamma data (requires ``httpx``).
- :mod:`agents.research.ranking` scores markets with transparent, explainable reasons.
- :mod:`agents.research.memo` produces deterministic, plain-English research memos.
- :mod:`agents.research.journal` stores forecasts locally in SQLite.
- :mod:`agents.research.risk` enforces conservative paper-trading guardrails.
- :mod:`agents.research.paper_trading` records *simulated* trades in SQLite.
- :mod:`agents.research.connector` bridges forecasts to simulated paper trades.

By design this package never touches wallets, private keys, or authenticated
trading endpoints. It reads public market data only and never places trades.

Imports here are intentionally **lazy**. Importing :mod:`agents.research` (or
any of ``journal``/``ranking``/``memo``) does not import ``httpx``. Only
accessing the network layer (``MarketDataClient``) imports ``market_data``,
which is the single module that depends on ``httpx``. This lets the offline
commands (journal, ranking, memo) run on a plain interpreter with no
third-party packages installed.
"""

from typing import TYPE_CHECKING, Any

__all__ = [
    "MarketDataClient",
    "MarketDataError",
    "MarketSnapshot",
    "normalize_market",
    "RankedMarket",
    "rank_markets",
    "score_market",
    "Memo",
    "build_memo",
    "render_memo",
    "ForecastJournal",
    "ForecastValidationError",
    "RiskLimits",
    "RiskDecision",
    "DEFAULT_LIMITS",
    "evaluate_paper_buy",
    "evaluate_paper_sell",
    "PaperTradingJournal",
    "PaperTradeError",
    "PaperTrade",
    "Position",
    "PAPER_LABEL",
    "paper_trade_from_forecast",
    "ForecastPaperTradeResult",
    "ForecastNotFoundError",
    "RecommendationGateError",
]

# Map each public name to the submodule that defines it. ``market_data`` is the
# only entry that pulls in ``httpx``, and it is only imported on demand.
_LAZY_EXPORTS = {
    "MarketDataClient": "agents.research.market_data",
    "MarketDataError": "agents.research.models",
    "MarketSnapshot": "agents.research.models",
    "normalize_market": "agents.research.models",
    "RankedMarket": "agents.research.ranking",
    "rank_markets": "agents.research.ranking",
    "score_market": "agents.research.ranking",
    "Memo": "agents.research.memo",
    "build_memo": "agents.research.memo",
    "render_memo": "agents.research.memo",
    "ForecastJournal": "agents.research.journal",
    "ForecastValidationError": "agents.research.journal",
    "RiskLimits": "agents.research.risk",
    "RiskDecision": "agents.research.risk",
    "DEFAULT_LIMITS": "agents.research.risk",
    "evaluate_paper_buy": "agents.research.risk",
    "evaluate_paper_sell": "agents.research.risk",
    "PaperTradingJournal": "agents.research.paper_trading",
    "PaperTradeError": "agents.research.paper_trading",
    "PaperTrade": "agents.research.paper_trading",
    "Position": "agents.research.paper_trading",
    "PAPER_LABEL": "agents.research.paper_trading",
    "paper_trade_from_forecast": "agents.research.connector",
    "ForecastPaperTradeResult": "agents.research.connector",
    "ForecastNotFoundError": "agents.research.connector",
    "RecommendationGateError": "agents.research.connector",
}


def __getattr__(name: str) -> Any:
    """Lazily import public symbols on first access (PEP 562).

    This keeps ``import agents.research`` cheap and httpx-free.
    """
    module_path = _LAZY_EXPORTS.get(name)
    if module_path is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    import importlib

    module = importlib.import_module(module_path)
    value = getattr(module, name)
    globals()[name] = value  # Cache so subsequent access skips __getattr__.
    return value


def __dir__() -> list[str]:
    return sorted(list(globals().keys()) + __all__)


if TYPE_CHECKING:
    # Help type checkers and IDEs without triggering runtime imports.
    from agents.research.journal import ForecastJournal, ForecastValidationError
    from agents.research.market_data import MarketDataClient
    from agents.research.memo import Memo, build_memo, render_memo
    from agents.research.models import (
        MarketDataError,
        MarketSnapshot,
        normalize_market,
    )
    from agents.research.paper_trading import (
        PAPER_LABEL,
        PaperTrade,
        PaperTradeError,
        PaperTradingJournal,
        Position,
    )
    from agents.research.connector import (
        ForecastNotFoundError,
        ForecastPaperTradeResult,
        RecommendationGateError,
        paper_trade_from_forecast,
    )
    from agents.research.ranking import RankedMarket, rank_markets, score_market
    from agents.research.risk import (
        DEFAULT_LIMITS,
        RiskDecision,
        RiskLimits,
        evaluate_paper_buy,
        evaluate_paper_sell,
    )
