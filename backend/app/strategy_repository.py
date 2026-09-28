"""Durable persistence for immutable StrategyDefinition versions."""

from __future__ import annotations

import json
import os
import sqlite3
import tempfile
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

from research.materialization import (
    derived_strategy_version_hash,
    derived_strategy_version_id,
)
from strategies import (
    StrategyDefinition,
    StrategyStatus,
    StrategyVersion,
)


class StrategyPersistenceError(RuntimeError):
    """Raised when strategy persistence or integrity checks fail."""

    code = "STRATEGY_PERSISTENCE_ERROR"

    def __init__(self, message: str, *, code: str | None = None) -> None:
        super().__init__(message)
        if code is not None:
            self.code = code


@dataclass(frozen=True)
class StrategyRecord:
    """Mutable strategy identity metadata, separate from immutable versions."""

    strategy_id: str
    name: str
    description: str
    created_at: datetime
    updated_at: datetime
    status: str
    metadata: Mapping[str, Any]
    lifecycle_status: str = "ACTIVE"
    is_protected: bool = False
    deleted_at: datetime | None = None


@dataclass(frozen=True)
class StrategyDependency:
    kind: str
    identifier: str
    detail: str


@dataclass(frozen=True)
class BulkStrategyTrashResult:
    requested: int
    trashed: tuple[str, ...]
    skipped_protected: tuple[str, ...]
    blocked_dependencies: tuple[str, ...]
    not_found: tuple[str, ...]


@dataclass(frozen=True)
class StrategyDraft:
    """Persisted editable work state; it may contain invalid editor data."""

    strategy_id: str
    draft_revision: int
    active_mode: str
    rule_based_draft: Mapping[str, Any]
    regime_draft: Mapping[str, Any]
    active_strategy_payload: Mapping[str, Any] | None
    source_metadata: Mapping[str, Any]
    created_at: datetime
    updated_at: datetime
    draft_hash: str


@dataclass(frozen=True)
class StrategyVersionRecord:
    """Immutable version plus persistence provenance used by the library UI."""

    version: StrategyVersion
    source_draft_revision: int | None
    source_draft_hash: str | None
    source_provenance: Mapping[str, Any]


class StrategyRepository:
    """SQLite repository for immutable strategy version snapshots.

    Each operation uses a short-lived connection. Version allocation is done
    inside an ``IMMEDIATE`` transaction and protected by a database constraint,
    so separate application threads cannot allocate the same version number.
    """

    SCHEMA_VERSION = 4
    ACTIVE = "ACTIVE"
    TRASHED = "TRASHED"
    DESTRUCTIVE_OPERATION_BLOCKED = "DESTRUCTIVE_OPERATION_BLOCKED"
    _BACKUP_RETENTION = 5

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
            if self._migration_requires_backup(connection):
                self._create_safety_backup(connection)
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
                    latest_version_number INTEGER NOT NULL DEFAULT 0,
                    updated_at TEXT,
                    status TEXT,
                    metadata_json TEXT
                    ,lifecycle_status TEXT NOT NULL DEFAULT 'ACTIVE'
                    ,is_protected INTEGER NOT NULL DEFAULT 0 CHECK(is_protected IN (0, 1))
                    ,deleted_at TEXT
                );

                CREATE TABLE IF NOT EXISTS strategy_versions (
                    strategy_id TEXT NOT NULL,
                    version_id TEXT NOT NULL,
                    version_number INTEGER NOT NULL CHECK (version_number > 0),
                    created_at TEXT NOT NULL,
                    configuration_json TEXT NOT NULL,
                    content_hash TEXT NOT NULL,
                    status TEXT NOT NULL,
                    source_draft_revision INTEGER,
                    source_draft_hash TEXT,
                    source_provenance_json TEXT,
                    PRIMARY KEY (strategy_id, version_id),
                    UNIQUE (strategy_id, version_number),
                    FOREIGN KEY (strategy_id) REFERENCES strategies(strategy_id)
                );

                CREATE TABLE IF NOT EXISTS materialized_strategy_versions (
                    strategy_id TEXT NOT NULL,
                    version_id TEXT NOT NULL UNIQUE,
                    version_number INTEGER NOT NULL CHECK (version_number > 0),
                    created_at TEXT NOT NULL,
                    configuration_json TEXT NOT NULL,
                    content_hash TEXT NOT NULL,
                    status TEXT NOT NULL,
                    materialization_provenance_json TEXT NOT NULL,
                    version_json TEXT NOT NULL,
                    PRIMARY KEY (strategy_id, version_id)
                );

                CREATE TABLE IF NOT EXISTS strategy_drafts (
                    strategy_id TEXT PRIMARY KEY,
                    draft_revision INTEGER NOT NULL CHECK (draft_revision >= 1),
                    active_mode TEXT NOT NULL,
                    rule_based_draft_json TEXT NOT NULL,
                    regime_draft_json TEXT NOT NULL,
                    active_strategy_payload_json TEXT,
                    source_metadata_json TEXT NOT NULL,
                    draft_hash TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    FOREIGN KEY (strategy_id) REFERENCES strategies(strategy_id)
                );
                """
            )
            self._ensure_column(connection, "strategies", "updated_at", "TEXT")
            self._ensure_column(connection, "strategies", "status", "TEXT")
            self._ensure_column(connection, "strategies", "metadata_json", "TEXT")
            self._ensure_column(
                connection, "strategies", "lifecycle_status", "TEXT NOT NULL DEFAULT 'ACTIVE'"
            )
            self._ensure_column(
                connection, "strategies", "is_protected", "INTEGER NOT NULL DEFAULT 0"
            )
            self._ensure_column(connection, "strategies", "deleted_at", "TEXT")
            self._ensure_column(connection, "strategy_versions", "source_draft_revision", "INTEGER")
            self._ensure_column(connection, "strategy_versions", "source_draft_hash", "TEXT")
            self._ensure_column(connection, "strategy_versions", "source_provenance_json", "TEXT")
            connection.execute(
                "UPDATE strategies SET updated_at = COALESCE(updated_at, created_at), "
                "status = COALESCE(status, 'active'), "
                "metadata_json = COALESCE(metadata_json, '{}'), "
                "lifecycle_status = COALESCE(lifecycle_status, 'ACTIVE'), "
                "is_protected = COALESCE(is_protected, 0)"
            )
            row = connection.execute(
                "SELECT schema_version FROM schema_metadata WHERE schema_key = ?",
                ("strategy_repository",),
            ).fetchone()
            if row is None:
                connection.execute(
                    "INSERT INTO schema_metadata(schema_key, schema_version) VALUES (?, ?)",
                    ("strategy_repository", self.SCHEMA_VERSION),
                )
            elif int(row["schema_version"]) in {1, 2, 3}:
                connection.execute(
                    "UPDATE schema_metadata SET schema_version = ? WHERE schema_key = ?",
                    (self.SCHEMA_VERSION, "strategy_repository"),
                )
            elif int(row["schema_version"]) != self.SCHEMA_VERSION:
                raise StrategyPersistenceError("unsupported strategy database schema version")
            row = connection.execute(
                "SELECT schema_version FROM schema_metadata WHERE schema_key = ?",
                ("strategy_repository",),
            ).fetchone()
            if row is None or row["schema_version"] != self.SCHEMA_VERSION:
                raise StrategyPersistenceError("unsupported strategy database schema version")
            collision = connection.execute(
                """
                SELECT ordinary.version_id
                FROM strategy_versions AS ordinary
                INNER JOIN materialized_strategy_versions AS derived
                    ON derived.version_id = ordinary.version_id
                LIMIT 1
                """
            ).fetchone()
            if collision is not None:
                raise StrategyPersistenceError(
                    "strategy version identity is present in multiple stores",
                    code="STRATEGY_VERSION_IDENTITY_COLLISION",
                )
        except sqlite3.Error as exc:
            raise StrategyPersistenceError("could not initialize strategy database") from exc
        finally:
            connection.close()

    def _migration_requires_backup(self, connection: sqlite3.Connection) -> bool:
        if not self.db_path.exists():
            return False
        try:
            table = connection.execute(
                "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'schema_metadata'"
            ).fetchone()
            if table is None:
                return (
                    connection.execute(
                        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'strategies'"
                    ).fetchone()
                    is not None
                )
            row = connection.execute(
                "SELECT schema_version FROM schema_metadata WHERE schema_key = ?",
                ("strategy_repository",),
            ).fetchone()
            return row is None or int(row["schema_version"]) < self.SCHEMA_VERSION
        except sqlite3.Error as exc:
            raise StrategyPersistenceError("could not inspect strategy schema version") from exc

    def _create_safety_backup(self, connection: sqlite3.Connection) -> None:
        backup_dir = self.db_path.parent / "backups"
        backup_dir.mkdir(parents=True, exist_ok=True)
        timestamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
        target = backup_dir / f"strategy-before-schema-v{self.SCHEMA_VERSION}-{timestamp}.db"
        temporary = target.with_suffix(".db.tmp")
        destination = sqlite3.connect(temporary)
        try:
            connection.backup(destination)
            destination.commit()
            destination.close()
            with temporary.open("rb") as handle:
                os.fsync(handle.fileno())
            temporary.replace(target)
        except (OSError, sqlite3.Error) as exc:
            try:
                destination.close()
            except sqlite3.Error:
                pass
            temporary.unlink(missing_ok=True)
            raise StrategyPersistenceError(
                "could not create strategy schema safety backup"
            ) from exc
        finally:
            try:
                destination.close()
            except sqlite3.Error:
                pass
        backups = sorted(
            backup_dir.glob("strategy-before-schema-v*.db"),
            key=lambda item: item.stat().st_mtime,
            reverse=True,
        )
        for old_backup in backups[self._BACKUP_RETENTION :]:
            old_backup.unlink(missing_ok=True)

    def _is_temporary_database(self) -> bool:
        try:
            return self.db_path.resolve().is_relative_to(Path(tempfile.gettempdir()).resolve())
        except OSError:
            return False

    @staticmethod
    def _ensure_column(
        connection: sqlite3.Connection, table: str, column: str, definition: str
    ) -> None:
        columns = {
            str(row["name"]) for row in connection.execute(f"PRAGMA table_info({table})").fetchall()
        }
        if column not in columns:
            connection.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")

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
            if self._version_id_exists(connection, version.version_id):
                raise StrategyPersistenceError(
                    "strategy version identity collides with an existing version",
                    code="STRATEGY_VERSION_IDENTITY_COLLISION",
                )
            created_at = version.created_at.isoformat()
            connection.execute(
                """
                INSERT INTO strategies(
                    strategy_id, name, description, created_at, latest_version_number,
                    updated_at, status, metadata_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(strategy_id) DO UPDATE SET
                    name = excluded.name,
                    description = excluded.description,
                    latest_version_number = excluded.latest_version_number,
                    updated_at = excluded.updated_at
                """,
                (
                    definition.strategy_id,
                    definition.name,
                    definition.description,
                    created_at,
                    version_number,
                    created_at,
                    "active",
                    "{}",
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

    def create_strategy(
        self,
        strategy_id: str,
        name: str,
        description: str,
        metadata: Mapping[str, Any] | None = None,
    ) -> StrategyRecord:
        """Create a mutable strategy identity without requiring an executable version."""
        strategy_id = self._required_text(strategy_id, "strategy_id")
        name = self._required_text(name, "name")
        description = str(description)
        metadata_json = self._safe_json(dict(metadata or {}))
        now = datetime.now(UTC)
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            try:
                connection.execute(
                    "INSERT INTO strategies(strategy_id, name, description, created_at, "
                    "latest_version_number, updated_at, status, metadata_json, "
                    "lifecycle_status, is_protected, deleted_at) "
                    "VALUES (?, ?, ?, ?, 0, ?, 'active', ?, 'ACTIVE', 0, NULL)",
                    (
                        strategy_id,
                        name,
                        description,
                        now.isoformat(),
                        now.isoformat(),
                        metadata_json,
                    ),
                )
            except sqlite3.IntegrityError as exc:
                self._rollback(connection)
                raise StrategyPersistenceError(
                    "strategy identity already exists", code="STRATEGY_ALREADY_EXISTS"
                ) from exc
            connection.execute("COMMIT")
            return StrategyRecord(
                strategy_id=strategy_id,
                name=name,
                description=description,
                created_at=now,
                updated_at=now,
                status="active",
                metadata=dict(metadata or {}),
            )
        except StrategyPersistenceError:
            raise
        except sqlite3.Error as exc:
            self._rollback(connection)
            raise StrategyPersistenceError("could not create strategy identity") from exc
        finally:
            connection.close()

    def get_strategy(self, strategy_id: str) -> StrategyRecord | None:
        connection = self._connect()
        try:
            row = connection.execute(
                "SELECT strategy_id, name, description, created_at, updated_at, status, "
                "metadata_json, lifecycle_status, is_protected, deleted_at "
                "FROM strategies WHERE strategy_id = ?",
                (strategy_id,),
            ).fetchone()
            return None if row is None else self._row_to_strategy(row)
        except (sqlite3.Error, TypeError, ValueError, json.JSONDecodeError) as exc:
            raise StrategyPersistenceError("could not load strategy identity") from exc
        finally:
            connection.close()

    def list_strategies(self, lifecycle_status: str = ACTIVE) -> tuple[StrategyRecord, ...]:
        if lifecycle_status not in {self.ACTIVE, self.TRASHED, "ALL"}:
            raise StrategyPersistenceError("invalid strategy lifecycle status")
        connection = self._connect()
        try:
            where = "" if lifecycle_status == "ALL" else " WHERE lifecycle_status = ?"
            params: tuple[str, ...] = () if not where else (lifecycle_status,)
            rows = connection.execute(
                "SELECT strategy_id, name, description, created_at, updated_at, status, "
                "metadata_json, lifecycle_status, is_protected, deleted_at "
                f"FROM strategies{where} ORDER BY strategy_id",
                params,
            ).fetchall()
            return tuple(self._row_to_strategy(row) for row in rows)
        except (sqlite3.Error, TypeError, ValueError, json.JSONDecodeError) as exc:
            raise StrategyPersistenceError("could not list strategy identities") from exc
        finally:
            connection.close()

    def trash(self, strategy_id: str) -> str:
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT lifecycle_status, is_protected FROM strategies WHERE strategy_id = ?",
                (strategy_id,),
            ).fetchone()
            if row is None:
                connection.execute("ROLLBACK")
                return "not_found"
            if int(row["is_protected"]):
                connection.execute("ROLLBACK")
                return "protected"
            if row["lifecycle_status"] == self.TRASHED:
                connection.execute("COMMIT")
                return "already_trashed"
            connection.execute(
                "UPDATE strategies SET lifecycle_status = ?, deleted_at = ? WHERE strategy_id = ?",
                (self.TRASHED, datetime.now(UTC).isoformat(), strategy_id),
            )
            connection.execute("COMMIT")
            return "trashed"
        except sqlite3.Error as exc:
            self._rollback(connection)
            raise StrategyPersistenceError("could not move strategy to trash") from exc
        finally:
            connection.close()

    def restore(self, strategy_id: str) -> str:
        connection = self._connect()
        try:
            cursor = connection.execute(
                "UPDATE strategies SET lifecycle_status = ?, deleted_at = NULL "
                "WHERE strategy_id = ?",
                (self.ACTIVE, strategy_id),
            )
            return "restored" if cursor.rowcount else "not_found"
        except sqlite3.Error as exc:
            raise StrategyPersistenceError("could not restore strategy") from exc
        finally:
            connection.close()

    def set_protected(self, strategy_id: str, protected: bool) -> str:
        connection = self._connect()
        try:
            cursor = connection.execute(
                "UPDATE strategies SET is_protected = ? WHERE strategy_id = ?",
                (1 if protected else 0, strategy_id),
            )
            return "updated" if cursor.rowcount else "not_found"
        except sqlite3.Error as exc:
            raise StrategyPersistenceError("could not update strategy protection") from exc
        finally:
            connection.close()

    def bulk_trash(self, strategy_ids: tuple[str, ...]) -> BulkStrategyTrashResult:
        trashed: list[str] = []
        skipped: list[str] = []
        not_found: list[str] = []
        for strategy_id in dict.fromkeys(strategy_ids):
            result = self.trash(strategy_id)
            if result in {"trashed", "already_trashed"}:
                trashed.append(strategy_id)
            elif result == "protected":
                skipped.append(strategy_id)
            else:
                not_found.append(strategy_id)
        return BulkStrategyTrashResult(
            requested=len(strategy_ids),
            trashed=tuple(trashed),
            skipped_protected=tuple(skipped),
            blocked_dependencies=(),
            not_found=tuple(not_found),
        )

    def dependencies(self, strategy_id: str) -> tuple[StrategyDependency, ...]:
        connection = self._connect()
        try:
            return self._dependencies_in_connection(connection, strategy_id)
        except sqlite3.Error as exc:
            raise StrategyPersistenceError("could not inspect strategy dependencies") from exc
        finally:
            connection.close()

    @staticmethod
    def _dependencies_in_connection(
        connection: sqlite3.Connection, strategy_id: str
    ) -> tuple[StrategyDependency, ...]:
        version_rows = connection.execute(
            "SELECT version_id FROM strategy_versions WHERE strategy_id = ? "
            "UNION SELECT version_id FROM materialized_strategy_versions WHERE strategy_id = ?",
            (strategy_id, strategy_id),
        ).fetchall()
        version_ids = tuple(str(row["version_id"]) for row in version_rows)
        tables = [
            str(row["name"])
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table' ORDER BY name"
            ).fetchall()
        ]
        dependencies: list[StrategyDependency] = []
        for table in tables:
            if table.startswith("sqlite_") or table in {
                "strategies",
                "strategy_drafts",
                "strategy_versions",
                "materialized_strategy_versions",
                "schema_metadata",
            }:
                continue
            columns = {
                str(row["name"])
                for row in connection.execute(f"PRAGMA table_info({table})").fetchall()
            }
            direct_columns = columns.intersection({"strategy_id", "strategy_definition_id"})
            version_columns = columns.intersection(
                {
                    "strategy_version_id",
                    "base_strategy_version_id",
                    "derived_strategy_version_id",
                    "selected_strategy_version_id",
                    "selected_derived_strategy_version_id",
                }
            )
            payload_columns = columns.intersection(
                {
                    "payload_json",
                    "run_json",
                    "experiment_json",
                    "definition_json",
                    "preflight_json",
                    "canonical_json",
                    "provenance_json",
                    "evidence_json",
                    "selection_json",
                    "result_json",
                }
            )
            for column in sorted(direct_columns):
                rows = connection.execute(
                    f"SELECT DISTINCT {column} AS value FROM {table} WHERE {column} = ?",
                    (strategy_id,),
                ).fetchall()
                dependencies.extend(
                    StrategyDependency(
                        kind=table,
                        identifier=f"{table}:{column}:{row['value']}",
                        detail=f"{table} references Strategy {strategy_id}",
                    )
                    for row in rows
                )
            for column in sorted(version_columns):
                if not version_ids:
                    continue
                placeholders = ", ".join("?" for _ in version_ids)
                rows = connection.execute(
                    f"SELECT DISTINCT {column} AS value FROM {table} "
                    f"WHERE {column} IN ({placeholders})",
                    version_ids,
                ).fetchall()
                dependencies.extend(
                    StrategyDependency(
                        kind=table,
                        identifier=f"{table}:{column}:{row['value']}",
                        detail=f"{table} references StrategyVersion {row['value']}",
                    )
                    for row in rows
                )
            if not direct_columns and not version_columns:
                for column in sorted(payload_columns):
                    references = (strategy_id, *version_ids)
                    for reference in references:
                        rows = connection.execute(
                            f"SELECT rowid AS row_id FROM {table} "
                            f"WHERE {column} LIKE '%' || ? || '%'",
                            (reference,),
                        ).fetchall()
                        dependencies.extend(
                            StrategyDependency(
                                kind=table,
                                identifier=f"{table}:{column}:{row['row_id']}",
                                detail=f"{table} payload references {reference}",
                            )
                            for row in rows
                        )
        return tuple(dependencies)

    def permanently_delete(self, strategy_id: str) -> str | tuple[StrategyDependency, ...]:
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT lifecycle_status, is_protected FROM strategies WHERE strategy_id = ?",
                (strategy_id,),
            ).fetchone()
            if row is None:
                connection.execute("ROLLBACK")
                return "not_found"
            if row["lifecycle_status"] != self.TRASHED:
                connection.execute("ROLLBACK")
                return "must_be_trashed"
            if int(row["is_protected"]):
                connection.execute("ROLLBACK")
                return "protected"
            dependencies = self._dependencies_in_connection(connection, strategy_id)
            if dependencies:
                connection.execute("ROLLBACK")
                return dependencies
            connection.execute("DELETE FROM strategy_drafts WHERE strategy_id = ?", (strategy_id,))
            connection.execute(
                "DELETE FROM strategy_versions WHERE strategy_id = ?", (strategy_id,)
            )
            connection.execute(
                "DELETE FROM materialized_strategy_versions WHERE strategy_id = ?", (strategy_id,)
            )
            connection.execute("DELETE FROM strategies WHERE strategy_id = ?", (strategy_id,))
            connection.execute("COMMIT")
            return "deleted"
        except sqlite3.Error as exc:
            self._rollback(connection)
            raise StrategyPersistenceError("could not permanently delete strategy") from exc
        finally:
            connection.close()

    def diagnostics(self) -> dict[str, object]:
        connection = self._connect()
        try:
            schema = connection.execute(
                "SELECT schema_version FROM schema_metadata WHERE schema_key = ?",
                ("strategy_repository",),
            ).fetchone()
            return {
                "database_path": str(self.db_path.resolve()),
                "schema_version": None if schema is None else int(schema["schema_version"]),
                "strategy_count": int(
                    connection.execute("SELECT COUNT(*) FROM strategies").fetchone()[0]
                ),
                "draft_count": int(
                    connection.execute("SELECT COUNT(*) FROM strategy_drafts").fetchone()[0]
                ),
                "version_count": int(
                    connection.execute("SELECT COUNT(*) FROM strategy_versions").fetchone()[0]
                ),
            }
        except sqlite3.Error as exc:
            raise StrategyPersistenceError("could not read strategy diagnostics") from exc
        finally:
            connection.close()

    def get_draft(self, strategy_id: str) -> StrategyDraft | None:
        connection = self._connect()
        try:
            row = connection.execute(
                "SELECT strategy_id, draft_revision, active_mode, rule_based_draft_json, "
                "regime_draft_json, active_strategy_payload_json, source_metadata_json, "
                "draft_hash, created_at, updated_at FROM strategy_drafts WHERE strategy_id = ?",
                (strategy_id,),
            ).fetchone()
            return None if row is None else self._row_to_draft(row)
        except (sqlite3.Error, TypeError, ValueError, json.JSONDecodeError) as exc:
            raise StrategyPersistenceError("could not load strategy draft") from exc
        finally:
            connection.close()

    def save_draft(
        self,
        *,
        strategy_id: str,
        expected_revision: int,
        active_mode: str,
        rule_based_draft: Mapping[str, Any],
        regime_draft: Mapping[str, Any],
        active_strategy_payload: Mapping[str, Any] | None,
        source_metadata: Mapping[str, Any] | None,
        name: str,
        description: str,
    ) -> StrategyDraft:
        """Atomically save both mode drafts with optimistic concurrency."""
        if active_mode not in {"rule_based", "regime_state_machine"}:
            raise StrategyPersistenceError("active_mode is invalid", code="DRAFT_INVALID")
        if expected_revision < 0:
            raise StrategyPersistenceError("expected_revision is invalid", code="DRAFT_INVALID")
        strategy_id = self._required_text(strategy_id, "strategy_id")
        rule_json = self._safe_json(dict(rule_based_draft))
        regime_json = self._safe_json(dict(regime_draft))
        payload_json = (
            None
            if active_strategy_payload is None
            else self._safe_json(dict(active_strategy_payload))
        )
        source_json = self._safe_json(dict(source_metadata or {}))
        now = datetime.now(UTC)
        draft_hash = self._hash_json(
            {
                "strategy_id": strategy_id,
                "active_mode": active_mode,
                "rule_based_draft": json.loads(rule_json),
                "regime_draft": json.loads(regime_json),
                "active_strategy_payload": None
                if payload_json is None
                else json.loads(payload_json),
                "source_metadata": json.loads(source_json),
            }
        )
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            strategy = connection.execute(
                "SELECT strategy_id FROM strategies WHERE strategy_id = ?", (strategy_id,)
            ).fetchone()
            if strategy is None:
                self._rollback(connection)
                raise StrategyPersistenceError(
                    "strategy identity was not found", code="STRATEGY_NOT_FOUND"
                )
            current = connection.execute(
                "SELECT draft_revision FROM strategy_drafts WHERE strategy_id = ?", (strategy_id,)
            ).fetchone()
            current_revision = 0 if current is None else int(current["draft_revision"])
            if current_revision != expected_revision:
                self._rollback(connection)
                raise StrategyPersistenceError(
                    "draft revision is stale",
                    code="DRAFT_REVISION_CONFLICT",
                )
            revision = current_revision + 1
            if current is None:
                connection.execute(
                    "INSERT INTO strategy_drafts(strategy_id, draft_revision, active_mode, "
                    "rule_based_draft_json, regime_draft_json, active_strategy_payload_json, "
                    "source_metadata_json, draft_hash, created_at, updated_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        strategy_id,
                        revision,
                        active_mode,
                        rule_json,
                        regime_json,
                        payload_json,
                        source_json,
                        draft_hash,
                        now.isoformat(),
                        now.isoformat(),
                    ),
                )
                created_at = now
            else:
                connection.execute(
                    "UPDATE strategy_drafts SET draft_revision = ?, active_mode = ?, "
                    "rule_based_draft_json = ?, regime_draft_json = ?, "
                    "active_strategy_payload_json = ?, source_metadata_json = ?, "
                    "draft_hash = ?, updated_at = ? WHERE strategy_id = ?",
                    (
                        revision,
                        active_mode,
                        rule_json,
                        regime_json,
                        payload_json,
                        source_json,
                        draft_hash,
                        now.isoformat(),
                        strategy_id,
                    ),
                )
                existing = connection.execute(
                    "SELECT created_at FROM strategy_drafts WHERE strategy_id = ?", (strategy_id,)
                ).fetchone()
                created_at = self._parse_datetime(existing["created_at"])
            connection.execute(
                "UPDATE strategies SET name = ?, description = ?, updated_at = ? "
                "WHERE strategy_id = ?",
                (str(name), str(description), now.isoformat(), strategy_id),
            )
            connection.execute("COMMIT")
            return StrategyDraft(
                strategy_id=strategy_id,
                draft_revision=revision,
                active_mode=active_mode,
                rule_based_draft=json.loads(rule_json),
                regime_draft=json.loads(regime_json),
                active_strategy_payload=None if payload_json is None else json.loads(payload_json),
                source_metadata=json.loads(source_json),
                created_at=created_at,
                updated_at=now,
                draft_hash=draft_hash,
            )
        except StrategyPersistenceError:
            raise
        except (sqlite3.Error, TypeError, ValueError, json.JSONDecodeError) as exc:
            self._rollback(connection)
            raise StrategyPersistenceError("could not save strategy draft") from exc
        finally:
            connection.close()

    def create_version_from_draft(
        self,
        *,
        strategy_id: str,
        expected_revision: int,
        definition: StrategyDefinition,
    ) -> StrategyVersion:
        """Atomically snapshot the saved active draft into a new immutable version."""
        if definition.strategy_id != strategy_id:
            raise StrategyPersistenceError(
                "strategy identity does not match draft", code="DRAFT_INVALID"
            )
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT draft_revision, active_strategy_payload_json, active_mode, draft_hash "
                "FROM strategy_drafts WHERE strategy_id = ?",
                (strategy_id,),
            ).fetchone()
            if row is None:
                self._rollback(connection)
                raise StrategyPersistenceError(
                    "strategy draft was not found", code="DRAFT_NOT_FOUND"
                )
            if int(row["draft_revision"]) != expected_revision:
                self._rollback(connection)
                raise StrategyPersistenceError(
                    "draft revision is stale", code="DRAFT_REVISION_CONFLICT"
                )
            if row["active_strategy_payload_json"] is None:
                self._rollback(connection)
                raise StrategyPersistenceError(
                    "active draft has not passed serialization and validation",
                    code="DRAFT_INVALID",
                )
            stored = json.loads(row["active_strategy_payload_json"])
            if StrategyDefinition.from_dict(stored).to_json() != definition.to_json():
                self._rollback(connection)
                raise StrategyPersistenceError(
                    "draft payload changed before version creation", code="DRAFT_REVISION_CONFLICT"
                )
            latest = connection.execute(
                "SELECT COALESCE(MAX(version_number), 0) AS latest_version_number "
                "FROM strategy_versions WHERE strategy_id = ?",
                (strategy_id,),
            ).fetchone()
            version_number = int(latest["latest_version_number"]) + 1
            now = datetime.now(UTC)
            version = StrategyVersion(
                strategy_id=strategy_id,
                version_id=f"{strategy_id}-v{version_number}-{uuid4().hex[:12]}",
                version_number=version_number,
                created_at=now,
                configuration=definition,
                status=StrategyStatus.DRAFT,
            )
            provenance = self._safe_json(
                {
                    "source": "strategy_draft",
                    "draft_revision": expected_revision,
                    "draft_hash": row["draft_hash"],
                    "active_mode": row["active_mode"],
                }
            )
            connection.execute(
                "INSERT INTO strategy_versions(strategy_id, version_id, version_number, "
                "created_at, "
                "configuration_json, content_hash, status, source_draft_revision, "
                "source_draft_hash, "
                "source_provenance_json) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    strategy_id,
                    version.version_id,
                    version_number,
                    now.isoformat(),
                    definition.to_json(),
                    version.content_hash,
                    version.status.value,
                    expected_revision,
                    row["draft_hash"],
                    provenance,
                ),
            )
            connection.execute(
                "UPDATE strategies SET latest_version_number = ?, updated_at = ? "
                "WHERE strategy_id = ?",
                (version_number, now.isoformat(), strategy_id),
            )
            connection.execute("COMMIT")
            return version
        except StrategyPersistenceError:
            raise
        except (sqlite3.Error, TypeError, ValueError, json.JSONDecodeError) as exc:
            self._rollback(connection)
            raise StrategyPersistenceError("could not create version from draft") from exc
        finally:
            connection.close()

    def persist_exact_strategy_version(self, version: StrategyVersion) -> StrategyVersion:
        """Persist one already-materialized version without changing its identity.

        This is an internal persistence boundary for PHASE 8D execution.  It
        intentionally never calls materialization or allocates an ordinary
        sequential version number.
        """
        try:
            self._validate_exact_materialized_version(version)
        except StrategyPersistenceError:
            raise
        except (TypeError, ValueError) as exc:
            raise StrategyPersistenceError(
                "materialized strategy version failed integrity validation",
                code="STRATEGY_VERSION_INTEGRITY_ERROR",
            ) from exc

        provenance = version.materialization_provenance
        assert provenance is not None
        version_json = self._safe_version_json(version)
        provenance_json = self._safe_json(provenance.to_dict())
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            base_rows = self._version_id_rows(connection, provenance.base_strategy_version_id)
            if len(base_rows) != 1:
                self._rollback(connection)
                raise StrategyPersistenceError(
                    "base strategy version identity is missing or ambiguous",
                    code="BASE_STRATEGY_VERSION_INTEGRITY_ERROR",
                )
            base_content_hash = base_rows[0]["row"]["content_hash"]
            if base_content_hash != provenance.base_strategy_version_hash:
                self._rollback(connection)
                raise StrategyPersistenceError(
                    "base strategy version hash does not match provenance",
                    code="BASE_STRATEGY_VERSION_INTEGRITY_ERROR",
                )
            existing_rows = self._version_id_rows(connection, version.version_id)
            if existing_rows:
                if len(existing_rows) != 1 or existing_rows[0]["source"] != "derived":
                    self._rollback(connection)
                    raise StrategyPersistenceError(
                        "strategy version identity collides with an existing version",
                        code="STRATEGY_VERSION_IDENTITY_COLLISION",
                    )
                restored = self._row_to_materialized_version(existing_rows[0]["row"])
                if restored == version:
                    connection.execute("COMMIT")
                    return restored
                self._rollback(connection)
                raise StrategyPersistenceError(
                    "materialized strategy version identity already has different content",
                    code="STRATEGY_VERSION_IMMUTABILITY_CONFLICT",
                )

            connection.execute(
                """
                INSERT INTO materialized_strategy_versions(
                    strategy_id, version_id, version_number, created_at,
                    configuration_json, content_hash, status,
                    materialization_provenance_json, version_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    version.strategy_id,
                    version.version_id,
                    version.version_number,
                    version.created_at.isoformat(),
                    version.configuration.to_json(),
                    version.content_hash,
                    version.status.value,
                    provenance_json,
                    version_json,
                ),
            )
            connection.execute("COMMIT")
            return version
        except StrategyPersistenceError:
            self._rollback(connection)
            raise
        except sqlite3.IntegrityError as exc:
            self._rollback(connection)
            raise StrategyPersistenceError(
                "materialized strategy version identity conflicts with existing data",
                code="STRATEGY_VERSION_IDENTITY_COLLISION",
            ) from exc
        except sqlite3.Error as exc:
            self._rollback(connection)
            raise StrategyPersistenceError(
                "could not persist materialized strategy version"
            ) from exc
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

    def list_version_records(self, strategy_id: str) -> tuple[StrategyVersionRecord, ...]:
        """Return ordinary version metadata without weakening immutable snapshots."""
        connection = self._connect()
        try:
            rows = connection.execute(
                """
                SELECT strategy_id, version_id, version_number, created_at,
                       configuration_json, content_hash, status,
                       source_draft_revision, source_draft_hash, source_provenance_json
                FROM strategy_versions
                WHERE strategy_id = ?
                ORDER BY version_number ASC
                """,
                (strategy_id,),
            ).fetchall()
            return tuple(self._row_to_version_record(row) for row in rows)
        except (sqlite3.Error, TypeError, ValueError, json.JSONDecodeError) as exc:
            raise StrategyPersistenceError("could not list strategy version metadata") from exc
        finally:
            connection.close()

    def get_version_record(self, strategy_id: str, version_id: str) -> StrategyVersionRecord | None:
        connection = self._connect()
        try:
            row = connection.execute(
                """
                SELECT strategy_id, version_id, version_number, created_at,
                       configuration_json, content_hash, status,
                       source_draft_revision, source_draft_hash, source_provenance_json
                FROM strategy_versions
                WHERE strategy_id = ? AND version_id = ?
                """,
                (strategy_id, version_id),
            ).fetchone()
            return None if row is None else self._row_to_version_record(row)
        except (sqlite3.Error, TypeError, ValueError, json.JSONDecodeError) as exc:
            raise StrategyPersistenceError("could not load strategy version metadata") from exc
        finally:
            connection.close()

    def create_draft_from_version(
        self,
        *,
        strategy_id: str,
        version_id: str,
        expected_revision: int,
        replace_current: bool,
    ) -> StrategyDraft:
        """Persist a new editable draft from one immutable ordinary version."""
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            strategy = connection.execute(
                "SELECT name, description FROM strategies WHERE strategy_id = ?",
                (strategy_id,),
            ).fetchone()
            if strategy is None:
                self._rollback(connection)
                raise StrategyPersistenceError(
                    "strategy identity was not found", code="STRATEGY_NOT_FOUND"
                )
            row = connection.execute(
                """
                SELECT version_id, version_number, configuration_json, content_hash
                FROM strategy_versions
                WHERE strategy_id = ? AND version_id = ?
                """,
                (strategy_id, version_id),
            ).fetchone()
            if row is None:
                self._rollback(connection)
                raise StrategyPersistenceError(
                    "strategy version was not found", code="VERSION_NOT_FOUND"
                )
            current = connection.execute(
                "SELECT draft_revision FROM strategy_drafts WHERE strategy_id = ?",
                (strategy_id,),
            ).fetchone()
            current_revision = 0 if current is None else int(current["draft_revision"])
            if current_revision != expected_revision:
                self._rollback(connection)
                raise StrategyPersistenceError(
                    "draft revision is stale", code="DRAFT_REVISION_CONFLICT"
                )
            if current is not None and not replace_current:
                self._rollback(connection)
                raise StrategyPersistenceError(
                    "replacing the current draft requires confirmation",
                    code="DRAFT_REPLACE_CONFIRMATION_REQUIRED",
                )
            configuration = json.loads(row["configuration_json"], parse_constant=_reject_nonfinite)
            if not isinstance(configuration, Mapping):
                raise ValueError("version configuration must be an object")
            mode = str(configuration.get("strategy_mode") or "rule_based")
            if mode not in {"rule_based", "regime_state_machine"}:
                mode = "rule_based"
            assets = [
                str(item.get("symbol"))
                for item in configuration.get("assets", [])
                if isinstance(item, Mapping) and item.get("symbol")
            ]
            mode_payload = {
                "assets": assets,
                "editor_state": {"sourcePayload": dict(configuration)},
                "source_payload": dict(configuration),
            }
            now = datetime.now(UTC)
            revision = current_revision + 1
            rule_payload = (
                mode_payload if mode == "rule_based" else {"assets": [], "editor_state": {}}
            )
            regime_payload = (
                mode_payload
                if mode == "regime_state_machine"
                else {"assets": [], "editor_state": {}}
            )
            source_metadata = {
                "source": "immutable_version",
                "source_version_id": version_id,
                "source_version_number": int(row["version_number"]),
                "source_version_hash": str(row["content_hash"]),
                "price_field": str(configuration.get("price_field", "adjusted_close")),
                "rebalance_frequency": str(
                    (configuration.get("rebalance_policy") or {}).get("frequency", "daily")
                ),
                "rebalance_threshold_percent": str(
                    (configuration.get("rebalance_policy") or {}).get("threshold") or ""
                ),
            }
            draft_hash = self._hash_json(
                {
                    "strategy_id": strategy_id,
                    "active_mode": mode,
                    "rule_based_draft": rule_payload,
                    "regime_draft": regime_payload,
                    "active_strategy_payload": dict(configuration),
                    "source_metadata": source_metadata,
                }
            )
            if current is None:
                connection.execute(
                    """INSERT INTO strategy_drafts(
                        strategy_id, draft_revision, active_mode, rule_based_draft_json,
                        regime_draft_json, active_strategy_payload_json, source_metadata_json,
                        draft_hash, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        strategy_id,
                        revision,
                        mode,
                        self._safe_json(rule_payload),
                        self._safe_json(regime_payload),
                        self._safe_json(dict(configuration)),
                        self._safe_json(source_metadata),
                        draft_hash,
                        now.isoformat(),
                        now.isoformat(),
                    ),
                )
                created_at = now
            else:
                connection.execute(
                    """UPDATE strategy_drafts SET draft_revision = ?, active_mode = ?,
                        rule_based_draft_json = ?, regime_draft_json = ?,
                        active_strategy_payload_json = ?, source_metadata_json = ?,
                        draft_hash = ?, updated_at = ? WHERE strategy_id = ?""",
                    (
                        revision,
                        mode,
                        self._safe_json(rule_payload),
                        self._safe_json(regime_payload),
                        self._safe_json(dict(configuration)),
                        self._safe_json(source_metadata),
                        draft_hash,
                        now.isoformat(),
                        strategy_id,
                    ),
                )
                created_at = self._parse_datetime(
                    connection.execute(
                        "SELECT created_at FROM strategy_drafts WHERE strategy_id = ?",
                        (strategy_id,),
                    ).fetchone()["created_at"]
                )
            connection.execute(
                "UPDATE strategies SET name = ?, description = ?, updated_at = ? "
                "WHERE strategy_id = ?",
                (
                    str(configuration.get("name", strategy["name"])),
                    str(configuration.get("description", strategy["description"])),
                    now.isoformat(),
                    strategy_id,
                ),
            )
            connection.execute("COMMIT")
            return StrategyDraft(
                strategy_id=strategy_id,
                draft_revision=revision,
                active_mode=mode,
                rule_based_draft=rule_payload,
                regime_draft=regime_payload,
                active_strategy_payload=dict(configuration),
                source_metadata=source_metadata,
                created_at=created_at,
                updated_at=now,
                draft_hash=draft_hash,
            )
        except StrategyPersistenceError:
            raise
        except (sqlite3.Error, TypeError, ValueError, json.JSONDecodeError) as exc:
            self._rollback(connection)
            raise StrategyPersistenceError("could not create draft from version") from exc
        finally:
            connection.close()

    def clone_strategy_from_version(
        self,
        *,
        source_strategy_id: str,
        source_version_id: str,
        new_strategy_id: str,
        name: str,
        description: str,
    ) -> tuple[StrategyRecord, StrategyDraft]:
        """Create a new identity and editable draft with no version lineage."""
        new_strategy_id = self._required_text(new_strategy_id, "strategy_id")
        name = self._required_text(name, "name")
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            source = connection.execute(
                "SELECT version_number, configuration_json, content_hash "
                "FROM strategy_versions WHERE strategy_id = ? AND version_id = ?",
                (source_strategy_id, source_version_id),
            ).fetchone()
            if source is None:
                self._rollback(connection)
                raise StrategyPersistenceError(
                    "strategy version was not found", code="VERSION_NOT_FOUND"
                )
            if connection.execute(
                "SELECT 1 FROM strategies WHERE strategy_id = ?", (new_strategy_id,)
            ).fetchone():
                self._rollback(connection)
                raise StrategyPersistenceError(
                    "strategy identity already exists", code="STRATEGY_ALREADY_EXISTS"
                )
            configuration = json.loads(
                source["configuration_json"], parse_constant=_reject_nonfinite
            )
            mode = str(configuration.get("strategy_mode") or "rule_based")
            assets = [
                str(item["symbol"])
                for item in configuration.get("assets", [])
                if isinstance(item, Mapping) and item.get("symbol")
            ]
            rule_payload = (
                {
                    "assets": assets,
                    "editor_state": {"sourcePayload": configuration},
                    "source_payload": configuration,
                }
                if mode == "rule_based"
                else {"assets": [], "editor_state": {}}
            )
            regime_payload = (
                {
                    "assets": assets,
                    "editor_state": {"sourcePayload": configuration},
                    "source_payload": configuration,
                }
                if mode == "regime_state_machine"
                else {"assets": [], "editor_state": {}}
            )
            now = datetime.now(UTC)
            metadata = {
                "cloned_from_strategy_id": source_strategy_id,
                "cloned_from_version_id": source_version_id,
            }
            draft_hash = self._hash_json(
                {
                    "strategy_id": new_strategy_id,
                    "active_mode": mode,
                    "rule_based_draft": rule_payload,
                    "regime_draft": regime_payload,
                    "active_strategy_payload": configuration,
                    "source_metadata": metadata,
                }
            )
            connection.execute(
                "INSERT INTO strategies(strategy_id, name, description, created_at, "
                "latest_version_number, updated_at, status, metadata_json, "
                "lifecycle_status, is_protected, deleted_at) "
                "VALUES (?, ?, ?, ?, 0, ?, 'active', ?, 'ACTIVE', 0, NULL)",
                (
                    new_strategy_id,
                    name,
                    description,
                    now.isoformat(),
                    now.isoformat(),
                    self._safe_json(metadata),
                ),
            )
            connection.execute(
                "INSERT INTO strategy_drafts(strategy_id, draft_revision, active_mode, "
                "rule_based_draft_json, regime_draft_json, active_strategy_payload_json, "
                "source_metadata_json, draft_hash, created_at, updated_at) "
                "VALUES (?, 1, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    new_strategy_id,
                    mode,
                    self._safe_json(rule_payload),
                    self._safe_json(regime_payload),
                    self._safe_json(configuration),
                    self._safe_json(metadata),
                    draft_hash,
                    now.isoformat(),
                    now.isoformat(),
                ),
            )
            connection.execute("COMMIT")
            record = StrategyRecord(
                new_strategy_id,
                name,
                description,
                now,
                now,
                "active",
                metadata,
                self.ACTIVE,
                False,
                None,
            )
            draft = StrategyDraft(
                new_strategy_id,
                1,
                mode,
                rule_payload,
                regime_payload,
                configuration,
                metadata,
                now,
                now,
                draft_hash,
            )
            return record, draft
        except StrategyPersistenceError:
            raise
        except (sqlite3.Error, TypeError, ValueError, json.JSONDecodeError) as exc:
            self._rollback(connection)
            raise StrategyPersistenceError("could not clone strategy from version") from exc
        finally:
            connection.close()

    def catalog(self) -> tuple[dict[str, object], ...]:
        """Return the minimal Strategy Lab catalog metadata."""
        connection = self._connect()
        try:
            rows = connection.execute(
                """
                SELECT strategy_id, name, latest_version_number,
                       (SELECT COUNT(*) FROM strategy_versions versions
                        WHERE versions.strategy_id = strategies.strategy_id) AS version_count
                FROM strategies
                ORDER BY strategy_id ASC
                """
            ).fetchall()
            return tuple(
                {
                    "strategy_id": row["strategy_id"],
                    "name": row["name"],
                    "version_count": int(row["version_count"]),
                    "latest_version": (
                        int(row["latest_version_number"])
                        if row["latest_version_number"] is not None
                        else None
                    ),
                }
                for row in rows
            )
        except sqlite3.Error as exc:
            raise StrategyPersistenceError("could not list strategy catalog") from exc
        finally:
            connection.close()

    def get(self, strategy_id: str, version_id: str) -> StrategyVersion | None:
        """Return one immutable version, or ``None`` when it does not exist."""
        connection = self._connect()
        try:
            rows = self._version_id_rows(connection, version_id)
            if len(rows) > 1:
                raise StrategyPersistenceError(
                    "strategy version identity is present in multiple stores",
                    code="STRATEGY_VERSION_IDENTITY_COLLISION",
                )
            if not rows or rows[0]["row"]["strategy_id"] != strategy_id:
                return None
            return (
                self._row_to_version(rows[0]["row"])
                if rows[0]["source"] == "ordinary"
                else self._row_to_materialized_version(rows[0]["row"])
            )
        except StrategyPersistenceError:
            raise
        except sqlite3.Error as exc:
            raise StrategyPersistenceError("could not load strategy version") from exc
        finally:
            connection.close()

    def get_any_version(self, version_id: str) -> StrategyVersion | None:
        """Load an ordinary or derived version by its globally unique identity."""
        connection = self._connect()
        try:
            rows = self._version_id_rows(connection, version_id)
            if len(rows) > 1:
                raise StrategyPersistenceError(
                    "strategy version identity is present in multiple stores",
                    code="STRATEGY_VERSION_IDENTITY_COLLISION",
                )
            if not rows:
                return None
            return (
                self._row_to_version(rows[0]["row"])
                if rows[0]["source"] == "ordinary"
                else self._row_to_materialized_version(rows[0]["row"])
            )
        except StrategyPersistenceError:
            raise
        except sqlite3.Error as exc:
            raise StrategyPersistenceError("could not load strategy version") from exc
        finally:
            connection.close()

    def clear(self) -> None:
        """Remove all records from a temporary test database only."""
        if not self._is_temporary_database():
            raise StrategyPersistenceError(
                "destructive strategy clear is blocked for the canonical database",
                code=self.DESTRUCTIVE_OPERATION_BLOCKED,
            )
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute("DELETE FROM strategy_versions")
            connection.execute("DELETE FROM materialized_strategy_versions")
            connection.execute("DELETE FROM strategy_drafts")
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

    @classmethod
    def _row_to_version_record(cls, row: sqlite3.Row) -> StrategyVersionRecord:
        return StrategyVersionRecord(
            version=cls._row_to_version(row),
            source_draft_revision=(
                None if row["source_draft_revision"] is None else int(row["source_draft_revision"])
            ),
            source_draft_hash=(
                None if row["source_draft_hash"] is None else str(row["source_draft_hash"])
            ),
            source_provenance=json.loads(row["source_provenance_json"] or "{}"),
        )

    @classmethod
    def _row_to_materialized_version(cls, row: sqlite3.Row) -> StrategyVersion:
        required_fields = (
            "strategy_id",
            "version_id",
            "version_number",
            "created_at",
            "configuration_json",
            "content_hash",
            "status",
            "materialization_provenance_json",
            "version_json",
        )
        if any(row[field] is None for field in required_fields):
            raise StrategyPersistenceError("stored materialized strategy version is incomplete")
        try:
            version_payload = json.loads(row["version_json"], parse_constant=_reject_nonfinite)
            version = StrategyVersion.from_dict(version_payload)
            if version.materialization_provenance is None:
                raise ValueError("materialization provenance is missing")
            if version.strategy_id != row["strategy_id"] or version.version_id != row["version_id"]:
                raise ValueError("stored version identity does not match its columns")
            if version.version_number != row["version_number"]:
                raise ValueError("stored version number does not match its columns")
            if version.created_at.isoformat() != row["created_at"]:
                raise ValueError("stored created_at does not match its columns")
            if version.configuration.to_json() != row["configuration_json"]:
                raise ValueError("stored configuration does not match its columns")
            if version.content_hash != row["content_hash"] or version.status.value != row["status"]:
                raise ValueError("stored version metadata does not match its columns")
            provenance = version.materialization_provenance
            if cls._safe_json(provenance.to_dict()) != row["materialization_provenance_json"]:
                raise ValueError("stored materialization provenance does not match its columns")
            cls._validate_exact_materialized_version(version)
            return version
        except (TypeError, ValueError, OverflowError, json.JSONDecodeError) as exc:
            raise StrategyPersistenceError(
                "stored materialized strategy version failed integrity checks",
                code="STRATEGY_VERSION_INTEGRITY_ERROR",
            ) from exc

    @classmethod
    def _validate_exact_materialized_version(cls, version: StrategyVersion) -> None:
        if not isinstance(version, StrategyVersion):
            raise StrategyPersistenceError(
                "materialized strategy version is invalid",
                code="STRATEGY_VERSION_INTEGRITY_ERROR",
            )
        provenance = version.materialization_provenance
        if provenance is None:
            raise StrategyPersistenceError(
                "only materialized strategy versions may use exact persistence",
                code="STRATEGY_VERSION_INTEGRITY_ERROR",
            )
        expected_hash = derived_strategy_version_hash(
            version.configuration,
            base_strategy_version_id=provenance.base_strategy_version_id,
            base_strategy_version_hash=provenance.base_strategy_version_hash,
            parameter_set_hash=provenance.parameter_set_hash,
            binding_hash=provenance.binding_hash,
            materialization_spec_hash=provenance.materialization_spec_hash,
            binding_audit=provenance.binding_audit,
        )
        if provenance.derived_strategy_version_hash != expected_hash:
            raise StrategyPersistenceError(
                "derived strategy version hash does not match provenance",
                code="STRATEGY_VERSION_INTEGRITY_ERROR",
            )
        if version.version_id != derived_strategy_version_id(
            provenance.base_strategy_version_id, expected_hash
        ):
            raise StrategyPersistenceError(
                "derived strategy version id does not match provenance",
                code="STRATEGY_VERSION_INTEGRITY_ERROR",
            )
        canonical = StrategyVersion(
            strategy_id=version.strategy_id,
            version_id=version.version_id,
            version_number=version.version_number,
            created_at=version.created_at,
            configuration=version.configuration,
            status=version.status,
            materialization_provenance=version.materialization_provenance,
        )
        if version.content_hash != canonical.content_hash:
            raise StrategyPersistenceError(
                "derived strategy content hash does not match configuration",
                code="STRATEGY_VERSION_INTEGRITY_ERROR",
            )

    @classmethod
    def _safe_json(cls, payload: object) -> str:
        return json.dumps(
            payload,
            ensure_ascii=True,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )

    @classmethod
    def _safe_version_json(cls, version: StrategyVersion) -> str:
        return cls._safe_json(version.to_dict())

    @classmethod
    def _row_to_strategy(cls, row: sqlite3.Row) -> StrategyRecord:
        return StrategyRecord(
            strategy_id=str(row["strategy_id"]),
            name=str(row["name"]),
            description=str(row["description"]),
            created_at=cls._parse_datetime(row["created_at"]),
            updated_at=cls._parse_datetime(row["updated_at"] or row["created_at"]),
            status=str(row["status"] or "active"),
            metadata=json.loads(row["metadata_json"] or "{}"),
            lifecycle_status=str(row["lifecycle_status"] or cls.ACTIVE),
            is_protected=bool(row["is_protected"]),
            deleted_at=(
                None if row["deleted_at"] is None else cls._parse_datetime(row["deleted_at"])
            ),
        )

    @classmethod
    def _row_to_draft(cls, row: sqlite3.Row) -> StrategyDraft:
        rule_based = json.loads(row["rule_based_draft_json"])
        regime = json.loads(row["regime_draft_json"])
        active_payload = (
            None
            if row["active_strategy_payload_json"] is None
            else json.loads(row["active_strategy_payload_json"])
        )
        source_metadata = json.loads(row["source_metadata_json"])
        return StrategyDraft(
            strategy_id=str(row["strategy_id"]),
            draft_revision=int(row["draft_revision"]),
            active_mode=str(row["active_mode"]),
            rule_based_draft=rule_based,
            regime_draft=regime,
            active_strategy_payload=active_payload,
            source_metadata=source_metadata,
            created_at=cls._parse_datetime(row["created_at"]),
            updated_at=cls._parse_datetime(row["updated_at"]),
            draft_hash=str(row["draft_hash"]),
        )

    @staticmethod
    def _required_text(value: object, label: str) -> str:
        if not isinstance(value, str) or not value.strip():
            raise StrategyPersistenceError(f"{label} must be non-empty", code="DRAFT_INVALID")
        return value.strip()

    @classmethod
    def _hash_json(cls, payload: object) -> str:
        import hashlib

        return hashlib.sha256(cls._safe_json(payload).encode("utf-8")).hexdigest()

    @staticmethod
    def _parse_datetime(value: object) -> datetime:
        if not isinstance(value, str):
            raise ValueError("stored datetime is invalid")
        return datetime.fromisoformat(value.replace("Z", "+00:00"))

    @staticmethod
    def _version_id_rows(
        connection: sqlite3.Connection, version_id: str
    ) -> list[dict[str, object]]:
        ordinary = connection.execute(
            "SELECT strategy_id, version_id, version_number, created_at, "
            "configuration_json, content_hash, status "
            "FROM strategy_versions WHERE version_id = ?",
            (version_id,),
        ).fetchall()
        derived = connection.execute(
            "SELECT strategy_id, version_id, version_number, created_at, "
            "configuration_json, content_hash, status, "
            "materialization_provenance_json, version_json "
            "FROM materialized_strategy_versions WHERE version_id = ?",
            (version_id,),
        ).fetchall()
        return [
            *({"source": "ordinary", "row": row} for row in ordinary),
            *({"source": "derived", "row": row} for row in derived),
        ]

    @classmethod
    def _version_id_count(cls, connection: sqlite3.Connection, version_id: str) -> int:
        return len(cls._version_id_rows(connection, version_id))

    @classmethod
    def _version_id_exists(cls, connection: sqlite3.Connection, version_id: str) -> bool:
        return cls._version_id_count(connection, version_id) > 0


def _reject_nonfinite(value: str) -> None:
    raise ValueError(f"non-finite JSON constant {value}")


# PHASE 5A imported this name directly. Keep it as a compatibility alias while
# the implementation is now durable and repository-backed.
StrategyVersionStore = StrategyRepository
