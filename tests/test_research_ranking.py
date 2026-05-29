"""Tests for public-data market ranking.

These tests are written with the standard-library ``unittest`` so they run under
both ``python -m pytest`` and ``python -m unittest`` without extra dependencies.
"""

import os
import sys
import unittest
from datetime import datetime, timezone

# Make the repo root importable regardless of the working directory.
_REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from agents.research.models import MarketSnapshot, normalize_market
from agents.research.ranking import rank_markets, score_market


def make_market(**overrides) -> MarketSnapshot:
    """Build a reasonable default market snapshot, with overrides."""
    defaults = dict(
        id="1",
        question="Will it rain tomorrow?",
        description="A weather market.",
        outcomes=["Yes", "No"],
        outcome_prices=[0.6, 0.4],
        liquidity=20_000.0,
        volume=20_000.0,
        spread=0.01,
        active=True,
        closed=False,
        archived=False,
        restricted=False,
        end_date="2999-01-01T00:00:00Z",
    )
    defaults.update(overrides)
    return MarketSnapshot(**defaults)


class TestScoreMarket(unittest.TestCase):
    NOW = datetime(2024, 1, 1, tzinfo=timezone.utc)

    def test_quality_market_scores_positive_with_reasons(self):
        ranked = score_market(make_market(), now=self.NOW)
        self.assertGreater(ranked.score, 0)
        self.assertTrue(ranked.reasons)
        # Reasons must explain the score in plain text.
        joined = " ".join(ranked.reasons)
        self.assertIn("question", joined)

    def test_closed_market_is_excluded(self):
        ranked = score_market(make_market(closed=True), now=self.NOW)
        self.assertEqual(ranked.score, float("-inf"))
        self.assertIn("closed", " ".join(ranked.reasons))

    def test_archived_market_is_excluded(self):
        ranked = score_market(make_market(archived=True), now=self.NOW)
        self.assertEqual(ranked.score, float("-inf"))

    def test_restricted_market_is_included_by_default(self):
        # 'restricted' is a trading-jurisdiction flag, not a quality signal.
        # Read-only research includes it by default (with a transparency note).
        ranked = score_market(make_market(restricted=True), now=self.NOW)
        self.assertNotEqual(ranked.score, float("-inf"))
        self.assertGreater(ranked.score, 0)
        self.assertTrue(any("trading-restricted" in r for r in ranked.reasons))

    def test_restricted_market_excluded_when_opted_in(self):
        ranked = score_market(
            make_market(restricted=True), now=self.NOW, exclude_restricted=True
        )
        self.assertEqual(ranked.score, float("-inf"))
        self.assertIn("restricted", " ".join(ranked.reasons))

    def test_inactive_market_is_excluded(self):
        ranked = score_market(make_market(active=False), now=self.NOW)
        self.assertEqual(ranked.score, float("-inf"))

    def test_higher_liquidity_scores_higher(self):
        low = score_market(make_market(liquidity=5_000.0), now=self.NOW)
        high = score_market(make_market(liquidity=500_000.0), now=self.NOW)
        self.assertGreater(high.score, low.score)

    def test_tighter_spread_scores_higher(self):
        wide = score_market(make_market(spread=0.2), now=self.NOW)
        tight = score_market(make_market(spread=0.01), now=self.NOW)
        self.assertGreater(tight.score, wide.score)

    def test_future_end_date_scores_higher_than_past(self):
        past = score_market(
            make_market(end_date="2000-01-01T00:00:00Z"), now=self.NOW
        )
        future = score_market(
            make_market(end_date="2999-01-01T00:00:00Z"), now=self.NOW
        )
        self.assertGreater(future.score, past.score)

    def test_missing_question_does_not_get_question_bonus(self):
        with_q = score_market(make_market(question="A real question?"), now=self.NOW)
        without_q = score_market(make_market(question=""), now=self.NOW)
        self.assertGreater(with_q.score, without_q.score)

    def test_out_of_range_prices_do_not_get_price_bonus(self):
        good = score_market(make_market(outcome_prices=[0.5, 0.5]), now=self.NOW)
        bad = score_market(make_market(outcome_prices=[1.5, -0.5]), now=self.NOW)
        self.assertGreater(good.score, bad.score)


class TestRankMarkets(unittest.TestCase):
    NOW = datetime(2024, 1, 1, tzinfo=timezone.utc)

    def test_excluded_markets_are_dropped(self):
        markets = [
            make_market(id="1"),
            make_market(id="2", closed=True),
            make_market(id="3", archived=True),
        ]
        ranked = rank_markets(markets, now=self.NOW)
        ids = [rm.market.id for rm in ranked]
        self.assertEqual(ids, ["1"])

    def test_restricted_kept_by_default_dropped_when_opted_in(self):
        markets = [
            make_market(id="1", restricted=False),
            make_market(id="2", restricted=True),
        ]
        kept = rank_markets(markets, now=self.NOW)
        self.assertEqual({rm.market.id for rm in kept}, {"1", "2"})

        strict = rank_markets(markets, now=self.NOW, exclude_restricted=True)
        self.assertEqual([rm.market.id for rm in strict], ["1"])

    def test_sorted_best_first(self):
        markets = [
            make_market(id="1", liquidity=1_000.0, volume=1_000.0),
            make_market(id="2", liquidity=500_000.0, volume=500_000.0),
        ]
        ranked = rank_markets(markets, now=self.NOW)
        self.assertEqual(ranked[0].market.id, "2")

    def test_limit_truncates_results(self):
        markets = [make_market(id=str(i)) for i in range(5)]
        ranked = rank_markets(markets, limit=2, now=self.NOW)
        self.assertEqual(len(ranked), 2)

    def test_tie_break_is_deterministic_by_id(self):
        markets = [make_market(id="3"), make_market(id="1"), make_market(id="2")]
        ranked = rank_markets(markets, now=self.NOW)
        # All identical scores, so order falls back to ascending id.
        self.assertEqual([rm.market.id for rm in ranked], ["1", "2", "3"])


class TestNormalizeMarket(unittest.TestCase):
    def test_parses_stringified_json_fields(self):
        raw = {
            "id": 42,
            "question": "Will X happen?",
            "outcomes": '["Yes", "No"]',
            "outcomePrices": '["0.7", "0.3"]',
            "liquidity": "12345.6",
            "active": True,
            "closed": False,
        }
        snapshot = normalize_market(raw)
        self.assertEqual(snapshot.id, "42")
        self.assertEqual(snapshot.outcomes, ["Yes", "No"])
        self.assertEqual(snapshot.outcome_prices, [0.7, 0.3])
        self.assertAlmostEqual(snapshot.liquidity, 12345.6)
        self.assertEqual(snapshot.implied_probability, 0.7)

    def test_handles_missing_and_malformed_fields(self):
        raw = {"id": 7, "outcomes": "not-json", "outcomePrices": None}
        snapshot = normalize_market(raw)
        self.assertEqual(snapshot.outcomes, [])
        self.assertEqual(snapshot.outcome_prices, [])
        self.assertIsNone(snapshot.implied_probability)
        # Falls back to "Yes" when no outcomes are defined.
        self.assertEqual(snapshot.primary_outcome(), "Yes")


if __name__ == "__main__":
    unittest.main()
