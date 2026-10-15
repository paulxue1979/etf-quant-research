"""Durable approval boundary for Codex-prepared IS grids."""

from __future__ import annotations

import json
import sqlite3
from contextlib import closing
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import uuid4

from research.canonical import canonical_json, sha256_hash


class ControlledExperimentError(RuntimeError):
    """Stable, non-sensitive pending-configuration failure."""


class ControlledExperimentRepository:
    LEASE = timedelta(minutes=5)

    def __init__(self, path: Path | None = None) -> None:
        self.path = path or Path(__file__).resolve().parents[2] / "data" / "strategy.db"

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
    def _schema(connection: sqlite3.Connection) -> None:
        connection.execute(
            """CREATE TABLE IF NOT EXISTS pending_experiment_configurations (
                config_id TEXT PRIMARY KEY,
                client_request_key TEXT NOT NULL UNIQUE,
                intent_hash TEXT NOT NULL,
                proposal_hash TEXT NOT NULL,
                config_json TEXT NOT NULL,
                config_hash TEXT NOT NULL,
                approval_status TEXT NOT NULL CHECK(approval_status IN
                    ('PROPOSED','REJECTED','APPROVED','STALE')),
                execution_status TEXT NOT NULL CHECK(execution_status IN
                    ('NOT_STARTED','RUNNING','COMPLETED','FAILED')),
                experiment_id TEXT NOT NULL UNIQUE,
                worker_token TEXT,
                lease_expires_at TEXT,
                approved_at TEXT,
                rejected_at TEXT,
                error_code TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )"""
        )
        connection.execute(
            """CREATE TABLE IF NOT EXISTS pending_experiment_events (
                event_id TEXT PRIMARY KEY,
                config_id TEXT NOT NULL,
                event_type TEXT NOT NULL,
                occurred_at TEXT NOT NULL,
                detail_json TEXT NOT NULL,
                FOREIGN KEY(config_id) REFERENCES pending_experiment_configurations(config_id)
            )"""
        )
        connection.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS uq_active_pending_experiment_proposal "
            "ON pending_experiment_configurations(proposal_hash) "
            "WHERE approval_status = 'PROPOSED'"
        )

    @staticmethod
    def _event(connection: sqlite3.Connection, config_id: str, event: str) -> None:
        connection.execute(
            "INSERT INTO pending_experiment_events VALUES (?, ?, ?, ?, ?)",
            (uuid4().hex, config_id, event, datetime.now(UTC).isoformat(), "{}"),
        )

    @staticmethod
    def _audit(connection: sqlite3.Connection, config_id: str) -> list[dict[str, str]]:
        rows = connection.execute(
            "SELECT event_type, occurred_at FROM pending_experiment_events "
            "WHERE config_id = ? ORDER BY occurred_at, event_id",
            (config_id,),
        ).fetchall()
        return [
            {"event_type": row["event_type"], "occurred_at": row["occurred_at"]} for row in rows
        ]

    @staticmethod
    def _record(row: sqlite3.Row) -> dict[str, Any]:
        body = json.loads(row["config_json"])
        if (
            sha256_hash(body) != row["config_hash"]
            or body["config_id"] != row["config_id"]
            or body["experiment_id"] != row["experiment_id"]
            or body["client_request_key"] != row["client_request_key"]
            or body["intent_hash"] != row["intent_hash"]
            or body["proposal_hash"] != row["proposal_hash"]
        ):
            raise ControlledExperimentError("EXPERIMENT_CONFIG_INTEGRITY_ERROR")
        return {
            **body,
            "configuration_hash": row["config_hash"],
            "approval_status": row["approval_status"],
            "execution_status": row["execution_status"],
            "approved_at": row["approved_at"],
            "rejected_at": row["rejected_at"],
            "error_code": row["error_code"],
            "updated_at": row["updated_at"],
        }

    def prepare(self, body: dict[str, Any], intent_hash: str, proposal_hash: str) -> dict[str, Any]:
        now = datetime.now(UTC).isoformat()
        with closing(self._connect()) as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                self._schema(connection)
                existing = connection.execute(
                    "SELECT * FROM pending_experiment_configurations WHERE client_request_key = ?",
                    (body["client_request_key"],),
                ).fetchone()
                if existing is not None:
                    if existing["intent_hash"] != intent_hash:
                        raise ControlledExperimentError("IDEMPOTENCY_KEY_CONFLICT")
                    result = self._record(existing)
                else:
                    duplicate = connection.execute(
                        "SELECT config_id FROM pending_experiment_configurations "
                        "WHERE proposal_hash = ? AND approval_status = 'PROPOSED'",
                        (proposal_hash,),
                    ).fetchone()
                    if duplicate is not None:
                        raise ControlledExperimentError("ACTIVE_PROPOSAL_EXISTS")
                    connection.execute(
                        """INSERT INTO pending_experiment_configurations
                           (config_id, client_request_key, intent_hash, proposal_hash, config_json,
                            config_hash, approval_status, execution_status, experiment_id,
                            created_at, updated_at)
                           VALUES (?, ?, ?, ?, ?, ?, 'PROPOSED', 'NOT_STARTED', ?, ?, ?)""",
                        (
                            body["config_id"],
                            body["client_request_key"],
                            intent_hash,
                            proposal_hash,
                            canonical_json(body),
                            sha256_hash(body),
                            body["experiment_id"],
                            now,
                            now,
                        ),
                    )
                    self._event(connection, body["config_id"], "PREPARED")
                    result = self._record(
                        connection.execute(
                            "SELECT * FROM pending_experiment_configurations WHERE config_id = ?",
                            (body["config_id"],),
                        ).fetchone()
                    )
                connection.execute("COMMIT")
                result["audit_events"] = self._audit(connection, result["config_id"])
                return result
            except sqlite3.IntegrityError as exc:
                connection.execute("ROLLBACK")
                raise ControlledExperimentError("ACTIVE_PROPOSAL_EXISTS") from exc
            except Exception:
                connection.execute("ROLLBACK")
                raise

    def get(self, config_id: str) -> dict[str, Any]:
        if not self.path.exists():
            raise ControlledExperimentError("EXPERIMENT_CONFIG_NOT_FOUND")
        with closing(self._connect(read_only=True)) as connection:
            table = connection.execute(
                "SELECT 1 FROM sqlite_master WHERE name = 'pending_experiment_configurations'"
            ).fetchone()
            if table is None:
                raise ControlledExperimentError("EXPERIMENT_CONFIG_NOT_FOUND")
            row = connection.execute(
                "SELECT * FROM pending_experiment_configurations WHERE config_id = ?",
                (config_id,),
            ).fetchone()
            events = self._audit(connection, config_id) if row is not None else []
        if row is None:
            raise ControlledExperimentError("EXPERIMENT_CONFIG_NOT_FOUND")
        return {**self._record(row), "audit_events": events}

    def get_by_key(self, key: str) -> dict[str, Any] | None:
        if not self.path.exists():
            return None
        with closing(self._connect(read_only=True)) as connection:
            table = connection.execute(
                "SELECT 1 FROM sqlite_master WHERE name = 'pending_experiment_configurations'"
            ).fetchone()
            if table is None:
                return None
            row = connection.execute(
                "SELECT * FROM pending_experiment_configurations WHERE client_request_key = ?",
                (key,),
            ).fetchone()
            events = self._audit(connection, row["config_id"]) if row is not None else []
        return {**self._record(row), "audit_events": events} if row is not None else None

    def list(self, limit: int = 25) -> list[dict[str, Any]]:
        if not self.path.exists():
            return []
        with closing(self._connect(read_only=True)) as connection:
            table = connection.execute(
                "SELECT 1 FROM sqlite_master WHERE name = 'pending_experiment_configurations'"
            ).fetchone()
            if table is None:
                return []
            rows = connection.execute(
                "SELECT * FROM pending_experiment_configurations ORDER BY created_at DESC LIMIT ?",
                (min(max(limit, 1), 50),),
            ).fetchall()
        return [self._record(row) for row in rows]

    def reject(self, config_id: str) -> dict[str, Any]:
        with closing(self._connect()) as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                row = connection.execute(
                    "SELECT * FROM pending_experiment_configurations WHERE config_id = ?",
                    (config_id,),
                ).fetchone()
                if row is None:
                    raise ControlledExperimentError("EXPERIMENT_CONFIG_NOT_FOUND")
                if row["approval_status"] != "PROPOSED":
                    raise ControlledExperimentError("EXPERIMENT_CONFIG_NOT_PROPOSED")
                now = datetime.now(UTC).isoformat()
                connection.execute(
                    "UPDATE pending_experiment_configurations SET approval_status = 'REJECTED', "
                    "rejected_at = ?, updated_at = ? WHERE config_id = ?",
                    (now, now, config_id),
                )
                self._event(connection, config_id, "REJECTED")
                connection.execute("COMMIT")
            except Exception:
                connection.execute("ROLLBACK")
                raise
        return self.get(config_id)

    def stale(self, config_id: str, code: str) -> None:
        with closing(self._connect()) as connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                "UPDATE pending_experiment_configurations SET approval_status = 'STALE', "
                "error_code = ?, updated_at = ? WHERE config_id = ? "
                "AND approval_status = 'PROPOSED'",
                (code, datetime.now(UTC).isoformat(), config_id),
            )
            self._event(connection, config_id, "STALE")
            connection.execute("COMMIT")

    def claim(self, config_id: str) -> str | None:
        """Return a worker token, or None when another live worker owns the grid."""
        with closing(self._connect()) as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                row = connection.execute(
                    "SELECT * FROM pending_experiment_configurations WHERE config_id = ?",
                    (config_id,),
                ).fetchone()
                if row is None:
                    raise ControlledExperimentError("EXPERIMENT_CONFIG_NOT_FOUND")
                self._record(row)
                if row["approval_status"] == "REJECTED" or row["approval_status"] == "STALE":
                    raise ControlledExperimentError("EXPERIMENT_CONFIG_NOT_PROPOSED")
                if row["execution_status"] == "COMPLETED":
                    connection.execute("COMMIT")
                    return None
                now = datetime.now(UTC)
                if (
                    row["worker_token"] is not None
                    and row["lease_expires_at"] is not None
                    and datetime.fromisoformat(row["lease_expires_at"]) > now
                ):
                    connection.execute("COMMIT")
                    return None
                token = uuid4().hex
                connection.execute(
                    "UPDATE pending_experiment_configurations SET approval_status = 'APPROVED', "
                    "execution_status = 'RUNNING', worker_token = ?, lease_expires_at = ?, "
                    "approved_at = COALESCE(approved_at, ?), updated_at = ? WHERE config_id = ?",
                    (
                        token,
                        (now + self.LEASE).isoformat(),
                        now.isoformat(),
                        now.isoformat(),
                        config_id,
                    ),
                )
                self._event(connection, config_id, "EXECUTION_CLAIMED")
                connection.execute("COMMIT")
                return token
            except Exception:
                connection.execute("ROLLBACK")
                raise

    def heartbeat(self, config_id: str, token: str) -> None:
        with closing(self._connect()) as connection:
            connection.execute(
                "UPDATE pending_experiment_configurations SET lease_expires_at = ? "
                "WHERE config_id = ? AND worker_token = ? AND execution_status = 'RUNNING'",
                ((datetime.now(UTC) + self.LEASE).isoformat(), config_id, token),
            )

    def finish(
        self, config_id: str, token: str, status: str, error_code: str | None = None
    ) -> None:
        if status not in {"COMPLETED", "FAILED", "RUNNING"}:
            raise ControlledExperimentError("EXPERIMENT_EXECUTION_STATUS_INVALID")
        with closing(self._connect()) as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                cursor = connection.execute(
                    "UPDATE pending_experiment_configurations SET execution_status = ?, "
                    "error_code = ?, worker_token = NULL, lease_expires_at = NULL, updated_at = ? "
                    "WHERE config_id = ? AND worker_token = ?",
                    (status, error_code, datetime.now(UTC).isoformat(), config_id, token),
                )
                if cursor.rowcount != 1:
                    raise ControlledExperimentError("EXPERIMENT_EXECUTION_LEASE_LOST")
                self._event(connection, config_id, f"EXECUTION_{status}")
                connection.execute("COMMIT")
            except Exception:
                connection.execute("ROLLBACK")
                raise
