"""Rule-based intent parsing for the local chat-agent shell.

Parsing is deterministic and dependency-light (stdlib only). It is intentionally
conservative: when a required value is missing, the matching intent is returned
with the missing field listed rather than silently guessing. Truly unmatched
messages fall back to ``Intent.UNKNOWN`` with a short reason.

A future LLM-backed parser can plug in behind the same :class:`ParsedIntent`
contract without touching the dispatch layer in :mod:`agents.chat.agent`.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional


class Intent(str, Enum):
    """Supported intents for the local chat shell."""

    SHOW_FORECASTS = "SHOW_FORECASTS"
    SHOW_PORTFOLIO = "SHOW_PORTFOLIO"
    SHOW_HISTORY = "SHOW_HISTORY"
    SHOW_RUNS = "SHOW_RUNS"
    RESEARCH_MARKET = "RESEARCH_MARKET"
    SCAN_MARKETS = "SCAN_MARKETS"
    RUN_PAPER_AGENT = "RUN_PAPER_AGENT"
    HELP = "HELP"
    UNKNOWN = "UNKNOWN"


@dataclass
class ParsedIntent:
    """Structured result of parsing one user message."""

    intent: Intent
    market_id: Optional[str] = None
    price: Optional[float] = None
    size: Optional[float] = None
    limit: Optional[int] = None
    missing: list[str] = field(default_factory=list)
    reason: Optional[str] = None
    raw: str = ""

    @property
    def is_unknown(self) -> bool:
        return self.intent == Intent.UNKNOWN


_MARKET_ID_PATTERNS = (
    re.compile(r"market[\s\-_]*id[\s:=]*([A-Za-z0-9_-]{1,64})"),
    re.compile(r"\bmarket\s+([A-Za-z0-9_-]{1,64})"),
    re.compile(r"\bfor\s+market\s+([A-Za-z0-9_-]{1,64})"),
)
_TRAILING_ID_PATTERN = re.compile(r"\b(\d{2,})\b")

_PRICE_PATTERN = re.compile(r"\b(?:price|at)\s+([0-9]*\.?[0-9]+)")
_SIZE_PATTERN = re.compile(r"\b(?:size|amount|usdc)\s+([0-9]*\.?[0-9]+)")
_LIMIT_PATTERN = re.compile(r"\b(?:limit|top|first|last)\s+([0-9]+)")


def _normalize(text: str) -> str:
    return (text or "").strip().lower()


def _extract_market_id(text: str) -> Optional[str]:
    for pattern in _MARKET_ID_PATTERNS:
        match = pattern.search(text)
        if match:
            candidate = match.group(1)
            # Skip purely-numeric matches that look like price/size by accident.
            if candidate and not candidate.replace(".", "").isdigit() or candidate.isdigit():
                return candidate
    return None


def _extract_float(pattern: re.Pattern[str], text: str) -> Optional[float]:
    match = pattern.search(text)
    if not match:
        return None
    try:
        return float(match.group(1))
    except ValueError:
        return None


def _extract_int(pattern: re.Pattern[str], text: str) -> Optional[int]:
    match = pattern.search(text)
    if not match:
        return None
    try:
        return int(match.group(1))
    except ValueError:
        return None


def _fallback_market_id(text: str, exclude: set[str]) -> Optional[str]:
    """When no ``market <id>`` phrase was found, accept a standalone integer."""
    for match in _TRAILING_ID_PATTERN.finditer(text):
        token = match.group(1)
        if token in exclude:
            continue
        return token
    return None


def parse_intent(message: str) -> ParsedIntent:
    """Parse a single user message into a :class:`ParsedIntent`.

    Matching is keyword-based and deterministic. Order matters: more specific
    phrases ("run paper agent", "agent runs") are checked before the bare
    keywords they overlap with.
    """
    raw = message or ""
    text = _normalize(raw)

    if not text:
        return ParsedIntent(
            intent=Intent.UNKNOWN,
            reason="Empty message. Try 'help' to see supported commands.",
            raw=raw,
        )

    if text in {"help", "?", "h", "commands"} or "help" in text.split():
        return ParsedIntent(intent=Intent.HELP, raw=raw)

    # Order: most specific first.
    if "run" in text and ("paper agent" in text or "paper-agent" in text or "agent" in text):
        if "paper agent" in text or "paper-agent" in text or (
            "run" in text and "agent" in text and "show" not in text and "list" not in text
        ):
            return _parse_run_paper_agent(text, raw)

    if "agent run" in text or "agent runs" in text or text.endswith(" runs") or text == "runs" or "show runs" in text or "list runs" in text:
        return ParsedIntent(
            intent=Intent.SHOW_RUNS,
            limit=_extract_int(_LIMIT_PATTERN, text),
            raw=raw,
        )

    if "research" in text:
        return _parse_research(text, raw)

    if "scan" in text:
        return ParsedIntent(
            intent=Intent.SCAN_MARKETS,
            limit=_extract_int(_LIMIT_PATTERN, text),
            raw=raw,
        )

    if "forecast" in text:
        return ParsedIntent(
            intent=Intent.SHOW_FORECASTS,
            limit=_extract_int(_LIMIT_PATTERN, text),
            raw=raw,
        )

    if "portfolio" in text or "positions" in text or "open paper" in text:
        return ParsedIntent(intent=Intent.SHOW_PORTFOLIO, raw=raw)

    if "history" in text or "trade ledger" in text or "trades" in text:
        return ParsedIntent(
            intent=Intent.SHOW_HISTORY,
            limit=_extract_int(_LIMIT_PATTERN, text),
            raw=raw,
        )

    return ParsedIntent(
        intent=Intent.UNKNOWN,
        reason=(
            "I didn't recognize that. Try 'help' for the list of supported "
            "commands."
        ),
        raw=raw,
    )


def _parse_research(text: str, raw: str) -> ParsedIntent:
    market_id = _extract_market_id(text)
    if market_id is None:
        market_id = _fallback_market_id(text, exclude=set())
    if market_id is None:
        return ParsedIntent(
            intent=Intent.RESEARCH_MARKET,
            missing=["market_id"],
            reason="Please tell me which market id to research (e.g. 'research market 540819').",
            raw=raw,
        )
    return ParsedIntent(
        intent=Intent.RESEARCH_MARKET, market_id=market_id, raw=raw
    )


def _parse_run_paper_agent(text: str, raw: str) -> ParsedIntent:
    price = _extract_float(_PRICE_PATTERN, text)
    size = _extract_float(_SIZE_PATTERN, text)

    market_id = _extract_market_id(text)
    if market_id is None:
        # Strip the matched price/size phrases so their digits can't be
        # mistaken for a market id by the trailing-integer fallback.
        residual = _PRICE_PATTERN.sub(" ", text)
        residual = _SIZE_PATTERN.sub(" ", residual)
        market_id = _fallback_market_id(residual, exclude=set())

    missing: list[str] = []
    if market_id is None:
        missing.append("market_id")
    if price is None:
        missing.append("price")
    if size is None:
        missing.append("size")

    reason = None
    if missing:
        reason = (
            "I can run the paper agent once you tell me: "
            + ", ".join(missing)
            + ". Example: 'run paper agent for market 540819 at price 0.57 size 10'."
        )

    return ParsedIntent(
        intent=Intent.RUN_PAPER_AGENT,
        market_id=market_id,
        price=price,
        size=size,
        missing=missing,
        reason=reason,
        raw=raw,
    )
