"""Durable persistence for immutable StrategyDefinition versions."""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

from strategies import StrategyDefinition, StrategyStatus, StrategyVersion


class StrategyPersistenceError(RuntimeError):
    """Raised when strategy persistence or integrity checks fail."""


class StrategyRepository:
    """SQLite repository for immutable strategy version snapshots.

    Each operation uses a short-lived connection. Version allocation is done
    inside an ``IMMEDIATE`` transaction and protected by a database constraint,
    so separate application threads cannot allocate the same version number.
    """

    SCHEMA_VERSION = 1

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
            raise StrategyPersistenceError("could not prepare strategy database") from exc

    def _connect(self) -> sqlite3.Connection:
        try:
            connection = sqlite3.connect(
                self.db_path,
                timeout=30.0,
                isolation_level=None,
            )
            connection.row_factory = sqlite3.Row
            connection.execute("PRAGMA foreign_keys = ON")
            connection.execute("PRAGMA busy_timeout = 30000")
            return connection
        except sqlite3.Error as exc:
            raise StrategyPersistenceError("could not open strategy database") from exc

    def _initialize(self) -> None:
        connection = self._connect()
        try:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS schema_metadata (
                    schema_key TEXT PRIMARY KEY,
                    schema_version INTEGER NOT NULL
                );

                CREATE TABLE IF NOT EXISTS strategies (
                    strategy_id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    description TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    latest_version_number INTEGER NOT NULL DEFAULT 0
                );

                CREATE TABLE IF NOT EXISTS strategy_versions (
                    strategy_id TEXT NOT NULL,
                    version_id TEXT NOT NULL,
                    version_number INTEGER NOT NULL CHECK (version_number > 0),
                    created_at TEXT NOT NULL,
                    configuration_json TEXT NOT NULL,
                    content_hash TEXT NOT NULL,
                    status TEXT NOT NULL,
                    PRIMARY KEY (strategy_id, version_id),
                    UNIQUE (strategy_id, version_number),
                    FOREIGN KEY (strategy_id) REFERENCES strategies(strategy_id)
                );
                """
            )
            connection.execute(
                """
                INSERT OR IGNORE INTO schema_metadata(schema_key, schema_version)
                VALUES (?, ?)
                """,
                ("strategy_repository", self.SCHEMA_VERSION),
            )
            row = connection.execute(
                "SELECT schema_version FROM schema_metadata WHERE schema_key = ?",
                ("strategy_repository",),
            ).fetchone()
            if row is None or row["schema_version"] != self.SCHEMA_VERSION:
                raise StrategyPersistenceError("unsupported strategy database schema version")
        except sqlite3.Error as exc:
            raise StrategyPersistenceError("could not initialize strategy database") from exc
        finally:
            connection.close()

    def create(self, definition: StrategyDefinition) -> StrategyVersion:
        """Persist a new immutable version and return its domain snapshot."""
        if not isinstance(definition, StrategyDefinition):
            raise StrategyPersistenceError("strategy definition is invalid")

        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            latest = connection.execute(
                """
                SELECT COALESCE(MAX(version_number), 0) AS latest_version_number
                FROM strategy_versions
                WHERE strategy_id = ?
                """,
                (definition.strategy_id,),
            ).fetchone()
            version_number = int(latest["latest_version_number"]) + 1
            version = StrategyVersion(
                strategy_id=definition.strategy_id,
                version_id=f"{definition.strategy_id}-v{version_number}-{uuid4().hex[:12]}",
                version_number=version_number,
                created_at=datetime.now(UTC),
                configuration=definition,
                status=StrategyStatus.DRAFT,
            )
            created_at = version.created_at.isoformat()
            connection.execute(
                """
                INSERT INTO strategies(
                    strategy_id, name, description, created_at, latest_version_number
                ) VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(strategy_id) DO UPDATE SET
                    name = excluded.name,
                    description = excluded.description,
                    latest_version_number = excluded.latest_version_number
                """,
                (
                    definition.strategy_id,
                    definition.name,
                    definition.description,
                    created_at,
                    version_number,
                ),
            )
            connection.execute(
                """
                INSERT INTO strategy_versions(
                    strategy_id, version_id, version_number, created_at,
                    configuration_json, content_hash, status
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    version.strategy_id,
                    version.version_id,
                    version.version_number,
                    created_at,
                    definition.to_json(),
                    version.content_hash,
                    version.status.value,
                ),
            )
            connection.execute("COMMIT")
            return version
        except sqlite3.Error as exc:
            self._rollback(connection)
            raise StrategyPersistenceError("could not persist strategy version") from exc
        except ValueError as exc:
            self._rollback(connection)
            raise StrategyPersistenceError("could not create strategy version") from exc
        finally:
            connection.close()

    def list(self, strategy_id: str) -> tuple[StrategyVersion, ...]:
        """Return all immutable versions for one strategy in version order."""
        connection = self._connect()
        try:
            rows = connection.execute(
                """
                SELECT strategy_id, version_id, version_number, created_at,
                       configuration_json, content_hash, status
                FROM strategy_versions
                WHERE strategy_id = ?
                ORDER BY version_number ASC
                """,
                (strategy_id,),
            ).fetchall()
            return tuple(self._row_to_version(row) for row in rows)
        except sqlite3.Error as exc:
            raise StrategyPersistenceError("could not list strategy versions") from exc
        finally:
            connection.close()

    def get(self, strategy_id: str, version_id: str) -> StrategyVersion | None:
        """Return one immutable version, or ``None`` when it does not exist."""
        connection = self._connect()
        try:
            row = connection.execute(
                """
                SELECT strategy_id, version_id, version_number, created_at,
                       configuration_json, content_hash, status
                FROM strategy_versions
                WHERE strategy_id = ? AND version_id = ?
                """,
                (strategy_id, version_id),
            ).fetchone()
            return None if row is None else self._row_to_version(row)
        except sqlite3.Error as exc:
            raise StrategyPersistenceError("could not load strategy version") from exc
        finally:
            connection.close()

    def clear(self) -> None:
        """Remove all records; intended for isolated local tests."""
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute("DELETE FROM strategy_versions")
            connection.execute("DELETE FROM strategies")
            connection.execute("COMMIT")
        except sqlite3.Error as exc:
            self._rollback(connection)
            raise StrategyPersistenceError("could not clear strategy database") from exc
        finally:
            connection.close()

    @staticmethod
    def _rollback(connection: sqlite3.Connection) -> None:
        try:
            connection.execute("ROLLBACK")
        except sqlite3.Error:
            pass

    @classmethod
    def _row_to_version(cls, row: sqlite3.Row) -> StrategyVersion:
        required_fields = (
            "strategy_id",
            "version_id",
            "version_number",
            "created_at",
            "configuration_json",
            "content_hash",
            "status",
        )
        if any(row[field] is None for field in required_fields):
            raise StrategyPersistenceError("stored strategy version is incomplete")

        configuration_json = row["configuration_json"]
        if not isinstance(configuration_json, str):
            raise StrategyPersistenceError("stored strategy configuration is invalid")
        try:
            configuration = json.loads(configuration_json, parse_constant=_reject_nonfinite)
            if not isinstance(configuration, Mapping):
                raise ValueError("configuration must be an object")
            version_payload: dict[str, Any] = {
                "strategy_id": row["strategy_id"],
                "version_id": row["version_id"],
                "version_number": row["version_number"],
                "created_at": row["created_at"],
                "configuration": configuration,
                "content_hash": row["content_hash"],
                "status": row["status"],
            }
            serialized = json.dumps(
                version_payload,
                ensure_ascii=True,
                sort_keys=True,
                separators=(",", ":"),
                allow_nan=False,
            )
            return StrategyVersion.from_json(serialized)
        except (TypeError, ValueError, OverflowError, json.JSONDecodeError) as exc:
            raise StrategyPersistenceError(
                "stored strategy version failed integrity checks"
            ) from exc


def _reject_nonfinite(value: str) -> None:
    raise ValueError(f"non-finite JSON constant {value}")


# PHASE 5A imported this name directly. Keep it as a compatibility alias while
# the implementation is now durable and repository-backed.
StrategyVersionStore = StrategyRepository
