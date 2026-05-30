"""Local SQLite agent-run audit log.

Every invocation of the deterministic research workflow (see
:mod:`agents.research.workflow`) is recorded as a single ``agent_runs`` row so
the chain of decisions is auditable after the fact. This is the substrate a
future chat agent would call: each user-facing action becomes one auditable
run with a status, an error (if any), and JSON-encoded payloads for memo /
risk / scanner context.

This module is stdlib-only. It never touches a wallet, a private key, or any
authenticated trading endpoint, and it never places real orders.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Optional

DEFAULT_DB_PATH = "local_db_agent_runs.sqlite3"

STATUS_STARTED = "STARTED"
STATUS_COMPLETED = "COMPLETED"
STATUS_SKIPPED = "SKIPPED"
STATUS_FAILED = "FAILED"

VALID_STATUSES = (STATUS_STARTED, STATUS_COMPLETED, STATUS_SKIPPED, STATUS_FAILED)
TERMINAL_STATUSES = (STATUS_COMPLETED, STATUS_SKIPPED, STATUS_FAILED)


_CREATE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS agent_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    status TEXT NOT NULL,
    market_id TEXT NOT NULL,
    price REAL,
    size_usdc REAL,
    recommendation TEXT,
    forecast_id INTEGER,
    paper_trade_id INTEGER,
    scanner TEXT,
    memo TEXT,
    risk TEXT,
    error TEXT,
    notes TEXT,
    started_at TEXT NOT NULL,
    completed_at TEXT
)
"""


class AgentRunError(ValueError):
    """Raised when an agent-run row fails validation."""


@dataclass
class AgentRun:
    """A single auditable agent-run record."""

    id: int
    status: str
    market_id: str
    price: Optional[float]
    size_usdc: Optional[float]
    recommendation: Optional[str]
    forecast_id: Optional[int]
    paper_trade_id: Optional[int]
    scanner: Optional[str]
    memo: Optional[str]
    risk: Optional[str]
    error: Optional[str]
    notes: Optional[str]
    started_at: str
    completed_at: Optional[str]


def _dump_json(value: Any) -> Optional[str]:
    """Serialize a JSON-encodable payload to text, or return ``None``."""
    if value is None:
        return None
    if isinstance(value, str):
        return value
    try:
        return json.dumps(value, sort_keys=True)
    except (TypeError, ValueError) as err:
        raise AgentRunError(f"value is not JSON-serializable: {err}") from err


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class AgentRunsJournal:
    """A thin, validated wrapper over the local ``agent_runs`` table.

    Pass ``":memory:"`` for ephemeral use in tests.
    """

    def __init__(self, db_path: str = DEFAULT_DB_PATH) -> None:
        self.db_path = db_path
        self._conn = sqlite3.connect(self.db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        with self._conn:
            self._conn.execute(_CREATE_TABLE_SQL)

    def close(self) -> None:
        self._conn.close()

    def __enter__(self) -> "AgentRunsJournal":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()

    # ------------------------------------------------------------------
    # Mutations
    # ------------------------------------------------------------------
    def start_run(
        self,
        market_id: str,
        price: Optional[float] = None,
        size_usdc: Optional[float] = None,
        scanner: Any = None,
        notes: Optional[str] = None,
        started_at: Optional[str] = None,
    ) -> AgentRun:
        """Open a new run row in status ``STARTED`` and return it."""
        if not market_id or not str(market_id).strip():
            raise AgentRunError("market_id is required.")
        if price is not None:
            try:
                price = float(price)
            except (TypeError, ValueError) as err:
                raise AgentRunError("price must be numeric.") from err
        if size_usdc is not None:
            try:
                size_usdc = float(size_usdc)
            except (TypeError, ValueError) as err:
                raise AgentRunError("size_usdc must be numeric.") from err

        started_at = started_at or _now_iso()
        scanner_text = _dump_json(scanner)

        with self._conn:
            cursor = self._conn.execute(
                """
                INSERT INTO agent_runs (
                    status, market_id, price, size_usdc, scanner, notes, started_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    STATUS_STARTED,
                    str(market_id),
                    price,
                    size_usdc,
                    scanner_text,
                    notes,
                    started_at,
                ),
            )
            new_id = int(cursor.lastrowid)

        return self.get_run(new_id)  # type: ignore[return-value]

    def complete_run(
        self,
        run_id: int,
        forecast_id: Optional[int] = None,
        paper_trade_id: Optional[int] = None,
        memo: Any = None,
        risk: Any = None,
        recommendation: Optional[str] = None,
        notes: Optional[str] = None,
        completed_at: Optional[str] = None,
    ) -> AgentRun:
        """Mark an in-flight run as ``COMPLETED``."""
        return self._finalize(
            run_id,
            STATUS_COMPLETED,
            forecast_id=forecast_id,
            paper_trade_id=paper_trade_id,
            memo=memo,
            risk=risk,
            recommendation=recommendation,
            notes=notes,
            completed_at=completed_at,
        )

    def skip_run(
        self,
        run_id: int,
        reason: str,
        forecast_id: Optional[int] = None,
        memo: Any = None,
        risk: Any = None,
        recommendation: Optional[str] = None,
        completed_at: Optional[str] = None,
    ) -> AgentRun:
        """Mark an in-flight run as ``SKIPPED`` with a reason."""
        if not reason or not str(reason).strip():
            raise AgentRunError("skip reason is required.")
        return self._finalize(
            run_id,
            STATUS_SKIPPED,
            forecast_id=forecast_id,
            memo=memo,
            risk=risk,
            recommendation=recommendation,
            notes=reason,
            completed_at=completed_at,
        )

    def fail_run(
        self,
        run_id: int,
        error: str,
        forecast_id: Optional[int] = None,
        memo: Any = None,
        risk: Any = None,
        recommendation: Optional[str] = None,
        completed_at: Optional[str] = None,
    ) -> AgentRun:
        """Mark an in-flight run as ``FAILED`` with an error message."""
        if not error or not str(error).strip():
            raise AgentRunError("error message is required.")
        return self._finalize(
            run_id,
            STATUS_FAILED,
            forecast_id=forecast_id,
            memo=memo,
            risk=risk,
            recommendation=recommendation,
            error=error,
            completed_at=completed_at,
        )

    def _finalize(
        self,
        run_id: int,
        status: str,
        forecast_id: Optional[int] = None,
        paper_trade_id: Optional[int] = None,
        memo: Any = None,
        risk: Any = None,
        recommendation: Optional[str] = None,
        notes: Optional[str] = None,
        error: Optional[str] = None,
        completed_at: Optional[str] = None,
    ) -> AgentRun:
        if status not in VALID_STATUSES:
            raise AgentRunError(
                f"status must be one of {VALID_STATUSES} (got {status!r})."
            )
        existing = self.get_run(run_id)
        if existing is None:
            raise AgentRunError(f"No agent run found with id {run_id}.")
        if existing.status != STATUS_STARTED:
            raise AgentRunError(
                f"Run #{run_id} is already in terminal status "
                f"{existing.status!r}; cannot transition to {status!r}."
            )

        memo_text = _dump_json(memo)
        risk_text = _dump_json(risk)
        completed_at = completed_at or _now_iso()

        with self._conn:
            self._conn.execute(
                """
                UPDATE agent_runs SET
                    status = ?,
                    forecast_id = COALESCE(?, forecast_id),
                    paper_trade_id = COALESCE(?, paper_trade_id),
                    recommendation = COALESCE(?, recommendation),
                    memo = COALESCE(?, memo),
                    risk = COALESCE(?, risk),
                    notes = COALESCE(?, notes),
                    error = COALESCE(?, error),
                    completed_at = ?
                WHERE id = ?
                """,
                (
                    status,
                    forecast_id,
                    paper_trade_id,
                    recommendation,
                    memo_text,
                    risk_text,
                    notes,
                    error,
                    completed_at,
                    run_id,
                ),
            )

        return self.get_run(run_id)  # type: ignore[return-value]

    # ------------------------------------------------------------------
    # Reads
    # ------------------------------------------------------------------
    def get_run(self, run_id: int) -> Optional[AgentRun]:
        row = self._conn.execute(
            "SELECT * FROM agent_runs WHERE id = ?", (int(run_id),)
        ).fetchone()
        return self._row_to_run(row) if row else None

    def list_runs(self, limit: Optional[int] = None) -> list[AgentRun]:
        query = "SELECT * FROM agent_runs ORDER BY id DESC"
        params: tuple = ()
        if limit is not None:
            query += " LIMIT ?"
            params = (int(limit),)
        rows = self._conn.execute(query, params).fetchall()
        return [self._row_to_run(row) for row in rows]

    @staticmethod
    def _row_to_run(row: sqlite3.Row) -> AgentRun:
        return AgentRun(
            id=row["id"],
            status=row["status"],
            market_id=row["market_id"],
            price=row["price"],
            size_usdc=row["size_usdc"],
            recommendation=row["recommendation"],
            forecast_id=row["forecast_id"],
            paper_trade_id=row["paper_trade_id"],
            scanner=row["scanner"],
            memo=row["memo"],
            risk=row["risk"],
            error=row["error"],
            notes=row["notes"],
            started_at=row["started_at"],
            completed_at=row["completed_at"],
        )
