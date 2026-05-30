"""Tests for the read-only local operator UI payloads."""

import os
import sys
import tempfile
import unittest

_REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from agents.research.connector import paper_trade_from_forecast
from agents.research.journal import ForecastJournal
from agents.research.paper_trading import PaperTradingJournal
from agents.research.runs import AgentRunsJournal
from scripts.python.operator_ui import (
    OperatorApp,
    build_payload,
    forecast_to_dict,
    position_to_dict,
    run_to_dict,
    trade_to_dict,
)


class TestOperatorUiSerialization(unittest.TestCase):
    def test_forecast_payload_contains_expected_fields(self):
        with ForecastJournal(":memory:") as journal:
            record = journal.add_forecast(
                market_id="m1",
                forecast_probability=0.57,
                question="Will it happen?",
                market_probability=0.5,
                confidence="medium",
                recommendation="PAPER_TRADE",
                notes="thesis",
            )
            payload = forecast_to_dict(record)
        self.assertEqual(payload["id"], 1)
        self.assertEqual(payload["market_id"], "m1")
        self.assertEqual(payload["forecast_probability"], 0.57)
        self.assertEqual(payload["recommendation"], "PAPER_TRADE")

    def test_position_payload_contains_exposure(self):
        with PaperTradingJournal(":memory:") as journal:
            journal.record_buy(market_id="m1", price=0.5, size_usdc=10.0)
            position = journal.get_position("m1")
            payload = position_to_dict(position)
        self.assertEqual(payload["market_id"], "m1")
        self.assertEqual(payload["shares"], 20.0)
        self.assertEqual(payload["average_price"], 0.5)
        self.assertEqual(payload["exposure"], 10.0)

    def test_trade_payload_includes_provenance(self):
        with PaperTradingJournal(":memory:") as journal:
            trade = journal.record_buy(
                market_id="m1",
                price=0.5,
                size_usdc=10.0,
                source="manual",
            )
            payload = trade_to_dict(trade)
        self.assertEqual(payload["source"], "manual")
        self.assertIsNone(payload["forecast_id"])
        self.assertFalse(payload["from_agent"])


class TestOperatorUiPayloads(unittest.TestCase):
    def test_summary_and_history_payloads_read_local_databases(self):
        with tempfile.TemporaryDirectory() as tmp:
            forecast_db = os.path.join(tmp, "forecasts.sqlite3")
            paper_db = os.path.join(tmp, "paper.sqlite3")
            runs_db = os.path.join(tmp, "runs.sqlite3")

            with ForecastJournal(forecast_db) as forecasts:
                forecast = forecasts.add_forecast(
                    market_id="m1",
                    forecast_probability=0.57,
                    question="Will it happen?",
                    confidence="medium",
                    recommendation="PAPER_TRADE",
                    notes="agent read",
                )

            app = OperatorApp(
                forecast_db_path=forecast_db,
                paper_db_path=paper_db,
                runs_db_path=runs_db,
            )
            with ForecastJournal(forecast_db) as forecasts, PaperTradingJournal(
                paper_db
            ) as paper:
                paper_trade_from_forecast(
                    forecast_id=forecast.id,
                    price=0.57,
                    size_usdc=10.0,
                    forecast_journal=forecasts,
                    paper_journal=paper,
                )

            summary = build_payload("/api/summary", {}, app)
            self.assertEqual(summary["forecast_count"], 1)
            self.assertEqual(summary["open_position_count"], 1)
            self.assertEqual(summary["trade_count"], 1)
            self.assertEqual(summary["agent_trade_count"], 1)
            self.assertEqual(summary["run_count"], 0)

            history = build_payload("/api/paper/history", {"limit": ["10"]}, app)
            self.assertEqual(len(history["trades"]), 1)
            self.assertEqual(history["trades"][0]["forecast_id"], forecast.id)
            self.assertTrue(history["trades"][0]["from_agent"])

            portfolio = build_payload("/api/paper/portfolio", {}, app)
            self.assertEqual(len(portfolio["positions"]), 1)
            self.assertEqual(portfolio["total_open_exposure"], 10.0)

    def test_unknown_api_path_raises_key_error(self):
        app = OperatorApp(":memory:", ":memory:", ":memory:")
        with self.assertRaises(KeyError):
            build_payload("/api/nope", {}, app)


class TestOperatorUiRunsApi(unittest.TestCase):
    def test_runs_payload_reflects_journal(self):
        with tempfile.TemporaryDirectory() as tmp:
            forecast_db = os.path.join(tmp, "f.sqlite3")
            paper_db = os.path.join(tmp, "p.sqlite3")
            runs_db = os.path.join(tmp, "r.sqlite3")
            with AgentRunsJournal(runs_db) as runs:
                run = runs.start_run(market_id="m1", price=0.5, size_usdc=10.0)
                runs.fail_run(run.id, error="kaboom")

            app = OperatorApp(forecast_db, paper_db, runs_db)
            payload = build_payload("/api/runs", {"limit": ["10"]}, app)
            self.assertEqual(len(payload["runs"]), 1)
            self.assertEqual(payload["runs"][0]["status"], "FAILED")
            self.assertEqual(payload["runs"][0]["error"], "kaboom")
            self.assertEqual(payload["runs"][0]["market_id"], "m1")

            summary = build_payload("/api/summary", {}, app)
            self.assertEqual(summary["run_count"], 1)
            self.assertEqual(summary["failed_run_count"], 1)
            self.assertEqual(summary["completed_run_count"], 0)

    def test_run_to_dict_includes_payloads(self):
        with AgentRunsJournal(":memory:") as runs:
            run = runs.start_run(market_id="m1", price=0.5, size_usdc=10.0)
            done = runs.complete_run(
                run.id,
                forecast_id=3,
                paper_trade_id=4,
                memo={"recommendation": "PAPER_TRADE"},
                risk={"rejected": False},
                recommendation="PAPER_TRADE",
            )
            payload = run_to_dict(done)
        self.assertEqual(payload["status"], "COMPLETED")
        self.assertEqual(payload["forecast_id"], 3)
        self.assertEqual(payload["paper_trade_id"], 4)
        self.assertIn("PAPER_TRADE", payload["memo"])


if __name__ == "__main__":
    unittest.main()
