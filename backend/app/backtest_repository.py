"""SQLite persistence for immutable backtest research artifacts."""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from backend.app.backtest_models import (
    ANALYSIS_VERSION,
    METADATA_METRIC_NAMES,
    BacktestRun,
    BacktestRunMetadata,
    dumps,
)


class BacktestPersistenceError(RuntimeError):
    """Raised when a stored backtest run cannot be safely persisted or restored."""


@dataclass(frozen=True)
class BacktestRunRecord:
    """One immutable run plus the analysis version stored with it."""

    run: BacktestRun
    analysis_version: str

    def __post_init__(self) -> None:
        if not isinstance(self.run, BacktestRun):
            raise TypeError("run must be a BacktestRun")
        if not isinstance(self.analysis_version, str) or not self.analysis_version.strip():
            raise ValueError("analysis_version must be a non-empty string")
        object.__setattr__(self, "analysis_version", self.analysis_version.strip())


@dataclass(frozen=True)
class BacktestMetadataPage:
    """A bounded page of metadata rows with a database-side total count."""

    items: tuple[BacktestRunMetadata, ...]
    total: int
    limit: int
    offset: int
    sort_by: str
    order: str


@dataclass(frozen=True)
class BacktestMetadataBackfillReport:
    """Result of an explicit, bounded legacy metadata backfill."""

    scanned: int
    inserted: int


@dataclass(frozen=True)
class BacktestRunDependency:
    kind: str
    identifier: str
    detail: str


@dataclass(frozen=True)
class BulkTrashResult:
    requested: int
    trashed: tuple[str, ...]
    skipped_protected: tuple[str, ...]
    blocked_dependencies: tuple[str, ...]
    not_found: tuple[str, ...]


class BacktestRepository:
    """Durable repository that never updates or overwrites a completed run."""

    SCHEMA_VERSION = 1
    METADATA_TABLE = "backtest_run_metadata"
    MANAGEMENT_TABLE = "backtest_run_management"
    ACTIVE = "ACTIVE"
    TRASHED = "TRASHED"
    RUN_TYPES = ("MANUAL", "GRID_CANDIDATE", "OFFICIAL_OOS")

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
            raise BacktestPersistenceError("could not prepare backtest database") from exc

    def _connect(self) -> sqlite3.Connection:
        try:
            connection = sqlite3.connect(self.db_path, timeout=30.0, isolation_level=None)
            connection.row_factory = sqlite3.Row
            connection.execute("PRAGMA foreign_keys = ON")
            connection.execute("PRAGMA busy_timeout = 30000")
            return connection
        except sqlite3.Error as exc:
            raise BacktestPersistenceError("could not open backtest database") from exc

    def _initialize(self) -> None:
        connection = self._connect()
        try:
            self.ensure_schema(connection)
        except sqlite3.Error as exc:
            raise BacktestPersistenceError("could not initialize backtest database") from exc
        finally:
            connection.close()

    @staticmethod
    def ensure_schema(connection: sqlite3.Connection) -> None:
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS backtest_runs (
                backtest_run_id TEXT PRIMARY KEY,
                strategy_id TEXT NOT NULL,
                strategy_version_id TEXT NOT NULL,
                created_at TEXT NOT NULL,
                strategy_version_content_hash TEXT NOT NULL,
                start_date TEXT NOT NULL,
                end_date TEXT NOT NULL,
                price_field_used TEXT NOT NULL,
                engine_version TEXT NOT NULL,
                analysis_version TEXT NOT NULL,
                run_json TEXT NOT NULL
            )
            """
        )
        connection.execute(
            "CREATE INDEX IF NOT EXISTS idx_backtest_runs_strategy "
            "ON backtest_runs(strategy_id, created_at DESC, backtest_run_id DESC)"
        )
        # The v2 name is additive so existing databases do not need a destructive
        # index rebuild; the planner can use it for the complete deterministic key.
        connection.execute(
            "CREATE INDEX IF NOT EXISTS idx_backtest_runs_strategy_created_v2 "
            "ON backtest_runs(strategy_id, created_at DESC, backtest_run_id DESC)"
        )
        connection.execute(
            "CREATE INDEX IF NOT EXISTS idx_backtest_runs_created "
            "ON backtest_runs(created_at DESC, backtest_run_id DESC)"
        )
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS backtest_run_metadata (
                backtest_run_id TEXT PRIMARY KEY,
                strategy_id TEXT NOT NULL,
                strategy_version_id TEXT NOT NULL,
                created_at TEXT NOT NULL,
                strategy_version_content_hash TEXT NOT NULL,
                start_date TEXT NOT NULL,
                end_date TEXT NOT NULL,
                price_field_used TEXT NOT NULL,
                engine_version TEXT NOT NULL,
                analysis_version TEXT NOT NULL,
                initial_capital REAL NOT NULL,
                final_equity REAL NOT NULL,
                experiment_id TEXT,
                candidate_id TEXT,
                candidate_index INTEGER,
                metadata_projection_version TEXT NOT NULL,
                metadata_json TEXT NOT NULL,
                cagr_value REAL,
                sharpe_ratio_value REAL,
                sortino_ratio_value REAL,
                max_drawdown_value REAL,
                total_return_value REAL,
                annualized_volatility_value REAL,
                calmar_ratio_value REAL,
                win_rate_value REAL,
                profit_factor_value REAL,
                average_trade_return_value REAL,
                best_trade_value REAL,
                worst_trade_value REAL,
                average_holding_period_value REAL,
                turnover_value REAL,
                FOREIGN KEY(backtest_run_id) REFERENCES backtest_runs(backtest_run_id)
                    ON DELETE CASCADE
            )
            """
        )
        connection.execute(
            "CREATE INDEX IF NOT EXISTS idx_backtest_metadata_strategy_created "
            "ON backtest_run_metadata(strategy_id, created_at DESC, backtest_run_id DESC)"
        )
        connection.execute(
            "CREATE INDEX IF NOT EXISTS idx_backtest_metadata_version_created "
            "ON backtest_run_metadata(strategy_version_id, created_at DESC, backtest_run_id DESC)"
        )
        connection.execute(
            "CREATE INDEX IF NOT EXISTS idx_backtest_metadata_experiment_candidate "
            "ON backtest_run_metadata("
            "experiment_id, candidate_index, created_at DESC, backtest_run_id DESC)"
        )
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS backtest_run_management (
                backtest_run_id TEXT PRIMARY KEY,
                lifecycle_status TEXT NOT NULL DEFAULT 'ACTIVE'
                    CHECK(lifecycle_status IN ('ACTIVE', 'TRASHED')),
                deleted_at TEXT,
                is_protected INTEGER NOT NULL DEFAULT 0 CHECK(is_protected IN (0, 1)),
                run_type TEXT NOT NULL DEFAULT 'MANUAL'
                    CHECK(run_type IN ('MANUAL', 'GRID_CANDIDATE', 'OFFICIAL_OOS')),
                FOREIGN KEY(backtest_run_id) REFERENCES backtest_runs(backtest_run_id)
                    ON DELETE CASCADE
            )
            """
        )
        connection.execute(
            "CREATE INDEX IF NOT EXISTS idx_backtest_management_status_created "
            "ON backtest_run_management(lifecycle_status, deleted_at, backtest_run_id)"
        )
        connection.execute(
            "INSERT OR IGNORE INTO backtest_run_management(backtest_run_id) "
            "SELECT backtest_run_id FROM backtest_runs"
        )

    def create(self, run: BacktestRun) -> BacktestRun:
        """Insert one immutable run; repeated inputs still receive a new id."""
        if not isinstance(run, BacktestRun):
            raise BacktestPersistenceError("backtest run is invalid")
        payload = dumps(run.to_dict())
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            self.persist_in_transaction(connection, run, payload=payload)
            connection.execute("COMMIT")
            return run
        except sqlite3.IntegrityError as exc:
            raise BacktestPersistenceError("backtest run already exists") from exc
        except sqlite3.Error as exc:
            raise BacktestPersistenceError("could not persist backtest run") from exc
        finally:
            connection.close()

    @classmethod
    def persist_in_transaction(
        cls,
        connection: sqlite3.Connection,
        run: BacktestRun,
        *,
        payload: str | None = None,
    ) -> BacktestRun:
        """Insert a run without beginning or committing the caller transaction."""
        if not isinstance(run, BacktestRun):
            raise BacktestPersistenceError("backtest run is invalid")
        serialized = payload if payload is not None else dumps(run.to_dict())
        result = run.backtest_result
        analysis = run.performance_analysis
        try:
            connection.execute(
                """
                INSERT INTO backtest_runs(
                    backtest_run_id, strategy_id, strategy_version_id, created_at,
                    strategy_version_content_hash, start_date, end_date, price_field_used,
                    engine_version, analysis_version, run_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    run.backtest_run_id,
                    run.strategy_id,
                    run.strategy_version_id,
                    run.created_at.isoformat(),
                    run.strategy_version_content_hash,
                    result.start_date.isoformat(),
                    result.end_date.isoformat(),
                    analysis.price_field_used.value,
                    result.engine_version,
                    ANALYSIS_VERSION,
                    serialized,
                ),
            )
            metadata = BacktestRunMetadata.from_run(run)
            connection.execute(
                """
                INSERT INTO backtest_run_metadata(
                    backtest_run_id, strategy_id, strategy_version_id, created_at,
                    strategy_version_content_hash, start_date, end_date, price_field_used,
                    engine_version, analysis_version, initial_capital, final_equity,
                    experiment_id, candidate_id, candidate_index, metadata_projection_version,
                    metadata_json, cagr_value, sharpe_ratio_value, sortino_ratio_value,
                    max_drawdown_value, total_return_value, annualized_volatility_value,
                    calmar_ratio_value, win_rate_value, profit_factor_value,
                    average_trade_return_value, best_trade_value, worst_trade_value,
                    average_holding_period_value, turnover_value
                ) VALUES (
                    ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                    ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
                )
                """,
                cls._metadata_values(metadata),
            )
            connection.execute(
                """
                INSERT OR IGNORE INTO backtest_run_management(
                    backtest_run_id, lifecycle_status, deleted_at, is_protected, run_type
                ) VALUES (?, 'ACTIVE', NULL, 0, ?)
                """,
                (run.backtest_run_id, metadata.run_type),
            )
        except sqlite3.IntegrityError as exc:
            raise BacktestPersistenceError("backtest run already exists") from exc
        except (sqlite3.Error, TypeError, ValueError, OverflowError) as exc:
            raise BacktestPersistenceError("could not persist backtest run") from exc
        return run

    def get(self, backtest_run_id: str) -> BacktestRun | None:
        """Return one run or None when its id is unknown."""
        connection = self._connect()
        try:
            row = connection.execute(
                "SELECT run_json FROM backtest_runs WHERE backtest_run_id = ?",
                (backtest_run_id,),
            ).fetchone()
            if row is None:
                return None
            return self._decode(row["run_json"])
        except sqlite3.Error as exc:
            raise BacktestPersistenceError("could not load backtest run") from exc
        finally:
            connection.close()

    def list(self, strategy_id: str | None = None) -> tuple[BacktestRun, ...]:
        """Return immutable runs, newest first, optionally scoped to a strategy."""
        return tuple(record.run for record in self.list_records(strategy_id))

    def list_records(self, strategy_id: str | None = None) -> tuple[BacktestRunRecord, ...]:
        """Return immutable runs with their stored analysis version, newest first."""
        connection = self._connect()
        try:
            if strategy_id is None:
                rows = connection.execute(
                    "SELECT run_json, analysis_version FROM backtest_runs "
                    "ORDER BY created_at DESC, backtest_run_id DESC"
                ).fetchall()
            else:
                rows = connection.execute(
                    """
                    SELECT run_json, analysis_version FROM backtest_runs
                    WHERE strategy_id = ?
                    ORDER BY created_at DESC, backtest_run_id DESC
                    """,
                    (strategy_id,),
                ).fetchall()
            return tuple(self._record(row) for row in rows)
        except sqlite3.Error as exc:
            raise BacktestPersistenceError("could not list backtest runs") from exc
        finally:
            connection.close()

    def list_metadata(
        self,
        strategy_id: str | None = None,
        *,
        strategy_version_id: str | None = None,
        experiment_id: str | None = None,
        sort_by: str = "created_at",
        order: str = "desc",
        limit: int = 50,
        offset: int = 0,
        lifecycle_status: str = "ACTIVE",
        run_type: str | None = None,
        search: str | None = None,
        created_from: str | None = None,
        created_to: str | None = None,
        date_from: str | None = None,
        date_to: str | None = None,
    ) -> BacktestMetadataPage:
        """List only bounded metadata columns; never selects or decodes run_json."""
        if sort_by != "created_at" and sort_by not in METADATA_METRIC_NAMES:
            raise BacktestPersistenceError("unsupported backtest metadata sort field")
        if order not in {"asc", "desc"}:
            raise BacktestPersistenceError("unsupported backtest metadata sort order")
        if not 1 <= limit <= 100 or offset < 0:
            raise BacktestPersistenceError("invalid backtest metadata page")
        where, params = self._metadata_filters(
            strategy_id,
            strategy_version_id,
            experiment_id,
            lifecycle_status=lifecycle_status,
            run_type=run_type,
            search=search,
            created_from=created_from,
            created_to=created_to,
            date_from=date_from,
            date_to=date_to,
        )
        sort_sql = self._metadata_sort(sort_by, order)
        connection = self._connect()
        try:
            total = int(
                connection.execute(
                    f"SELECT COUNT(*) FROM backtest_runs r LEFT JOIN backtest_run_metadata m "
                    f"ON m.backtest_run_id = r.backtest_run_id "
                    f"LEFT JOIN backtest_run_management rm "
                    f"ON rm.backtest_run_id = r.backtest_run_id {where}",
                    params,
                ).fetchone()[0]
            )
            rows = connection.execute(
                f"{self._metadata_select()} LEFT JOIN backtest_run_metadata m "
                f"ON m.backtest_run_id = r.backtest_run_id LEFT JOIN backtest_run_management rm "
                f"ON rm.backtest_run_id = r.backtest_run_id {where} "
                f"ORDER BY {sort_sql} LIMIT ? OFFSET ?",
                (*params, limit, offset),
            ).fetchall()
            items = tuple(self._metadata_from_row(row) for row in rows)
            return BacktestMetadataPage(items, total, limit, offset, sort_by, order)
        except (sqlite3.Error, TypeError, ValueError, OverflowError, json.JSONDecodeError) as exc:
            if isinstance(exc, BacktestPersistenceError):
                raise
            raise BacktestPersistenceError("could not list backtest metadata") from exc
        finally:
            connection.close()

    def get_metadata(self, backtest_run_id: str) -> BacktestRunMetadata | None:
        """Read one metadata projection without loading the canonical artifact."""
        connection = self._connect()
        try:
            row = connection.execute(
                f"{self._metadata_select()} LEFT JOIN backtest_run_metadata m "
                "ON m.backtest_run_id = r.backtest_run_id WHERE r.backtest_run_id = ?",
                (backtest_run_id,),
            ).fetchone()
            return None if row is None else self._metadata_from_row(row)
        except (sqlite3.Error, TypeError, ValueError, OverflowError, json.JSONDecodeError) as exc:
            if isinstance(exc, BacktestPersistenceError):
                raise
            raise BacktestPersistenceError("could not load backtest metadata") from exc
        finally:
            connection.close()

    def backfill_metadata(self, *, limit: int = 100) -> BacktestMetadataBackfillReport:
        """Explicitly project legacy rows in a bounded transaction.

        History requests never invoke this method, so legacy JSON is only parsed
        when an operator explicitly schedules a rebuild.
        """
        if not 1 <= limit <= 1_000:
            raise BacktestPersistenceError("invalid metadata backfill limit")
        connection = self._connect()
        scanned = inserted = 0
        try:
            connection.execute("BEGIN IMMEDIATE")
            rows = connection.execute(
                """
                SELECT r.backtest_run_id, r.run_json
                FROM backtest_runs r
                LEFT JOIN backtest_run_metadata m ON m.backtest_run_id = r.backtest_run_id
                WHERE m.backtest_run_id IS NULL
                ORDER BY r.created_at DESC, r.backtest_run_id DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
            columns = self._metadata_columns()
            placeholders = ", ".join("?" for _ in columns.split(", "))
            for row in rows:
                scanned += 1
                try:
                    run = self._decode(row["run_json"])
                except BacktestPersistenceError as exc:
                    raise BacktestPersistenceError(
                        f"metadata backfill failed for run {row['backtest_run_id']}"
                    ) from exc
                metadata = BacktestRunMetadata.from_run(run)
                cursor = connection.execute(
                    f"INSERT OR IGNORE INTO backtest_run_metadata({columns}) "
                    f"VALUES ({placeholders})",
                    self._metadata_values(metadata),
                )
                inserted += cursor.rowcount
            connection.execute("COMMIT")
            return BacktestMetadataBackfillReport(scanned=scanned, inserted=inserted)
        except BacktestPersistenceError:
            try:
                connection.execute("ROLLBACK")
            except sqlite3.Error:
                pass
            raise
        except (sqlite3.Error, TypeError, ValueError, OverflowError, json.JSONDecodeError) as exc:
            try:
                connection.execute("ROLLBACK")
            except sqlite3.Error:
                pass
            if isinstance(exc, BacktestPersistenceError):
                raise
            raise BacktestPersistenceError("could not backfill backtest metadata") from exc
        finally:
            connection.close()

    def get_records(self, backtest_run_ids: tuple[str, ...]) -> tuple[BacktestRunRecord, ...]:
        """Load existing runs in request order without creating or updating data."""
        if not backtest_run_ids:
            return ()
        placeholders = ", ".join("?" for _ in backtest_run_ids)
        connection = self._connect()
        try:
            rows = connection.execute(
                f"SELECT backtest_run_id, run_json, analysis_version FROM backtest_runs "
                f"WHERE backtest_run_id IN ({placeholders})",
                backtest_run_ids,
            ).fetchall()
            by_id = {str(row["backtest_run_id"]): self._record(row) for row in rows}
            return tuple(
                record for run_id in backtest_run_ids if (record := by_id.get(run_id)) is not None
            )
        except sqlite3.Error as exc:
            raise BacktestPersistenceError("could not load backtest runs") from exc
        finally:
            connection.close()

    def clear(self) -> None:
        """Remove all runs; intended for isolated tests only."""
        connection = self._connect()
        try:
            connection.execute("DELETE FROM backtest_runs")
        except sqlite3.Error as exc:
            raise BacktestPersistenceError("could not clear backtest runs") from exc
        finally:
            connection.close()

    def dependencies(self, backtest_run_id: str) -> tuple[BacktestRunDependency, ...]:
        """Return durable research-chain references that block permanent deletion."""
        connection = self._connect()
        try:
            return self._dependencies_in_connection(connection, backtest_run_id)
        except sqlite3.Error as exc:
            raise BacktestPersistenceError("could not inspect backtest dependencies") from exc
        finally:
            connection.close()

    def trash(self, backtest_run_id: str) -> str:
        """Move a run to trash; repeated trash requests are idempotent."""
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT lifecycle_status, is_protected FROM backtest_run_management "
                "WHERE backtest_run_id = ?",
                (backtest_run_id,),
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
                "UPDATE backtest_run_management SET lifecycle_status = 'TRASHED', "
                "deleted_at = ? WHERE backtest_run_id = ?",
                (datetime.now(UTC).isoformat(), backtest_run_id),
            )
            connection.execute("COMMIT")
            return "trashed"
        except sqlite3.Error as exc:
            try:
                connection.execute("ROLLBACK")
            except sqlite3.Error:
                pass
            raise BacktestPersistenceError("could not move backtest run to trash") from exc
        finally:
            connection.close()

    def bulk_trash(self, backtest_run_ids: tuple[str, ...]) -> BulkTrashResult:
        trashed: list[str] = []
        skipped: list[str] = []
        not_found: list[str] = []
        for run_id in dict.fromkeys(backtest_run_ids):
            result = self.trash(run_id)
            if result in {"trashed", "already_trashed"}:
                trashed.append(run_id)
            elif result == "protected":
                skipped.append(run_id)
            else:
                not_found.append(run_id)
        return BulkTrashResult(
            len(backtest_run_ids), tuple(trashed), tuple(skipped), (), tuple(not_found)
        )

    def restore(self, backtest_run_id: str) -> str:
        return self._set_status(backtest_run_id, self.ACTIVE)

    def set_protected(self, backtest_run_id: str, protected: bool) -> str:
        connection = self._connect()
        try:
            cursor = connection.execute(
                "UPDATE backtest_run_management SET is_protected = ? WHERE backtest_run_id = ?",
                (1 if protected else 0, backtest_run_id),
            )
            return "updated" if cursor.rowcount else "not_found"
        except sqlite3.Error as exc:
            raise BacktestPersistenceError("could not update backtest protection") from exc
        finally:
            connection.close()

    def permanently_delete(self, backtest_run_id: str) -> str | tuple[BacktestRunDependency, ...]:
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT lifecycle_status, is_protected FROM backtest_run_management "
                "WHERE backtest_run_id = ?",
                (backtest_run_id,),
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
            deps = self._dependencies_in_connection(connection, backtest_run_id)
            if deps:
                connection.execute("ROLLBACK")
                return deps
            connection.execute(
                "DELETE FROM backtest_runs WHERE backtest_run_id = ?", (backtest_run_id,)
            )
            connection.execute("COMMIT")
            return "deleted"
        except sqlite3.Error as exc:
            try:
                connection.execute("ROLLBACK")
            except sqlite3.Error:
                pass
            raise BacktestPersistenceError("could not permanently delete backtest run") from exc
        finally:
            connection.close()

    @staticmethod
    def _dependencies_in_connection(
        connection: sqlite3.Connection, backtest_run_id: str
    ) -> tuple[BacktestRunDependency, ...]:
        dependencies: list[BacktestRunDependency] = []
        tables = {
            str(row[0])
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            ).fetchall()
        }
        if "research_experiment_results" in tables:
            for row in connection.execute(
                "SELECT experiment_result_id, experiment_id, candidate_id "
                "FROM research_experiment_results WHERE backtest_run_id = ?",
                (backtest_run_id,),
            ):
                dependencies.append(
                    BacktestRunDependency(
                        "EXPERIMENT_CANDIDATE",
                        str(row["experiment_result_id"]),
                        f"Experiment {row['experiment_id']} / Candidate {row['candidate_id']}",
                    )
                )
        if "research_oos_results" in tables:
            for row in connection.execute(
                "SELECT oos_result_id, protocol_id FROM research_oos_results "
                "WHERE backtest_run_id = ?",
                (backtest_run_id,),
            ):
                dependencies.append(
                    BacktestRunDependency(
                        "OFFICIAL_OOS",
                        str(row["oos_result_id"]),
                        f"Official OOS / Protocol {row['protocol_id']}",
                    )
                )
        return tuple(dependencies)

    def _set_status(self, backtest_run_id: str, status: str) -> str:
        connection = self._connect()
        try:
            row = connection.execute(
                "SELECT lifecycle_status FROM backtest_run_management WHERE backtest_run_id = ?",
                (backtest_run_id,),
            ).fetchone()
            if row is None:
                return "not_found"
            if row["lifecycle_status"] == status:
                return "already_active" if status == self.ACTIVE else "already_trashed"
            connection.execute(
                "UPDATE backtest_run_management SET lifecycle_status = ?, deleted_at = ? "
                "WHERE backtest_run_id = ?",
                (
                    status,
                    None if status == self.ACTIVE else datetime.now(UTC).isoformat(),
                    backtest_run_id,
                ),
            )
            return "restored" if status == self.ACTIVE else "trashed"
        except sqlite3.Error as exc:
            raise BacktestPersistenceError("could not update backtest lifecycle") from exc
        finally:
            connection.close()

    @staticmethod
    def _metadata_columns() -> str:
        return (
            "backtest_run_id, strategy_id, strategy_version_id, created_at, "
            "strategy_version_content_hash, start_date, end_date, price_field_used, "
            "engine_version, analysis_version, initial_capital, final_equity, "
            "experiment_id, candidate_id, candidate_index, metadata_projection_version, "
            "metadata_json, cagr_value, sharpe_ratio_value, sortino_ratio_value, "
            "max_drawdown_value, total_return_value, annualized_volatility_value, "
            "calmar_ratio_value, win_rate_value, profit_factor_value, "
            "average_trade_return_value, best_trade_value, worst_trade_value, "
            "average_holding_period_value, turnover_value"
        )

    @classmethod
    def _metadata_values(cls, metadata: BacktestRunMetadata) -> tuple[object, ...]:
        return (
            metadata.backtest_run_id,
            metadata.strategy_id,
            metadata.strategy_version_id,
            metadata.created_at,
            metadata.strategy_version_content_hash,
            metadata.start_date,
            metadata.end_date,
            metadata.price_field_used,
            metadata.engine_version,
            metadata.analysis_version,
            metadata.initial_capital,
            metadata.final_equity,
            metadata.experiment_id,
            metadata.candidate_id,
            metadata.candidate_index,
            metadata.projection_version,
            dumps(metadata.to_payload()),
            *(metadata.metric_values.get(name) for name in METADATA_METRIC_NAMES),
        )

    @staticmethod
    def _metadata_select() -> str:
        return (
            "SELECT r.backtest_run_id, r.strategy_id, r.strategy_version_id, r.created_at, "
            "r.strategy_version_content_hash, r.start_date, r.end_date, r.price_field_used, "
            "r.engine_version, r.analysis_version, "
            "m.strategy_id AS metadata_strategy_id, "
            "m.strategy_version_id AS metadata_strategy_version_id, "
            "m.created_at AS metadata_created_at, "
            "m.strategy_version_content_hash AS metadata_strategy_version_content_hash, "
            "m.start_date AS metadata_start_date, m.end_date AS metadata_end_date, "
            "m.price_field_used AS metadata_price_field_used, "
            "m.engine_version AS metadata_engine_version, "
            "m.analysis_version AS metadata_analysis_version, "
            "m.initial_capital AS metadata_initial_capital, "
            "m.final_equity AS metadata_final_equity, m.experiment_id, m.candidate_id, "
            "m.candidate_index, m.metadata_projection_version, m.metadata_json, "
            + ", ".join(f"m.{name}_value AS {name}_value" for name in METADATA_METRIC_NAMES)
            + ", rm.lifecycle_status, rm.deleted_at, rm.is_protected, rm.run_type "
            + " FROM backtest_runs r"
        )

    @staticmethod
    def _metadata_filters(
        strategy_id: str | None,
        strategy_version_id: str | None,
        experiment_id: str | None,
        *,
        lifecycle_status: str,
        run_type: str | None,
        search: str | None,
        created_from: str | None,
        created_to: str | None,
        date_from: str | None,
        date_to: str | None,
    ) -> tuple[str, tuple[str, ...]]:
        clauses: list[str] = []
        params: list[str] = []
        for column, value in (
            ("r.strategy_id", strategy_id),
            ("r.strategy_version_id", strategy_version_id),
            ("m.experiment_id", experiment_id),
        ):
            if value is not None:
                clauses.append(f"{column} = ?")
                params.append(value)
        if lifecycle_status not in {"ACTIVE", "TRASHED", "ALL"}:
            raise BacktestPersistenceError("unsupported backtest lifecycle status")
        if lifecycle_status != "ALL":
            clauses.append("COALESCE(rm.lifecycle_status, 'ACTIVE') = ?")
            params.append(lifecycle_status)
        if run_type is not None:
            if run_type not in BacktestRepository.RUN_TYPES:
                raise BacktestPersistenceError("unsupported backtest run type")
            clauses.append("COALESCE(rm.run_type, 'MANUAL') = ?")
            params.append(run_type)
        if search:
            term = f"%{search.strip()}%"
            clauses.append(
                "(r.backtest_run_id LIKE ? OR r.strategy_id LIKE ? OR r.strategy_version_id LIKE ?)"
            )
            params.extend((term, term, term))
        for column, value, comparator in (
            ("r.created_at", created_from, ">="),
            ("r.created_at", created_to, "<="),
            ("r.start_date", date_from, ">="),
            ("r.end_date", date_to, "<="),
        ):
            if value:
                clauses.append(f"{column} {comparator} ?")
                params.append(value)
        return ("WHERE " + " AND ".join(clauses)) if clauses else "", tuple(params)

    @staticmethod
    def _metadata_sort(sort_by: str, order: str) -> str:
        if sort_by == "created_at":
            return f"r.created_at {order.upper()}, r.backtest_run_id {order.upper()}"
        column = f"m.{sort_by}_value"
        return (
            f"CASE WHEN {column} IS NULL THEN 1 ELSE 0 END ASC, "
            f"{column} {order.upper()}, r.created_at DESC, r.backtest_run_id DESC"
        )

    @classmethod
    def _metadata_from_row(cls, row: sqlite3.Row) -> BacktestRunMetadata:
        raw = row["metadata_json"]
        if raw is None:
            metrics = {
                name: {
                    "value": None,
                    "status": "not_evaluable",
                    "reason": "metadata projection is missing; run backfill_metadata explicitly",
                }
                for name in METADATA_METRIC_NAMES
            }
            return BacktestRunMetadata(
                backtest_run_id=str(row["backtest_run_id"]),
                strategy_id=str(row["strategy_id"]),
                strategy_version_id=str(row["strategy_version_id"]),
                created_at=str(row["created_at"]),
                strategy_version_content_hash=str(row["strategy_version_content_hash"]),
                start_date=str(row["start_date"]),
                end_date=str(row["end_date"]),
                price_field_used=str(row["price_field_used"]),
                engine_version=str(row["engine_version"]),
                analysis_version=str(row["analysis_version"]),
                initial_capital=0.0,
                final_equity=0.0,
                configuration_snapshot={},
                data_snapshot_reference={},
                provenance={},
                metrics=metrics,
                metric_values={name: None for name in METADATA_METRIC_NAMES},
                projection_status="missing",
                projection_version="missing",
                lifecycle_status=str(row["lifecycle_status"] or "ACTIVE"),
                deleted_at=row["deleted_at"],
                is_protected=bool(row["is_protected"]),
                run_type=str(row["run_type"] or "MANUAL"),
            )
        try:
            payload = json.loads(raw, parse_constant=_reject_nonfinite)
            if not isinstance(payload, dict):
                raise ValueError("metadata projection must be an object")
            for field in (
                "strategy_id",
                "strategy_version_id",
                "created_at",
                "strategy_version_content_hash",
                "start_date",
                "end_date",
                "price_field_used",
                "engine_version",
                "analysis_version",
            ):
                if row[field] != row[f"metadata_{field}"]:
                    raise ValueError("metadata projection identity does not match canonical row")
            metrics = payload.get("metrics")
            if not isinstance(metrics, dict) or set(metrics) != set(METADATA_METRIC_NAMES):
                raise ValueError("metadata projection metrics are invalid")
            for name in METADATA_METRIC_NAMES:
                metric = metrics[name]
                if not isinstance(metric, dict) or metric.get("status") not in {
                    "available",
                    "not_evaluable",
                }:
                    raise ValueError("metadata projection metric is invalid")
                projected = metric.get("value") if metric["status"] == "available" else None
                stored = row[f"{name}_value"]
                if projected != stored:
                    raise ValueError("metadata projection metric column does not match payload")
            metadata = BacktestRunMetadata(
                backtest_run_id=str(row["backtest_run_id"]),
                strategy_id=str(row["strategy_id"]),
                strategy_version_id=str(row["strategy_version_id"]),
                created_at=str(row["created_at"]),
                strategy_version_content_hash=str(row["strategy_version_content_hash"]),
                start_date=str(row["start_date"]),
                end_date=str(row["end_date"]),
                price_field_used=str(row["price_field_used"]),
                engine_version=str(row["engine_version"]),
                analysis_version=str(row["analysis_version"]),
                initial_capital=float(row["metadata_initial_capital"]),
                final_equity=float(row["metadata_final_equity"]),
                configuration_snapshot=payload.get("configuration_snapshot", {}),
                data_snapshot_reference=payload.get("data_snapshot_reference", {}),
                provenance=payload.get("provenance", {}),
                metrics=metrics,
                metric_values={name: row[f"{name}_value"] for name in METADATA_METRIC_NAMES},
                experiment_id=row["experiment_id"],
                candidate_id=row["candidate_id"],
                candidate_index=row["candidate_index"],
                projection_status=str(payload.get("projection_status", "available")),
                projection_version=str(
                    payload.get("projection_version", row["metadata_projection_version"])
                ),
                lifecycle_status=str(row["lifecycle_status"] or "ACTIVE"),
                deleted_at=row["deleted_at"],
                is_protected=bool(row["is_protected"]),
                run_type=str(row["run_type"] or "MANUAL"),
            )
            if metadata.projection_version != row["metadata_projection_version"]:
                raise ValueError("metadata projection version does not match payload")
            metadata.to_payload()
            return metadata
        except (KeyError, TypeError, ValueError, OverflowError, json.JSONDecodeError) as exc:
            raise BacktestPersistenceError(
                "stored backtest metadata failed integrity checks"
            ) from exc

    @staticmethod
    def _decode(raw: object) -> BacktestRun:
        if not isinstance(raw, str):
            raise BacktestPersistenceError("stored backtest run is invalid")
        try:
            payload = json.loads(raw, parse_constant=_reject_nonfinite)
            return BacktestRun.from_dict(payload)
        except (KeyError, TypeError, ValueError, OverflowError, json.JSONDecodeError) as exc:
            raise BacktestPersistenceError("stored backtest run failed integrity checks") from exc

    @classmethod
    def _record(cls, row: sqlite3.Row) -> BacktestRunRecord:
        try:
            return BacktestRunRecord(
                run=cls._decode(row["run_json"]),
                analysis_version=str(row["analysis_version"]),
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise BacktestPersistenceError("stored backtest run failed integrity checks") from exc


def _reject_nonfinite(value: str) -> None:
    raise ValueError(f"non-finite JSON constant {value}")


__all__ = [
    "BacktestMetadataBackfillReport",
    "BacktestMetadataPage",
    "BacktestPersistenceError",
    "BacktestRepository",
    "BacktestRunRecord",
]
