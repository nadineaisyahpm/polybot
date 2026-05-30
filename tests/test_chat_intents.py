"""Tests for the rule-based chat intent parser."""

import os
import sys
import unittest

_REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from agents.chat.intents import Intent, parse_intent


class TestSimpleIntents(unittest.TestCase):
    def test_show_forecasts(self):
        self.assertEqual(parse_intent("show forecasts").intent, Intent.SHOW_FORECASTS)
        self.assertEqual(parse_intent("forecasts").intent, Intent.SHOW_FORECASTS)

    def test_show_portfolio(self):
        self.assertEqual(
            parse_intent("show my paper portfolio").intent, Intent.SHOW_PORTFOLIO
        )
        self.assertEqual(parse_intent("portfolio").intent, Intent.SHOW_PORTFOLIO)

    def test_show_history(self):
        self.assertEqual(parse_intent("show paper history").intent, Intent.SHOW_HISTORY)
        self.assertEqual(parse_intent("history").intent, Intent.SHOW_HISTORY)

    def test_show_runs(self):
        self.assertEqual(parse_intent("show agent runs").intent, Intent.SHOW_RUNS)
        self.assertEqual(parse_intent("runs").intent, Intent.SHOW_RUNS)

    def test_scan_markets(self):
        parsed = parse_intent("scan markets")
        self.assertEqual(parsed.intent, Intent.SCAN_MARKETS)
        self.assertIsNone(parsed.limit)

    def test_scan_markets_with_limit(self):
        parsed = parse_intent("scan markets limit 20")
        self.assertEqual(parsed.intent, Intent.SCAN_MARKETS)
        self.assertEqual(parsed.limit, 20)

    def test_help_intent(self):
        self.assertEqual(parse_intent("help").intent, Intent.HELP)
        self.assertEqual(parse_intent("?").intent, Intent.HELP)

    def test_empty_message_is_unknown_with_reason(self):
        parsed = parse_intent("   ")
        self.assertEqual(parsed.intent, Intent.UNKNOWN)
        self.assertIsNotNone(parsed.reason)

    def test_garbage_is_unknown(self):
        parsed = parse_intent("xyzzy frobnicate")
        self.assertEqual(parsed.intent, Intent.UNKNOWN)
        self.assertIsNotNone(parsed.reason)


class TestResearchIntent(unittest.TestCase):
    def test_research_market_with_id(self):
        parsed = parse_intent("research market 540819")
        self.assertEqual(parsed.intent, Intent.RESEARCH_MARKET)
        self.assertEqual(parsed.market_id, "540819")

    def test_research_market_id_phrase(self):
        parsed = parse_intent("research market-id 540819")
        self.assertEqual(parsed.intent, Intent.RESEARCH_MARKET)
        self.assertEqual(parsed.market_id, "540819")

    def test_research_without_id_returns_missing(self):
        parsed = parse_intent("research")
        self.assertEqual(parsed.intent, Intent.RESEARCH_MARKET)
        self.assertIsNone(parsed.market_id)
        self.assertIn("market_id", parsed.missing)
        self.assertIsNotNone(parsed.reason)


class TestRunPaperAgentIntent(unittest.TestCase):
    def test_full_run_command(self):
        parsed = parse_intent(
            "run paper agent for market 540819 at price 0.57 size 10"
        )
        self.assertEqual(parsed.intent, Intent.RUN_PAPER_AGENT)
        self.assertEqual(parsed.market_id, "540819")
        self.assertEqual(parsed.price, 0.57)
        self.assertEqual(parsed.size, 10.0)
        self.assertEqual(parsed.missing, [])

    def test_missing_price_and_size_listed(self):
        parsed = parse_intent("run paper agent for market 540819")
        self.assertEqual(parsed.intent, Intent.RUN_PAPER_AGENT)
        self.assertEqual(parsed.market_id, "540819")
        self.assertIn("price", parsed.missing)
        self.assertIn("size", parsed.missing)
        self.assertIsNotNone(parsed.reason)

    def test_missing_market_id_listed(self):
        parsed = parse_intent("run paper agent at price 0.57 size 10")
        self.assertEqual(parsed.intent, Intent.RUN_PAPER_AGENT)
        self.assertIn("market_id", parsed.missing)


class TestCaseInsensitivity(unittest.TestCase):
    def test_uppercase_is_handled(self):
        parsed = parse_intent("SHOW FORECASTS")
        self.assertEqual(parsed.intent, Intent.SHOW_FORECASTS)

    def test_mixed_case_research(self):
        parsed = parse_intent("Research Market 540819")
        self.assertEqual(parsed.intent, Intent.RESEARCH_MARKET)
        self.assertEqual(parsed.market_id, "540819")


if __name__ == "__main__":
    unittest.main()
