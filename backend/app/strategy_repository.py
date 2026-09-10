"""Durable persistence for immutable StrategyDefinition versions."""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Mapping
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


class StrategyRepository:
    """SQLite repository for immutable strategy version snapshots.

    Each operation uses a short-lived connection. Version allocation is done
    inside an ``IMMEDIATE`` transaction and protected by a database constraint,
    so separate application threads cannot allocate the same version number.
    """

    SCHEMA_VERSION = 2

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
                """
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
            elif int(row["schema_version"]) == 1:
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
        """Remove all records; intended for isolated local tests."""
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute("DELETE FROM strategy_versions")
            connection.execute("DELETE FROM materialized_strategy_versions")
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
            version_payload = json.loads(
                row["version_json"], parse_constant=_reject_nonfinite
            )
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
