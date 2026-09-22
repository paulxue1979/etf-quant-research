"""SQLite persistence for immutable backtest research artifacts."""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
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


class BacktestRepository:
    """Durable repository that never updates or overwrites a completed run."""

    SCHEMA_VERSION = 1
    METADATA_TABLE = "backtest_run_metadata"

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
    ) -> BacktestMetadataPage:
        """List only bounded metadata columns; never selects or decodes run_json."""
        if sort_by != "created_at" and sort_by not in METADATA_METRIC_NAMES:
            raise BacktestPersistenceError("unsupported backtest metadata sort field")
        if order not in {"asc", "desc"}:
            raise BacktestPersistenceError("unsupported backtest metadata sort order")
        if not 1 <= limit <= 100 or offset < 0:
            raise BacktestPersistenceError("invalid backtest metadata page")
        where, params = self._metadata_filters(strategy_id, strategy_version_id, experiment_id)
        sort_sql = self._metadata_sort(sort_by, order)
        connection = self._connect()
        try:
            total = int(
                connection.execute(
                    f"SELECT COUNT(*) FROM backtest_runs r LEFT JOIN backtest_run_metadata m "
                    f"ON m.backtest_run_id = r.backtest_run_id {where}",
                    params,
                ).fetchone()[0]
            )
            rows = connection.execute(
                f"{self._metadata_select()} LEFT JOIN backtest_run_metadata m "
                f"ON m.backtest_run_id = r.backtest_run_id {where} "
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
            + " FROM backtest_runs r"
        )

    @staticmethod
    def _metadata_filters(
        strategy_id: str | None, strategy_version_id: str | None, experiment_id: str | None
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
