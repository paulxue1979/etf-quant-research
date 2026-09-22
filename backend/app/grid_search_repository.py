"""Lightweight persistence for PHASE 11H grid definitions and progress."""

from __future__ import annotations

import json
import math
import re
import sqlite3
from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from research.canonical import canonical_json
from research.grid_search import GridSearchDefinition, GridSearchPreflight

_STATUS_VALUES = {"prepared", "running", "completed", "failed", "cancelled"}
_SENSITIVE = re.compile(
    r"(?:api[_-]?key|access[_-]?token|authorization|credential|password|secret)", re.I
)


class GridSearchPersistenceError(RuntimeError):
    pass


class GridSearchRepository:
    """Store small optimization metadata without duplicating BacktestRun artifacts."""

    SCHEMA_VERSION = 1

    def __init__(self, db_path: str | Path | None = None) -> None:
        self.db_path = (
            Path(db_path)
            if db_path is not None
            else Path(__file__).resolve().parents[2] / "data" / "strategy.db"
        )
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.db_path, timeout=30.0, isolation_level=None)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA busy_timeout = 30000")
        return connection

    def _initialize(self) -> None:
        connection = self._connect()
        try:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS grid_search_definitions (
                    experiment_id TEXT PRIMARY KEY,
                    schema_version INTEGER NOT NULL,
                    definition_hash TEXT NOT NULL,
                    definition_json TEXT NOT NULL,
                    preflight_json TEXT NOT NULL,
                    status TEXT NOT NULL,
                    cancel_requested INTEGER NOT NULL DEFAULT 0 CHECK(cancel_requested IN (0, 1)),
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    FOREIGN KEY(experiment_id) REFERENCES experiments(experiment_id)
                );
                CREATE TABLE IF NOT EXISTS grid_search_candidate_plans (
                    experiment_id TEXT NOT NULL,
                    candidate_index INTEGER NOT NULL CHECK(candidate_index >= 0),
                    source_index INTEGER NOT NULL CHECK(source_index >= 0),
                    parameter_set_hash TEXT NOT NULL,
                    semantic_hash TEXT NOT NULL,
                    strategy_configuration_hash TEXT NOT NULL,
                    backtest_configuration_hash TEXT NOT NULL,
                    PRIMARY KEY(experiment_id, candidate_index),
                    UNIQUE(experiment_id, semantic_hash),
                    FOREIGN KEY(experiment_id) REFERENCES grid_search_definitions(experiment_id),
                    FOREIGN KEY(parameter_set_hash)
                        REFERENCES experiment_parameter_sets(parameter_set_hash)
                );
                CREATE INDEX IF NOT EXISTS idx_grid_search_status
                    ON grid_search_definitions(status, updated_at);
                """
            )
        except sqlite3.Error as exc:
            raise GridSearchPersistenceError("could not initialize grid search schema") from exc
        finally:
            connection.close()

    def save_preflight(self, preflight: GridSearchPreflight) -> None:
        if not isinstance(preflight, GridSearchPreflight) or not preflight.can_execute:
            raise GridSearchPersistenceError("only executable preflight plans may be persisted")
        definition_json = self._safe_json(preflight.definition.to_dict())
        preflight_json = self._safe_json(preflight.to_dict(include_candidates=False))
        now = datetime.now(UTC).isoformat()
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            existing = connection.execute(
                "SELECT definition_hash, definition_json, preflight_json "
                "FROM grid_search_definitions WHERE experiment_id = ?",
                (preflight.definition.experiment_id,),
            ).fetchone()
            if existing is not None:
                if (
                    existing["definition_hash"] != preflight.definition.definition_hash
                    or existing["definition_json"] != definition_json
                    or existing["preflight_json"] != preflight_json
                ):
                    raise GridSearchPersistenceError(
                        "experiment already has a different immutable grid definition"
                    )
                connection.execute("COMMIT")
                return
            connection.execute(
                """
                INSERT INTO grid_search_definitions(
                    experiment_id, schema_version, definition_hash, definition_json,
                    preflight_json, status, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, 'prepared', ?, ?)
                """,
                (
                    preflight.definition.experiment_id,
                    self.SCHEMA_VERSION,
                    preflight.definition.definition_hash,
                    definition_json,
                    preflight_json,
                    now,
                    now,
                ),
            )
            for plan in preflight.plans:
                connection.execute(
                    """
                    INSERT INTO grid_search_candidate_plans(
                        experiment_id, candidate_index, source_index, parameter_set_hash,
                        semantic_hash, strategy_configuration_hash,
                        backtest_configuration_hash
                    ) VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        preflight.definition.experiment_id,
                        plan.candidate_index,
                        plan.source_index,
                        plan.parameter_set.content_hash,
                        plan.semantic_hash,
                        plan.derived_strategy_configuration_hash,
                        plan.backtest_configuration_hash,
                    ),
                )
            connection.execute("COMMIT")
        except GridSearchPersistenceError:
            self._rollback(connection)
            raise
        except sqlite3.Error as exc:
            self._rollback(connection)
            raise GridSearchPersistenceError("could not persist grid preflight") from exc
        finally:
            connection.close()

    def get_definition(self, experiment_id: str) -> GridSearchDefinition | None:
        connection = self._connect()
        try:
            row = connection.execute(
                "SELECT definition_json, definition_hash FROM grid_search_definitions "
                "WHERE experiment_id = ?",
                (experiment_id,),
            ).fetchone()
            if row is None:
                return None
            payload = self._decode_json(row["definition_json"])
            definition = GridSearchDefinition.from_dict(payload)
            if definition.definition_hash != row["definition_hash"]:
                raise GridSearchPersistenceError("stored grid definition hash mismatch")
            return definition
        except (sqlite3.Error, TypeError, ValueError, json.JSONDecodeError) as exc:
            if isinstance(exc, GridSearchPersistenceError):
                raise
            raise GridSearchPersistenceError("stored grid definition is invalid") from exc
        finally:
            connection.close()

    def candidate_configuration_hash(self, experiment_id: str, candidate_index: int) -> str | None:
        connection = self._connect()
        try:
            row = connection.execute(
                "SELECT backtest_configuration_hash FROM grid_search_candidate_plans "
                "WHERE experiment_id = ? AND candidate_index = ?",
                (experiment_id, candidate_index),
            ).fetchone()
            return None if row is None else str(row["backtest_configuration_hash"])
        finally:
            connection.close()

    def request_cancel(self, experiment_id: str) -> bool:
        connection = self._connect()
        try:
            cursor = connection.execute(
                "UPDATE grid_search_definitions SET cancel_requested = 1, updated_at = ? "
                "WHERE experiment_id = ? AND status IN ('prepared', 'running')",
                (datetime.now(UTC).isoformat(), experiment_id),
            )
            return cursor.rowcount == 1
        except sqlite3.Error as exc:
            raise GridSearchPersistenceError("could not request grid cancellation") from exc
        finally:
            connection.close()

    def cancel_requested(self, experiment_id: str) -> bool:
        connection = self._connect()
        try:
            row = connection.execute(
                "SELECT cancel_requested FROM grid_search_definitions WHERE experiment_id = ?",
                (experiment_id,),
            ).fetchone()
            if row is None:
                raise GridSearchPersistenceError("grid definition was not found")
            return bool(row["cancel_requested"])
        finally:
            connection.close()

    def set_status(self, experiment_id: str, status: str) -> None:
        if status not in _STATUS_VALUES:
            raise GridSearchPersistenceError("grid status is invalid")
        connection = self._connect()
        try:
            cursor = connection.execute(
                "UPDATE grid_search_definitions SET status = ?, updated_at = ? "
                "WHERE experiment_id = ?",
                (status, datetime.now(UTC).isoformat(), experiment_id),
            )
            if cursor.rowcount != 1:
                raise GridSearchPersistenceError("grid definition was not found")
        except sqlite3.Error as exc:
            raise GridSearchPersistenceError("could not update grid status") from exc
        finally:
            connection.close()

    def progress(self, experiment_id: str) -> dict[str, Any]:
        """Return counts through scalar/indexed columns; never select result_json or run_json."""
        connection = self._connect()
        try:
            definition = connection.execute(
                "SELECT status, cancel_requested, preflight_json, definition_hash "
                "FROM grid_search_definitions WHERE experiment_id = ?",
                (experiment_id,),
            ).fetchone()
            if definition is None:
                raise GridSearchPersistenceError("grid definition was not found")
            preflight = self._decode_json(definition["preflight_json"])
            executable = int(preflight["executable_count"])
            tables = {
                str(row["name"])
                for row in connection.execute(
                    "SELECT name FROM sqlite_master WHERE type = 'table' AND name IN "
                    "('candidate_executions', 'research_experiment_results')"
                ).fetchall()
            }
            execution_counts = (
                connection.execute(
                    "SELECT COUNT(*) AS started, "
                    "SUM(CASE WHEN status = 'pending' THEN 1 ELSE 0 END) AS pending, "
                    "SUM(CASE WHEN status = 'running' THEN 1 ELSE 0 END) AS running, "
                    "SUM(CASE WHEN status = 'failed' THEN 1 ELSE 0 END) AS failed, "
                    "SUM(CASE WHEN status = 'failed' AND failure_retryable = 1 "
                    "AND retry_count <= max_retries THEN 1 ELSE 0 END) AS retryable "
                    "FROM candidate_executions WHERE experiment_id = ?",
                    (experiment_id,),
                ).fetchone()
                if "candidate_executions" in tables
                else None
            )
            counts = {
                key: (0 if execution_counts is None else int(execution_counts[key] or 0))
                for key in ("started", "pending", "running", "failed", "retryable")
            }
            completed_results = (
                int(
                    connection.execute(
                        "SELECT COUNT(*) FROM research_experiment_results WHERE experiment_id = ?",
                        (experiment_id,),
                    ).fetchone()[0]
                )
                if "research_experiment_results" in tables
                else 0
            )
            started = counts["started"]
            retryable_failed = counts["retryable"]
            pending = max(0, executable - started) + counts.get("pending", 0)
            cancelled = pending + retryable_failed if definition["status"] == "cancelled" else 0
            if cancelled:
                pending = 0
            terminal_failed = counts.get("failed", 0) - retryable_failed
            terminal = completed_results + terminal_failed + cancelled
            return {
                "experiment_id": experiment_id,
                "definition_hash": str(definition["definition_hash"]),
                "status": str(definition["status"]),
                "cancel_requested": bool(definition["cancel_requested"]),
                "theoretical": int(preflight["theoretical_count"]),
                "pruned": int(preflight["pruned_count"]),
                "valid": int(preflight["valid_count"]),
                "duplicate": int(preflight["duplicate_count"]),
                "executable": executable,
                "pending": pending,
                "running": counts.get("running", 0),
                "completed": completed_results,
                "failed": terminal_failed,
                "retryable": 0 if cancelled else retryable_failed,
                "cancelled": cancelled,
                "progress_percentage": (100.0 * terminal / executable if executable else 0.0),
            }
        except (sqlite3.Error, KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
            if isinstance(exc, GridSearchPersistenceError):
                raise
            raise GridSearchPersistenceError("could not read grid progress") from exc
        finally:
            connection.close()

    @classmethod
    def _safe_json(cls, value: object) -> str:
        cls._assert_safe(value)
        return canonical_json(value)

    @classmethod
    def _assert_safe(cls, value: object) -> None:
        if isinstance(value, Mapping):
            for key, item in value.items():
                if _SENSITIVE.search(str(key)):
                    raise GridSearchPersistenceError("grid payload contains sensitive fields")
                cls._assert_safe(item)
        elif isinstance(value, (list, tuple)):
            for item in value:
                cls._assert_safe(item)
        elif isinstance(value, float) and not math.isfinite(value):
            raise GridSearchPersistenceError("grid payload contains non-finite values")

    @classmethod
    def _decode_json(cls, raw: str) -> Any:
        payload = json.loads(
            raw, parse_constant=lambda value: (_ for _ in ()).throw(ValueError(value))
        )
        if raw != cls._safe_json(payload):
            raise GridSearchPersistenceError("stored grid payload is not canonical JSON")
        return payload

    @staticmethod
    def _rollback(connection: sqlite3.Connection) -> None:
        try:
            connection.execute("ROLLBACK")
        except sqlite3.Error:
            pass


__all__ = ["GridSearchPersistenceError", "GridSearchRepository"]
