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
                "SELECT proposal_json, status, updated_at FROM operator_draft_change_sets "
                "WHERE change_set_id = ?",
                (change_set_id,),
            ).fetchone()
            if row is None:
                return None
            proposal = json.loads(row["proposal_json"])
            proposal["status"] = row["status"]
            proposal["updated_at"] = row["updated_at"]
            return proposal
        except (sqlite3.Error, ValueError, KeyError, TypeError) as exc:
            raise ChangeSetStorageError("CHANGE_SET_READ_FAILED") from exc
        finally:
            connection.close()

    def reject(self, change_set_id: str) -> bool:
        """Backend-only lifecycle operation; deliberately not exposed via MCP."""
        connection = self._connect(write=True)
        assert connection is not None
        try:
            connection.execute("BEGIN IMMEDIATE")
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
