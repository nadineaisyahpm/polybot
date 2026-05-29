"""Local SQLite paper (simulated) trading journal.

This is a *simulation only*. It uses the standard-library :mod:`sqlite3` module
and never touches a wallet, private key, CLOB authenticated endpoint, or real
order placement. Every position recorded here is fictional play-money used to
learn the mechanics of a prediction market.

Design notes:

- The ``paper_trades`` table is an append-only ledger of simulated BUY/SELL
  fills. Positions, exposure, and realized P&L are *derived* by replaying the
  ledger, so the source of truth is always auditable.
- Risk guardrails (:mod:`agents.research.risk`) are enforced *before* any BUY or
  SELL is written. A rejected trade is never persisted.
- Cost basis uses the average-cost method. "Open exposure" for a market is the
  remaining cost basis of the open position (USDC committed and not yet sold).
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional

from agents.research.risk import (
    DEFAULT_LIMITS,
    RiskDecision,
    RiskLimits,
    evaluate_paper_buy,
    evaluate_paper_sell,
)

DEFAULT_DB_PATH = "local_db_paper_trading.sqlite3"

# Labels used consistently in all outputs so a simulated trade is never mistaken
# for a real one.
PAPER_LABEL = "PAPER/SIMULATED"

# Provenance of a simulated trade. This lets the app (and a future UI)
# distinguish a hand-entered paper trade from one that came out of the
# research -> forecast -> risk pipeline.
SOURCE_MANUAL = "manual"
SOURCE_AGENT = "agent_recommended"
SOURCE_MANUAL_OVERRIDE = "manual_override"
VALID_SOURCES = (SOURCE_MANUAL, SOURCE_AGENT, SOURCE_MANUAL_OVERRIDE)

_CREATE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS paper_trades (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    market_id TEXT NOT NULL,
    question TEXT,
    outcome TEXT NOT NULL DEFAULT 'Yes',
    side TEXT NOT NULL,
    price REAL NOT NULL,
    shares REAL NOT NULL,
    size_usdc REAL NOT NULL,
    realized_pnl REAL NOT NULL DEFAULT 0,
    notes TEXT,
    simulated INTEGER NOT NULL DEFAULT 1,
    source TEXT NOT NULL DEFAULT 'manual',
    forecast_id INTEGER,
    confidence TEXT,
    created_at TEXT NOT NULL
)
"""

# Columns added after the original Milestone 2 schema. Used to migrate existing
# databases in place (SQLite ALTER TABLE ADD COLUMN).
_MIGRATION_COLUMNS = {
    "source": "TEXT NOT NULL DEFAULT 'manual'",
    "forecast_id": "INTEGER",
    "confidence": "TEXT",
}


class PaperTradeError(Exception):
    """Raised when a simulated trade is rejected by validation or guardrails."""

    def __init__(self, message: str, reasons: Optional[list[str]] = None) -> None:
        super().__init__(message)
        self.reasons = reasons or []


@dataclass
class PaperTrade:
    """A single simulated fill from the ledger."""

    id: int
    market_id: str
    question: Optional[str]
    outcome: str
    side: str
    price: float
    shares: float
    size_usdc: float
    realized_pnl: float
    notes: Optional[str]
    simulated: bool
    created_at: str
    source: str = SOURCE_MANUAL
    forecast_id: Optional[int] = None
    confidence: Optional[str] = None

    @property
    def from_agent(self) -> bool:
        """True when this trade originated from a forecast recommendation."""
        return self.source == SOURCE_AGENT


@dataclass
class Position:
    """A derived open position for one market + outcome."""

    market_id: str
    outcome: str
    shares: float
    cost_basis: float
    realized_pnl: float
    question: Optional[str] = None

    @property
    def average_price(self) -> Optional[float]:
        if self.shares <= 0:
            return None
        return self.cost_basis / self.shares

    @property
    def exposure(self) -> float:
        """Open exposure in USDC: the cost basis still committed to the market."""
        return max(self.cost_basis, 0.0)

    def market_value(self, mark_price: Optional[float]) -> Optional[float]:
        """Current value at a given mark price, if one is provided."""
        if mark_price is None:
            return None
        return self.shares * mark_price

    def unrealized_pnl(self, mark_price: Optional[float]) -> Optional[float]:
        value = self.market_value(mark_price)
        if value is None:
            return None
        return value - self.cost_basis


class PaperTradingJournal:
    """A validated, guardrail-enforced SQLite paper-trading ledger.

    Pass ``":memory:"`` as the path for ephemeral use in tests. No third-party
    packages are required; this is stdlib ``sqlite3`` only.
    """

    def __init__(
        self,
        db_path: str = DEFAULT_DB_PATH,
        limits: RiskLimits = DEFAULT_LIMITS,
    ) -> None:
        self.db_path = db_path
        self.limits = limits
        self._conn = sqlite3.connect(self.db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._create_schema()

    def _create_schema(self) -> None:
        with self._conn:
            self._conn.execute(_CREATE_TABLE_SQL)
        self._migrate_schema()

    def _migrate_schema(self) -> None:
        """Add provenance columns to pre-existing (Milestone 2) databases.

        SQLite supports ``ALTER TABLE ADD COLUMN`` for simple additions, so older
        ledgers gain ``source``/``forecast_id``/``confidence`` without losing data.
        """
        existing = {
            row["name"]
            for row in self._conn.execute("PRAGMA table_info(paper_trades)").fetchall()
        }
        missing = {
            name: ddl
            for name, ddl in _MIGRATION_COLUMNS.items()
            if name not in existing
        }
        if not missing:
            return
        with self._conn:
            for name, ddl in missing.items():
                self._conn.execute(
                    f"ALTER TABLE paper_trades ADD COLUMN {name} {ddl}"
                )

    def close(self) -> None:
        self._conn.close()

    def __enter__(self) -> "PaperTradingJournal":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()

    # ------------------------------------------------------------------
    # Derived state (positions / exposure) from the append-only ledger.
    # ------------------------------------------------------------------
    def _replay(self) -> dict[tuple[str, str], Position]:
        """Replay the ledger into per-(market, outcome) positions."""
        rows = self._conn.execute(
            "SELECT * FROM paper_trades ORDER BY id ASC"
        ).fetchall()

        positions: dict[tuple[str, str], Position] = {}
        for row in rows:
            key = (row["market_id"], row["outcome"])
            pos = positions.get(key)
            if pos is None:
                pos = Position(
                    market_id=row["market_id"],
                    outcome=row["outcome"],
                    shares=0.0,
                    cost_basis=0.0,
                    realized_pnl=0.0,
                    question=row["question"],
                )
                positions[key] = pos

            if row["question"] and not pos.question:
                pos.question = row["question"]

            side = row["side"].upper()
            if side == "BUY":
                pos.shares += row["shares"]
                pos.cost_basis += row["size_usdc"]
            elif side == "SELL":
                avg = pos.cost_basis / pos.shares if pos.shares > 0 else 0.0
                cost_removed = avg * row["shares"]
                proceeds = row["shares"] * row["price"]
                pos.realized_pnl += proceeds - cost_removed
                pos.shares -= row["shares"]
                pos.cost_basis -= cost_removed
                # Guard against tiny negative dust from float math.
                if pos.shares < 1e-9:
                    pos.shares = 0.0
                    pos.cost_basis = 0.0
        return positions

    def get_position(self, market_id: str, outcome: str = "Yes") -> Position:
        """Return the current derived position (zeroed if none exists)."""
        positions = self._replay()
        key = (str(market_id), outcome)
        if key in positions:
            return positions[key]
        return Position(
            market_id=str(market_id),
            outcome=outcome,
            shares=0.0,
            cost_basis=0.0,
            realized_pnl=0.0,
        )

    def get_open_positions(self) -> list[Position]:
        """Return all positions that still hold shares, sorted by market/outcome."""
        positions = self._replay()
        open_positions = [p for p in positions.values() if p.shares > 0]
        open_positions.sort(key=lambda p: (p.market_id, p.outcome))
        return open_positions

    def market_exposure(self, market_id: str) -> float:
        """Total open exposure (USDC cost basis) across outcomes for a market."""
        positions = self._replay()
        return sum(
            p.exposure
            for (mid, _outcome), p in positions.items()
            if mid == str(market_id)
        )

    def total_exposure(self) -> float:
        """Total open exposure (USDC cost basis) across every market."""
        positions = self._replay()
        return sum(p.exposure for p in positions.values())

    def total_realized_pnl(self) -> float:
        positions = self._replay()
        return sum(p.realized_pnl for p in positions.values())

    # ------------------------------------------------------------------
    # Mutations (guardrail-enforced).
    # ------------------------------------------------------------------
    def record_buy(
        self,
        market_id: str,
        price: float,
        size_usdc: float,
        question: Optional[str] = None,
        outcome: str = "Yes",
        notes: Optional[str] = None,
        created_at: Optional[str] = None,
        source: str = SOURCE_MANUAL,
        forecast_id: Optional[int] = None,
        confidence: Optional[str] = None,
    ) -> PaperTrade:
        """Validate against risk limits and record a simulated BUY.

        Raises :class:`PaperTradeError` (with reasons) if guardrails reject it.
        Nothing is written when rejected.

        ``source`` records provenance (manual vs agent-recommended). ``forecast_id``
        links back to a row in the research forecast journal when applicable.
        """
        if not market_id or not str(market_id).strip():
            raise PaperTradeError("market_id is required.")
        outcome = outcome or "Yes"
        if source not in VALID_SOURCES:
            raise PaperTradeError(
                f"source must be one of {VALID_SOURCES} (got '{source}')."
            )

        decision: RiskDecision = evaluate_paper_buy(
            price=price,
            size_usdc=size_usdc,
            current_market_exposure=self.market_exposure(market_id),
            current_total_exposure=self.total_exposure(),
            limits=self.limits,
        )
        if decision.rejected:
            raise PaperTradeError(
                "Paper BUY rejected by risk guardrails.", reasons=decision.reasons
            )

        shares = size_usdc / price
        created_at = created_at or datetime.now(timezone.utc).isoformat()

        with self._conn:
            cursor = self._conn.execute(
                """
                INSERT INTO paper_trades (
                    market_id, question, outcome, side, price, shares,
                    size_usdc, realized_pnl, notes, simulated,
                    source, forecast_id, confidence, created_at
                ) VALUES (?, ?, ?, 'BUY', ?, ?, ?, 0, ?, 1, ?, ?, ?, ?)
                """,
                (
                    str(market_id),
                    question,
                    outcome,
                    price,
                    shares,
                    size_usdc,
                    notes,
                    source,
                    forecast_id,
                    confidence,
                    created_at,
                ),
            )
            new_id = cursor.lastrowid

        return PaperTrade(
            id=int(new_id),
            market_id=str(market_id),
            question=question,
            outcome=outcome,
            side="BUY",
            price=price,
            shares=shares,
            size_usdc=size_usdc,
            realized_pnl=0.0,
            notes=notes,
            simulated=True,
            created_at=created_at,
            source=source,
            forecast_id=forecast_id,
            confidence=confidence,
        )

    def record_sell(
        self,
        market_id: str,
        price: float,
        shares: float,
        outcome: str = "Yes",
        question: Optional[str] = None,
        notes: Optional[str] = None,
        created_at: Optional[str] = None,
        source: str = SOURCE_MANUAL,
        forecast_id: Optional[int] = None,
        confidence: Optional[str] = None,
    ) -> PaperTrade:
        """Validate and record a simulated SELL, reducing an open position.

        Raises :class:`PaperTradeError` (with reasons) if guardrails reject it
        (invalid price/amount, or selling more shares than held).
        """
        if not market_id or not str(market_id).strip():
            raise PaperTradeError("market_id is required.")
        outcome = outcome or "Yes"
        if source not in VALID_SOURCES:
            raise PaperTradeError(
                f"source must be one of {VALID_SOURCES} (got '{source}')."
            )

        position = self.get_position(market_id, outcome)
        decision = evaluate_paper_sell(
            price=price,
            shares_to_sell=shares,
            shares_held=position.shares,
            limits=self.limits,
        )
        if decision.rejected:
            raise PaperTradeError(
                "Paper SELL rejected by risk guardrails.", reasons=decision.reasons
            )

        avg = position.average_price or 0.0
        cost_removed = avg * shares
        proceeds = shares * price
        realized = proceeds - cost_removed
        created_at = created_at or datetime.now(timezone.utc).isoformat()

        with self._conn:
            cursor = self._conn.execute(
                """
                INSERT INTO paper_trades (
                    market_id, question, outcome, side, price, shares,
                    size_usdc, realized_pnl, notes, simulated,
                    source, forecast_id, confidence, created_at
                ) VALUES (?, ?, ?, 'SELL', ?, ?, ?, ?, ?, 1, ?, ?, ?, ?)
                """,
                (
                    str(market_id),
                    question or position.question,
                    outcome,
                    price,
                    shares,
                    proceeds,
                    realized,
                    notes,
                    source,
                    forecast_id,
                    confidence,
                    created_at,
                ),
            )
            new_id = cursor.lastrowid

        return PaperTrade(
            id=int(new_id),
            market_id=str(market_id),
            question=question or position.question,
            outcome=outcome,
            side="SELL",
            price=price,
            shares=shares,
            size_usdc=proceeds,
            realized_pnl=realized,
            notes=notes,
            simulated=True,
            created_at=created_at,
            source=source,
            forecast_id=forecast_id,
            confidence=confidence,
        )

    def history(self, limit: Optional[int] = None) -> list[PaperTrade]:
        """Return the simulated trade ledger, most recent first."""
        query = "SELECT * FROM paper_trades ORDER BY id DESC"
        params: tuple = ()
        if limit is not None:
            query += " LIMIT ?"
            params = (limit,)
        rows = self._conn.execute(query, params).fetchall()
        return [self._row_to_trade(row) for row in rows]

    @staticmethod
    def _row_to_trade(row: sqlite3.Row) -> PaperTrade:
        keys = row.keys()
        return PaperTrade(
            id=row["id"],
            market_id=row["market_id"],
            question=row["question"],
            outcome=row["outcome"],
            side=row["side"],
            price=row["price"],
            shares=row["shares"],
            size_usdc=row["size_usdc"],
            realized_pnl=row["realized_pnl"],
            notes=row["notes"],
            simulated=bool(row["simulated"]),
            created_at=row["created_at"],
            source=row["source"] if "source" in keys and row["source"] else SOURCE_MANUAL,
            forecast_id=row["forecast_id"] if "forecast_id" in keys else None,
            confidence=row["confidence"] if "confidence" in keys else None,
        )
