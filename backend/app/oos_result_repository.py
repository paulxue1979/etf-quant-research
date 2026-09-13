"""SQLite persistence for the official PHASE 8F-4 OOS result summary."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from research.canonical import canonical_json
from research.exceptions import (
    OosFinalizationConflictError,
    OosFinalizationPersistenceError,
    OosResultIntegrityError,
)
from research.oos import OosEvaluationResult


class OosResultRepository:
    """Append-only official OOS results, unique per research protocol."""

    SCHEMA_VERSION = 1
    _SCHEMA_KEY = "oos_result_repository"

    def __init__(self, db_path: str | Path | None = None) -> None:
        self.db_path = (
            Path(db_path)
            if db_path is not None
            else Path(__file__).resolve().parents[2] / "data" / "strategy.db"
        )
        try:
            self.db_path.parent.mkdir(parents=True, exist_ok=True)
            connection = self._connect()
            try:
                self.ensure_schema(connection)
            finally:
                connection.close()
        except OosFinalizationPersistenceError:
            raise
        except (OSError, sqlite3.Error) as exc:
            raise OosFinalizationPersistenceError(
                "could not prepare OOS result database"
            ) from exc

    def _connect(self) -> sqlite3.Connection:
        try:
            connection = sqlite3.connect(self.db_path, timeout=30.0, isolation_level=None)
            connection.row_factory = sqlite3.Row
            connection.execute("PRAGMA foreign_keys = ON")
            connection.execute("PRAGMA busy_timeout = 30000")
            return connection
        except sqlite3.Error as exc:
            raise OosFinalizationPersistenceError("could not open OOS result database") from exc

    @classmethod
    def ensure_schema(cls, connection: sqlite3.Connection) -> None:
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
            CREATE TABLE IF NOT EXISTS research_oos_results (
                oos_result_id TEXT PRIMARY KEY,
                protocol_id TEXT NOT NULL UNIQUE,
                execution_id TEXT NOT NULL UNIQUE,
                selection_decision_id TEXT NOT NULL,
                strategy_freeze_id TEXT NOT NULL,
                strategy_version_id TEXT NOT NULL,
                strategy_content_hash TEXT NOT NULL,
                backtest_run_id TEXT NOT NULL UNIQUE,
                oos_start TEXT NOT NULL,
                oos_end TEXT NOT NULL,
                warmup_start TEXT NOT NULL,
                price_field_used TEXT NOT NULL,
                configuration_hash TEXT NOT NULL,
                engine_version TEXT NOT NULL,
                analytics_version TEXT NOT NULL,
                data_provenance_json TEXT NOT NULL,
                performance_summary_json TEXT NOT NULL,
                result_hash TEXT NOT NULL,
                created_at TEXT NOT NULL,
                result_json TEXT NOT NULL
            )
            """
        )
        connection.execute(
            "CREATE INDEX IF NOT EXISTS idx_oos_results_strategy "
            "ON research_oos_results(strategy_version_id, created_at DESC)"
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
            raise OosFinalizationPersistenceError("unsupported OOS result schema version")

    def create(self, result: OosEvaluationResult) -> OosEvaluationResult:
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            stored = self.persist_in_transaction(connection, result)
            connection.execute("COMMIT")
            return stored
        except (OosFinalizationConflictError, OosFinalizationPersistenceError):
            self._rollback(connection)
            raise
        except sqlite3.Error as exc:
            self._rollback(connection)
            raise OosFinalizationPersistenceError("could not persist OOS result") from exc
        finally:
            connection.close()

    @classmethod
    def persist_in_transaction(
        cls,
        connection: sqlite3.Connection,
        result: OosEvaluationResult,
    ) -> OosEvaluationResult:
        if not isinstance(result, OosEvaluationResult):
            raise OosFinalizationPersistenceError("OOS result is invalid")
        existing = connection.execute(
            "SELECT * FROM research_oos_results WHERE protocol_id = ?",
            (result.protocol_id,),
        ).fetchone()
        if existing is not None:
            restored = cls._decode(existing)
            if restored.to_dict() == result.to_dict():
                return restored
            raise OosFinalizationConflictError(
                "protocol already has a different official OOS result"
            )
        try:
            connection.execute(
                """
                INSERT INTO research_oos_results(
                    oos_result_id, protocol_id, execution_id, selection_decision_id,
                    strategy_freeze_id, strategy_version_id, strategy_content_hash,
                    backtest_run_id, oos_start, oos_end, warmup_start, price_field_used,
                    configuration_hash, engine_version, analytics_version,
                    data_provenance_json, performance_summary_json, result_hash,
                    created_at, result_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                cls._values(result),
            )
        except sqlite3.IntegrityError as exc:
            raise OosFinalizationConflictError(
                "official OOS result identity conflicts with an existing result"
            ) from exc
        return result

    def get_by_protocol(self, protocol_id: str) -> OosEvaluationResult | None:
        connection = self._connect()
        try:
            row = connection.execute(
                "SELECT * FROM research_oos_results WHERE protocol_id = ?", (protocol_id,)
            ).fetchone()
            return None if row is None else self._decode(row)
        except OosResultIntegrityError:
            raise
        except sqlite3.Error as exc:
            raise OosFinalizationPersistenceError("could not load OOS result") from exc
        finally:
            connection.close()

    @classmethod
    def get_by_protocol_in_transaction(
        cls, connection: sqlite3.Connection, protocol_id: str
    ) -> OosEvaluationResult | None:
        row = connection.execute(
            "SELECT * FROM research_oos_results WHERE protocol_id = ?", (protocol_id,)
        ).fetchone()
        return None if row is None else cls._decode(row)

    @classmethod
    def _decode(cls, row: sqlite3.Row) -> OosEvaluationResult:
        try:
            raw = row["result_json"]
            payload = json.loads(raw, parse_constant=_reject_nonfinite)
            if canonical_json(payload) != raw:
                raise OosResultIntegrityError("stored OOS result is not canonical JSON")
            result = OosEvaluationResult.from_dict(payload)
            if result.result_hash != row["result_hash"]:
                raise OosResultIntegrityError("stored OOS result hash mismatch")
            if (
                result.protocol_id != row["protocol_id"]
                or result.backtest_run_id != row["backtest_run_id"]
            ):
                raise OosResultIntegrityError("stored OOS result identity mismatch")
            return result
        except OosResultIntegrityError:
            raise
        except (TypeError, ValueError, KeyError, OverflowError, json.JSONDecodeError) as exc:
            raise OosResultIntegrityError("stored OOS result failed integrity checks") from exc

    @staticmethod
    def _values(result: OosEvaluationResult) -> tuple[object, ...]:
        payload = canonical_json(result.to_dict())
        return (
            result.oos_result_id,
            result.protocol_id,
            result.execution_id,
            result.selection_decision_id,
            result.strategy_freeze_id,
            result.strategy_version_id,
            result.strategy_content_hash,
            result.backtest_run_id,
            result.oos_start.isoformat(),
            result.oos_end.isoformat(),
            result.warmup_start.isoformat(),
            result.price_field_used.value,
            result.configuration_hash,
            result.engine_version,
            result.analytics_version,
            canonical_json(result.data_provenance.to_dict()),
            canonical_json(result.performance_summary.to_dict()),
            result.result_hash,
            result.created_at.isoformat(),
            payload,
        )

    @staticmethod
    def _rollback(connection: sqlite3.Connection) -> None:
        try:
            connection.execute("ROLLBACK")
        except sqlite3.Error:
            pass


def _reject_nonfinite(value: str) -> object:
    raise ValueError(f"non-finite JSON value: {value}")


__all__ = ["OosResultRepository"]
