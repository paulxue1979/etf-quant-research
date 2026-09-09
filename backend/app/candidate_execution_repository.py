"""Durable PHASE 8D-2 candidate execution state and audit events."""

from __future__ import annotations

import json
import re
import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

from research.canonical import canonical_json
from research.exceptions import (
    CandidateExecutionConflictError,
    CandidateExecutionPersistenceError,
    ExecutionStateTransitionError,
)
from research.execution import (
    CandidateExecution,
    CandidateExecutionStatus,
    ExecutionEvent,
    candidate_id_for,
)


class CandidateExecutionRepository:
    """SQLite persistence for one immutable candidate execution lifecycle.

    This repository deliberately stores lifecycle metadata separately from the
    PHASE 8C candidate definition. It never runs a strategy or backtest.
    """

    SCHEMA_VERSION = 1
    _SCHEMA_KEY = "candidate_execution_repository"
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
            raise CandidateExecutionPersistenceError(
                "could not prepare candidate execution database"
            ) from exc

    def _connect(self) -> sqlite3.Connection:
        try:
            connection = sqlite3.connect(self.db_path, timeout=30.0, isolation_level=None)
            connection.row_factory = sqlite3.Row
            connection.execute("PRAGMA foreign_keys = ON")
            connection.execute("PRAGMA busy_timeout = 30000")
            return connection
        except sqlite3.Error as exc:
            raise CandidateExecutionPersistenceError(
                "could not open candidate execution database"
            ) from exc

    def _initialize(self) -> None:
        connection = self._connect()
        try:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS schema_metadata (
                    schema_key TEXT PRIMARY KEY,
                    schema_version INTEGER NOT NULL
                );
                CREATE TABLE IF NOT EXISTS candidate_executions (
                    execution_id TEXT PRIMARY KEY,
                    experiment_id TEXT NOT NULL,
                    candidate_id TEXT NOT NULL,
                    candidate_index INTEGER NOT NULL CHECK(candidate_index >= 0),
                    experiment_hash TEXT NOT NULL,
                    parameter_set_hash TEXT NOT NULL,
                    parameter_binding_hash TEXT NOT NULL,
                    base_strategy_version_id TEXT NOT NULL,
                    base_strategy_version_hash TEXT NOT NULL,
                    derived_strategy_version_id TEXT,
                    derived_strategy_version_hash TEXT,
                    status TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    max_retries INTEGER NOT NULL CHECK(max_retries >= 0),
                    retry_count INTEGER NOT NULL CHECK(retry_count >= 0),
                    claimed_at TEXT,
                    claimed_by TEXT,
                    lease_expires_at TEXT,
                    completed_at TEXT,
                    failed_at TEXT,
                    failure_code TEXT,
                    failure_message TEXT,
                    failure_retryable INTEGER,
                    identity_hash TEXT NOT NULL,
                    canonical_json TEXT NOT NULL,
                    UNIQUE(experiment_id, candidate_index),
                    FOREIGN KEY(experiment_id, candidate_index)
                        REFERENCES experiment_candidates(experiment_id, candidate_index)
                );
                CREATE TABLE IF NOT EXISTS experiment_execution_events (
                    event_sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                    event_id TEXT NOT NULL UNIQUE,
                    execution_id TEXT NOT NULL,
                    experiment_id TEXT NOT NULL,
                    candidate_id TEXT NOT NULL,
                    event_type TEXT NOT NULL,
                    from_state TEXT,
                    to_state TEXT,
                    occurred_at TEXT NOT NULL,
                    actor TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    schema_version INTEGER NOT NULL,
                    FOREIGN KEY(execution_id) REFERENCES candidate_executions(execution_id)
                );
                CREATE INDEX IF NOT EXISTS idx_candidate_execution_status
                    ON candidate_executions(experiment_id, status, candidate_index);
                CREATE INDEX IF NOT EXISTS idx_execution_events
                    ON experiment_execution_events(execution_id, event_sequence);
                """
            )
            row = connection.execute(
                "SELECT schema_version FROM schema_metadata WHERE schema_key = ?",
                (self._SCHEMA_KEY,),
            ).fetchone()
            if row is None:
                connection.execute(
                    "INSERT INTO schema_metadata(schema_key, schema_version) VALUES (?, ?)",
                    (self._SCHEMA_KEY, self.SCHEMA_VERSION),
                )
            elif int(row["schema_version"]) != self.SCHEMA_VERSION:
                raise CandidateExecutionPersistenceError(
                    "unsupported candidate execution database schema version"
                )
        except CandidateExecutionPersistenceError:
            raise
        except sqlite3.Error as exc:
            raise CandidateExecutionPersistenceError(
                "could not initialize candidate execution database"
            ) from exc
        finally:
            connection.close()

    def create_execution(self, execution: CandidateExecution) -> CandidateExecution:
        """Create a PENDING execution, or return the same immutable identity idempotently."""
        self._validate_execution(execution, require_pending=True)
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            self._validate_candidate_binding(connection, execution)
            existing = connection.execute(
                "SELECT * FROM candidate_executions WHERE experiment_id = ? "
                "AND candidate_index = ?",
                (execution.experiment_id, execution.candidate_index),
            ).fetchone()
            if existing is not None:
                restored = self._decode_execution(existing)
                if restored.identity_payload != execution.identity_payload:
                    raise CandidateExecutionConflictError(
                        "candidate already has a different execution identity"
                    )
                connection.execute("COMMIT")
                return restored
            payload = self._payload(execution)
            connection.execute(
                """
                INSERT INTO candidate_executions(
                    execution_id, experiment_id, candidate_id, candidate_index,
                    experiment_hash, parameter_set_hash, parameter_binding_hash,
                    base_strategy_version_id, base_strategy_version_hash,
                    derived_strategy_version_id, derived_strategy_version_hash,
                    status, created_at, max_retries, retry_count,
                    claimed_at, claimed_by, lease_expires_at, completed_at, failed_at,
                    failure_code, failure_message, failure_retryable, identity_hash,
                    canonical_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                self._execution_values(execution, payload),
            )
            self._insert_event(
                connection,
                execution,
                "EXECUTION_CREATED",
                actor="system",
                occurred_at=execution.created_at,
                from_state=None,
                to_state=CandidateExecutionStatus.PENDING,
                payload={"identity_hash": execution.identity_hash},
            )
            connection.execute("COMMIT")
            return execution
        except (CandidateExecutionConflictError, CandidateExecutionPersistenceError):
            self._rollback(connection)
            raise
        except sqlite3.IntegrityError as exc:
            self._rollback(connection)
            raise CandidateExecutionConflictError("candidate execution identity conflicts") from exc
        except sqlite3.Error as exc:
            self._rollback(connection)
            raise CandidateExecutionPersistenceError(
                "could not persist candidate execution"
            ) from exc
        finally:
            connection.close()

    def get_execution(self, execution_id: str) -> CandidateExecution | None:
        connection = self._connect()
        try:
            row = connection.execute(
                "SELECT * FROM candidate_executions WHERE execution_id = ?", (execution_id,)
            ).fetchone()
            return None if row is None else self._decode_execution(row)
        except CandidateExecutionPersistenceError:
            raise
        except (sqlite3.Error, TypeError, ValueError, OverflowError, json.JSONDecodeError) as exc:
            raise CandidateExecutionPersistenceError(
                "stored candidate execution failed integrity checks"
            ) from exc
        finally:
            connection.close()

    def get_execution_by_candidate(
        self, experiment_id: str, candidate_index: int
    ) -> CandidateExecution | None:
        connection = self._connect()
        try:
            row = connection.execute(
                "SELECT * FROM candidate_executions WHERE experiment_id = ? "
                "AND candidate_index = ?",
                (experiment_id, candidate_index),
            ).fetchone()
            return None if row is None else self._decode_execution(row)
        except CandidateExecutionPersistenceError:
            raise
        except (sqlite3.Error, TypeError, ValueError, OverflowError, json.JSONDecodeError) as exc:
            raise CandidateExecutionPersistenceError(
                "stored candidate execution failed integrity checks"
            ) from exc
        finally:
            connection.close()

    def claim_candidate(
        self,
        experiment_id: str,
        candidate_index: int,
        claimed_by: str,
        claimed_at: datetime,
        lease_expires_at: datetime,
    ) -> CandidateExecution:
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            execution = self._load_candidate(connection, experiment_id, candidate_index)
            if execution.status is CandidateExecutionStatus.RUNNING:
                if (
                    execution.lease_expires_at is not None
                    and execution.lease_expires_at <= claimed_at
                ):
                    recovered = execution.recover_expired(now=claimed_at)
                    self._update(connection, recovered)
                    self._insert_event(
                        connection,
                        recovered,
                        "LEASE_RECOVERED",
                        actor="recovery",
                        occurred_at=claimed_at,
                        from_state=CandidateExecutionStatus.RUNNING,
                        to_state=CandidateExecutionStatus.PENDING,
                        payload={"previous_owner": execution.claimed_by},
                    )
                    execution = recovered
            claimed = execution.claim(
                claimed_at=claimed_at,
                claimed_by=claimed_by,
                lease_expires_at=lease_expires_at,
            )
            self._update(connection, claimed)
            self._insert_event(
                connection,
                claimed,
                "EXECUTION_CLAIMED",
                actor=claimed_by,
                occurred_at=claimed_at,
                from_state=execution.status,
                to_state=claimed.status,
                payload={"lease_expires_at": lease_expires_at.isoformat()},
            )
            connection.execute("COMMIT")
            return claimed
        except (ExecutionStateTransitionError, CandidateExecutionPersistenceError):
            self._rollback(connection)
            raise
        except sqlite3.Error as exc:
            self._rollback(connection)
            raise CandidateExecutionPersistenceError("could not claim candidate execution") from exc
        finally:
            connection.close()

    def renew_lease(
        self, execution_id: str, actor: str, now: datetime, lease_expires_at: datetime
    ) -> CandidateExecution:
        return self._transition(
            execution_id,
            lambda execution: execution.renew(
                actor=actor, now=now, lease_expires_at=lease_expires_at
            ),
            event_type="LEASE_RENEWED",
            actor=actor,
            occurred_at=now,
            payload={"lease_expires_at": lease_expires_at.isoformat()},
        )

    def mark_completed(
        self, execution_id: str, actor: str, now: datetime, completed_at: datetime
    ) -> CandidateExecution:
        return self._transition(
            execution_id,
            lambda execution: execution.complete(actor=actor, now=now, completed_at=completed_at),
            event_type="EXECUTION_COMPLETED",
            actor=actor,
            occurred_at=completed_at,
            payload={},
        )

    def mark_failed(
        self,
        execution_id: str,
        actor: str,
        now: datetime,
        failed_at: datetime,
        failure_code: str,
        failure_message: str,
        retryable: bool,
    ) -> CandidateExecution:
        return self._transition(
            execution_id,
            lambda execution: execution.fail(
                actor=actor,
                now=now,
                failed_at=failed_at,
                failure_code=failure_code,
                failure_message=failure_message,
                retryable=retryable,
            ),
            event_type="EXECUTION_FAILED",
            actor=actor,
            occurred_at=failed_at,
            payload={"failure_code": failure_code, "retryable": retryable},
        )

    def retry_failed(
        self, execution_id: str, actor: str = "system", now: datetime | None = None
    ) -> CandidateExecution:
        occurred_at = now or datetime.now(UTC)
        return self._transition(
            execution_id,
            lambda execution: execution.retry(),
            event_type="RETRY_SCHEDULED",
            actor=actor,
            occurred_at=occurred_at,
            payload={},
        )

    def recover_expired_executions(
        self, now: datetime, actor: str = "recovery"
    ) -> tuple[CandidateExecution, ...]:
        connection = self._connect()
        recovered: list[CandidateExecution] = []
        try:
            connection.execute("BEGIN IMMEDIATE")
            rows = connection.execute(
                "SELECT * FROM candidate_executions WHERE status = ? AND lease_expires_at <= ? "
                "ORDER BY experiment_id, candidate_index",
                (CandidateExecutionStatus.RUNNING.value, now.isoformat()),
            ).fetchall()
            for row in rows:
                execution = self._decode_execution(row)
                updated = execution.recover_expired(now=now)
                self._update(connection, updated)
                self._insert_event(
                    connection,
                    updated,
                    "LEASE_RECOVERED",
                    actor=actor,
                    occurred_at=now,
                    from_state=execution.status,
                    to_state=updated.status,
                    payload={"previous_owner": execution.claimed_by},
                )
                recovered.append(updated)
            connection.execute("COMMIT")
            return tuple(recovered)
        except (CandidateExecutionPersistenceError, ExecutionStateTransitionError):
            self._rollback(connection)
            raise
        except sqlite3.Error as exc:
            self._rollback(connection)
            raise CandidateExecutionPersistenceError(
                "could not recover candidate executions"
            ) from exc
        finally:
            connection.close()

    def list_events(self, execution_id: str) -> tuple[ExecutionEvent, ...]:
        connection = self._connect()
        try:
            rows = connection.execute(
                "SELECT * FROM experiment_execution_events WHERE execution_id = ? "
                "ORDER BY event_sequence",
                (execution_id,),
            ).fetchall()
            return tuple(self._decode_event(row) for row in rows)
        except CandidateExecutionPersistenceError:
            raise
        except (sqlite3.Error, TypeError, ValueError, OverflowError, json.JSONDecodeError) as exc:
            raise CandidateExecutionPersistenceError(
                "stored execution events failed integrity checks"
            ) from exc
        finally:
            connection.close()

    def _transition(
        self,
        execution_id: str,
        transition: Any,
        *,
        event_type: str,
        actor: str,
        occurred_at: datetime,
        payload: dict[str, Any],
    ) -> CandidateExecution:
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            execution = self._load_execution(connection, execution_id)
            updated = transition(execution)
            self._update(connection, updated)
            self._insert_event(
                connection,
                updated,
                event_type,
                actor=actor,
                occurred_at=occurred_at,
                from_state=execution.status,
                to_state=updated.status,
                payload=payload,
            )
            connection.execute("COMMIT")
            return updated
        except (ExecutionStateTransitionError, CandidateExecutionPersistenceError):
            self._rollback(connection)
            raise
        except sqlite3.Error as exc:
            self._rollback(connection)
            raise CandidateExecutionPersistenceError(
                "could not update candidate execution"
            ) from exc
        finally:
            connection.close()

    def _validate_candidate_binding(
        self, connection: sqlite3.Connection, execution: CandidateExecution
    ) -> None:
        experiment = connection.execute(
            "SELECT * FROM experiments WHERE experiment_id = ?", (execution.experiment_id,)
        ).fetchone()
        if experiment is None:
            raise CandidateExecutionPersistenceError("EXPERIMENT_NOT_FOUND")
        if experiment["content_hash"] != execution.experiment_hash:
            raise CandidateExecutionConflictError(
                "experiment hash does not match stored experiment"
            )
        if experiment["base_strategy_version_id"] != execution.base_strategy_version_id:
            raise CandidateExecutionConflictError(
                "base strategy version id does not match experiment"
            )
        if experiment["base_strategy_version_hash"] != execution.base_strategy_version_hash:
            raise CandidateExecutionConflictError(
                "base strategy version hash does not match experiment"
            )
        candidate = connection.execute(
            "SELECT * FROM experiment_candidates WHERE experiment_id = ? AND candidate_index = ?",
            (execution.experiment_id, execution.candidate_index),
        ).fetchone()
        if candidate is None:
            raise CandidateExecutionPersistenceError("CANDIDATE_NOT_FOUND")
        expected_candidate_id = candidate_id_for(
            execution.experiment_id,
            execution.candidate_index,
            str(candidate["parameter_set_hash"]),
        )
        if candidate["parameter_set_hash"] != execution.parameter_set_hash:
            raise CandidateExecutionConflictError("parameter set hash does not match candidate")
        if expected_candidate_id != execution.candidate_id:
            raise CandidateExecutionConflictError("candidate id does not match candidate identity")

    def _load_candidate(
        self, connection: sqlite3.Connection, experiment_id: str, candidate_index: int
    ) -> CandidateExecution:
        row = connection.execute(
            "SELECT * FROM candidate_executions WHERE experiment_id = ? AND candidate_index = ?",
            (experiment_id, candidate_index),
        ).fetchone()
        if row is None:
            raise CandidateExecutionPersistenceError("CANDIDATE_EXECUTION_NOT_FOUND")
        return self._decode_execution(row)

    def _load_execution(
        self, connection: sqlite3.Connection, execution_id: str
    ) -> CandidateExecution:
        row = connection.execute(
            "SELECT * FROM candidate_executions WHERE execution_id = ?", (execution_id,)
        ).fetchone()
        if row is None:
            raise CandidateExecutionPersistenceError("CANDIDATE_EXECUTION_NOT_FOUND")
        return self._decode_execution(row)

    def _update(self, connection: sqlite3.Connection, execution: CandidateExecution) -> None:
        payload = self._payload(execution)
        cursor = connection.execute(
            """
            UPDATE candidate_executions SET
                status = ?, created_at = ?, max_retries = ?, retry_count = ?,
                claimed_at = ?, claimed_by = ?, lease_expires_at = ?, completed_at = ?,
                failed_at = ?, failure_code = ?, failure_message = ?, failure_retryable = ?,
                canonical_json = ?
            WHERE execution_id = ? AND identity_hash = ?
            """,
            (
                execution.status.value,
                execution.created_at.isoformat(),
                execution.max_retries,
                execution.retry_count,
                _iso(execution.claimed_at),
                execution.claimed_by,
                _iso(execution.lease_expires_at),
                _iso(execution.completed_at),
                _iso(execution.failed_at),
                execution.failure_code,
                execution.failure_message,
                _bool_int(execution.failure_retryable),
                payload,
                execution.execution_id,
                execution.identity_hash,
            ),
        )
        if cursor.rowcount != 1:
            raise CandidateExecutionConflictError("candidate execution changed concurrently")

    def _insert_event(
        self,
        connection: sqlite3.Connection,
        execution: CandidateExecution,
        event_type: str,
        *,
        actor: str,
        occurred_at: datetime,
        from_state: CandidateExecutionStatus | None,
        to_state: CandidateExecutionStatus | None,
        payload: dict[str, Any],
    ) -> None:
        event = ExecutionEvent(
            event_id=uuid4().hex,
            execution_id=execution.execution_id,
            experiment_id=execution.experiment_id,
            event_type=event_type,
            occurred_at=occurred_at,
            actor=actor,
            payload=payload,
            from_state=from_state,
            to_state=to_state,
        )
        connection.execute(
            """
            INSERT INTO experiment_execution_events(
                event_id, execution_id, experiment_id, candidate_id, event_type,
                from_state, to_state, occurred_at, actor, payload_json, schema_version
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                event.event_id,
                event.execution_id,
                event.experiment_id,
                execution.candidate_id,
                event.event_type,
                event.from_state.value if event.from_state else None,
                event.to_state.value if event.to_state else None,
                event.occurred_at.isoformat(),
                event.actor,
                self._safe_json(event.payload),
                event.schema_version,
            ),
        )

    def _validate_execution(self, execution: CandidateExecution, *, require_pending: bool) -> None:
        if not isinstance(execution, CandidateExecution):
            raise CandidateExecutionPersistenceError("execution is invalid")
        if require_pending and execution.status is not CandidateExecutionStatus.PENDING:
            raise CandidateExecutionPersistenceError("new execution must start PENDING")

    @classmethod
    def _payload(cls, execution: CandidateExecution) -> str:
        return cls._safe_json(execution.to_dict())

    @classmethod
    def _safe_json(cls, payload: object) -> str:
        cls._assert_safe(payload)
        try:
            return canonical_json(payload)
        except (TypeError, ValueError, OverflowError) as exc:
            raise CandidateExecutionPersistenceError("payload is not canonical JSON") from exc

    @classmethod
    def _assert_safe(cls, payload: object) -> None:
        if isinstance(payload, dict):
            for key, value in payload.items():
                if cls._SENSITIVE.search(str(key)):
                    raise CandidateExecutionPersistenceError(
                        "sensitive execution fields are not allowed"
                    )
                cls._assert_safe(value)
        elif isinstance(payload, (list, tuple)):
            for value in payload:
                cls._assert_safe(value)
        elif isinstance(payload, str) and re.search(
            r"(?:TIINGO_API_KEY|API_KEY|BEGIN\s+PRIVATE\s+KEY)", payload, re.IGNORECASE
        ):
            raise CandidateExecutionPersistenceError("sensitive execution values are not allowed")

    @staticmethod
    def _execution_values(execution: CandidateExecution, payload: str) -> tuple[object, ...]:
        return (
            execution.execution_id,
            execution.experiment_id,
            execution.candidate_id,
            execution.candidate_index,
            execution.experiment_hash,
            execution.parameter_set_hash,
            execution.parameter_binding_hash,
            execution.base_strategy_version_id,
            execution.base_strategy_version_hash,
            execution.derived_strategy_version_id,
            execution.derived_strategy_version_hash,
            execution.status.value,
            execution.created_at.isoformat(),
            execution.max_retries,
            execution.retry_count,
            _iso(execution.claimed_at),
            execution.claimed_by,
            _iso(execution.lease_expires_at),
            _iso(execution.completed_at),
            _iso(execution.failed_at),
            execution.failure_code,
            execution.failure_message,
            _bool_int(execution.failure_retryable),
            execution.identity_hash,
            payload,
        )

    @classmethod
    def _decode_execution(cls, row: sqlite3.Row) -> CandidateExecution:
        raw = row["canonical_json"]
        try:
            payload = json.loads(raw, parse_constant=_reject_nonfinite)
            if raw != cls._safe_json(payload):
                raise CandidateExecutionPersistenceError("stored execution is not canonical JSON")
            execution = CandidateExecution.from_dict(payload)
        except CandidateExecutionPersistenceError:
            raise
        except (TypeError, ValueError, OverflowError, json.JSONDecodeError) as exc:
            raise CandidateExecutionPersistenceError(
                "stored candidate execution failed integrity checks"
            ) from exc
        if execution.identity_hash != row["identity_hash"]:
            raise CandidateExecutionPersistenceError("stored execution identity hash mismatch")
        expected_columns = {
            "execution_id": execution.execution_id,
            "experiment_id": execution.experiment_id,
            "candidate_id": execution.candidate_id,
            "candidate_index": execution.candidate_index,
            "experiment_hash": execution.experiment_hash,
            "parameter_set_hash": execution.parameter_set_hash,
            "parameter_binding_hash": execution.parameter_binding_hash,
            "base_strategy_version_id": execution.base_strategy_version_id,
            "base_strategy_version_hash": execution.base_strategy_version_hash,
            "derived_strategy_version_id": execution.derived_strategy_version_id,
            "derived_strategy_version_hash": execution.derived_strategy_version_hash,
            "status": execution.status.value,
            "created_at": execution.created_at.isoformat(),
            "max_retries": execution.max_retries,
            "retry_count": execution.retry_count,
            "claimed_at": _iso(execution.claimed_at),
            "claimed_by": execution.claimed_by,
            "lease_expires_at": _iso(execution.lease_expires_at),
            "completed_at": _iso(execution.completed_at),
            "failed_at": _iso(execution.failed_at),
            "failure_code": execution.failure_code,
            "failure_message": execution.failure_message,
            "failure_retryable": _bool_int(execution.failure_retryable),
            "identity_hash": execution.identity_hash,
        }
        if any(row[key] != value for key, value in expected_columns.items()):
            raise CandidateExecutionPersistenceError(
                "stored execution columns do not match payload"
            )
        return execution

    @classmethod
    def _decode_event(cls, row: sqlite3.Row) -> ExecutionEvent:
        try:
            payload = json.loads(row["payload_json"], parse_constant=_reject_nonfinite)
            if row["payload_json"] != cls._safe_json(payload):
                raise CandidateExecutionPersistenceError(
                    "stored execution event is not canonical JSON"
                )
            event = ExecutionEvent(
                event_id=row["event_id"],
                execution_id=row["execution_id"],
                experiment_id=row["experiment_id"],
                event_type=row["event_type"],
                occurred_at=datetime.fromisoformat(row["occurred_at"]),
                actor=row["actor"],
                payload=payload,
                from_state=row["from_state"],
                to_state=row["to_state"],
                schema_version=row["schema_version"],
                sequence=row["event_sequence"],
            )
            return event
        except CandidateExecutionPersistenceError:
            raise
        except (TypeError, ValueError, OverflowError, json.JSONDecodeError) as exc:
            raise CandidateExecutionPersistenceError("stored execution event is malformed") from exc

    @staticmethod
    def _rollback(connection: sqlite3.Connection) -> None:
        try:
            connection.execute("ROLLBACK")
        except sqlite3.Error:
            pass


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value is not None else None


def _bool_int(value: bool | None) -> int | None:
    return None if value is None else int(value)


def _reject_nonfinite(value: str) -> None:
    raise ValueError(f"non-finite JSON constant {value}")


__all__ = ["CandidateExecutionRepository"]
