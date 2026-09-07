"""SQLite persistence for immutable backtest research artifacts."""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from pathlib import Path

from backend.app.backtest_models import BacktestRun, dumps


class BacktestPersistenceError(RuntimeError):
    """Raised when a stored backtest run cannot be safely persisted or restored."""


@dataclass(frozen=True)
class BacktestRunRecord:
    """One immutable run plus the analysis version stored with it."""

    run: BacktestRun
    analysis_version: str

    def __post_init__(self) -> None:
        if not isinstance(self.run, BacktestRun):
            raise TypeError("run must be a BacktestRun")
        if not isinstance(self.analysis_version, str) or not self.analysis_version.strip():
            raise ValueError("analysis_version must be a non-empty string")
        object.__setattr__(self, "analysis_version", self.analysis_version.strip())


class BacktestRepository:
    """Durable repository that never updates or overwrites a completed run."""

    SCHEMA_VERSION = 1

    def __init__(self, db_path: str | Path | None = None) -> None:
        self.db_path = (
            Path(db_path)
            if db_path is not None
            else Path(__file__).resolve().parents[2] / "data" / "strategy.db"
        )
        try:
            self.db_path.parent.mkdir(parents=True, exist_ok=True)
            self._initialize()
        except OSError as exc:
            raise BacktestPersistenceError("could not prepare backtest database") from exc

    def _connect(self) -> sqlite3.Connection:
        try:
            connection = sqlite3.connect(self.db_path, timeout=30.0, isolation_level=None)
            connection.row_factory = sqlite3.Row
            connection.execute("PRAGMA foreign_keys = ON")
            connection.execute("PRAGMA busy_timeout = 30000")
            return connection
        except sqlite3.Error as exc:
            raise BacktestPersistenceError("could not open backtest database") from exc

    def _initialize(self) -> None:
        connection = self._connect()
        try:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS backtest_runs (
                    backtest_run_id TEXT PRIMARY KEY,
                    strategy_id TEXT NOT NULL,
                    strategy_version_id TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    strategy_version_content_hash TEXT NOT NULL,
                    start_date TEXT NOT NULL,
                    end_date TEXT NOT NULL,
                    price_field_used TEXT NOT NULL,
                    engine_version TEXT NOT NULL,
                    analysis_version TEXT NOT NULL,
                    run_json TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_backtest_runs_strategy
                    ON backtest_runs(strategy_id, created_at DESC);
                """
            )
        except sqlite3.Error as exc:
            raise BacktestPersistenceError("could not initialize backtest database") from exc
        finally:
            connection.close()

    def create(self, run: BacktestRun) -> BacktestRun:
        """Insert one immutable run; repeated inputs still receive a new id."""
        if not isinstance(run, BacktestRun):
            raise BacktestPersistenceError("backtest run is invalid")
        payload = dumps(run.to_dict())
        result = run.backtest_result
        analysis = run.performance_analysis
        connection = self._connect()
        try:
            connection.execute(
                """
                INSERT INTO backtest_runs(
                    backtest_run_id, strategy_id, strategy_version_id, created_at,
                    strategy_version_content_hash, start_date, end_date, price_field_used,
                    engine_version, analysis_version, run_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    run.backtest_run_id,
                    run.strategy_id,
                    run.strategy_version_id,
                    run.created_at.isoformat(),
                    run.strategy_version_content_hash,
                    result.start_date.isoformat(),
                    result.end_date.isoformat(),
                    analysis.price_field_used.value,
                    result.engine_version,
                    "phase-4i.0",
                    payload,
                ),
            )
            return run
        except sqlite3.IntegrityError as exc:
            raise BacktestPersistenceError("backtest run already exists") from exc
        except sqlite3.Error as exc:
            raise BacktestPersistenceError("could not persist backtest run") from exc
        finally:
            connection.close()

    def get(self, backtest_run_id: str) -> BacktestRun | None:
        """Return one run or None when its id is unknown."""
        connection = self._connect()
        try:
            row = connection.execute(
                "SELECT run_json FROM backtest_runs WHERE backtest_run_id = ?",
                (backtest_run_id,),
            ).fetchone()
            if row is None:
                return None
            return self._decode(row["run_json"])
        except sqlite3.Error as exc:
            raise BacktestPersistenceError("could not load backtest run") from exc
        finally:
            connection.close()

    def list(self, strategy_id: str | None = None) -> tuple[BacktestRun, ...]:
        """Return immutable runs, newest first, optionally scoped to a strategy."""
        return tuple(record.run for record in self.list_records(strategy_id))

    def list_records(self, strategy_id: str | None = None) -> tuple[BacktestRunRecord, ...]:
        """Return immutable runs with their stored analysis version, newest first."""
        connection = self._connect()
        try:
            if strategy_id is None:
                rows = connection.execute(
                    "SELECT run_json, analysis_version FROM backtest_runs "
                    "ORDER BY created_at DESC, backtest_run_id DESC"
                ).fetchall()
            else:
                rows = connection.execute(
                    """
                    SELECT run_json, analysis_version FROM backtest_runs
                    WHERE strategy_id = ?
                    ORDER BY created_at DESC, backtest_run_id DESC
                    """,
                    (strategy_id,),
                ).fetchall()
            return tuple(self._record(row) for row in rows)
        except sqlite3.Error as exc:
            raise BacktestPersistenceError("could not list backtest runs") from exc
        finally:
            connection.close()

    def get_records(self, backtest_run_ids: tuple[str, ...]) -> tuple[BacktestRunRecord, ...]:
        """Load existing runs in request order without creating or updating data."""
        if not backtest_run_ids:
            return ()
        placeholders = ", ".join("?" for _ in backtest_run_ids)
        connection = self._connect()
        try:
            rows = connection.execute(
                f"SELECT backtest_run_id, run_json, analysis_version FROM backtest_runs "
                f"WHERE backtest_run_id IN ({placeholders})",
                backtest_run_ids,
            ).fetchall()
            by_id = {str(row["backtest_run_id"]): self._record(row) for row in rows}
            return tuple(
                record
                for run_id in backtest_run_ids
                if (record := by_id.get(run_id)) is not None
            )
        except sqlite3.Error as exc:
            raise BacktestPersistenceError("could not load backtest runs") from exc
        finally:
            connection.close()

    def clear(self) -> None:
        """Remove all runs; intended for isolated tests only."""
        connection = self._connect()
        try:
            connection.execute("DELETE FROM backtest_runs")
        except sqlite3.Error as exc:
            raise BacktestPersistenceError("could not clear backtest runs") from exc
        finally:
            connection.close()

    @staticmethod
    def _decode(raw: object) -> BacktestRun:
        if not isinstance(raw, str):
            raise BacktestPersistenceError("stored backtest run is invalid")
        try:
            payload = json.loads(raw, parse_constant=_reject_nonfinite)
            return BacktestRun.from_dict(payload)
        except (KeyError, TypeError, ValueError, OverflowError, json.JSONDecodeError) as exc:
            raise BacktestPersistenceError("stored backtest run failed integrity checks") from exc

    @classmethod
    def _record(cls, row: sqlite3.Row) -> BacktestRunRecord:
        try:
            return BacktestRunRecord(
                run=cls._decode(row["run_json"]),
                analysis_version=str(row["analysis_version"]),
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise BacktestPersistenceError("stored backtest run failed integrity checks") from exc


def _reject_nonfinite(value: str) -> None:
    raise ValueError(f"non-finite JSON constant {value}")


__all__ = ["BacktestPersistenceError", "BacktestRepository", "BacktestRunRecord"]
