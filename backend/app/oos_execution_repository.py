"""SQLite control-plane persistence for the PHASE 8F-2 OOS executor.

The repository owns only claims, leases, retries, recovery, and audit events.
It never evaluates a strategy, consumes OOS data, creates an OOS result, or
changes the Research Protocol state.
"""

from __future__ import annotations

import json
import re
import sqlite3
from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

from backend.app.research_protocol import (
    OOSObservationStatus,
    ProtocolStatus,
    ResearchProtocol,
    ResearchProtocolError,
)
from research.canonical import canonical_json
from research.exceptions import (
    OosExecutionError,
    OosExecutionIdentityConflictError,
    OosExecutionInProgressError,
    OosExecutionIntegrityError,
    OosExecutionNotFoundError,
    OosExecutionPersistenceError,
    OosOfficialResultExistsError,
    OosProtocolStateError,
)
from research.oos import OosEvaluationSpec, OosExecutionStatus
from research.oos_execution import OosExecution, OosExecutionEvent


class OosExecutionRepository:
    """Append-only-audit persistence for one execution lineage per protocol."""

    SCHEMA_VERSION = 1
    _SCHEMA_KEY = "oos_execution_repository"
    _SENSITIVE = re.compile(
        r"(?:api[_-]?key|access[_-]?token|authorization|credential|password|secret|private key)",
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
        except OosExecutionPersistenceError:
            raise
        except OSError as exc:
            raise OosExecutionPersistenceError("could not prepare OOS execution database") from exc

    def _connect(self) -> sqlite3.Connection:
        try:
            connection = sqlite3.connect(self.db_path, timeout=30.0, isolation_level=None)
            connection.row_factory = sqlite3.Row
            connection.execute("PRAGMA foreign_keys = ON")
            connection.execute("PRAGMA busy_timeout = 30000")
            return connection
        except sqlite3.Error as exc:
            raise OosExecutionPersistenceError("could not open OOS execution database") from exc

    def _initialize(self) -> None:
        connection = self._connect()
        try:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS schema_metadata (
                    schema_key TEXT PRIMARY KEY,
                    schema_version INTEGER NOT NULL
                );
                CREATE TABLE IF NOT EXISTS research_oos_executions (
                    execution_id TEXT PRIMARY KEY,
                    protocol_id TEXT NOT NULL UNIQUE,
                    oos_spec_hash TEXT NOT NULL,
                    selection_decision_id TEXT NOT NULL,
                    strategy_freeze_id TEXT NOT NULL,
                    strategy_version_id TEXT NOT NULL,
                    strategy_content_hash TEXT NOT NULL,
                    oos_start TEXT NOT NULL,
                    oos_end TEXT NOT NULL,
                    warmup_start TEXT NOT NULL,
                    configuration_hash TEXT NOT NULL,
                    status TEXT NOT NULL,
                    attempt_count INTEGER NOT NULL CHECK(attempt_count >= 0),
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    lease_token TEXT,
                    lease_owner TEXT,
                    lease_expires_at TEXT,
                    started_at TEXT,
                    finished_at TEXT,
                    failure_code TEXT,
                    failure_message_safe TEXT,
                    failure_retryable INTEGER,
                    identity_hash TEXT NOT NULL,
                    canonical_json TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS research_oos_execution_events (
                    event_sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                    event_id TEXT NOT NULL UNIQUE,
                    execution_id TEXT NOT NULL,
                    protocol_id TEXT NOT NULL,
                    event_type TEXT NOT NULL,
                    attempt_number INTEGER NOT NULL CHECK(attempt_number >= 0),
                    from_state TEXT,
                    to_state TEXT,
                    occurred_at TEXT NOT NULL,
                    actor TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    schema_version INTEGER NOT NULL,
                    FOREIGN KEY(execution_id) REFERENCES research_oos_executions(execution_id)
                );
                CREATE INDEX IF NOT EXISTS idx_oos_execution_events_execution
                    ON research_oos_execution_events(execution_id, event_sequence);
                CREATE INDEX IF NOT EXISTS idx_oos_execution_status
                    ON research_oos_executions(status, lease_expires_at);
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
                raise OosExecutionPersistenceError("unsupported OOS execution schema version")
        except OosExecutionPersistenceError:
            raise
        except sqlite3.Error as exc:
            raise OosExecutionPersistenceError("could not initialize OOS execution schema") from exc
        finally:
            connection.close()

    def get_or_create_execution(
        self, spec: OosEvaluationSpec, *, now: datetime | None = None
    ) -> OosExecution:
        if not isinstance(spec, OosEvaluationSpec):
            raise OosExecutionError("OOS spec is invalid", code="OOS_EXECUTION_INVALID")
        timestamp = now or datetime.now(UTC)
        candidate = OosExecution.pending(spec, now=timestamp)
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            existing_row = connection.execute(
                "SELECT * FROM research_oos_executions WHERE protocol_id = ?",
                (candidate.protocol_id,),
            ).fetchone()
            if existing_row is not None:
                existing = self._decode_execution(existing_row)
                if existing.identity_payload != candidate.identity_payload:
                    raise OosExecutionIdentityConflictError(
                        "protocol already has a different OOS execution identity"
                    )
                connection.execute("COMMIT")
                return existing
            self._assert_protocol_allows_execution(connection, candidate.protocol_id)
            payload = self._payload(candidate)
            connection.execute(
                """
                INSERT INTO research_oos_executions(
                    execution_id, protocol_id, oos_spec_hash, selection_decision_id,
                    strategy_freeze_id, strategy_version_id, strategy_content_hash,
                    oos_start, oos_end, warmup_start, configuration_hash, status,
                    attempt_count, created_at, updated_at, lease_token, lease_owner,
                    lease_expires_at, started_at, finished_at, failure_code,
                    failure_message_safe, failure_retryable, identity_hash, canonical_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                self._execution_values(candidate, payload),
            )
            self._insert_event(
                connection,
                candidate,
                "EXECUTION_CREATED",
                actor="system",
                occurred_at=candidate.created_at,
                from_state=None,
                to_state=candidate.status,
                payload={"identity_hash": candidate.identity_hash},
            )
            connection.execute("COMMIT")
            return candidate
        except (OosExecutionError, OosExecutionPersistenceError):
            self._rollback(connection)
            raise
        except sqlite3.IntegrityError as exc:
            self._rollback(connection)
            raise OosExecutionIdentityConflictError(
                "OOS execution identity conflicts with an existing lineage"
            ) from exc
        except sqlite3.Error as exc:
            self._rollback(connection)
            raise OosExecutionPersistenceError("could not persist OOS execution") from exc
        finally:
            connection.close()

    def get_execution(self, execution_id: str) -> OosExecution | None:
        connection = self._connect()
        try:
            row = connection.execute(
                "SELECT * FROM research_oos_executions WHERE execution_id = ?", (execution_id,)
            ).fetchone()
            return None if row is None else self._decode_execution(row)
        except OosExecutionPersistenceError:
            raise
        except (sqlite3.Error, TypeError, ValueError, OverflowError, json.JSONDecodeError) as exc:
            raise OosExecutionIntegrityError(
                "stored OOS execution failed integrity checks"
            ) from exc
        finally:
            connection.close()

    def get_execution_by_protocol(self, protocol_id: str) -> OosExecution | None:
        connection = self._connect()
        try:
            row = connection.execute(
                "SELECT * FROM research_oos_executions WHERE protocol_id = ?", (protocol_id,)
            ).fetchone()
            return None if row is None else self._decode_execution(row)
        except OosExecutionPersistenceError:
            raise
        except (sqlite3.Error, TypeError, ValueError, OverflowError, json.JSONDecodeError) as exc:
            raise OosExecutionIntegrityError(
                "stored OOS execution failed integrity checks"
            ) from exc
        finally:
            connection.close()

    def claim_execution(
        self,
        execution_id: str,
        owner: str,
        now: datetime,
        lease_expires_at: datetime | None = None,
        *,
        lease_duration: timedelta | None = None,
    ) -> OosExecution:
        expiry = self._expiry(now, lease_expires_at, lease_duration)
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            execution = self._load_execution(connection, execution_id)
            self._assert_protocol_allows_execution(connection, execution.protocol_id)
            if execution.status is OosExecutionStatus.RUNNING:
                if (
                    execution.lease_expires_at is None
                    or execution.lease_expires_at > now.astimezone(UTC)
                ):
                    raise OosExecutionInProgressError("OOS execution already has an active lease")
                recovered = execution.reclaim_expired(now=now)
                self._update(connection, recovered)
                self._insert_event(
                    connection,
                    recovered,
                    "LEASE_EXPIRED",
                    actor="recovery",
                    occurred_at=now,
                    from_state=execution.status,
                    to_state=recovered.status,
                    payload={"previous_owner": execution.lease_owner},
                )
                execution = recovered
            token = uuid4().hex
            claimed = execution.claim(
                owner=owner, now=now, lease_expires_at=expiry, lease_token=token
            )
            self._update(connection, claimed)
            self._insert_event(
                connection,
                claimed,
                "EXECUTION_CLAIMED",
                actor=owner,
                occurred_at=now,
                from_state=execution.status,
                to_state=claimed.status,
                payload={"lease_expires_at": claimed.lease_expires_at.isoformat()},
            )
            connection.execute("COMMIT")
            return claimed
        except (OosExecutionError, OosExecutionPersistenceError):
            self._rollback(connection)
            raise
        except sqlite3.Error as exc:
            self._rollback(connection)
            raise OosExecutionPersistenceError("could not claim OOS execution") from exc
        finally:
            connection.close()

    def renew_lease(
        self, execution_id: str, lease_token: str, now: datetime, lease_expires_at: datetime
    ) -> OosExecution:
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            execution = self._load_execution(connection, execution_id)
            updated = execution.renew(
                lease_token=lease_token, now=now, lease_expires_at=lease_expires_at
            )
            self._update(connection, updated)
            self._insert_event(
                connection,
                updated,
                "LEASE_RENEWED",
                actor=updated.lease_owner or "worker",
                occurred_at=now,
                from_state=execution.status,
                to_state=updated.status,
                payload={"lease_expires_at": updated.lease_expires_at.isoformat()},
            )
            connection.execute("COMMIT")
            return updated
        except (OosExecutionError, OosExecutionPersistenceError):
            self._rollback(connection)
            raise
        except sqlite3.Error as exc:
            self._rollback(connection)
            raise OosExecutionPersistenceError("could not renew OOS execution lease") from exc
        finally:
            connection.close()

    def mark_failed(
        self,
        execution_id: str,
        lease_token: str,
        now: datetime,
        failure_code: str,
        failure_message_safe: str | None = None,
        retryable: bool = False,
        *,
        failure_message: str | None = None,
    ) -> OosExecution:
        message = failure_message_safe if failure_message_safe is not None else failure_message
        if message is None:
            raise OosExecutionError(
                "safe failure message is required", code="OOS_EXECUTION_INVALID"
            )
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            execution = self._load_execution(connection, execution_id)
            updated = execution.fail(
                lease_token=lease_token,
                now=now,
                failure_code=failure_code,
                failure_message_safe=message,
                retryable=retryable,
            )
            self._update(connection, updated)
            self._insert_event(
                connection,
                updated,
                "EXECUTION_FAILED" if retryable else "EXECUTION_BLOCKED",
                actor=execution.lease_owner or "worker",
                occurred_at=now,
                from_state=execution.status,
                to_state=updated.status,
                payload={"failure_code": updated.failure_code, "retryable": retryable},
            )
            connection.execute("COMMIT")
            return updated
        except (OosExecutionError, OosExecutionPersistenceError):
            self._rollback(connection)
            raise
        except sqlite3.Error as exc:
            self._rollback(connection)
            raise OosExecutionPersistenceError("could not persist OOS execution failure") from exc
        finally:
            connection.close()

    def recover_stale_executions(
        self, now: datetime, *, actor: str = "recovery"
    ) -> tuple[OosExecution, ...]:
        connection = self._connect()
        recovered: list[OosExecution] = []
        try:
            connection.execute("BEGIN IMMEDIATE")
            rows = connection.execute(
                "SELECT * FROM research_oos_executions WHERE status = ? AND lease_expires_at <= ? "
                "ORDER BY protocol_id",
                (OosExecutionStatus.RUNNING.value, now.astimezone(UTC).isoformat()),
            ).fetchall()
            for row in rows:
                execution = self._decode_execution(row)
                updated = execution.reclaim_expired(now=now)
                self._update(connection, updated)
                self._insert_event(
                    connection,
                    updated,
                    "LEASE_EXPIRED",
                    actor=actor,
                    occurred_at=now,
                    from_state=execution.status,
                    to_state=updated.status,
                    payload={"previous_owner": execution.lease_owner},
                )
                recovered.append(updated)
            connection.execute("COMMIT")
            return tuple(recovered)
        except (OosExecutionError, OosExecutionPersistenceError):
            self._rollback(connection)
            raise
        except sqlite3.Error as exc:
            self._rollback(connection)
            raise OosExecutionPersistenceError("could not recover stale OOS executions") from exc
        finally:
            connection.close()

    def recover_stale(self, now: datetime, *, actor: str = "recovery") -> tuple[OosExecution, ...]:
        return self.recover_stale_executions(now, actor=actor)

    def list_events(self, execution_id: str) -> tuple[OosExecutionEvent, ...]:
        connection = self._connect()
        try:
            rows = connection.execute(
                "SELECT * FROM research_oos_execution_events WHERE execution_id = ? "
                "ORDER BY event_sequence",
                (execution_id,),
            ).fetchall()
            return tuple(self._decode_event(row) for row in rows)
        except OosExecutionPersistenceError:
            raise
        except (sqlite3.Error, TypeError, ValueError, OverflowError, json.JSONDecodeError) as exc:
            raise OosExecutionIntegrityError(
                "stored OOS execution events failed integrity checks"
            ) from exc
        finally:
            connection.close()

    def _assert_protocol_allows_execution(
        self, connection: sqlite3.Connection, protocol_id: str
    ) -> None:
        row = connection.execute(
            "SELECT payload_json FROM research_protocols WHERE protocol_id = ?", (protocol_id,)
        ).fetchone()
        if row is None:
            raise OosProtocolStateError("research protocol was not found")
        try:
            protocol = ResearchProtocol.from_dict(json.loads(row["payload_json"]))
            events = connection.execute(
                "SELECT event_type FROM research_protocol_events WHERE protocol_id = ? "
                "ORDER BY created_at ASC, event_id ASC",
                (protocol_id,),
            ).fetchall()
            for event in events:
                if event["event_type"] in ProtocolStatus.values():
                    protocol = protocol.with_status(str(event["event_type"]))
        except (ResearchProtocolError, TypeError, ValueError, json.JSONDecodeError) as exc:
            raise OosProtocolStateError("research protocol failed integrity checks") from exc
        if protocol.status != ProtocolStatus.SELECTION_RECORDED:
            raise OosProtocolStateError(
                "OOS execution requires a selection_recorded protocol",
                code="OOS_PROTOCOL_STATE_INVALID",
            )
        rows = connection.execute(
            "SELECT payload_json FROM research_oos_evaluations WHERE protocol_id = ?",
            (protocol_id,),
        ).fetchall()
        for observation in rows:
            try:
                status = json.loads(observation["payload_json"]).get("status")
            except (TypeError, ValueError, json.JSONDecodeError) as exc:
                raise OosProtocolStateError(
                    "stored OOS observation failed integrity checks"
                ) from exc
            if status in (OOSObservationStatus.OBSERVED, OOSObservationStatus.SEALED):
                raise OosOfficialResultExistsError(
                    "official OOS observation already exists for this protocol"
                )

    def _load_execution(self, connection: sqlite3.Connection, execution_id: str) -> OosExecution:
        row = connection.execute(
            "SELECT * FROM research_oos_executions WHERE execution_id = ?", (execution_id,)
        ).fetchone()
        if row is None:
            raise OosExecutionNotFoundError("OOS execution was not found")
        return self._decode_execution(row)

    def _update(self, connection: sqlite3.Connection, execution: OosExecution) -> None:
        payload = self._payload(execution)
        cursor = connection.execute(
            """
            UPDATE research_oos_executions SET status = ?, attempt_count = ?, updated_at = ?,
                lease_token = ?, lease_owner = ?, lease_expires_at = ?, started_at = ?,
                finished_at = ?, failure_code = ?, failure_message_safe = ?,
                failure_retryable = ?, canonical_json = ?
            WHERE execution_id = ? AND identity_hash = ?
            """,
            (
                execution.status.value,
                execution.attempt_count,
                execution.updated_at.isoformat(),
                execution.lease_token,
                execution.lease_owner,
                self._iso(execution.lease_expires_at),
                self._iso(execution.started_at),
                self._iso(execution.finished_at),
                execution.failure_code,
                execution.failure_message_safe,
                self._bool_int(execution.failure_retryable),
                payload,
                execution.execution_id,
                execution.identity_hash,
            ),
        )
        if cursor.rowcount != 1:
            raise OosExecutionIdentityConflictError("OOS execution changed concurrently")

    def complete_in_transaction(
        self,
        connection: sqlite3.Connection,
        execution: OosExecution,
        *,
        lease_token: str,
        now: datetime,
    ) -> OosExecution:
        """Persist COMPLETED without owning the surrounding transaction."""
        updated = execution.complete(lease_token=lease_token, now=now)
        self._update(connection, updated)
        self._insert_event(
            connection,
            updated,
            "EXECUTION_COMPLETED",
            actor=execution.lease_owner or "worker",
            occurred_at=now,
            from_state=execution.status,
            to_state=updated.status,
            payload={"finalization": "official_oos_observation"},
        )
        return updated

    def _insert_event(
        self,
        connection: sqlite3.Connection,
        execution: OosExecution,
        event_type: str,
        *,
        actor: str,
        occurred_at: datetime,
        from_state: OosExecutionStatus | None,
        to_state: OosExecutionStatus | None,
        payload: Mapping[str, object],
    ) -> None:
        event = OosExecutionEvent(
            event_id=f"oos-event-{uuid4().hex}",
            execution_id=execution.execution_id,
            protocol_id=execution.protocol_id,
            event_type=event_type,
            attempt_number=execution.attempt_count,
            occurred_at=occurred_at,
            actor=actor,
            payload=payload,
            from_state=from_state,
            to_state=to_state,
        )
        connection.execute(
            """
            INSERT INTO research_oos_execution_events(
                event_id, execution_id, protocol_id, event_type, attempt_number,
                from_state, to_state, occurred_at, actor, payload_json, schema_version
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                event.event_id,
                event.execution_id,
                event.protocol_id,
                event.event_type,
                event.attempt_number,
                event.from_state.value if event.from_state else None,
                event.to_state.value if event.to_state else None,
                event.occurred_at.isoformat(),
                event.actor,
                self._safe_json(event.payload),
                1,
            ),
        )

    @classmethod
    def _payload(cls, execution: OosExecution) -> str:
        return cls._safe_json(execution.to_dict())

    @classmethod
    def _safe_json(cls, payload: object) -> str:
        cls._assert_safe(payload)
        try:
            return canonical_json(payload)
        except (TypeError, ValueError, OverflowError) as exc:
            raise OosExecutionPersistenceError("payload is not canonical JSON") from exc

    @classmethod
    def _assert_safe(cls, payload: object) -> None:
        if isinstance(payload, Mapping):
            for key, value in payload.items():
                if cls._SENSITIVE.search(str(key)):
                    raise OosExecutionPersistenceError("sensitive execution fields are not allowed")
                cls._assert_safe(value)
        elif isinstance(payload, (list, tuple)):
            for value in payload:
                cls._assert_safe(value)
        elif isinstance(payload, str) and cls._SENSITIVE.search(payload):
            raise OosExecutionPersistenceError("sensitive execution values are not allowed")

    @classmethod
    def _decode_execution(cls, row: sqlite3.Row) -> OosExecution:
        try:
            payload = json.loads(row["canonical_json"], parse_constant=cls._reject_nonfinite)
            if row["canonical_json"] != cls._safe_json(payload):
                raise OosExecutionIntegrityError("stored OOS execution is not canonical JSON")
            execution = OosExecution.from_dict(payload)
        except OosExecutionIntegrityError:
            raise
        except (
            OosExecutionError,
            TypeError,
            ValueError,
            OverflowError,
            json.JSONDecodeError,
        ) as exc:
            raise OosExecutionIntegrityError("stored OOS execution is malformed") from exc
        if execution.identity_hash != row["identity_hash"]:
            raise OosExecutionIntegrityError("stored OOS execution identity hash mismatch")
        expected = {
            "execution_id": execution.execution_id,
            "protocol_id": execution.protocol_id,
            "oos_spec_hash": execution.oos_spec_hash,
            "selection_decision_id": execution.selection_decision_id,
            "strategy_freeze_id": execution.strategy_freeze_id,
            "strategy_version_id": execution.strategy_version_id,
            "strategy_content_hash": execution.strategy_content_hash,
            "oos_start": execution.oos_start.isoformat(),
            "oos_end": execution.oos_end.isoformat(),
            "warmup_start": execution.warmup_start.isoformat(),
            "configuration_hash": execution.configuration_hash,
            "status": execution.status.value,
            "attempt_count": execution.attempt_count,
            "created_at": execution.created_at.isoformat(),
            "updated_at": execution.updated_at.isoformat(),
            "lease_token": execution.lease_token,
            "lease_owner": execution.lease_owner,
            "lease_expires_at": cls._iso(execution.lease_expires_at),
            "started_at": cls._iso(execution.started_at),
            "finished_at": cls._iso(execution.finished_at),
            "failure_code": execution.failure_code,
            "failure_message_safe": execution.failure_message_safe,
            "failure_retryable": cls._bool_int(execution.failure_retryable),
        }
        if any(row[key] != value for key, value in expected.items()):
            raise OosExecutionIntegrityError("stored OOS execution columns do not match payload")
        return execution

    @classmethod
    def _decode_event(cls, row: sqlite3.Row) -> OosExecutionEvent:
        try:
            payload = json.loads(row["payload_json"], parse_constant=cls._reject_nonfinite)
            if row["payload_json"] != cls._safe_json(payload):
                raise OosExecutionIntegrityError("stored OOS event is not canonical JSON")
            return OosExecutionEvent(
                event_id=row["event_id"],
                execution_id=row["execution_id"],
                protocol_id=row["protocol_id"],
                event_type=row["event_type"],
                attempt_number=row["attempt_number"],
                occurred_at=datetime.fromisoformat(row["occurred_at"]),
                actor=row["actor"],
                payload=payload,
                from_state=row["from_state"],
                to_state=row["to_state"],
                sequence=row["event_sequence"],
            )
        except OosExecutionIntegrityError:
            raise
        except (
            OosExecutionError,
            TypeError,
            ValueError,
            OverflowError,
            json.JSONDecodeError,
        ) as exc:
            raise OosExecutionIntegrityError("stored OOS event is malformed") from exc

    @staticmethod
    def _execution_values(execution: OosExecution, payload: str) -> tuple[object, ...]:
        return (
            execution.execution_id,
            execution.protocol_id,
            execution.oos_spec_hash,
            execution.selection_decision_id,
            execution.strategy_freeze_id,
            execution.strategy_version_id,
            execution.strategy_content_hash,
            execution.oos_start.isoformat(),
            execution.oos_end.isoformat(),
            execution.warmup_start.isoformat(),
            execution.configuration_hash,
            execution.status.value,
            execution.attempt_count,
            execution.created_at.isoformat(),
            execution.updated_at.isoformat(),
            execution.lease_token,
            execution.lease_owner,
            OosExecutionRepository._iso(execution.lease_expires_at),
            OosExecutionRepository._iso(execution.started_at),
            OosExecutionRepository._iso(execution.finished_at),
            execution.failure_code,
            execution.failure_message_safe,
            OosExecutionRepository._bool_int(execution.failure_retryable),
            execution.identity_hash,
            payload,
        )

    @staticmethod
    def _expiry(now: datetime, expiry: datetime | None, duration: timedelta | None) -> datetime:
        if expiry is None:
            if duration is None:
                raise OosExecutionError(
                    "lease expiry or duration is required", code="OOS_EXECUTION_INVALID"
                )
            expiry = now + duration
        elif duration is not None:
            raise OosExecutionError(
                "lease expiry and duration are mutually exclusive", code="OOS_EXECUTION_INVALID"
            )
        return expiry

    @staticmethod
    def _iso(value: datetime | None) -> str | None:
        return value.isoformat() if value is not None else None

    @staticmethod
    def _bool_int(value: bool | None) -> int | None:
        return None if value is None else int(value)

    @staticmethod
    def _reject_nonfinite(value: str) -> object:
        raise ValueError(f"non-finite JSON value: {value}")

    @staticmethod
    def _rollback(connection: sqlite3.Connection) -> None:
        try:
            connection.execute("ROLLBACK")
        except sqlite3.Error:
            pass


__all__ = ["OosExecutionRepository"]
