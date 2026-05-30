"""Tests for the deterministic single-agent research workflow."""

import json
import os
import sys
import unittest

_REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from agents.research.journal import ForecastJournal
from agents.research.models import MarketDataError, MarketSnapshot
from agents.research.paper_trading import PaperTradingJournal
from agents.research.runs import (
    STATUS_COMPLETED,
    STATUS_FAILED,
    STATUS_SKIPPED,
    AgentRunsJournal,
)
from agents.research.workflow import run_paper_agent


def _market(
    market_id: str = "m1",
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


class StubClient:
    def __init__(self, market: MarketSnapshot | None = None, error: Exception | None = None):
        self._market = market
        self._error = error

    def fetch_market(self, market_id: str) -> MarketSnapshot:
        if self._error is not None:
            raise self._error
        assert self._market is not None
        return self._market


class WorkflowTestBase(unittest.TestCase):
    def setUp(self):
        self.fj = ForecastJournal(":memory:")
        self.pj = PaperTradingJournal(":memory:")
        self.rj = AgentRunsJournal(":memory:")

    def tearDown(self):
        self.fj.close()
        self.pj.close()
        self.rj.close()


class TestPaperTradePath(WorkflowTestBase):
    def test_paper_trade_recommendation_completes_run(self):
        client = StubClient(market=_market())
        result = run_paper_agent(
            market_id="m1",
            price=0.57,
            size_usdc=10.0,
            market_client=client,
            forecast_journal=self.fj,
            paper_journal=self.pj,
            runs_journal=self.rj,
        )
        self.assertEqual(result.status, STATUS_COMPLETED)
        self.assertIsNotNone(result.forecast_id)
        self.assertIsNotNone(result.paper_trade_id)
        self.assertTrue(result.is_paper_trade)
        # The run row is COMPLETED and links to both forecast and paper trade.
        run = self.rj.get_run(result.run.id)
        self.assertEqual(run.status, STATUS_COMPLETED)
        self.assertEqual(run.forecast_id, result.forecast_id)
        self.assertEqual(run.paper_trade_id, result.paper_trade_id)
        self.assertEqual(run.recommendation, "PAPER_TRADE")
        memo_payload = json.loads(run.memo)
        self.assertEqual(memo_payload["recommendation"], "PAPER_TRADE")
        risk_payload = json.loads(run.risk)
        self.assertFalse(risk_payload["rejected"])
        # One forecast + one paper trade exist in their journals.
        self.assertEqual(len(self.fj.list_forecasts()), 1)
        self.assertEqual(len(self.pj.history()), 1)


class TestSkipPath(WorkflowTestBase):
    def test_watch_recommendation_skips_without_trade(self):
        # liquidity above WATCH but below PAPER threshold -> WATCH
        market = _market(liquidity=20_000.0, volume=20_000.0)
        client = StubClient(market=market)
        result = run_paper_agent(
            market_id="m1",
            price=0.5,
            size_usdc=5.0,
            market_client=client,
            forecast_journal=self.fj,
            paper_journal=self.pj,
            runs_journal=self.rj,
        )
        self.assertEqual(result.status, STATUS_SKIPPED)
        self.assertIsNotNone(result.forecast_id)
        self.assertIsNone(result.paper_trade_id)
        self.assertIn("WATCH", result.skipped_reason or "")
        # No paper trade recorded.
        self.assertEqual(self.pj.history(), [])

    def test_no_trade_recommendation_skips(self):
        # No outcomes -> NO_TRADE, no forecast probability
        market = _market(outcomes=(), prices=())
        client = StubClient(market=market)
        result = run_paper_agent(
            market_id="m1",
            price=0.5,
            size_usdc=5.0,
            market_client=client,
            forecast_journal=self.fj,
            paper_journal=self.pj,
            runs_journal=self.rj,
        )
        self.assertEqual(result.status, STATUS_SKIPPED)
        self.assertIsNone(result.paper_trade_id)
        # NO_TRADE without usable prices => no forecast row written.
        self.assertIsNone(result.forecast_id)
        self.assertEqual(self.fj.list_forecasts(), [])


class TestFailurePaths(WorkflowTestBase):
    def test_market_data_error_marks_run_failed(self):
        client = StubClient(error=MarketDataError("network down"))
        result = run_paper_agent(
            market_id="m1",
            price=0.5,
            size_usdc=10.0,
            market_client=client,
            forecast_journal=self.fj,
            paper_journal=self.pj,
            runs_journal=self.rj,
        )
        self.assertEqual(result.status, STATUS_FAILED)
        self.assertIn("network down", result.error or "")
        run = self.rj.get_run(result.run.id)
        self.assertEqual(run.status, STATUS_FAILED)
        self.assertIn("MarketDataError", run.error or "")

    def test_risk_rejection_marks_run_failed(self):
        client = StubClient(market=_market())
        # 100 USDC trade exceeds the 10 USDC max paper trade size -> rejected.
        result = run_paper_agent(
            market_id="m1",
            price=0.5,
            size_usdc=100.0,
            market_client=client,
            forecast_journal=self.fj,
            paper_journal=self.pj,
            runs_journal=self.rj,
        )
        self.assertEqual(result.status, STATUS_FAILED)
        self.assertIsNotNone(result.forecast_id)
        self.assertIsNone(result.paper_trade_id)
        run = self.rj.get_run(result.run.id)
        risk_payload = json.loads(run.risk)
        self.assertTrue(risk_payload["rejected"])
        self.assertTrue(any("max paper trade size" in r for r in risk_payload["reasons"]))

    def test_unexpected_exception_is_captured(self):
        client = StubClient(error=RuntimeError("oops"))
        result = run_paper_agent(
            market_id="m1",
            price=0.5,
            size_usdc=10.0,
            market_client=client,
            forecast_journal=self.fj,
            paper_journal=self.pj,
            runs_journal=self.rj,
        )
        self.assertEqual(result.status, STATUS_FAILED)
        self.assertIn("oops", result.error or "")


class TestAlwaysFinalizes(WorkflowTestBase):
    def test_every_invocation_creates_one_terminal_run(self):
        client_ok = StubClient(market=_market())
        run_paper_agent(
            market_id="m1",
            price=0.57,
            size_usdc=10.0,
            market_client=client_ok,
            forecast_journal=self.fj,
            paper_journal=self.pj,
            runs_journal=self.rj,
        )
        client_bad = StubClient(error=MarketDataError("nope"))
        run_paper_agent(
            market_id="m2",
            price=0.5,
            size_usdc=10.0,
            market_client=client_bad,
            forecast_journal=self.fj,
            paper_journal=self.pj,
            runs_journal=self.rj,
        )
        runs = self.rj.list_runs()
        self.assertEqual(len(runs), 2)
        self.assertTrue(all(r.status != "STARTED" for r in runs))


if __name__ == "__main__":
    unittest.main()
