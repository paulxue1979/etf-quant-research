"""Durable, browser-approved StrategyVersion requests in the canonical store."""

from __future__ import annotations

import json
import sqlite3
from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

from backend.app.strategy_repository import StrategyRepository
from research.canonical import canonical_json, sha256_hash
from strategies import StrategyDefinition, StrategyValidator


class VersionRequestError(RuntimeError):
    """Stable request, identity, or storage failure code."""


class ControlledVersionRepository:
    """Bind a validated Draft and its latest version before human approval."""

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
    def _ensure_schema(connection: sqlite3.Connection) -> None:
        connection.execute(
            """CREATE TABLE IF NOT EXISTS pending_strategy_version_creations (
                request_id TEXT PRIMARY KEY,
                strategy_id TEXT NOT NULL,
                source TEXT NOT NULL CHECK(source = 'CODEX_MCP'),
                expected_draft_revision INTEGER NOT NULL CHECK(expected_draft_revision > 0),
                expected_draft_hash TEXT NOT NULL,
                base_latest_version_id TEXT,
                base_latest_version_hash TEXT,
                request_json TEXT NOT NULL,
                request_hash TEXT NOT NULL,
                status TEXT NOT NULL CHECK(status IN
                    ('PROPOSED','REJECTED','CREATED','STALE','FAILED')),
                prepared_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                approved_at TEXT,
                rejected_at TEXT,
                created_version_id TEXT UNIQUE,
                created_version_hash TEXT,
                error_code TEXT,
                FOREIGN KEY(strategy_id) REFERENCES strategies(strategy_id),
                FOREIGN KEY(strategy_id, created_version_id)
                    REFERENCES strategy_versions(strategy_id, version_id)
            )"""
        )
        connection.execute(
            "CREATE INDEX IF NOT EXISTS idx_version_requests_strategy "
            "ON pending_strategy_version_creations(strategy_id, prepared_at DESC)"
        )
        connection.execute(
            """CREATE TABLE IF NOT EXISTS strategy_version_creation_events (
                event_id TEXT PRIMARY KEY,
                request_id TEXT NOT NULL,
                event_type TEXT NOT NULL,
                occurred_at TEXT NOT NULL,
                detail_json TEXT NOT NULL,
                FOREIGN KEY(request_id) REFERENCES pending_strategy_version_creations(request_id)
            )"""
        )

    @staticmethod
    def _table_exists(connection: sqlite3.Connection) -> bool:
        return (
            connection.execute(
                "SELECT 1 FROM sqlite_master WHERE type = 'table' "
                "AND name = 'pending_strategy_version_creations'"
            ).fetchone()
            is not None
        )

    @staticmethod
    def _latest(connection: sqlite3.Connection, strategy_id: str) -> sqlite3.Row | None:
        return connection.execute(
            "SELECT version_id, content_hash FROM strategy_versions WHERE strategy_id = ? "
            "ORDER BY version_number DESC LIMIT 1",
            (strategy_id,),
        ).fetchone()

    @staticmethod
    def _event(
        connection: sqlite3.Connection, request_id: str, event_type: str, **detail: str
    ) -> None:
        connection.execute(
            "INSERT INTO strategy_version_creation_events "
            "(event_id, request_id, event_type, occurred_at, detail_json) VALUES (?, ?, ?, ?, ?)",
            (
                uuid4().hex,
                request_id,
                event_type,
                datetime.now(UTC).isoformat(),
                canonical_json(detail),
            ),
        )

    @staticmethod
    def _body(row: sqlite3.Row) -> dict[str, Any]:
        body = json.loads(row["request_json"])
        if sha256_hash(body) != row["request_hash"]:
            raise VersionRequestError("VERSION_REQUEST_INTEGRITY_ERROR")
        for field in (
            "request_id",
            "strategy_id",
            "source",
            "expected_draft_revision",
            "expected_draft_hash",
            "base_latest_version_id",
            "base_latest_version_hash",
        ):
            if body[field] != row[field]:
                raise VersionRequestError("VERSION_REQUEST_INTEGRITY_ERROR")
        return body

    @classmethod
    def _gate(cls, connection: sqlite3.Connection, row: sqlite3.Row) -> str | None:
        if row["status"] != "PROPOSED":
            return row["status"]
        strategy = connection.execute(
            "SELECT lifecycle_status FROM strategies WHERE strategy_id = ?", (row["strategy_id"],)
        ).fetchone()
        if strategy is None or strategy["lifecycle_status"] != "ACTIVE":
            return "STRATEGY_INACTIVE"
        draft = connection.execute(
            "SELECT draft_revision, draft_hash, active_strategy_payload_json "
            "FROM strategy_drafts WHERE strategy_id = ?",
            (row["strategy_id"],),
        ).fetchone()
        if (
            draft is None
            or draft["draft_revision"] != row["expected_draft_revision"]
            or (draft["draft_hash"] != row["expected_draft_hash"])
        ):
            return "DRAFT_STALE"
        latest = cls._latest(connection, row["strategy_id"])
        if (latest["version_id"] if latest else None) != row["base_latest_version_id"] or (
            (latest["content_hash"] if latest else None) != row["base_latest_version_hash"]
        ):
            return "VERSION_BASE_STALE"
        if draft["active_strategy_payload_json"] is None:
            return "DRAFT_INVALID"
        body = cls._body(row)
        payload = json.loads(draft["active_strategy_payload_json"])
        try:
            if (
                StrategyDefinition.from_dict(payload).to_json()
                != StrategyDefinition.from_dict(body["draft_snapshot"]).to_json()
            ):
                return "DRAFT_STALE"
            if not StrategyValidator().validate(payload).is_valid:
                return "DRAFT_INVALID"
        except (TypeError, ValueError, KeyError):
            return "DRAFT_INVALID"
        return None

    @classmethod
    def _record(cls, connection: sqlite3.Connection, row: sqlite3.Row) -> dict[str, Any]:
        body = cls._body(row)
        if row["status"] == "CREATED":
            version = connection.execute(
                "SELECT content_hash FROM strategy_versions WHERE strategy_id = ? "
                "AND version_id = ?",
                (row["strategy_id"], row["created_version_id"]),
            ).fetchone()
            if version is None or version["content_hash"] != row["created_version_hash"]:
                raise VersionRequestError("CREATED_VERSION_INTEGRITY_ERROR")
        reason = cls._gate(connection, row)
        return {
            **body,
            "status": row["status"],
            "updated_at": row["updated_at"],
            "approved_at": row["approved_at"],
            "rejected_at": row["rejected_at"],
            "created_version_id": row["created_version_id"],
            "created_version_hash": row["created_version_hash"],
            "error_code": row["error_code"],
            "approval_gate": {"can_approve": reason is None, "reason": reason},
        }

    def prepare(
        self,
        strategy_id: str,
        expected_revision: int,
        expected_hash: str,
        base_version_id: str | None,
        base_version_hash: str | None,
        client_request_key: str | None = None,
    ) -> dict[str, Any]:
        with closing(self._connect()) as connection:
            try:
                connection.execute("BEGIN IMMEDIATE")
                self._ensure_schema(connection)
                identity = {
                    "strategy_id": strategy_id,
                    "expected_draft_revision": expected_revision,
                    "expected_draft_hash": expected_hash,
                    "base_latest_version_id": base_version_id,
                    "base_latest_version_hash": base_version_hash,
                }
                fingerprint = (
                    {"intent": client_request_key, "scope": "version-creation"}
                    if client_request_key is not None
                    else identity
                )
                request_id = f"version-request-{sha256_hash(fingerprint)[:32]}"
                existing = connection.execute(
                    "SELECT * FROM pending_strategy_version_creations WHERE request_id = ?",
                    (request_id,),
                ).fetchone()
                if existing is not None:
                    body = self._body(existing)
                    if any(body[field] != value for field, value in identity.items()) or (
                        body.get("client_request_key") != client_request_key
                    ):
                        raise VersionRequestError("IDEMPOTENCY_KEY_CONFLICT")
                    record = self._record(connection, existing)
                    connection.execute("COMMIT")
                    return record
                strategy = connection.execute(
                    "SELECT name, lifecycle_status FROM strategies WHERE strategy_id = ?",
                    (strategy_id,),
                ).fetchone()
                if strategy is None:
                    raise VersionRequestError("STRATEGY_NOT_FOUND")
                if strategy["lifecycle_status"] != "ACTIVE":
                    raise VersionRequestError("STRATEGY_INACTIVE")
                draft = connection.execute(
                    "SELECT draft_revision, draft_hash, active_strategy_payload_json, active_mode "
                    "FROM strategy_drafts WHERE strategy_id = ?",
                    (strategy_id,),
                ).fetchone()
                if draft is None:
                    raise VersionRequestError("DRAFT_NOT_FOUND")
                if (
                    draft["draft_revision"] != expected_revision
                    or draft["draft_hash"] != expected_hash
                ):
                    raise VersionRequestError("DRAFT_STALE")
                latest = self._latest(connection, strategy_id)
                if (latest["version_id"] if latest else None) != base_version_id or (
                    (latest["content_hash"] if latest else None) != base_version_hash
                ):
                    raise VersionRequestError("VERSION_BASE_STALE")
                if draft["active_strategy_payload_json"] is None:
                    raise VersionRequestError("DRAFT_INVALID")
                snapshot = json.loads(draft["active_strategy_payload_json"])
                try:
                    if not StrategyValidator().validate(snapshot).is_valid:
                        raise VersionRequestError("DRAFT_INVALID")
                    definition = StrategyDefinition.from_dict(snapshot)
                except (TypeError, ValueError, KeyError) as exc:
                    raise VersionRequestError("DRAFT_INVALID") from exc
                if definition.strategy_id != strategy_id:
                    raise VersionRequestError("DRAFT_INVALID")
                pending = connection.execute(
                    "SELECT request_id FROM pending_strategy_version_creations "
                    "WHERE strategy_id = ? AND expected_draft_revision = ? "
                    "AND expected_draft_hash = ? AND base_latest_version_id IS ? "
                    "AND base_latest_version_hash IS ? AND status = 'PROPOSED' LIMIT 1",
                    (
                        strategy_id,
                        expected_revision,
                        expected_hash,
                        base_version_id,
                        base_version_hash,
                    ),
                ).fetchone()
                if pending is not None:
                    raise VersionRequestError("ACTIVE_VERSION_REQUEST_EXISTS")
                now = datetime.now(UTC).isoformat()
                body = {
                    "request_id": request_id,
                    "source": "CODEX_MCP",
                    **identity,
                    **({"client_request_key": client_request_key} if client_request_key else {}),
                    "strategy_name": strategy["name"],
                    "active_mode": draft["active_mode"],
                    "draft_snapshot": definition.to_dict(),
                    "prepared_at": now,
                }
                connection.execute(
                    "INSERT INTO pending_strategy_version_creations "
                    "(request_id, strategy_id, source, expected_draft_revision, "
                    "expected_draft_hash, base_latest_version_id, base_latest_version_hash, "
                    "request_json, request_hash, status, prepared_at, updated_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'PROPOSED', ?, ?)",
                    (
                        request_id,
                        strategy_id,
                        "CODEX_MCP",
                        expected_revision,
                        expected_hash,
                        base_version_id,
                        base_version_hash,
                        canonical_json(body),
                        sha256_hash(body),
                        now,
                        now,
                    ),
                )
                self._event(connection, request_id, "PROPOSED")
                row = connection.execute(
                    "SELECT * FROM pending_strategy_version_creations WHERE request_id = ?",
                    (request_id,),
                ).fetchone()
                record = self._record(connection, row)
                connection.execute("COMMIT")
                return record
            except Exception:
                if connection.in_transaction:
                    connection.execute("ROLLBACK")
                raise

    def get(self, request_id: str) -> dict[str, Any]:
        if not self.path.exists():
            raise VersionRequestError("VERSION_REQUEST_NOT_FOUND")
        with closing(self._connect(read_only=True)) as connection:
            if not self._table_exists(connection):
                raise VersionRequestError("VERSION_REQUEST_NOT_FOUND")
            row = connection.execute(
                "SELECT * FROM pending_strategy_version_creations WHERE request_id = ?",
                (request_id,),
            ).fetchone()
            if row is None:
                raise VersionRequestError("VERSION_REQUEST_NOT_FOUND")
            return self._record(connection, row)

    def list(self, strategy_id: str, *, limit: int = 25) -> list[dict[str, Any]]:
        if not self.path.exists():
            return []
        with closing(self._connect(read_only=True)) as connection:
            if not self._table_exists(connection):
                return []
            rows = connection.execute(
                "SELECT * FROM pending_strategy_version_creations WHERE strategy_id = ? "
                "ORDER BY prepared_at DESC, request_id DESC LIMIT ?",
                (strategy_id, min(max(limit, 1), 50)),
            ).fetchall()
            return [self._record(connection, row) for row in rows]

    def reject(self, request_id: str) -> dict[str, Any]:
        with closing(self._connect()) as connection:
            try:
                connection.execute("BEGIN IMMEDIATE")
                row = connection.execute(
                    "SELECT * FROM pending_strategy_version_creations WHERE request_id = ?",
                    (request_id,),
                ).fetchone()
                if row is None:
                    raise VersionRequestError("VERSION_REQUEST_NOT_FOUND")
                self._body(row)
                if row["status"] != "PROPOSED":
                    raise VersionRequestError("VERSION_REQUEST_NOT_PROPOSED")
                now = datetime.now(UTC).isoformat()
                connection.execute(
                    "UPDATE pending_strategy_version_creations SET status = 'REJECTED', "
                    "rejected_at = ?, updated_at = ? WHERE request_id = ?",
                    (now, now, request_id),
                )
                self._event(connection, request_id, "REJECTED")
                connection.execute("COMMIT")
            except Exception:
                if connection.in_transaction:
                    connection.execute("ROLLBACK")
                raise
        return self.get(request_id)

    def approve(self, request_id: str) -> dict[str, Any]:
        with closing(self._connect()) as connection:
            try:
                connection.execute("BEGIN IMMEDIATE")
                row = connection.execute(
                    "SELECT * FROM pending_strategy_version_creations WHERE request_id = ?",
                    (request_id,),
                ).fetchone()
                if row is None:
                    raise VersionRequestError("VERSION_REQUEST_NOT_FOUND")
                self._body(row)
                if row["status"] == "CREATED":
                    record = self._record(connection, row)
                    connection.execute("COMMIT")
                    return record
                if row["status"] != "PROPOSED":
                    raise VersionRequestError("VERSION_REQUEST_NOT_PROPOSED")
                reason = self._gate(connection, row)
                if reason is not None:
                    terminal = "FAILED" if reason == "DRAFT_INVALID" else "STALE"
                    now = datetime.now(UTC).isoformat()
                    connection.execute(
                        "UPDATE pending_strategy_version_creations SET status = ?, "
                        "error_code = ?, updated_at = ? WHERE request_id = ?",
                        (terminal, reason, now, request_id),
                    )
                    self._event(connection, request_id, terminal, reason=reason)
                    connection.execute("COMMIT")
                    return self.get(request_id)
                body = self._body(row)
                definition = StrategyDefinition.from_dict(body["draft_snapshot"])
                version = StrategyRepository._create_version_from_draft_in_transaction(
                    connection,
                    strategy_id=row["strategy_id"],
                    expected_revision=row["expected_draft_revision"],
                    definition=definition,
                )
                now = datetime.now(UTC).isoformat()
                connection.execute(
                    "UPDATE pending_strategy_version_creations SET status = 'CREATED', "
                    "created_version_id = ?, created_version_hash = ?, approved_at = ?, "
                    "updated_at = ? WHERE request_id = ?",
                    (version.version_id, version.content_hash, now, now, request_id),
                )
                self._event(connection, request_id, "CREATED", version_id=version.version_id)
                connection.execute("COMMIT")
            except Exception:
                if connection.in_transaction:
                    connection.execute("ROLLBACK")
                raise
        return self.get(request_id)


__all__ = ["ControlledVersionRepository", "VersionRequestError"]
