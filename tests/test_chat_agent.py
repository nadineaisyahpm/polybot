"""Tests for the local chat-agent dispatcher.

These tests deliberately do not import ``httpx`` so we can verify the offline
intents work without it. Network intents are exercised via an injected stub
``market_client_factory``.
"""

import os
import sys
import tempfile
import unittest

_REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from agents.chat.agent import ChatAgent
from agents.chat.intents import Intent
from agents.research.journal import ForecastJournal
from agents.research.models import MarketDataError, MarketSnapshot
from agents.research.paper_trading import PaperTradingJournal


def _market(
    market_id: str = "540819",
    question: str = "Will it happen?",
    outcomes=("Yes", "No"),
    prices=(0.57, 0.43),
    liquidity: float = 100_000.0,
    volume: float = 100_000.0,
    spread: float = 0.02,
) -> MarketSnapshot:
    return MarketSnapshot(
        id=market_id,
        question=question,
        description="desc",
        outcomes=list(outcomes),
        outcome_prices=list(prices),
        liquidity=liquidity,
        volume=volume,
        spread=spread,
    )


class StubMarketClient:
    def __init__(self, market=None, markets=None, error=None):
        self._market = market
        self._markets = markets or []
        self._error = error

    def fetch_market(self, market_id: str) -> MarketSnapshot:
        if self._error is not None:
            raise self._error
        if self._market is not None:
            return self._market
        for m in self._markets:
            if m.id == str(market_id):
                return m
        raise MarketDataError(f"No market {market_id}")

    def fetch_active_markets(self, limit: int = 20, offset: int = 0):
        if self._error is not None:
            raise self._error
        return list(self._markets)[:limit]


class ChatAgentTestBase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.forecast_db = os.path.join(self.tmp.name, "f.sqlite3")
        self.paper_db = os.path.join(self.tmp.name, "p.sqlite3")
        self.runs_db = os.path.join(self.tmp.name, "r.sqlite3")

    def tearDown(self):
        self.tmp.cleanup()

    def make_agent(self, market_client=None) -> ChatAgent:
        factory = (lambda: market_client) if market_client is not None else None
        return ChatAgent(
            forecast_db_path=self.forecast_db,
            paper_db_path=self.paper_db,
            runs_db_path=self.runs_db,
            market_client_factory=factory,
        )


class TestOfflineIntents(ChatAgentTestBase):
    def test_help_lists_supported_commands(self):
        agent = self.make_agent()
        response = agent.handle("help")
        self.assertEqual(response.intent, Intent.HELP)
        self.assertIn("PAPER/SIMULATED", response.text)
        self.assertIn("show forecasts", response.text)

    def test_unknown_message_explains_supported_commands(self):
        agent = self.make_agent()
        response = agent.handle("please buy bitcoin")
        self.assertEqual(response.intent, Intent.UNKNOWN)
        self.assertFalse(response.ok)
        self.assertIn("help", response.text.lower())

    def test_show_forecasts_empty(self):
        agent = self.make_agent()
        response = agent.handle("show forecasts")
        self.assertEqual(response.intent, Intent.SHOW_FORECASTS)
        self.assertEqual(response.data["forecasts"], [])
        self.assertIn("No forecasts", response.text)

    def test_show_forecasts_with_data(self):
        with ForecastJournal(self.forecast_db) as fj:
            fj.add_forecast(
                market_id="m1",
                forecast_probability=0.57,
                question="q",
                confidence="medium",
                recommendation="PAPER_TRADE",
            )
        agent = self.make_agent()
        response = agent.handle("show forecasts")
        self.assertEqual(len(response.data["forecasts"]), 1)
        self.assertIn("PAPER_TRADE", response.text)

    def test_show_portfolio_empty(self):
        agent = self.make_agent()
        response = agent.handle("show portfolio")
        self.assertEqual(response.intent, Intent.SHOW_PORTFOLIO)
        self.assertIn("PAPER/SIMULATED", response.text)
        self.assertEqual(response.data["positions"], [])

    def test_show_history_empty(self):
        agent = self.make_agent()
        response = agent.handle("show paper history")
        self.assertEqual(response.intent, Intent.SHOW_HISTORY)
        self.assertIn("PAPER/SIMULATED", response.text)

    def test_show_runs_empty(self):
        agent = self.make_agent()
        response = agent.handle("show agent runs")
        self.assertEqual(response.intent, Intent.SHOW_RUNS)
        self.assertIn("No agent runs", response.text)


class TestNetworkIntents(ChatAgentTestBase):
    def test_scan_markets_with_stub(self):
        client = StubMarketClient(markets=[_market("a"), _market("b", question="Q2")])
        agent = self.make_agent(market_client=client)
        response = agent.handle("scan markets")
        self.assertEqual(response.intent, Intent.SCAN_MARKETS)
        self.assertTrue(response.ok)
        self.assertGreaterEqual(len(response.data["markets"]), 1)

    def test_scan_markets_handles_market_data_error(self):
        client = StubMarketClient(error=MarketDataError("network down"))
        agent = self.make_agent(market_client=client)
        response = agent.handle("scan markets")
        self.assertFalse(response.ok)
        self.assertIn("network down", response.text)

    def test_research_market_with_stub(self):
        client = StubMarketClient(market=_market("540819"))
        agent = self.make_agent(market_client=client)
        response = agent.handle("research market 540819")
        self.assertEqual(response.intent, Intent.RESEARCH_MARKET)
        self.assertIn("Market:", response.text)
        self.assertIn("540819", response.text)

    def test_research_market_missing_id(self):
        agent = self.make_agent(market_client=StubMarketClient(market=_market()))
        response = agent.handle("research")
        self.assertEqual(response.intent, Intent.RESEARCH_MARKET)
        self.assertFalse(response.ok)
        self.assertIn("market id", response.text.lower())

    def test_run_paper_agent_completes(self):
        client = StubMarketClient(market=_market("540819"))
        agent = self.make_agent(market_client=client)
        response = agent.handle(
            "run paper agent for market 540819 at price 0.57 size 10"
        )
        self.assertEqual(response.intent, Intent.RUN_PAPER_AGENT)
        self.assertEqual(response.data["status"], "COMPLETED")
        self.assertIsNotNone(response.data["paper_trade_id"])
        self.assertIn("PAPER/SIMULATED", response.text)
        # Verify side effects landed in the journals.
        with PaperTradingJournal(self.paper_db) as pj:
            self.assertEqual(len(pj.history()), 1)

    def test_run_paper_agent_missing_values(self):
        agent = self.make_agent(market_client=StubMarketClient(market=_market()))
        response = agent.handle("run paper agent for market 540819")
        self.assertEqual(response.intent, Intent.RUN_PAPER_AGENT)
        self.assertFalse(response.ok)
        self.assertIn("price", response.text)
        self.assertIn("size", response.text)
        self.assertIn("price", response.data["missing"])

    def test_run_paper_agent_records_failed_run_on_network_error(self):
        client = StubMarketClient(error=MarketDataError("boom"))
        agent = self.make_agent(market_client=client)
        response = agent.handle(
            "run paper agent for market 540819 at price 0.57 size 10"
        )
        self.assertEqual(response.intent, Intent.RUN_PAPER_AGENT)
        self.assertEqual(response.data["status"], "FAILED")
        self.assertFalse(response.ok)


class TestOfflineImportsHaveNoHttpx(unittest.TestCase):
    """The chat layer must not import httpx as a side effect of offline use."""

    def test_offline_intents_do_not_require_httpx(self):
        import importlib

        # If httpx happens to be installed in the test env, this still passes;
        # the meaningful check is that nothing under agents.chat references it.
        chat_mod = importlib.import_module("agents.chat.agent")
        with open(chat_mod.__file__, "r", encoding="utf-8") as fh:
            source = fh.read()
        self.assertNotIn("import httpx", source)
        intents_mod = importlib.import_module("agents.chat.intents")
        with open(intents_mod.__file__, "r", encoding="utf-8") as fh:
            self.assertNotIn("import httpx", fh.read())


class TestRepl(ChatAgentTestBase):
    def test_repl_exits_cleanly(self):
        from scripts.python.chat_agent import run_repl

        agent = self.make_agent()
        messages = iter(["help", "exit"])
        out: list[str] = []

        def fake_input(_prompt):
            return next(messages)

        rc = run_repl(agent, input_fn=fake_input, output_fn=out.append)
        self.assertEqual(rc, 0)
        joined = "\n".join(out)
        self.assertIn("Polybot chat", joined)
        self.assertIn("agent>", joined)
        self.assertIn("bye", joined)

    def test_repl_handles_eof(self):
        from scripts.python.chat_agent import run_repl

        agent = self.make_agent()

        def fake_input(_prompt):
            raise EOFError()

        out: list[str] = []
        rc = run_repl(agent, input_fn=fake_input, output_fn=out.append)
        self.assertEqual(rc, 0)


if __name__ == "__main__":
    unittest.main()
