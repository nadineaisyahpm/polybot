"""Tests for the SQLite paper (simulated) trading journal.

Uses an in-memory SQLite database so tests are fast and leave no files behind.
Standard-library ``unittest`` so these run under both ``python -m pytest`` and
``python -m unittest`` with no third-party dependencies (no httpx needed).
"""

import os
import sys
import unittest

_REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from agents.research.paper_trading import (
    PAPER_LABEL,
    PaperTradeError,
    PaperTradingJournal,
)
from agents.research.risk import RiskLimits


class TestPaperBuy(unittest.TestCase):
    def setUp(self):
        self.journal = PaperTradingJournal(":memory:")

    def tearDown(self):
        self.journal.close()

    def test_buy_records_simulated_trade(self):
        trade = self.journal.record_buy(
            market_id="m1", price=0.5, size_usdc=10.0, question="Will X?"
        )
        self.assertEqual(trade.side, "BUY")
        self.assertTrue(trade.simulated)
        self.assertAlmostEqual(trade.shares, 20.0)  # 10 / 0.5
        self.assertAlmostEqual(trade.size_usdc, 10.0)

    def test_buy_updates_position_and_exposure(self):
        self.journal.record_buy(market_id="m1", price=0.5, size_usdc=10.0)
        pos = self.journal.get_position("m1")
        self.assertAlmostEqual(pos.shares, 20.0)
        self.assertAlmostEqual(pos.exposure, 10.0)
        self.assertAlmostEqual(self.journal.total_exposure(), 10.0)

    def test_buy_rejected_when_over_trade_size(self):
        with self.assertRaises(PaperTradeError) as ctx:
            self.journal.record_buy(market_id="m1", price=0.5, size_usdc=10.01)
        self.assertTrue(ctx.exception.reasons)

    def test_rejected_buy_is_not_persisted(self):
        with self.assertRaises(PaperTradeError):
            self.journal.record_buy(market_id="m1", price=0.5, size_usdc=999.0)
        self.assertEqual(self.journal.history(), [])
        self.assertAlmostEqual(self.journal.total_exposure(), 0.0)

    def test_buy_rejected_when_price_out_of_range(self):
        with self.assertRaises(PaperTradeError):
            self.journal.record_buy(market_id="m1", price=0.0, size_usdc=5.0)
        with self.assertRaises(PaperTradeError):
            self.journal.record_buy(market_id="m1", price=1.0, size_usdc=5.0)

    def test_per_market_exposure_limit_enforced_across_trades(self):
        # 25 USDC per-market cap: three 10s would be 30 > 25.
        self.journal.record_buy(market_id="m1", price=0.5, size_usdc=10.0)
        self.journal.record_buy(market_id="m1", price=0.5, size_usdc=10.0)
        with self.assertRaises(PaperTradeError) as ctx:
            self.journal.record_buy(market_id="m1", price=0.5, size_usdc=10.0)
        self.assertTrue(any("per-market" in r for r in ctx.exception.reasons))

    def test_total_exposure_limit_enforced_across_markets(self):
        # 100 USDC total cap across markets, 25 per market => need several markets.
        for i in range(4):  # 4 markets * 25 = 100 exactly
            self.journal.record_buy(market_id=f"m{i}", price=0.5, size_usdc=10.0)
            self.journal.record_buy(market_id=f"m{i}", price=0.5, size_usdc=10.0)
            self.journal.record_buy(market_id=f"m{i}", price=0.5, size_usdc=5.0)
        self.assertAlmostEqual(self.journal.total_exposure(), 100.0)
        # Any further buy breaches the 100 total cap.
        with self.assertRaises(PaperTradeError) as ctx:
            self.journal.record_buy(market_id="m_new", price=0.5, size_usdc=1.0)
        self.assertTrue(any("total open exposure" in r for r in ctx.exception.reasons))


class TestPaperSell(unittest.TestCase):
    def setUp(self):
        self.journal = PaperTradingJournal(":memory:")
        # Open a position: 20 shares at 0.5 (cost 10).
        self.journal.record_buy(market_id="m1", price=0.5, size_usdc=10.0)

    def tearDown(self):
        self.journal.close()

    def test_sell_reduces_position(self):
        self.journal.record_sell(market_id="m1", price=0.6, shares=10.0)
        pos = self.journal.get_position("m1")
        self.assertAlmostEqual(pos.shares, 10.0)
        # Cost basis halved (avg 0.5 * 10 remaining = 5).
        self.assertAlmostEqual(pos.exposure, 5.0)

    def test_sell_realizes_pnl(self):
        # Sell 10 shares at 0.6, cost basis 0.5 => profit 1.0.
        trade = self.journal.record_sell(market_id="m1", price=0.6, shares=10.0)
        self.assertAlmostEqual(trade.realized_pnl, 1.0)
        self.assertAlmostEqual(self.journal.total_realized_pnl(), 1.0)

    def test_cannot_sell_more_than_held(self):
        with self.assertRaises(PaperTradeError) as ctx:
            self.journal.record_sell(market_id="m1", price=0.6, shares=21.0)
        self.assertTrue(any("short selling" in r for r in ctx.exception.reasons))

    def test_full_sell_closes_position(self):
        self.journal.record_sell(market_id="m1", price=0.55, shares=20.0)
        pos = self.journal.get_position("m1")
        self.assertAlmostEqual(pos.shares, 0.0)
        self.assertAlmostEqual(pos.exposure, 0.0)
        self.assertEqual(self.journal.get_open_positions(), [])

    def test_sell_rejected_on_bad_price(self):
        with self.assertRaises(PaperTradeError):
            self.journal.record_sell(market_id="m1", price=1.5, shares=1.0)


class TestPaperPortfolioAndHistory(unittest.TestCase):
    def setUp(self):
        self.journal = PaperTradingJournal(":memory:")

    def tearDown(self):
        self.journal.close()

    def test_history_most_recent_first_and_labeled_simulated(self):
        self.journal.record_buy(market_id="a", price=0.5, size_usdc=5.0)
        self.journal.record_buy(market_id="b", price=0.5, size_usdc=5.0)
        trades = self.journal.history()
        self.assertEqual([t.market_id for t in trades], ["b", "a"])
        self.assertTrue(all(t.simulated for t in trades))

    def test_open_positions_sorted(self):
        self.journal.record_buy(market_id="z", price=0.5, size_usdc=5.0)
        self.journal.record_buy(market_id="a", price=0.5, size_usdc=5.0)
        positions = self.journal.get_open_positions()
        self.assertEqual([p.market_id for p in positions], ["a", "z"])

    def test_unrealized_pnl_uses_mark_price(self):
        self.journal.record_buy(market_id="m1", price=0.5, size_usdc=10.0)
        pos = self.journal.get_position("m1")
        # 20 shares; mark at 0.6 => value 12, cost 10 => +2 unrealized.
        self.assertAlmostEqual(pos.unrealized_pnl(0.6), 2.0)
        self.assertIsNone(pos.unrealized_pnl(None))

    def test_persists_across_connections(self):
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            db_path = os.path.join(tmp, "paper.sqlite3")
            with PaperTradingJournal(db_path) as j1:
                j1.record_buy(market_id="persist", price=0.5, size_usdc=10.0)
            with PaperTradingJournal(db_path) as j2:
                positions = j2.get_open_positions()
                self.assertEqual(len(positions), 1)
                self.assertAlmostEqual(positions[0].exposure, 10.0)

    def test_paper_label_constant(self):
        self.assertEqual(PAPER_LABEL, "PAPER/SIMULATED")

    def test_custom_limits_applied_to_journal(self):
        journal = PaperTradingJournal(":memory:", limits=RiskLimits(max_trade_size_usdc=3.0))
        with self.assertRaises(PaperTradeError):
            journal.record_buy(market_id="m1", price=0.5, size_usdc=5.0)
        journal.close()


if __name__ == "__main__":
    unittest.main()
