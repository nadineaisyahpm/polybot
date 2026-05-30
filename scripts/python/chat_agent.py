#!/usr/bin/env python3
"""Local chat-agent REPL for Polybot.

This is a small interactive shell over the deterministic chat layer in
:mod:`agents.chat`. There is no LLM here yet; intents are parsed by simple
rules so behavior is fully testable. The shell never reads a wallet, never
authenticates to trading endpoints, and never places a real order. Any trade
it records is labeled ``PAPER/SIMULATED``.

Usage:

    python3 scripts/python/chat_agent.py

Type 'help' for supported commands, 'exit' or 'quit' to leave.
"""

from __future__ import annotations

import argparse
import os
import sys

_REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from agents.chat.agent import ChatAgent  # noqa: E402
from agents.research.journal import DEFAULT_DB_PATH as FORECAST_DB_PATH  # noqa: E402
from agents.research.paper_trading import (  # noqa: E402
    DEFAULT_DB_PATH as PAPER_DB_PATH,
)
from agents.research.runs import DEFAULT_DB_PATH as RUNS_DB_PATH  # noqa: E402

WELCOME = (
    "Polybot chat (research-only, PAPER/SIMULATED trades only).\n"
    "No wallet, no private keys, no live orders.\n"
    "Type 'help' for commands, 'exit' or 'quit' to leave."
)

EXIT_TOKENS = {"exit", "quit", ":q", "bye"}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Local chat-agent REPL for the Polybot research and paper-trading "
            "tools. Research-only; never trades real money."
        )
    )
    parser.add_argument(
        "--forecast-db-path",
        default=FORECAST_DB_PATH,
        help="Forecast journal SQLite path.",
    )
    parser.add_argument(
        "--paper-db-path",
        default=PAPER_DB_PATH,
        help="Paper-trading SQLite path.",
    )
    parser.add_argument(
        "--runs-db-path",
        default=RUNS_DB_PATH,
        help="Agent runs SQLite path.",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=None,
        help="HTTP timeout in seconds for public API calls (default: 20).",
    )
    return parser


def run_repl(agent: ChatAgent, input_fn=input, output_fn=print) -> int:
    """Drive the REPL loop. Injectable I/O makes this testable."""
    output_fn(WELCOME)
    while True:
        try:
            message = input_fn("you> ")
        except (EOFError, KeyboardInterrupt):
            output_fn("\nbye")
            return 0
        stripped = (message or "").strip()
        if not stripped:
            continue
        if stripped.lower() in EXIT_TOKENS:
            output_fn("bye")
            return 0
        response = agent.handle(stripped)
        output_fn(f"agent> {response.text}")


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    agent = ChatAgent(
        forecast_db_path=args.forecast_db_path,
        paper_db_path=args.paper_db_path,
        runs_db_path=args.runs_db_path,
        timeout=args.timeout,
    )
    return run_repl(agent)


if __name__ == "__main__":
    raise SystemExit(main())
