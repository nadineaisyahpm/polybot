"""Tests for the SQLite forecast journal and deterministic memos.

Uses an in-memory SQLite database so tests are fast and leave no files behind.
Written with the standard-library ``unittest`` so it runs under both
``python -m pytest`` and ``python -m unittest``.
"""

import os
import sys
import unittest

# Make the repo root importable regardless of the working directory.
_REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from agents.research.journal import ForecastJournal, ForecastValidationError
from agents.research.models import MarketSnapshot
from agents.research.memo import build_memo, render_memo


class TestForecastJournal(unittest.TestCase):
    def setUp(self):
        # Fresh in-memory DB per test.
        self.journal = ForecastJournal(":memory:")

    def tearDown(self):
        self.journal.close()

    def test_add_and_retrieve_forecast(self):
        record = self.journal.add_forecast(
            market_id="123",
            forecast_probability=0.57,
            question="Will it happen?",
            notes="initial read",
        )
        self.assertEqual(record.id, 1)
        self.assertEqual(record.market_id, "123")
        self.assertAlmostEqual(record.forecast_probability, 0.57)
        # Defaults reflect the beginner-safe posture.
        self.assertEqual(record.outcome, "Yes")
        self.assertEqual(record.confidence, "low")
        self.assertEqual(record.recommendation, "NO_TRADE")
        self.assertIsNotNone(record.created_at)

        fetched = self.journal.get_forecast(1)
        self.assertIsNotNone(fetched)
        self.assertEqual(fetched.market_id, "123")

    def test_list_forecasts_most_recent_first(self):
        self.journal.add_forecast(market_id="a", forecast_probability=0.1)
        self.journal.add_forecast(market_id="b", forecast_probability=0.2)
        self.journal.add_forecast(market_id="c", forecast_probability=0.3)
        records = self.journal.list_forecasts()
        self.assertEqual([r.market_id for r in records], ["c", "b", "a"])

    def test_list_forecasts_respects_limit(self):
        for i in range(5):
            self.journal.add_forecast(market_id=str(i), forecast_probability=0.5)
        records = self.journal.list_forecasts(limit=2)
        self.assertEqual(len(records), 2)

    def test_rejects_out_of_range_probability(self):
        with self.assertRaises(ForecastValidationError):
            self.journal.add_forecast(market_id="x", forecast_probability=1.5)
        with self.assertRaises(ForecastValidationError):
            self.journal.add_forecast(market_id="x", forecast_probability=-0.1)

    def test_rejects_invalid_confidence(self):
        with self.assertRaises(ForecastValidationError):
            self.journal.add_forecast(
                market_id="x", forecast_probability=0.5, confidence="super-high"
            )

    def test_rejects_invalid_recommendation(self):
        with self.assertRaises(ForecastValidationError):
            self.journal.add_forecast(
                market_id="x",
                forecast_probability=0.5,
                recommendation="BUY_NOW",
            )

    def test_rejects_blank_market_id(self):
        with self.assertRaises(ForecastValidationError):
            self.journal.add_forecast(market_id="  ", forecast_probability=0.5)

    def test_market_probability_validated_when_present(self):
        with self.assertRaises(ForecastValidationError):
            self.journal.add_forecast(
                market_id="x",
                forecast_probability=0.5,
                market_probability=2.0,
            )

    def test_accepts_valid_full_record(self):
        record = self.journal.add_forecast(
            market_id="42",
            forecast_probability=0.62,
            question="Will the bill pass?",
            outcome="Yes",
            market_probability=0.6,
            confidence="medium",
            recommendation="WATCH",
            notes="liquidity is decent",
        )
        self.assertEqual(record.confidence, "medium")
        self.assertEqual(record.recommendation, "WATCH")
        self.assertAlmostEqual(record.market_probability, 0.6)

    def test_persists_across_connections(self):
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            db_path = os.path.join(tmp, "journal.sqlite3")
            with ForecastJournal(db_path) as j1:
                j1.add_forecast(market_id="persist", forecast_probability=0.5)
            with ForecastJournal(db_path) as j2:
                records = j2.list_forecasts()
            self.assertEqual(len(records), 1)
            self.assertEqual(records[0].market_id, "persist")


class TestMemo(unittest.TestCase):
    def _market(self, **overrides) -> MarketSnapshot:
        defaults = dict(
            id="999",
            question="Will the team win the championship?",
            description="Sports market.",
            outcomes=["Yes", "No"],
            outcome_prices=[0.55, 0.45],
            liquidity=60_000.0,
            volume=60_000.0,
            spread=0.02,
            active=True,
            closed=False,
            end_date="2999-01-01T00:00:00Z",
        )
        defaults.update(overrides)
        return MarketSnapshot(**defaults)

    def test_memo_is_deterministic(self):
        market = self._market()
        memo_a = render_memo(build_memo(market))
        memo_b = render_memo(build_memo(market))
        self.assertEqual(memo_a, memo_b)

    def test_memo_contains_required_sections(self):
        text = render_memo(build_memo(self._market()))
        for section in [
            "Market:",
            "Market ID:",
            "Current Implied Probability:",
            "Evidence For:",
            "Evidence Against:",
            "Unknowns:",
            "Forecast:",
            "Recommendation:",
            "Reasoning:",
        ]:
            self.assertIn(section, text)

    def test_thin_market_defaults_to_no_trade(self):
        memo = build_memo(self._market(liquidity=100.0, volume=100.0))
        self.assertEqual(memo.recommendation, "NO_TRADE")

    def test_missing_prices_force_no_trade(self):
        memo = build_memo(self._market(outcome_prices=[]))
        self.assertEqual(memo.recommendation, "NO_TRADE")
        self.assertIsNone(memo.forecast_probability)

    def test_healthy_market_can_reach_paper_trade(self):
        memo = build_memo(self._market(liquidity=100_000.0, volume=100_000.0))
        self.assertEqual(memo.recommendation, "PAPER_TRADE")
        # Even the best case never exceeds a practice (paper) recommendation.
        self.assertIn(memo.recommendation, ("NO_TRADE", "WATCH", "PAPER_TRADE"))

    def test_moderate_market_reaches_watch(self):
        memo = build_memo(self._market(liquidity=15_000.0, volume=15_000.0))
        self.assertEqual(memo.recommendation, "WATCH")

    def test_forecast_anchors_on_implied_probability(self):
        memo = build_memo(self._market(outcome_prices=[0.55, 0.45]))
        self.assertAlmostEqual(memo.forecast_probability, 0.55)


if __name__ == "__main__":
    unittest.main()
