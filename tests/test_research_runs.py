"""Tests for the SQLite agent-run audit log."""

import json
import os
import sys
import unittest

_REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from agents.research.runs import (
    STATUS_COMPLETED,
    STATUS_FAILED,
    STATUS_SKIPPED,
    STATUS_STARTED,
    AgentRunError,
    AgentRunsJournal,
)


class TestAgentRunsJournal(unittest.TestCase):
    def setUp(self):
        self.runs = AgentRunsJournal(":memory:")

    def tearDown(self):
        self.runs.close()

    def test_start_run_records_started_status(self):
        run = self.runs.start_run(market_id="m1", price=0.5, size_usdc=10.0)
        self.assertEqual(run.status, STATUS_STARTED)
        self.assertEqual(run.market_id, "m1")
        self.assertEqual(run.price, 0.5)
        self.assertEqual(run.size_usdc, 10.0)
        self.assertIsNone(run.completed_at)
        self.assertIsNone(run.error)

    def test_start_run_requires_market_id(self):
        with self.assertRaises(AgentRunError):
            self.runs.start_run(market_id="")

    def test_complete_run_serializes_memo_and_risk(self):
        run = self.runs.start_run(market_id="m1", price=0.5, size_usdc=10.0)
        memo = {"recommendation": "PAPER_TRADE", "confidence": "medium"}
        risk = {"rejected": False, "reasons": []}
        finalized = self.runs.complete_run(
            run.id,
            forecast_id=7,
            paper_trade_id=42,
            memo=memo,
            risk=risk,
            recommendation="PAPER_TRADE",
        )
        self.assertEqual(finalized.status, STATUS_COMPLETED)
        self.assertEqual(finalized.forecast_id, 7)
        self.assertEqual(finalized.paper_trade_id, 42)
        self.assertEqual(finalized.recommendation, "PAPER_TRADE")
        self.assertIsNotNone(finalized.completed_at)
        self.assertEqual(json.loads(finalized.memo)["recommendation"], "PAPER_TRADE")
        self.assertEqual(json.loads(finalized.risk)["rejected"], False)

    def test_skip_run_requires_reason(self):
        run = self.runs.start_run(market_id="m1")
        with self.assertRaises(AgentRunError):
            self.runs.skip_run(run.id, reason="")

    def test_skip_run_records_reason_in_notes(self):
        run = self.runs.start_run(market_id="m1")
        finalized = self.runs.skip_run(
            run.id, reason="recommendation=NO_TRADE", recommendation="NO_TRADE"
        )
        self.assertEqual(finalized.status, STATUS_SKIPPED)
        self.assertEqual(finalized.recommendation, "NO_TRADE")
        self.assertIn("NO_TRADE", finalized.notes or "")

    def test_fail_run_requires_error(self):
        run = self.runs.start_run(market_id="m1")
        with self.assertRaises(AgentRunError):
            self.runs.fail_run(run.id, error="")

    def test_fail_run_records_error(self):
        run = self.runs.start_run(market_id="m1")
        finalized = self.runs.fail_run(run.id, error="boom")
        self.assertEqual(finalized.status, STATUS_FAILED)
        self.assertEqual(finalized.error, "boom")
        self.assertIsNotNone(finalized.completed_at)

    def test_cannot_finalize_twice(self):
        run = self.runs.start_run(market_id="m1")
        self.runs.complete_run(run.id)
        with self.assertRaises(AgentRunError):
            self.runs.complete_run(run.id)
        with self.assertRaises(AgentRunError):
            self.runs.fail_run(run.id, error="late")

    def test_finalize_unknown_run_raises(self):
        with self.assertRaises(AgentRunError):
            self.runs.complete_run(999)

    def test_list_runs_orders_most_recent_first(self):
        first = self.runs.start_run(market_id="a")
        second = self.runs.start_run(market_id="b")
        self.runs.complete_run(first.id)
        self.runs.fail_run(second.id, error="x")
        rows = self.runs.list_runs()
        self.assertEqual([r.id for r in rows], [second.id, first.id])

    def test_list_runs_respects_limit(self):
        for index in range(5):
            self.runs.start_run(market_id=f"m{index}")
        self.assertEqual(len(self.runs.list_runs(limit=2)), 2)

    def test_get_run_returns_none_for_missing(self):
        self.assertIsNone(self.runs.get_run(404))

    def test_invalid_price_rejected(self):
        with self.assertRaises(AgentRunError):
            self.runs.start_run(market_id="m1", price="not-a-number")

    def test_non_json_payload_rejected(self):
        run = self.runs.start_run(market_id="m1")
        with self.assertRaises(AgentRunError):
            self.runs.complete_run(run.id, memo=object())


if __name__ == "__main__":
    unittest.main()
