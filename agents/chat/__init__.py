"""Deterministic local chat-agent layer.

This package maps short, plain-English user messages to the existing safe
research and paper-trading tools. There is no LLM here yet: parsing is rule-
based so behavior is fully testable and the safety boundary is obvious.

The chat layer never touches a wallet, never authenticates to trading
endpoints, and never places a real order. Every paper-trading response is
labeled ``PAPER/SIMULATED``.

Public surface:

- :class:`Intent` / :class:`ParsedIntent` / :func:`parse_intent`
  (:mod:`agents.chat.intents`)
- :class:`ChatAgent` / :class:`ChatResponse` (:mod:`agents.chat.agent`)
"""

from agents.chat.agent import ChatAgent, ChatResponse
from agents.chat.intents import Intent, ParsedIntent, parse_intent

__all__ = [
    "ChatAgent",
    "ChatResponse",
    "Intent",
    "ParsedIntent",
    "parse_intent",
]
