"""SQLite persistence for immutable PHASE 8D-4 experiment results."""

from __future__ import annotations

import json
import re
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from research.canonical import canonical_json
from research.exceptions import (
    ExperimentResultConflictError,
    ExperimentResultPersistenceError,
)
from research.experiment_result import ExperimentResult


@dataclass(frozen=True)
class ExperimentResultSummary:
    """Lightweight experiment result row; ``result_json`` is never selected."""

    experiment_result_id: str | None
    experiment_id: str
    candidate_id: str | None
    candidate_index: int
    parameter_set_hash: str
    result_status: str
    derived_strategy_version_id: str | None
    derived_strategy_version_hash: str | None
    backtest_run_id: str | None
    is_start: str | None
    is_end: str | None
    price_field_used: str | None
    engine_version: str | None
    analysis_version: str | None
    performance_summary: dict[str, Any]
    result_hash: str | None
    created_at: str | None
    completed_at: str | None = None
    failure_code: str | None = None
    failure_summary: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return dict(self.__dict__)


class ExperimentResultRepository:
    """Store one official result per experiment candidate, append-only."""

    SCHEMA_VERSION = 1
    _SCHEMA_KEY = "experiment_result_repository"
    _SENSITIVE = re.compile(
        r"(?:api[_-]?key|access[_-]?token|authorization|credential|password|secret)",
        re.IGNORECASE,
    )

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
            raise ExperimentResultPersistenceError(
                "could not prepare experiment result database"
            ) from exc

    def _connect(self) -> sqlite3.Connection:
        try:
            connection = sqlite3.connect(self.db_path, timeout=30.0, isolation_level=None)
            connection.row_factory = sqlite3.Row
            connection.execute("PRAGMA foreign_keys = ON")
            connection.execute("PRAGMA busy_timeout = 30000")
            return connection
        except sqlite3.Error as exc:
            raise ExperimentResultPersistenceError(
                "could not open experiment result database"
            ) from exc

    def _initialize(self) -> None:
        connection = self._connect()
        try:
            self.ensure_schema(connection)
        except ExperimentResultPersistenceError:
            raise
        except sqlite3.Error as exc:
            raise ExperimentResultPersistenceError(
                "could not initialize experiment result database"
            ) from exc
        finally:
            connection.close()

    @classmethod
    def ensure_schema(cls, connection: sqlite3.Connection) -> None:
        """Ensure the result schema exists on an already-open connection."""
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS schema_metadata (
                schema_key TEXT PRIMARY KEY,
                schema_version INTEGER NOT NULL
            )
            """
        )
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS research_experiment_results (
                experiment_result_id TEXT PRIMARY KEY,
                experiment_id TEXT NOT NULL,
                candidate_id TEXT NOT NULL,
                candidate_index INTEGER NOT NULL CHECK(candidate_index >= 0),
                parameter_set_hash TEXT NOT NULL,
                parameter_space_hash TEXT NOT NULL,
                candidate_set_hash TEXT NOT NULL,
                base_strategy_version_id TEXT NOT NULL,
                base_strategy_version_hash TEXT NOT NULL,
                derived_strategy_version_id TEXT NOT NULL,
                derived_strategy_version_hash TEXT NOT NULL,
                binding_hash TEXT NOT NULL,
                backtest_run_id TEXT NOT NULL,
                is_start TEXT NOT NULL,
                is_end TEXT NOT NULL,
                warmup_start TEXT,
                warmup_end TEXT,
                price_field_used TEXT NOT NULL,
                backtest_configuration_hash TEXT NOT NULL,
                engine_version TEXT NOT NULL,
                analysis_version TEXT NOT NULL,
                data_snapshot_reference_json TEXT NOT NULL,
                performance_summary_json TEXT NOT NULL,
                result_hash TEXT NOT NULL,
                created_at TEXT NOT NULL,
                result_json TEXT NOT NULL,
                UNIQUE(experiment_id, candidate_id)
            )
            """
        )
        connection.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_experiment_results_experiment
                ON research_experiment_results(experiment_id, candidate_index)
            """
        )
        row = connection.execute(
            "SELECT schema_version FROM schema_metadata WHERE schema_key = ?",
            (cls._SCHEMA_KEY,),
        ).fetchone()
        if row is None:
            connection.execute(
                "INSERT INTO schema_metadata(schema_key, schema_version) VALUES (?, ?)",
                (cls._SCHEMA_KEY, cls.SCHEMA_VERSION),
            )
        elif int(row["schema_version"]) != cls.SCHEMA_VERSION:
            raise ExperimentResultPersistenceError(
                "unsupported experiment result database schema version"
            )

    def create(self, result: ExperimentResult) -> ExperimentResult:
        """Insert an immutable result or return the same result idempotently."""
        if not isinstance(result, ExperimentResult):
            raise ExperimentResultPersistenceError("experiment result is invalid")
        payload = self._safe_json(result.to_dict())
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            stored = self.persist_in_transaction(connection, result, payload=payload)
            connection.execute("COMMIT")
            return stored
        except (ExperimentResultConflictError, ExperimentResultPersistenceError):
            self._rollback(connection)
            raise
        except sqlite3.IntegrityError as exc:
            self._rollback(connection)
            raise ExperimentResultConflictError(
                "experiment result identity conflicts with an existing result"
            ) from exc
        except sqlite3.Error as exc:
            self._rollback(connection)
            raise ExperimentResultPersistenceError("could not persist experiment result") from exc
        finally:
            connection.close()

    @classmethod
    def persist_in_transaction(
        cls,
        connection: sqlite3.Connection,
        result: ExperimentResult,
        *,
        payload: str | None = None,
    ) -> ExperimentResult:
        """Persist one result without beginning or committing a transaction."""
        if not isinstance(result, ExperimentResult):
            raise ExperimentResultPersistenceError("experiment result is invalid")
        serialized = payload if payload is not None else cls._safe_json(result.to_dict())
        existing = connection.execute(
            "SELECT * FROM research_experiment_results "
            "WHERE experiment_id = ? AND candidate_id = ?",
            (result.experiment_id, result.candidate_id),
        ).fetchone()
        if existing is not None:
            restored = cls._decode(existing)
            if restored.hash_payload() == result.hash_payload():
                return restored
            raise ExperimentResultConflictError(
                "candidate already has a different experiment result"
            )
        try:
            connection.execute(
                """
                INSERT INTO research_experiment_results(
                    experiment_result_id, experiment_id, candidate_id, candidate_index,
                    parameter_set_hash, parameter_space_hash, candidate_set_hash,
                    base_strategy_version_id, base_strategy_version_hash,
                    derived_strategy_version_id, derived_strategy_version_hash, binding_hash,
                    backtest_run_id, is_start, is_end, warmup_start, warmup_end,
                    price_field_used, backtest_configuration_hash, engine_version,
                    analysis_version, data_snapshot_reference_json,
                    performance_summary_json, result_hash, created_at, result_json
                ) VALUES (
                    ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                    ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                    ?, ?, ?, ?, ?, ?
                )
                """,
                cls._values(result, serialized),
            )
        except sqlite3.IntegrityError as exc:
            raise ExperimentResultConflictError(
                "experiment result identity conflicts with an existing result"
            ) from exc
        return result

    @classmethod
    def get_by_candidate_in_transaction(
        cls,
        connection: sqlite3.Connection,
        experiment_id: str,
        candidate_id: str,
    ) -> ExperimentResult | None:
        """Read one result from a caller-owned transaction."""
        row = connection.execute(
            "SELECT * FROM research_experiment_results "
            "WHERE experiment_id = ? AND candidate_id = ?",
            (experiment_id, candidate_id),
        ).fetchone()
        return None if row is None else cls._decode(row)

    def get(self, experiment_result_id: str) -> ExperimentResult | None:
        connection = self._connect()
        try:
            row = connection.execute(
                "SELECT * FROM research_experiment_results WHERE experiment_result_id = ?",
                (experiment_result_id,),
            ).fetchone()
            return None if row is None else self._decode(row)
        except ExperimentResultPersistenceError:
            raise
        except (sqlite3.Error, TypeError, ValueError, OverflowError, json.JSONDecodeError) as exc:
            raise ExperimentResultPersistenceError(
                "stored experiment result failed integrity checks"
            ) from exc
        finally:
            connection.close()

    def get_by_candidate(self, experiment_id: str, candidate_id: str) -> ExperimentResult | None:
        connection = self._connect()
        try:
            row = connection.execute(
                "SELECT * FROM research_experiment_results "
                "WHERE experiment_id = ? AND candidate_id = ?",
                (experiment_id, candidate_id),
            ).fetchone()
            return None if row is None else self._decode(row)
        except ExperimentResultPersistenceError:
            raise
        except (sqlite3.Error, TypeError, ValueError, OverflowError, json.JSONDecodeError) as exc:
            raise ExperimentResultPersistenceError(
                "stored experiment result failed integrity checks"
            ) from exc
        finally:
            connection.close()

    def list(self, experiment_id: str | None = None) -> tuple[ExperimentResult, ...]:
        connection = self._connect()
        try:
            if experiment_id is None:
                rows = connection.execute(
                    "SELECT * FROM research_experiment_results "
                    "ORDER BY experiment_id, candidate_index"
                ).fetchall()
            else:
                rows = connection.execute(
                    "SELECT * FROM research_experiment_results "
                    "WHERE experiment_id = ? ORDER BY candidate_index",
                    (experiment_id,),
                ).fetchall()
            return tuple(self._decode(row) for row in rows)
        except ExperimentResultPersistenceError:
            raise
        except (sqlite3.Error, TypeError, ValueError, OverflowError, json.JSONDecodeError) as exc:
            raise ExperimentResultPersistenceError(
                "stored experiment results failed integrity checks"
            ) from exc
        finally:
            connection.close()

    def list_summaries(
        self, experiment_id: str, *, limit: int = 100, offset: int = 0
    ) -> tuple[int, tuple[ExperimentResultSummary, ...]]:
        """Return an experiment-scoped page without selecting result_json."""
        if not experiment_id or not 1 <= limit <= 500 or offset < 0:
            raise ExperimentResultPersistenceError("invalid experiment result summary page")
        connection = self._connect()
        try:
            candidate_table_count = int(
                connection.execute(
                    "SELECT COUNT(*) FROM sqlite_master WHERE type = 'table' "
                    "AND name IN ('experiment_candidates', 'candidate_executions')"
                ).fetchone()[0]
            )
            if candidate_table_count == 2:
                return self._list_candidate_summaries(
                    connection, experiment_id, limit=limit, offset=offset
                )
            total = int(
                connection.execute(
                    "SELECT COUNT(*) FROM research_experiment_results WHERE experiment_id = ?",
                    (experiment_id,),
                ).fetchone()[0]
            )
            rows = connection.execute(
                """
                SELECT experiment_result_id, experiment_id, candidate_id, candidate_index,
                       parameter_set_hash, derived_strategy_version_id,
                       derived_strategy_version_hash, backtest_run_id, is_start, is_end,
                       price_field_used, engine_version, analysis_version,
                       performance_summary_json, result_hash, created_at
                FROM research_experiment_results
                WHERE experiment_id = ?
                ORDER BY candidate_index ASC, experiment_result_id ASC
                LIMIT ? OFFSET ?
                """,
                (experiment_id, limit, offset),
            ).fetchall()
            summaries = tuple(self._decode_summary(row) for row in rows)
            return total, summaries
        except ExperimentResultPersistenceError:
            raise
        except (sqlite3.Error, TypeError, ValueError, OverflowError, json.JSONDecodeError) as exc:
            raise ExperimentResultPersistenceError(
                "stored experiment result summaries failed integrity checks"
            ) from exc
        finally:
            connection.close()

    @classmethod
    def _list_candidate_summaries(
        cls,
        connection: sqlite3.Connection,
        experiment_id: str,
        *,
        limit: int,
        offset: int,
    ) -> tuple[int, tuple[ExperimentResultSummary, ...]]:
        total = int(
            connection.execute(
                "SELECT COUNT(*) FROM experiment_candidates WHERE experiment_id = ?",
                (experiment_id,),
            ).fetchone()[0]
        )
        rows = connection.execute(
            """
            SELECT r.experiment_result_id, c.experiment_id,
                   COALESCE(r.candidate_id, e.candidate_id) AS candidate_id,
                   c.candidate_index, c.parameter_set_hash,
                   CASE
                       WHEN r.experiment_result_id IS NOT NULL THEN 'completed'
                       WHEN e.status IS NOT NULL THEN e.status
                       ELSE 'pending'
                   END AS result_status,
                   COALESCE(r.derived_strategy_version_id, e.derived_strategy_version_id)
                       AS derived_strategy_version_id,
                   COALESCE(r.derived_strategy_version_hash, e.derived_strategy_version_hash)
                       AS derived_strategy_version_hash,
                   r.backtest_run_id, r.is_start, r.is_end, r.price_field_used,
                   r.engine_version, r.analysis_version, r.performance_summary_json,
                   r.result_hash, COALESCE(r.created_at, e.created_at) AS created_at,
                   e.completed_at, e.failure_code, e.failure_message AS failure_summary
            FROM experiment_candidates c
            LEFT JOIN candidate_executions e
              ON e.experiment_id = c.experiment_id
             AND e.candidate_index = c.candidate_index
            LEFT JOIN research_experiment_results r
              ON r.experiment_id = c.experiment_id
             AND r.candidate_index = c.candidate_index
            WHERE c.experiment_id = ?
            ORDER BY c.candidate_index ASC
            LIMIT ? OFFSET ?
            """,
            (experiment_id, limit, offset),
        ).fetchall()
        return total, tuple(cls._decode_summary(row) for row in rows)

    def clear(self) -> None:
        """Remove results; intended for isolated tests only."""
        connection = self._connect()
        try:
            connection.execute("DELETE FROM research_experiment_results")
        except sqlite3.Error as exc:
            raise ExperimentResultPersistenceError("could not clear experiment results") from exc
        finally:
            connection.close()

    @classmethod
    def _values(cls, result: ExperimentResult, payload: str) -> tuple[object, ...]:
        return (
            result.experiment_result_id,
            result.experiment_id,
            result.candidate_id,
            result.candidate_index,
            result.parameter_set_hash,
            result.parameter_space_hash,
            result.candidate_set_hash,
            result.base_strategy_version_id,
            result.base_strategy_version_hash,
            result.derived_strategy_version_id,
            result.derived_strategy_version_hash,
            result.binding_hash,
            result.backtest_run_id,
            result.is_start.isoformat(),
            result.is_end.isoformat(),
            result.warmup_start.isoformat() if result.warmup_start else None,
            result.warmup_end.isoformat() if result.warmup_end else None,
            result.price_field_used.value,
            result.backtest_configuration_hash,
            result.engine_version,
            result.analysis_version,
            cls._safe_json(dict(result.data_snapshot_reference)),
            cls._safe_json(dict(result.performance_summary)),
            result.result_hash,
            result.created_at.isoformat(),
            payload,
        )

    @classmethod
    def _decode(cls, row: sqlite3.Row) -> ExperimentResult:
        try:
            raw = row["result_json"]
            payload = json.loads(raw, parse_constant=_reject_nonfinite)
            if raw != cls._safe_json(payload):
                raise ExperimentResultPersistenceError(
                    "stored experiment result is not canonical JSON"
                )
            result = ExperimentResult.from_dict(payload)
            if any(
                (
                    row["experiment_result_id"] != result.experiment_result_id,
                    row["experiment_id"] != result.experiment_id,
                    row["candidate_id"] != result.candidate_id,
                    int(row["candidate_index"]) != result.candidate_index,
                    row["parameter_set_hash"] != result.parameter_set_hash,
                    row["parameter_space_hash"] != result.parameter_space_hash,
                    row["candidate_set_hash"] != result.candidate_set_hash,
                    row["base_strategy_version_id"] != result.base_strategy_version_id,
                    row["base_strategy_version_hash"] != result.base_strategy_version_hash,
                    row["derived_strategy_version_id"] != result.derived_strategy_version_id,
                    row["derived_strategy_version_hash"] != result.derived_strategy_version_hash,
                    row["binding_hash"] != result.binding_hash,
                    row["backtest_run_id"] != result.backtest_run_id,
                    row["is_start"] != result.is_start.isoformat(),
                    row["is_end"] != result.is_end.isoformat(),
                    row["warmup_start"]
                    != (result.warmup_start.isoformat() if result.warmup_start else None),
                    row["warmup_end"]
                    != (result.warmup_end.isoformat() if result.warmup_end else None),
                    row["price_field_used"] != result.price_field_used.value,
                    row["backtest_configuration_hash"] != result.backtest_configuration_hash,
                    row["engine_version"] != result.engine_version,
                    row["analysis_version"] != result.analysis_version,
                    row["result_hash"] != result.result_hash,
                    row["created_at"] != result.created_at.isoformat(),
                    row["data_snapshot_reference_json"]
                    != cls._safe_json(dict(result.data_snapshot_reference)),
                    row["performance_summary_json"]
                    != cls._safe_json(dict(result.performance_summary)),
                )
            ):
                raise ExperimentResultPersistenceError(
                    "stored experiment result columns do not match payload"
                )
            return result
        except ExperimentResultPersistenceError:
            raise
        except (KeyError, TypeError, ValueError, OverflowError, json.JSONDecodeError) as exc:
            raise ExperimentResultPersistenceError(
                "stored experiment result failed integrity checks"
            ) from exc

    @classmethod
    def _decode_summary(cls, row: sqlite3.Row) -> ExperimentResultSummary:
        raw_summary = row["performance_summary_json"]
        performance_summary = (
            json.loads(raw_summary, parse_constant=_reject_nonfinite)
            if raw_summary is not None
            else {}
        )
        if not isinstance(performance_summary, dict):
            raise ExperimentResultPersistenceError("stored experiment result summary is invalid")
        cls._assert_safe(performance_summary)
        return ExperimentResultSummary(
            experiment_result_id=_optional_text(row["experiment_result_id"]),
            experiment_id=str(row["experiment_id"]),
            candidate_id=_optional_text(row["candidate_id"]),
            candidate_index=int(row["candidate_index"]),
            parameter_set_hash=str(row["parameter_set_hash"]),
            result_status=str(row["result_status"])
            if "result_status" in row.keys()
            else "completed",
            derived_strategy_version_id=_optional_text(row["derived_strategy_version_id"]),
            derived_strategy_version_hash=_optional_text(row["derived_strategy_version_hash"]),
            backtest_run_id=_optional_text(row["backtest_run_id"]),
            is_start=_optional_text(row["is_start"]),
            is_end=_optional_text(row["is_end"]),
            price_field_used=_optional_text(row["price_field_used"]),
            engine_version=_optional_text(row["engine_version"]),
            analysis_version=_optional_text(row["analysis_version"]),
            performance_summary=performance_summary,
            result_hash=_optional_text(row["result_hash"]),
            created_at=_optional_text(row["created_at"]),
            completed_at=(
                _optional_text(row["completed_at"]) if "completed_at" in row.keys() else None
            ),
            failure_code=(
                _optional_text(row["failure_code"]) if "failure_code" in row.keys() else None
            ),
            failure_summary=(
                _optional_text(row["failure_summary"]) if "failure_summary" in row.keys() else None
            ),
        )

    @classmethod
    def _safe_json(cls, payload: object) -> str:
        cls._assert_safe(payload)
        try:
            return canonical_json(payload)
        except (TypeError, ValueError, OverflowError) as exc:
            raise ExperimentResultPersistenceError(
                "experiment result payload is not canonical JSON"
            ) from exc

    @classmethod
    def _assert_safe(cls, payload: object) -> None:
        if isinstance(payload, dict):
            for key, value in payload.items():
                if cls._SENSITIVE.search(str(key)):
                    raise ExperimentResultPersistenceError(
                        "sensitive result fields are not allowed"
                    )
                cls._assert_safe(value)
        elif isinstance(payload, (list, tuple)):
            for value in payload:
                cls._assert_safe(value)
        elif isinstance(payload, str) and re.search(
            r"(?:TIINGO_API_KEY|API_KEY|BEGIN\s+PRIVATE\s+KEY)", payload, re.IGNORECASE
        ):
            raise ExperimentResultPersistenceError("sensitive result values are not allowed")

    @staticmethod
    def _rollback(connection: sqlite3.Connection) -> None:
        try:
            connection.execute("ROLLBACK")
        except sqlite3.Error:
            pass


def _optional_text(value: object) -> str | None:
    return None if value is None else str(value)


def _reject_nonfinite(value: str) -> None:
    raise ValueError(f"non-finite JSON constant {value}")


__all__ = ["ExperimentResultRepository", "ExperimentResultSummary"]
