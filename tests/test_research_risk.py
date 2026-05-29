"""Tests for paper-trading risk guardrails.

Standard-library ``unittest`` so these run under both ``python -m pytest`` and
``python -m unittest`` without third-party dependencies (no httpx needed).
"""

import os
import sys
import unittest

_REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from agents.research.risk import (
    DEFAULT_LIMITS,
    RiskLimits,
    evaluate_paper_buy,
    evaluate_paper_sell,
)


class TestDefaultLimits(unittest.TestCase):
    def test_defaults_match_spec(self):
        self.assertEqual(DEFAULT_LIMITS.max_trade_size_usdc, 10.0)
        self.assertEqual(DEFAULT_LIMITS.max_exposure_per_market_usdc, 25.0)
        self.assertEqual(DEFAULT_LIMITS.max_total_open_exposure_usdc, 100.0)
        self.assertEqual(DEFAULT_LIMITS.min_price, 0.01)
        self.assertEqual(DEFAULT_LIMITS.max_price, 0.99)


class TestEvaluatePaperBuy(unittest.TestCase):
    def test_approves_trade_within_all_limits(self):
        decision = evaluate_paper_buy(
            price=0.5,
            size_usdc=10.0,
            current_market_exposure=0.0,
            current_total_exposure=0.0,
        )
        self.assertTrue(decision.approved)
        self.assertFalse(decision.rejected)

    def test_rejects_trade_size_over_limit(self):
        decision = evaluate_paper_buy(
            price=0.5,
            size_usdc=10.01,
            current_market_exposure=0.0,
            current_total_exposure=0.0,
        )
        self.assertTrue(decision.rejected)
        self.assertTrue(any("max paper trade size" in r for r in decision.reasons))

    def test_rejects_price_below_floor(self):
        decision = evaluate_paper_buy(
            price=0.005,
            size_usdc=5.0,
            current_market_exposure=0.0,
            current_total_exposure=0.0,
        )
        self.assertTrue(decision.rejected)
        self.assertTrue(any("price" in r for r in decision.reasons))

    def test_rejects_price_above_ceiling(self):
        decision = evaluate_paper_buy(
            price=0.995,
            size_usdc=5.0,
            current_market_exposure=0.0,
            current_total_exposure=0.0,
        )
        self.assertTrue(decision.rejected)

    def test_accepts_price_at_exact_bounds(self):
        low = evaluate_paper_buy(0.01, 5.0, 0.0, 0.0)
        high = evaluate_paper_buy(0.99, 5.0, 0.0, 0.0)
        self.assertTrue(low.approved)
        self.assertTrue(high.approved)

    def test_rejects_per_market_exposure_breach(self):
        # Already 20 committed on this market; +9 would be 29 > 25.
        decision = evaluate_paper_buy(
            price=0.5,
            size_usdc=9.0,
            current_market_exposure=20.0,
            current_total_exposure=20.0,
        )
        self.assertTrue(decision.rejected)
        self.assertTrue(any("per-market limit" in r for r in decision.reasons))

    def test_rejects_total_exposure_breach(self):
        # 95 total committed; +9 would be 104 > 100. Keep per-market within range.
        decision = evaluate_paper_buy(
            price=0.5,
            size_usdc=9.0,
            current_market_exposure=0.0,
            current_total_exposure=95.0,
        )
        self.assertTrue(decision.rejected)
        self.assertTrue(any("total open exposure" in r for r in decision.reasons))

    def test_rejects_nonpositive_size(self):
        self.assertTrue(evaluate_paper_buy(0.5, 0.0, 0.0, 0.0).rejected)
        self.assertTrue(evaluate_paper_buy(0.5, -5.0, 0.0, 0.0).rejected)

    def test_reports_multiple_violations_at_once(self):
        decision = evaluate_paper_buy(
            price=0.005,  # bad price
            size_usdc=50.0,  # over trade size + exposure
            current_market_exposure=0.0,
            current_total_exposure=0.0,
        )
        self.assertTrue(decision.rejected)
        self.assertGreaterEqual(len(decision.reasons), 2)

    def test_custom_limits_are_respected(self):
        limits = RiskLimits(max_trade_size_usdc=5.0)
        decision = evaluate_paper_buy(0.5, 7.0, 0.0, 0.0, limits=limits)
        self.assertTrue(decision.rejected)


class TestEvaluatePaperSell(unittest.TestCase):
    def test_approves_valid_sell(self):
        decision = evaluate_paper_sell(price=0.6, shares_to_sell=5.0, shares_held=10.0)
        self.assertTrue(decision.approved)

    def test_rejects_selling_more_than_held(self):
        decision = evaluate_paper_sell(price=0.6, shares_to_sell=11.0, shares_held=10.0)
        self.assertTrue(decision.rejected)
        self.assertTrue(any("short selling" in r for r in decision.reasons))

    def test_rejects_bad_price(self):
        self.assertTrue(
            evaluate_paper_sell(price=1.5, shares_to_sell=1.0, shares_held=10.0).rejected
        )

    def test_rejects_nonpositive_shares(self):
        self.assertTrue(
            evaluate_paper_sell(price=0.5, shares_to_sell=0.0, shares_held=10.0).rejected
        )

    def test_allows_selling_exact_holdings(self):
        decision = evaluate_paper_sell(price=0.5, shares_to_sell=10.0, shares_held=10.0)
        self.assertTrue(decision.approved)


if __name__ == "__main__":
    unittest.main()
