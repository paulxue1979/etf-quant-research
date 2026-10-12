"""Isolated durable storage for Codex draft proposals, never strategy drafts."""

from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from research.canonical import canonical_json


class ChangeSetStorageError(RuntimeError):
    """Non-sensitive proposal persistence failure."""


class OperatorChangeSetRepository:
    """Store immutable proposal bodies in a dedicated SQLite file."""

    def __init__(self, path: Path | None = None) -> None:
        self.path = path or Path(__file__).resolve().parents[2] / "data" / "operator_change_sets.db"

    def _connect(self, *, write: bool) -> sqlite3.Connection | None:
        if not write and not self.path.exists():
            return None
        try:
            if write:
                self.path.parent.mkdir(parents=True, exist_ok=True)
                connection = sqlite3.connect(self.path, timeout=5.0, isolation_level=None)
            else:
                connection = sqlite3.connect(
                    self.path.resolve().as_uri() + "?mode=ro",
                    uri=True,
                    timeout=5.0,
                    isolation_level=None,
                )
                connection.execute("PRAGMA query_only = ON")
            connection.row_factory = sqlite3.Row
            return connection
        except (OSError, sqlite3.Error) as exc:
            raise ChangeSetStorageError("CHANGE_SET_STORAGE_UNAVAILABLE") from exc

    def insert(self, proposal: dict[str, Any]) -> None:
        connection = self._connect(write=True)
        assert connection is not None
        try:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                "CREATE TABLE IF NOT EXISTS operator_draft_change_sets ("
                "change_set_id TEXT PRIMARY KEY, strategy_id TEXT NOT NULL, "
                "expected_draft_revision INTEGER NOT NULL, before_hash TEXT NOT NULL, "
                "status TEXT NOT NULL CHECK(status IN ('PROPOSED','REJECTED')), "
                "proposal_json TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL)"
            )
            connection.execute(
                "INSERT INTO operator_draft_change_sets VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    proposal["change_set_id"],
                    proposal["strategy_id"],
                    proposal["expected_draft_revision"],
                    proposal["before_hash"],
                    "PROPOSED",
                    canonical_json(proposal),
                    proposal["created_at"],
                    proposal["created_at"],
                ),
            )
            connection.execute("COMMIT")
        except (sqlite3.Error, TypeError, ValueError) as exc:
            connection.execute("ROLLBACK")
            raise ChangeSetStorageError("CHANGE_SET_STORAGE_FAILED") from exc
        finally:
            connection.close()

    def get(self, change_set_id: str) -> dict[str, Any] | None:
        connection = self._connect(write=False)
        if connection is None:
            return None
        try:
            row = connection.execute(
                "SELECT change_set_id, proposal_json, status, updated_at "
                "FROM operator_draft_change_sets "
                "WHERE change_set_id = ?",
                (change_set_id,),
            ).fetchone()
            if row is None:
                return None
            return self._project(connection, row)
        except (sqlite3.Error, ValueError, KeyError, TypeError) as exc:
            raise ChangeSetStorageError("CHANGE_SET_READ_FAILED") from exc
        finally:
            connection.close()

    def list_for_strategy(self, strategy_id: str, limit: int = 50) -> list[dict[str, Any]]:
        connection = self._connect(write=False)
        if connection is None:
            return []
        try:
            rows = connection.execute(
                "SELECT change_set_id, proposal_json, status, updated_at "
                "FROM operator_draft_change_sets WHERE strategy_id = ? "
                "ORDER BY created_at DESC LIMIT ?",
                (strategy_id, min(limit, 50)),
            ).fetchall()
            return [self._project(connection, row) for row in rows]
        except (sqlite3.Error, ValueError, KeyError, TypeError) as exc:
            raise ChangeSetStorageError("CHANGE_SET_READ_FAILED") from exc
        finally:
            connection.close()

    @staticmethod
    def _project(connection: sqlite3.Connection, row: sqlite3.Row) -> dict[str, Any]:
        proposal = json.loads(row["proposal_json"])
        proposal["status"] = row["status"]
        proposal["updated_at"] = row["updated_at"]
        has_decisions = connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' "
            "AND name = 'operator_change_set_decisions'"
        ).fetchone()
        if has_decisions:
            decision = connection.execute(
                "SELECT result, audit_json, decided_at FROM operator_change_set_decisions "
                "WHERE change_set_id = ?",
                (row["change_set_id"],),
            ).fetchone()
            if decision is not None:
                proposal["status"] = decision["result"]
                proposal["updated_at"] = decision["decided_at"]
                proposal["decision_audit"] = json.loads(decision["audit_json"])
        has_failures = connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' "
            "AND name = 'operator_change_set_failures'"
        ).fetchone()
        if has_failures:
            failure = connection.execute(
                "SELECT failure_code, failure_stage, safe_message, failed_at "
                "FROM operator_change_set_failures WHERE change_set_id = ?",
                (row["change_set_id"],),
            ).fetchone()
            if failure is not None:
                if proposal["status"] in {"APPLIED", "REJECTED"}:
                    raise ChangeSetStorageError("CHANGE_SET_INTEGRITY_ERROR")
                proposal["status"] = "FAILED"
                proposal["updated_at"] = failure["failed_at"]
                proposal["failure_code"] = failure["failure_code"]
                proposal["failure_stage"] = failure["failure_stage"]
                proposal["safe_message"] = failure["safe_message"]
                proposal["failed_at"] = failure["failed_at"]
                proposal["failure_audit"] = {
                    "change_set_id": row["change_set_id"],
                    "attempted_at": failure["failed_at"],
                    "failure_code": failure["failure_code"],
                    "failure_stage": failure["failure_stage"],
                    "result": "FAILED",
                }
        return proposal

    def record_failure(
        self, change_set_id: str, failure_code: str, failure_stage: str, safe_message: str
    ) -> None:
        """Persist one terminal failure only after the Draft transaction has rolled back."""
        connection = self._connect(write=True)
        assert connection is not None
        try:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT status FROM operator_draft_change_sets WHERE change_set_id = ?",
                (change_set_id,),
            ).fetchone()
            if row is None or row["status"] != "PROPOSED":
                raise ChangeSetStorageError("CHANGE_SET_INTEGRITY_ERROR")
            has_decisions = connection.execute(
                "SELECT 1 FROM sqlite_master WHERE type = 'table' "
                "AND name = 'operator_change_set_decisions'"
            ).fetchone()
            if (
                has_decisions
                and connection.execute(
                    "SELECT 1 FROM operator_change_set_decisions WHERE change_set_id = ?",
                    (change_set_id,),
                ).fetchone()
            ):
                raise ChangeSetStorageError("CHANGE_SET_INTEGRITY_ERROR")
            connection.execute(
                "CREATE TABLE IF NOT EXISTS operator_change_set_failures ("
                "change_set_id TEXT PRIMARY KEY, failure_code TEXT NOT NULL, "
                "failure_stage TEXT NOT NULL, safe_message TEXT NOT NULL, failed_at TEXT NOT NULL)"
            )
            connection.execute(
                "INSERT OR IGNORE INTO operator_change_set_failures VALUES (?, ?, ?, ?, ?)",
                (
                    change_set_id,
                    failure_code,
                    failure_stage,
                    safe_message,
                    datetime.now(UTC).isoformat(),
                ),
            )
            connection.execute("COMMIT")
        except (sqlite3.Error, ChangeSetStorageError) as exc:
            if connection.in_transaction:
                connection.execute("ROLLBACK")
            raise ChangeSetStorageError("CHANGE_SET_FAILURE_RECORD_UNAVAILABLE") from exc
        finally:
            connection.close()

    def reject(self, change_set_id: str) -> bool:
        """Backend-only lifecycle operation; deliberately not exposed via MCP."""
        connection = self._connect(write=True)
        assert connection is not None
        try:
            connection.execute("BEGIN IMMEDIATE")
            for table in ("operator_change_set_decisions", "operator_change_set_failures"):
                exists = connection.execute(
                    "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?",
                    (table,),
                ).fetchone()
                if (
                    exists
                    and connection.execute(
                        f"SELECT 1 FROM {table} WHERE change_set_id = ?", (change_set_id,)
                    ).fetchone()
                ):
                    connection.execute("ROLLBACK")
                    return False
            updated = connection.execute(
                "UPDATE operator_draft_change_sets SET status = 'REJECTED', updated_at = ? "
                "WHERE change_set_id = ? AND status = 'PROPOSED'",
                (datetime.now(UTC).isoformat(), change_set_id),
            ).rowcount
            connection.execute("COMMIT")
            return updated == 1
        except sqlite3.Error as exc:
            connection.execute("ROLLBACK")
            raise ChangeSetStorageError("CHANGE_SET_REJECT_FAILED") from exc
        finally:
            connection.close()
