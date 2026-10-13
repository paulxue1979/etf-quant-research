"""Durable pending backtests and atomic terminal run persistence."""

from __future__ import annotations

import json
import sqlite3
from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from backend.app.backtest_models import BacktestRun
from backend.app.backtest_repository import BacktestRepository
from research.canonical import canonical_json, sha256_hash


class ControlledBacktestStorageError(RuntimeError):
    """A typed, non-sensitive storage or state conflict."""


class ControlledBacktestRepository:
    """Keep the plan and final run in the same SQLite transaction boundary."""

    def __init__(self, path: Path | None = None) -> None:
        self.path = path or Path(__file__).resolve().parents[2] / "data" / "strategy.db"

    @staticmethod
    def _ensure_schema(connection: sqlite3.Connection) -> None:
        connection.execute(
            """CREATE TABLE IF NOT EXISTS pending_backtest_configurations (
                    config_id TEXT PRIMARY KEY,
                    strategy_id TEXT NOT NULL,
                    strategy_version_id TEXT NOT NULL,
                    strategy_version_hash TEXT NOT NULL,
                    config_json TEXT NOT NULL,
                    config_hash TEXT NOT NULL,
                    status TEXT NOT NULL CHECK(status IN
                        ('PROPOSED','REJECTED','APPROVED','RUNNING','COMPLETED','FAILED','STALE')),
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    approved_at TEXT,
                    execution_id TEXT UNIQUE,
                    backtest_run_id TEXT UNIQUE,
                    failure_code TEXT,
                    failure_stage TEXT,
                    failure_message TEXT,
                    FOREIGN KEY(backtest_run_id) REFERENCES backtest_runs(backtest_run_id)
                )"""
        )
        connection.execute(
            "CREATE INDEX IF NOT EXISTS idx_pending_backtest_created "
            "ON pending_backtest_configurations(created_at DESC, config_id DESC)"
        )

    def _connect(self, *, read_only: bool = False) -> sqlite3.Connection:
        if not read_only:
            self.path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(
            self.path.resolve().as_uri() + "?mode=ro" if read_only else self.path,
            uri=read_only,
            timeout=30,
            isolation_level=None,
        )
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA query_only = ON" if read_only else "PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA busy_timeout = 30000")
        return connection

    @staticmethod
    def _record(row: sqlite3.Row) -> dict[str, Any]:
        body = json.loads(row["config_json"])
        if sha256_hash(body) != row["config_hash"]:
            raise ControlledBacktestStorageError("PLAN_INTEGRITY_ERROR")
        return {
            **body,
            "status": row["status"],
            "updated_at": row["updated_at"],
            "approved_at": row["approved_at"],
            "execution_id": row["execution_id"],
            "backtest_run_id": row["backtest_run_id"],
            "failure_code": row["failure_code"],
            "failure_stage": row["failure_stage"],
            "failure_message": row["failure_message"],
        }

    def insert(self, body: dict[str, Any]) -> dict[str, Any]:
        now = body["created_at"]
        with closing(self._connect()) as connection:
            connection.execute("BEGIN IMMEDIATE")
            self._ensure_schema(connection)
            connection.execute(
                """INSERT INTO pending_backtest_configurations
                   (config_id, strategy_id, strategy_version_id, strategy_version_hash,
                    config_json, config_hash, status, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, 'PROPOSED', ?, ?)""",
                (
                    body["config_id"],
                    body["strategy_id"],
                    body["strategy_version_id"],
                    body["strategy_version_hash"],
                    canonical_json(body),
                    sha256_hash(body),
                    now,
                    now,
                ),
            )
            connection.execute("COMMIT")
        return self.get(body["config_id"])

    def get(self, config_id: str) -> dict[str, Any]:
        if not self.path.exists():
            raise ControlledBacktestStorageError("PLAN_NOT_FOUND")
        with closing(self._connect(read_only=True)) as connection:
            if not self._table_exists(connection):
                raise ControlledBacktestStorageError("PLAN_NOT_FOUND")
            row = connection.execute(
                "SELECT * FROM pending_backtest_configurations WHERE config_id = ?",
                (config_id,),
            ).fetchone()
        if row is None:
            raise ControlledBacktestStorageError("PLAN_NOT_FOUND")
        return self._record(row)

    def list(self, *, limit: int = 25) -> list[dict[str, Any]]:
        if not self.path.exists():
            return []
        with closing(self._connect(read_only=True)) as connection:
            if not self._table_exists(connection):
                return []
            rows = connection.execute(
                "SELECT * FROM pending_backtest_configurations "
                "ORDER BY created_at DESC, config_id DESC LIMIT ?",
                (min(max(limit, 1), 50),),
            ).fetchall()
        return [self._record(row) for row in rows]

    @staticmethod
    def _table_exists(connection: sqlite3.Connection) -> bool:
        return (
            connection.execute(
                "SELECT 1 FROM sqlite_master WHERE type = 'table' "
                "AND name = 'pending_backtest_configurations'"
            ).fetchone()
            is not None
        )

    def get_run(self, run_id: str) -> BacktestRun | None:
        with closing(self._connect(read_only=True)) as connection:
            row = connection.execute(
                "SELECT run_json FROM backtest_runs WHERE backtest_run_id = ?", (run_id,)
            ).fetchone()
        return BacktestRun.from_dict(json.loads(row["run_json"])) if row else None

    def transition(
        self, config_id: str, expected: str, target: str, **fields: Any
    ) -> dict[str, Any]:
        allowed = {"REJECTED", "STALE", "RUNNING", "FAILED"}
        if target not in allowed:
            raise ControlledBacktestStorageError("PLAN_INVALID_TRANSITION")
        now = datetime.now(UTC).isoformat()
        columns = ["status = ?", "updated_at = ?"]
        values: list[Any] = [target, now]
        for key in (
            "approved_at",
            "execution_id",
            "failure_code",
            "failure_stage",
            "failure_message",
        ):
            if key in fields:
                columns.append(f"{key} = ?")
                values.append(fields[key])
        with closing(self._connect()) as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT * FROM pending_backtest_configurations WHERE config_id = ?",
                (config_id,),
            ).fetchone()
            if row is None:
                raise ControlledBacktestStorageError("PLAN_NOT_FOUND")
            self._record(row)
            if row["status"] != expected:
                raise ControlledBacktestStorageError(
                    "PLAN_ALREADY_EXECUTED"
                    if row["status"] in ("RUNNING", "COMPLETED")
                    else "PLAN_NOT_PROPOSED"
                )
            connection.execute(
                f"UPDATE pending_backtest_configurations SET {', '.join(columns)} "
                "WHERE config_id = ? AND status = ?",
                (*values, config_id, expected),
            )
            connection.execute("COMMIT")
        return self.get(config_id)

    def complete_with_run(
        self, config_id: str, execution_id: str, run: BacktestRun
    ) -> dict[str, Any]:
        with closing(self._connect()) as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT * FROM pending_backtest_configurations WHERE config_id = ?",
                (config_id,),
            ).fetchone()
            if row is None:
                raise ControlledBacktestStorageError("PLAN_NOT_FOUND")
            self._record(row)
            if row["status"] != "RUNNING" or row["execution_id"] != execution_id:
                raise ControlledBacktestStorageError("PLAN_EXECUTION_CONFLICT")
            BacktestRepository.persist_in_transaction(connection, run)
            connection.execute(
                "UPDATE pending_backtest_configurations SET status = 'COMPLETED', "
                "backtest_run_id = ?, updated_at = ? WHERE config_id = ?",
                (run.backtest_run_id, datetime.now(UTC).isoformat(), config_id),
            )
            connection.execute("COMMIT")
        return self.get(config_id)


__all__ = ["ControlledBacktestRepository", "ControlledBacktestStorageError"]
