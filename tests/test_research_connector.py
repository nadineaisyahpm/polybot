"""Tests for the forecast -> risk -> paper-trade connector.

Standard-library ``unittest`` so these run under both ``python -m pytest`` and
``python -m unittest`` with no third-party dependencies (no httpx needed).
"""

import os
import sys
import unittest

_REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from agents.research.connector import (
    ForecastNotFoundError,
    RecommendationGateError,
    paper_trade_from_forecast,
)
from agents.research.journal import ForecastJournal
from agents.research.paper_trading import (
    SOURCE_AGENT,
    SOURCE_MANUAL_OVERRIDE,
    PaperTradeError,
    PaperTradingJournal,
)


class ConnectorTestBase(unittest.TestCase):
    def setUp(self):
        self.fj = ForecastJournal(":memory:")
        self.pj = PaperTradingJournal(":memory:")

    def tearDown(self):
        self.fj.close()
        self.pj.close()

    def _save_forecast(self, recommendation="PAPER_TRADE", **overrides):
        kwargs = dict(
            market_id="m1",
            forecast_probability=0.57,
            question="Will it happen?",
            outcome="Yes",
            confidence="medium",
            recommendation=recommendation,
            notes="thesis here",
        )
        kwargs.update(overrides)
        return self.fj.add_forecast(**kwargs)


class TestPaperTradeFromForecast(ConnectorTestBase):
    def test_paper_trade_recommendation_creates_agent_trade(self):
        forecast = self._save_forecast(recommendation="PAPER_TRADE")
        result = paper_trade_from_forecast(
            forecast_id=forecast.id,
            price=0.57,
            size_usdc=10.0,
            forecast_journal=self.fj,
            paper_journal=self.pj,
        )
        self.assertFalse(result.overridden)
        self.assertEqual(result.trade.source, SOURCE_AGENT)
        self.assertTrue(result.trade.from_agent)
        self.assertEqual(result.trade.forecast_id, forecast.id)
        # Metadata copied from the forecast.
        self.assertEqual(result.trade.market_id, "m1")
        self.assertEqual(result.trade.outcome, "Yes")
        self.assertEqual(result.trade.question, "Will it happen?")
        self.assertEqual(result.trade.confidence, "medium")

    def test_non_paper_recommendation_blocked_without_override(self):
        forecast = self._save_forecast(recommendation="NO_TRADE")
        with self.assertRaises(RecommendationGateError):
            paper_trade_from_forecast(
                forecast_id=forecast.id,
                price=0.57,
                size_usdc=10.0,
                forecast_journal=self.fj,
                paper_journal=self.pj,
            )
        # Nothing recorded.
        self.assertEqual(self.pj.history(), [])

    def test_non_paper_recommendation_allowed_with_override(self):
        forecast = self._save_forecast(recommendation="WATCH")
        result = paper_trade_from_forecast(
            forecast_id=forecast.id,
            price=0.57,
            size_usdc=10.0,
            forecast_journal=self.fj,
            paper_journal=self.pj,
            override=True,
        )
        self.assertTrue(result.overridden)
        self.assertEqual(result.trade.source, SOURCE_MANUAL_OVERRIDE)
        self.assertFalse(result.trade.from_agent)

    def test_missing_forecast_raises(self):
        with self.assertRaises(ForecastNotFoundError):
            paper_trade_from_forecast(
                forecast_id=999,
                price=0.5,
                size_usdc=10.0,
                forecast_journal=self.fj,
                paper_journal=self.pj,
            )

    def test_risk_guardrails_still_apply(self):
        forecast = self._save_forecast(recommendation="PAPER_TRADE")
        # 20 USDC exceeds the 10 USDC max trade size; must be rejected.
        with self.assertRaises(PaperTradeError) as ctx:
            paper_trade_from_forecast(
                forecast_id=forecast.id,
                price=0.5,
                size_usdc=20.0,
                forecast_journal=self.fj,
                paper_journal=self.pj,
            )
        self.assertTrue(any("max paper trade size" in r for r in ctx.exception.reasons))
        self.assertEqual(self.pj.history(), [])

    def test_notes_merge_includes_forecast_context(self):
        forecast = self._save_forecast(recommendation="PAPER_TRADE", notes="liquidity ok")
        result = paper_trade_from_forecast(
            forecast_id=forecast.id,
            price=0.57,
            size_usdc=10.0,
            forecast_journal=self.fj,
            paper_journal=self.pj,
            notes="sizing small",
        )
        notes = result.trade.notes or ""
        self.assertIn(f"from forecast #{forecast.id}", notes)
        self.assertIn("liquidity ok", notes)
        self.assertIn("sizing small", notes)

    def test_override_notes_flag_marked(self):
        forecast = self._save_forecast(recommendation="NO_TRADE")
        result = paper_trade_from_forecast(
            forecast_id=forecast.id,
            price=0.57,
            size_usdc=10.0,
            forecast_journal=self.fj,
            paper_journal=self.pj,
            override=True,
        )
        self.assertIn("manual override", (result.trade.notes or ""))

    def test_trade_persists_in_ledger_with_provenance(self):
        forecast = self._save_forecast(recommendation="PAPER_TRADE")
        paper_trade_from_forecast(
            forecast_id=forecast.id,
            price=0.57,
            size_usdc=10.0,
            forecast_journal=self.fj,
            paper_journal=self.pj,
        )
        history = self.pj.history()
        self.assertEqual(len(history), 1)
        self.assertEqual(history[0].source, SOURCE_AGENT)
        self.assertEqual(history[0].forecast_id, forecast.id)


class TestSchemaMigration(unittest.TestCase):
    def test_old_schema_db_is_migrated(self):
        import sqlite3
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            db_path = os.path.join(tmp, "old_paper.sqlite3")
            # Create a Milestone-2-era table WITHOUT provenance columns.
            conn = sqlite3.connect(db_path)
            conn.execute(
                """
                CREATE TABLE paper_trades (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    market_id TEXT NOT NULL,
                    question TEXT,
                    outcome TEXT NOT NULL DEFAULT 'Yes',
                    side TEXT NOT NULL,
                    price REAL NOT NULL,
                    shares REAL NOT NULL,
                    size_usdc REAL NOT NULL,
                    realized_pnl REAL NOT NULL DEFAULT 0,
                    notes TEXT,
                    simulated INTEGER NOT NULL DEFAULT 1,
                    created_at TEXT NOT NULL
                )
                """
            )
            conn.execute(
                """
                INSERT INTO paper_trades (
                    market_id, question, outcome, side, price, shares,
                    size_usdc, realized_pnl, notes, simulated, created_at
                ) VALUES ('legacy', 'old?', 'Yes', 'BUY', 0.5, 20, 10, 0, 'old', 1,
                          '2024-01-01T00:00:00+00:00')
                """
            )
            conn.commit()
            conn.close()

            # Opening with the journal should migrate and read the legacy row.
            with PaperTradingJournal(db_path) as journal:
                history = journal.history()
                self.assertEqual(len(history), 1)
                self.assertEqual(history[0].market_id, "legacy")
                # Legacy rows default to manual provenance.
                self.assertEqual(history[0].source, "manual")
                self.assertIsNone(history[0].forecast_id)
                # New writes with provenance succeed on the migrated DB.
                trade = journal.record_buy(
                    market_id="new", price=0.5, size_usdc=5.0, source="manual"
                )
                self.assertEqual(trade.source, "manual")


if __name__ == "__main__":
    unittest.main()
