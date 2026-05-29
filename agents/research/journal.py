"""Local SQLite forecast journaling.

Forecasts are stored locally using the standard-library :mod:`sqlite3` module.
This is a research journal, not a trading ledger: it records what you predicted
and why, so you can review your calibration over time. Nothing here touches a
wallet or places a trade.

All writes go through :meth:`ForecastJournal.add_forecast`, which validates input
strictly (probabilities in range, confidence and recommendation from fixed
enumerations) before persisting.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional

from agents.research.memo import CONFIDENCE_LEVELS, RECOMMENDATIONS

DEFAULT_DB_PATH = "local_db_research.sqlite3"

_CREATE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS forecasts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    market_id TEXT NOT NULL,
    question TEXT,
    outcome TEXT NOT NULL DEFAULT 'Yes',
    market_probability REAL,
    forecast_probability REAL NOT NULL,
    confidence TEXT NOT NULL,
    recommendation TEXT NOT NULL,
    notes TEXT,
    created_at TEXT NOT NULL
)
"""


class ForecastValidationError(ValueError):
    """Raised when a forecast fails validation before being stored."""


@dataclass
class ForecastRecord:
    """A single stored forecast row."""

    id: int
    market_id: str
    question: Optional[str]
    outcome: str
    market_probability: Optional[float]
    forecast_probability: float
    confidence: str
    recommendation: str
    notes: Optional[str]
    created_at: str


def _validate_probability(value: Optional[float], field_name: str) -> Optional[float]:
    if value is None:
        return None
    try:
        numeric = float(value)
    except (TypeError, ValueError) as err:
        raise ForecastValidationError(
            f"{field_name} must be a number between 0 and 1."
        ) from err
    if not 0.0 <= numeric <= 1.0:
        raise ForecastValidationError(
            f"{field_name} must be between 0 and 1 (got {numeric})."
        )
    return numeric


class ForecastJournal:
    """A thin, validated wrapper over a SQLite ``forecasts`` table.

    Pass ``":memory:"`` as the path for ephemeral use in tests.
    """

    def __init__(self, db_path: str = DEFAULT_DB_PATH) -> None:
        self.db_path = db_path
        # ``check_same_thread=False`` keeps the CLI simple; access is single-threaded.
        self._conn = sqlite3.connect(self.db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._create_schema()

    def _create_schema(self) -> None:
        with self._conn:
            self._conn.execute(_CREATE_TABLE_SQL)

    def close(self) -> None:
        """Close the underlying database connection."""
        self._conn.close()

    def __enter__(self) -> "ForecastJournal":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()

    def add_forecast(
        self,
        market_id: str,
        forecast_probability: float,
        question: Optional[str] = None,
        outcome: str = "Yes",
        market_probability: Optional[float] = None,
        confidence: str = "low",
        recommendation: str = "NO_TRADE",
        notes: Optional[str] = None,
        created_at: Optional[str] = None,
    ) -> ForecastRecord:
        """Validate and persist a forecast, returning the stored record.

        Raises :class:`ForecastValidationError` if any field is invalid. The
        defaults (``confidence='low'``, ``recommendation='NO_TRADE'``) reflect
        the beginner-safe posture of this tool.
        """
        if not market_id or not str(market_id).strip():
            raise ForecastValidationError("market_id is required.")

        validated_forecast = _validate_probability(
            forecast_probability, "forecast_probability"
        )
        if validated_forecast is None:
            raise ForecastValidationError("forecast_probability is required.")

        validated_market_prob = _validate_probability(
            market_probability, "market_probability"
        )

        if confidence not in CONFIDENCE_LEVELS:
            raise ForecastValidationError(
                f"confidence must be one of {CONFIDENCE_LEVELS} (got '{confidence}')."
            )

        if recommendation not in RECOMMENDATIONS:
            raise ForecastValidationError(
                f"recommendation must be one of {RECOMMENDATIONS} "
                f"(got '{recommendation}')."
            )

        outcome = outcome or "Yes"
        created_at = created_at or datetime.now(timezone.utc).isoformat()

        with self._conn:
            cursor = self._conn.execute(
                """
                INSERT INTO forecasts (
                    market_id, question, outcome, market_probability,
                    forecast_probability, confidence, recommendation,
                    notes, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    str(market_id),
                    question,
                    outcome,
                    validated_market_prob,
                    validated_forecast,
                    confidence,
                    recommendation,
                    notes,
                    created_at,
                ),
            )
            new_id = cursor.lastrowid

        return ForecastRecord(
            id=int(new_id),
            market_id=str(market_id),
            question=question,
            outcome=outcome,
            market_probability=validated_market_prob,
            forecast_probability=validated_forecast,
            confidence=confidence,
            recommendation=recommendation,
            notes=notes,
            created_at=created_at,
        )

    def list_forecasts(self, limit: Optional[int] = None) -> list[ForecastRecord]:
        """Return stored forecasts, most recent first."""
        query = "SELECT * FROM forecasts ORDER BY id DESC"
        params: tuple = ()
        if limit is not None:
            query += " LIMIT ?"
            params = (limit,)
        rows = self._conn.execute(query, params).fetchall()
        return [self._row_to_record(row) for row in rows]

    def get_forecast(self, forecast_id: int) -> Optional[ForecastRecord]:
        """Return a single forecast by id, or ``None`` if not found."""
        row = self._conn.execute(
            "SELECT * FROM forecasts WHERE id = ?", (forecast_id,)
        ).fetchone()
        return self._row_to_record(row) if row else None

    @staticmethod
    def _row_to_record(row: sqlite3.Row) -> ForecastRecord:
        return ForecastRecord(
            id=row["id"],
            market_id=row["market_id"],
            question=row["question"],
            outcome=row["outcome"],
            market_probability=row["market_probability"],
            forecast_probability=row["forecast_probability"],
            confidence=row["confidence"],
            recommendation=row["recommendation"],
            notes=row["notes"],
            created_at=row["created_at"],
        )
