"""SQLite persistence for immutable PHASE 8C experiment artifacts.

This repository stores experiment definitions and their generated candidates only.
It does not execute experiments, evaluate results, rank candidates, or select a
strategy.  Each public write is a single transaction so an experiment cannot be
left without its bound parameter space or partially written candidate set.
"""

from __future__ import annotations

import json
import re
import sqlite3
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

from backend.app.experiment_result_repository import ExperimentResultRepository
from backend.app.research_protocol import ResearchProtocolRepository
from backend.app.strategy_repository import StrategyRepository
from research.candidates import ParameterCandidateSet
from research.canonical import canonical_json, sha256_hash
from research.enums import ExperimentStatus
from research.exceptions import (
    ExperimentResultConflictError,
    ExperimentResultPersistenceError,
    InvalidExperimentError,
)
from research.execution import candidate_id_for
from research.experiment_result import ExperimentResult
from research.experiments import Experiment, ParameterSet, ParameterSpace


class ExperimentPersistenceError(RuntimeError):
    """Raised when an experiment cannot be safely persisted or restored."""

    code = "EXPERIMENT_PERSISTENCE_ERROR"

    def __init__(self, message: str, *, code: str | None = None) -> None:
        super().__init__(message)
        if code is not None:
            self.code = code


@dataclass(frozen=True)
class ExperimentCandidateRecord:
    """One immutable candidate row in deterministic generation order."""

    experiment_id: str
    candidate_index: int
    parameter_set_hash: str
    candidate_set_hash: str
    parameter_set: ParameterSet
    candidate_status: str


@dataclass(frozen=True)
class ExperimentEventRecord:
    """One append-only experiment audit event."""

    event_id: str
    experiment_id: str
    event_type: str
    created_at: datetime
    payload: Mapping[str, Any]
    transition_key: str | None = None
    from_status: ExperimentStatus | None = None
    to_status: ExperimentStatus | None = None
    provenance: Mapping[str, Any] = field(default_factory=dict)


class ExperimentRepository:
    """Durable storage for PHASE 8C experiment definitions and candidates."""

    SCHEMA_VERSION = 2
    _SCHEMA_KEY = "experiment_repository"
    _SENSITIVE_KEY = re.compile(
        r"(?:api[_-]?key|access[_-]?token|auth(?:orization)?|credential|password|secret)",
        re.IGNORECASE,
    )

    def __init__(
        self,
        db_path: str | Path | None = None,
        protocol_repository: ResearchProtocolRepository | None = None,
        strategy_repository: StrategyRepository | None = None,
    ) -> None:
        self.db_path = (
            Path(db_path)
            if db_path is not None
            else Path(__file__).resolve().parents[2] / "data" / "strategy.db"
        )
        self._protocols = protocol_repository or ResearchProtocolRepository(self.db_path)
        self._strategies = strategy_repository or StrategyRepository(self.db_path)
        try:
            self.db_path.parent.mkdir(parents=True, exist_ok=True)
            self._initialize()
        except OSError as exc:
            raise ExperimentPersistenceError("could not prepare experiment database") from exc

    def _connect(self) -> sqlite3.Connection:
        try:
            connection = sqlite3.connect(self.db_path, timeout=30.0, isolation_level=None)
            connection.row_factory = sqlite3.Row
            connection.execute("PRAGMA foreign_keys = ON")
            connection.execute("PRAGMA busy_timeout = 30000")
            return connection
        except sqlite3.Error as exc:
            raise ExperimentPersistenceError("could not open experiment database") from exc

    def _initialize(self) -> None:
        connection = self._connect()
        try:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS schema_metadata (
                    schema_key TEXT PRIMARY KEY,
                    schema_version INTEGER NOT NULL
                );
                CREATE TABLE IF NOT EXISTS experiment_parameter_spaces (
                    parameter_space_hash TEXT PRIMARY KEY,
                    schema_version INTEGER NOT NULL,
                    canonical_json TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS experiment_parameter_sets (
                    parameter_set_hash TEXT PRIMARY KEY,
                    parameter_space_hash TEXT,
                    canonical_json TEXT NOT NULL,
                    FOREIGN KEY(parameter_space_hash)
                        REFERENCES experiment_parameter_spaces(parameter_space_hash)
                );
                CREATE TABLE IF NOT EXISTS experiments (
                    experiment_id TEXT PRIMARY KEY,
                    protocol_id TEXT NOT NULL,
                    strategy_definition_id TEXT NOT NULL,
                    base_strategy_version_id TEXT NOT NULL,
                    base_strategy_version_hash TEXT NOT NULL,
                    parameter_space_hash TEXT NOT NULL,
                    objective_spec_hash TEXT NOT NULL,
                    is_start_date TEXT NOT NULL,
                    is_end_date TEXT NOT NULL,
                    engine_version TEXT NOT NULL,
                    analysis_version TEXT NOT NULL,
                    status TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    content_hash TEXT NOT NULL,
                    experiment_json TEXT NOT NULL,
                    FOREIGN KEY(protocol_id) REFERENCES research_protocols(protocol_id),
                    FOREIGN KEY(parameter_space_hash)
                        REFERENCES experiment_parameter_spaces(parameter_space_hash)
                );
                CREATE TABLE IF NOT EXISTS experiment_candidate_sets (
                    experiment_id TEXT PRIMARY KEY,
                    parameter_space_hash TEXT NOT NULL,
                    candidate_set_hash TEXT NOT NULL,
                    theoretical_candidate_count INTEGER NOT NULL
                        CHECK(theoretical_candidate_count >= 0),
                    candidate_count INTEGER NOT NULL CHECK(candidate_count >= 0),
                    canonical_json TEXT NOT NULL,
                    FOREIGN KEY(experiment_id) REFERENCES experiments(experiment_id),
                    FOREIGN KEY(parameter_space_hash)
                        REFERENCES experiment_parameter_spaces(parameter_space_hash)
                );
                CREATE TABLE IF NOT EXISTS experiment_candidates (
                    experiment_id TEXT NOT NULL,
                    candidate_index INTEGER NOT NULL CHECK(candidate_index >= 0),
                    parameter_set_hash TEXT NOT NULL,
                    candidate_set_hash TEXT NOT NULL,
                    candidate_status TEXT NOT NULL,
                    PRIMARY KEY(experiment_id, candidate_index),
                    UNIQUE(experiment_id, parameter_set_hash),
                    FOREIGN KEY(experiment_id) REFERENCES experiments(experiment_id),
                    FOREIGN KEY(parameter_set_hash)
                        REFERENCES experiment_parameter_sets(parameter_set_hash)
                );
                CREATE TABLE IF NOT EXISTS experiment_events (
                    event_sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                    event_id TEXT NOT NULL UNIQUE,
                    experiment_id TEXT NOT NULL,
                    event_type TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    transition_key TEXT,
                    from_status TEXT,
                    to_status TEXT,
                    provenance_json TEXT NOT NULL DEFAULT '{}',
                    FOREIGN KEY(experiment_id) REFERENCES experiments(experiment_id)
                );
                CREATE INDEX IF NOT EXISTS idx_experiment_protocol
                    ON experiments(protocol_id, created_at ASC, experiment_id ASC);
                CREATE INDEX IF NOT EXISTS idx_experiment_events
                    ON experiment_events(experiment_id, event_sequence ASC);
                """
            )
            self._migrate_event_columns(connection)
            connection.execute(
                "CREATE UNIQUE INDEX IF NOT EXISTS uq_experiment_transition_key "
                "ON experiment_events(experiment_id, transition_key) "
                "WHERE transition_key IS NOT NULL"
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
            elif int(row["schema_version"]) == 1:
                connection.execute(
                    "UPDATE schema_metadata SET schema_version = ? WHERE schema_key = ?",
                    (self.SCHEMA_VERSION, self._SCHEMA_KEY),
                )
            elif int(row["schema_version"]) != self.SCHEMA_VERSION:
                raise ExperimentPersistenceError("unsupported experiment database schema version")
        except ExperimentPersistenceError:
            raise
        except sqlite3.Error as exc:
            raise ExperimentPersistenceError("could not initialize experiment database") from exc
        finally:
            connection.close()

    def create(
        self,
        experiment: Experiment,
        candidates: ParameterCandidateSet | None = None,
    ) -> Experiment:
        """Persist one experiment and, optionally, its complete candidate set atomically."""
        if not isinstance(experiment, Experiment):
            raise ExperimentPersistenceError("experiment is invalid")
        if candidates is not None and not isinstance(candidates, ParameterCandidateSet):
            raise ExperimentPersistenceError("candidate set is invalid")

        self._validate_external_bindings(experiment)
        experiment_payload = self._canonical_payload(experiment.to_dict())
        self._validate_experiment_integrity(experiment, experiment_payload)
        if candidates is not None:
            self._validate_candidate_set(experiment, candidates)

        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            self._insert_parameter_space(connection, experiment.parameter_space)
            if experiment.parameter_set is not None:
                self._insert_parameter_set(connection, experiment.parameter_set)
            connection.execute(
                """
                INSERT INTO experiments(
                    experiment_id, protocol_id, strategy_definition_id,
                    base_strategy_version_id, base_strategy_version_hash,
                    parameter_space_hash, objective_spec_hash, is_start_date,
                    is_end_date, engine_version, analysis_version, status,
                    created_at, content_hash, experiment_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    experiment.experiment_id,
                    experiment.protocol_id,
                    experiment.strategy_definition_id,
                    experiment.base_strategy_version_id,
                    experiment.base_strategy_version_hash,
                    experiment.parameter_space_hash,
                    experiment.objective_spec_hash,
                    experiment.is_start_date.isoformat(),
                    experiment.is_end_date.isoformat(),
                    experiment.engine_version,
                    experiment.analysis_version,
                    experiment.status.value,
                    experiment.created_at.isoformat(),
                    experiment.content_hash,
                    experiment_payload,
                ),
            )
            self._insert_event(
                connection,
                experiment.experiment_id,
                "EXPERIMENT_CREATED",
                {"content_hash": experiment.content_hash, "status": experiment.status.value},
                experiment.created_at,
            )
            if experiment.status is ExperimentStatus.SPACE_FROZEN:
                self._insert_event(
                    connection,
                    experiment.experiment_id,
                    "SPACE_FROZEN",
                    {"parameter_space_hash": experiment.parameter_space_hash},
                    experiment.created_at,
                )
            elif experiment.status is not ExperimentStatus.DRAFT:
                self._insert_event(
                    connection,
                    experiment.experiment_id,
                    "STATUS_CHANGED",
                    {"status": experiment.status.value},
                    experiment.created_at,
                )
            if candidates is not None:
                candidate_set_payload = self._candidate_set_payload(candidates)
                connection.execute(
                    """
                    INSERT INTO experiment_candidate_sets(
                        experiment_id, parameter_space_hash, candidate_set_hash,
                        theoretical_candidate_count, candidate_count, canonical_json
                    ) VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (
                        experiment.experiment_id,
                        candidates.parameter_space.content_hash,
                        candidates.candidate_set_hash,
                        candidates.theoretical_candidate_count,
                        candidates.candidate_count,
                        candidate_set_payload,
                    ),
                )
                for candidate_index, candidate in enumerate(candidates.candidates):
                    self._insert_candidate(
                        connection,
                        experiment.experiment_id,
                        candidate_index,
                        candidate,
                        candidates.candidate_set_hash,
                    )
                self._insert_event(
                    connection,
                    experiment.experiment_id,
                    "CANDIDATES_GENERATED",
                    {
                        "candidate_set_hash": candidates.candidate_set_hash,
                        "candidate_count": candidates.candidate_count,
                    },
                    experiment.created_at,
                )
            connection.execute("COMMIT")
            return experiment
        except ExperimentPersistenceError:
            self._rollback(connection)
            raise
        except sqlite3.IntegrityError as exc:
            self._rollback(connection)
            if "experiments.experiment_id" in str(
                exc
            ) or "UNIQUE constraint failed: experiments" in str(exc):
                raise ExperimentPersistenceError("experiment already exists") from exc
            raise ExperimentPersistenceError("could not persist experiment atomically") from exc
        except sqlite3.Error as exc:
            self._rollback(connection)
            raise ExperimentPersistenceError("could not persist experiment atomically") from exc
        except Exception as exc:
            self._rollback(connection)
            raise ExperimentPersistenceError("could not persist experiment atomically") from exc
        finally:
            connection.close()

    def save_parameter_space(self, parameter_space: ParameterSpace) -> ParameterSpace:
        """Persist an immutable parameter space independently of an experiment."""
        if not isinstance(parameter_space, ParameterSpace):
            raise ExperimentPersistenceError("parameter space is invalid")
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            self._insert_parameter_space(connection, parameter_space)
            connection.execute("COMMIT")
            return parameter_space
        except ExperimentPersistenceError:
            self._rollback(connection)
            raise
        except sqlite3.Error as exc:
            self._rollback(connection)
            raise ExperimentPersistenceError("could not persist parameter space") from exc
        finally:
            connection.close()

    def save_parameter_set(self, parameter_set: ParameterSet) -> ParameterSet:
        """Persist one immutable parameter set independently of a candidate."""
        if not isinstance(parameter_set, ParameterSet):
            raise ExperimentPersistenceError("parameter set is invalid")
        if parameter_set.parameter_space is not None:
            self.save_parameter_space(parameter_set.parameter_space)
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            self._insert_parameter_set(connection, parameter_set)
            connection.execute("COMMIT")
            return parameter_set
        except ExperimentPersistenceError:
            self._rollback(connection)
            raise
        except sqlite3.Error as exc:
            self._rollback(connection)
            raise ExperimentPersistenceError("could not persist parameter set") from exc
        finally:
            connection.close()

    def get(self, experiment_id: str) -> Experiment | None:
        """Return one experiment after checking all stored integrity bindings."""
        connection = self._connect()
        try:
            row = connection.execute(
                "SELECT * FROM experiments WHERE experiment_id = ?", (experiment_id,)
            ).fetchone()
            if row is None:
                return None
            experiment = self._decode_experiment(row)
            self._validate_external_bindings(experiment)
            self._validate_parameter_space_row(connection, experiment.parameter_space)
            return experiment
        except ExperimentPersistenceError:
            raise
        except (sqlite3.Error, TypeError, ValueError, OverflowError, json.JSONDecodeError) as exc:
            raise ExperimentPersistenceError("stored experiment failed integrity checks") from exc
        finally:
            connection.close()

    def transition_status(
        self,
        experiment_id: str,
        expected_status: ExperimentStatus | str,
        target_status: ExperimentStatus | str,
        event_type: str,
        transition_key: str,
        provenance: Mapping[str, Any] | None = None,
    ) -> Experiment:
        """Atomically persist one validated lifecycle transition and its event."""
        expected = self._coerce_status(expected_status, "expected_status")
        target = self._coerce_status(target_status, "target_status")
        if target is ExperimentStatus.COMPLETED:
            raise ExperimentPersistenceError(
                "completed experiments require an immutable result",
                code="EXPERIMENT_RESULT_REQUIRED",
            )
        event_name = self._validate_transition_text(event_type, "event_type")
        key = self._validate_transition_text(transition_key, "transition_key")
        provenance_payload = {} if provenance is None else dict(provenance)
        provenance_json = self._canonical_payload(provenance_payload)

        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT * FROM experiments WHERE experiment_id = ?", (experiment_id,)
            ).fetchone()
            if row is None:
                self._rollback(connection)
                raise ExperimentPersistenceError(
                    "experiment was not found", code="EXPERIMENT_NOT_FOUND"
                )

            experiment = self._decode_experiment(row)
            self._validate_external_bindings(experiment)
            self._validate_parameter_space_row(connection, experiment.parameter_space)
            existing = connection.execute(
                "SELECT * FROM experiment_events WHERE experiment_id = ? "
                "AND transition_key = ?",
                (experiment_id, key),
            ).fetchone()
            if existing is not None:
                event = self._decode_event(existing)
                if not self._event_matches_transition(
                    event,
                    expected=expected,
                    target=target,
                    event_type=event_name,
                    transition_key=key,
                    provenance_json=provenance_json,
                ):
                    self._rollback(connection)
                    raise ExperimentPersistenceError(
                        "transition key conflicts with an existing lifecycle event",
                        code="EXPERIMENT_TRANSITION_CONFLICT",
                    )
                if experiment.status is not target:
                    self._rollback(connection)
                    raise ExperimentPersistenceError(
                        "stored lifecycle event does not match experiment state",
                        code="EXPERIMENT_TRANSITION_INTEGRITY_ERROR",
                    )
                connection.execute("COMMIT")
                return experiment

            if experiment.status is not expected:
                self._rollback(connection)
                raise ExperimentPersistenceError(
                    "experiment status no longer matches expected status",
                    code="EXPERIMENT_STALE_STATE",
                )
            try:
                updated = experiment.with_status(target)
            except InvalidExperimentError as exc:
                self._rollback(connection)
                raise ExperimentPersistenceError(
                    "experiment lifecycle transition is invalid",
                    code="INVALID_EXPERIMENT_TRANSITION",
                ) from exc

            payload = self._canonical_payload(updated.to_dict())
            self._validate_experiment_integrity(updated, payload)
            self._update_experiment_row(
                connection,
                experiment_id,
                expected,
                updated,
                payload,
            )
            self._insert_event(
                connection,
                experiment_id,
                event_name,
                {
                    "from_status": expected.value,
                    "to_status": target.value,
                    "transition_key": key,
                },
                datetime.now(UTC),
                transition_key=key,
                from_status=expected,
                to_status=target,
                provenance=provenance_payload,
            )
            connection.execute("COMMIT")
            return updated
        except ExperimentPersistenceError:
            self._rollback(connection)
            raise
        except sqlite3.IntegrityError as exc:
            self._rollback(connection)
            raise ExperimentPersistenceError(
                "experiment lifecycle transition conflicts with persisted data",
                code="EXPERIMENT_TRANSITION_CONFLICT",
            ) from exc
        except (sqlite3.Error, TypeError, ValueError, OverflowError, json.JSONDecodeError) as exc:
            self._rollback(connection)
            raise ExperimentPersistenceError(
                "could not persist experiment lifecycle transition atomically",
                code="EXPERIMENT_TRANSITION_INTEGRITY_ERROR",
            ) from exc
        except Exception as exc:
            self._rollback(connection)
            raise ExperimentPersistenceError(
                "could not persist experiment lifecycle transition atomically",
                code="EXPERIMENT_TRANSITION_INTEGRITY_ERROR",
            ) from exc
        finally:
            connection.close()

    def finalize_result(
        self,
        result: ExperimentResult,
        expected_status: ExperimentStatus | str = ExperimentStatus.RUNNING,
        transition_key: str | None = None,
        provenance: Mapping[str, Any] | None = None,
        event_type: str = "EXPERIMENT_COMPLETED",
    ) -> ExperimentResult:
        """Persist one result and complete its experiment in one SQLite transaction.

        Backtest accounting remains owned by ``BacktestRepository``.  This method
        only atomically binds the already-created immutable result to the frozen
        experiment lifecycle and its append-only terminal event.
        """
        if not isinstance(result, ExperimentResult):
            raise ExperimentPersistenceError(
                "experiment result is invalid", code="EXPERIMENT_RESULT_CONFLICT"
            )
        expected = self._coerce_status(expected_status, "expected_status")
        if expected is not ExperimentStatus.RUNNING:
            raise ExperimentPersistenceError(
                "experiment result finalization requires running status",
                code="INVALID_EXPERIMENT_TRANSITION",
            )
        key = self._validate_transition_text(
            transition_key or f"experiment-result:{result.experiment_result_id}",
            "transition_key",
        )
        event_name = self._validate_transition_text(event_type, "event_type")
        supplied_provenance = {} if provenance is None else dict(provenance)
        reserved_provenance = {
            "result_id": result.experiment_result_id,
            "result_hash": result.result_hash,
            "candidate_id": result.candidate_id,
            "backtest_run_id": result.backtest_run_id,
        }
        for provenance_field, expected_value in reserved_provenance.items():
            if (
                provenance_field in supplied_provenance
                and supplied_provenance[provenance_field] != expected_value
            ):
                raise ExperimentPersistenceError(
                    f"result provenance field {provenance_field} does not match result identity",
                    code="EXPERIMENT_RESULT_CONFLICT",
                )
        provenance_payload = {
            **supplied_provenance,
            **reserved_provenance,
        }
        provenance_json = self._canonical_payload(provenance_payload)
        event_payload = {
            "result_id": result.experiment_result_id,
            "result_hash": result.result_hash,
            "candidate_id": result.candidate_id,
            "backtest_run_id": result.backtest_run_id,
        }
        event_payload_json = self._canonical_payload(event_payload)

        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            ExperimentResultRepository.ensure_schema(connection)
            row = connection.execute(
                "SELECT * FROM experiments WHERE experiment_id = ?",
                (result.experiment_id,),
            ).fetchone()
            if row is None:
                raise ExperimentPersistenceError(
                    "experiment was not found", code="EXPERIMENT_NOT_FOUND"
                )
            experiment = self._decode_experiment(row)
            self._validate_external_bindings(experiment)
            self._validate_parameter_space_row(connection, experiment.parameter_space)
            self._validate_result_binding(connection, experiment, result)

            existing_event_row = connection.execute(
                "SELECT * FROM experiment_events WHERE experiment_id = ? "
                "AND transition_key = ?",
                (result.experiment_id, key),
            ).fetchone()
            if existing_event_row is not None:
                event = self._decode_event(existing_event_row)
                if not self._event_matches_result(
                    event,
                    expected=expected,
                    event_type=event_name,
                    transition_key=key,
                    provenance_json=provenance_json,
                    payload_json=event_payload_json,
                ):
                    raise ExperimentPersistenceError(
                        "transition key conflicts with an existing result finalization",
                        code="EXPERIMENT_TRANSITION_CONFLICT",
                    )
                stored = ExperimentResultRepository.get_by_candidate_in_transaction(
                    connection, result.experiment_id, result.candidate_id
                )
                if stored is None or stored.hash_payload() != result.hash_payload():
                    raise ExperimentPersistenceError(
                        "terminal event has no matching immutable result",
                        code="EXPERIMENT_TRANSITION_INTEGRITY_ERROR",
                    )
                if experiment.status is not ExperimentStatus.COMPLETED:
                    raise ExperimentPersistenceError(
                        "terminal event does not match experiment state",
                        code="EXPERIMENT_TRANSITION_INTEGRITY_ERROR",
                    )
                connection.execute("COMMIT")
                return stored

            existing_result = ExperimentResultRepository.get_by_candidate_in_transaction(
                connection, result.experiment_id, result.candidate_id
            )
            if existing_result is not None:
                if existing_result.hash_payload() != result.hash_payload():
                    raise ExperimentResultConflictError(
                        "candidate already has a different experiment result"
                    )
                raise ExperimentPersistenceError(
                    "result exists without its terminal lifecycle event",
                    code="EXPERIMENT_TRANSITION_INTEGRITY_ERROR",
                )
            if experiment.status is not expected:
                raise ExperimentPersistenceError(
                    "experiment status no longer matches expected status",
                    code="EXPERIMENT_STALE_STATE",
                )
            try:
                updated = experiment.with_status(ExperimentStatus.COMPLETED)
            except InvalidExperimentError as exc:
                raise ExperimentPersistenceError(
                    "experiment lifecycle transition is invalid",
                    code="INVALID_EXPERIMENT_TRANSITION",
                ) from exc
            updated_payload = self._canonical_payload(updated.to_dict())
            self._validate_experiment_integrity(updated, updated_payload)
            ExperimentResultRepository.persist_in_transaction(connection, result)
            self._update_experiment_row(
                connection,
                result.experiment_id,
                expected,
                updated,
                updated_payload,
            )
            self._insert_event(
                connection,
                result.experiment_id,
                event_name,
                event_payload,
                result.created_at,
                transition_key=key,
                from_status=expected,
                to_status=ExperimentStatus.COMPLETED,
                provenance=provenance_payload,
            )
            connection.execute("COMMIT")
            return result
        except ExperimentPersistenceError:
            self._rollback(connection)
            raise
        except ExperimentResultConflictError as exc:
            self._rollback(connection)
            raise ExperimentPersistenceError(
                str(exc), code="EXPERIMENT_RESULT_CONFLICT"
            ) from exc
        except ExperimentResultPersistenceError as exc:
            self._rollback(connection)
            raise ExperimentPersistenceError(
                str(exc), code="EXPERIMENT_RESULT_PERSISTENCE_ERROR"
            ) from exc
        except sqlite3.IntegrityError as exc:
            self._rollback(connection)
            raise ExperimentPersistenceError(
                "experiment result finalization conflicts with persisted data",
                code="EXPERIMENT_RESULT_CONFLICT",
            ) from exc
        except (sqlite3.Error, TypeError, ValueError, OverflowError, json.JSONDecodeError) as exc:
            self._rollback(connection)
            raise ExperimentPersistenceError(
                "could not persist experiment result atomically",
                code="EXPERIMENT_TRANSITION_INTEGRITY_ERROR",
            ) from exc
        except Exception as exc:
            self._rollback(connection)
            raise ExperimentPersistenceError(
                "could not persist experiment result atomically",
                code="EXPERIMENT_TRANSITION_INTEGRITY_ERROR",
            ) from exc
        finally:
            connection.close()

    def _validate_result_binding(
        self,
        connection: sqlite3.Connection,
        experiment: Experiment,
        result: ExperimentResult,
    ) -> None:
        """Validate that a result belongs to this exact frozen experiment."""
        expected_configuration_hash = sha256_hash(
            experiment.backtest_configuration.snapshot({})
        )
        if any(
            (
                result.experiment_id != experiment.experiment_id,
                result.base_strategy_version_id != experiment.base_strategy_version_id,
                result.base_strategy_version_hash != experiment.base_strategy_version_hash,
                result.parameter_space_hash != experiment.parameter_space_hash,
                result.is_start != experiment.is_start_date,
                result.is_end != experiment.is_end_date,
                result.price_field_used is not experiment.backtest_configuration.price_field_used,
                result.backtest_configuration_hash != expected_configuration_hash,
                result.engine_version != experiment.engine_version,
                result.analysis_version != experiment.analysis_version,
            )
        ):
            raise ExperimentPersistenceError(
                "experiment result does not match frozen experiment",
                code="EXPERIMENT_RESULT_CONFLICT",
            )
        derived = self._strategies.get_any_version(result.derived_strategy_version_id)
        if derived is None or derived.content_hash != result.derived_strategy_version_hash:
            raise ExperimentPersistenceError(
                "derived strategy version is missing or has a hash mismatch",
                code="EXPERIMENT_RESULT_CONFLICT",
            )
        candidate_row = connection.execute(
            "SELECT * FROM experiment_candidates WHERE experiment_id = ? "
            "AND candidate_index = ?",
            (experiment.experiment_id, result.candidate_index),
        ).fetchone()
        if candidate_row is None:
            raise ExperimentPersistenceError(
                "experiment result candidate is missing",
                code="EXPERIMENT_RESULT_CONFLICT",
            )
        expected_candidate_id = candidate_id_for(
            experiment.experiment_id,
            result.candidate_index,
            result.parameter_set_hash,
        )
        if any(
            (
                result.candidate_id != expected_candidate_id,
                candidate_row["parameter_set_hash"] != result.parameter_set_hash,
                candidate_row["candidate_set_hash"] != result.candidate_set_hash,
            )
        ):
            raise ExperimentPersistenceError(
                "experiment result candidate binding is invalid",
                code="EXPERIMENT_RESULT_CONFLICT",
            )
        candidate_set = connection.execute(
            "SELECT candidate_set_hash, parameter_space_hash FROM experiment_candidate_sets "
            "WHERE experiment_id = ?",
            (experiment.experiment_id,),
        ).fetchone()
        if candidate_set is None or any(
            (
                candidate_set["candidate_set_hash"] != result.candidate_set_hash,
                candidate_set["parameter_space_hash"] != result.parameter_space_hash,
            )
        ):
            raise ExperimentPersistenceError(
                "experiment result candidate set binding is invalid",
                code="EXPERIMENT_RESULT_CONFLICT",
            )

    def list(self, protocol_id: str | None = None) -> tuple[Experiment, ...]:
        """Return immutable experiments in explicit creation order."""
        connection = self._connect()
        try:
            if protocol_id is None:
                rows = connection.execute(
                    "SELECT * FROM experiments ORDER BY created_at ASC, experiment_id ASC"
                ).fetchall()
            else:
                rows = connection.execute(
                    "SELECT * FROM experiments WHERE protocol_id = ? "
                    "ORDER BY created_at ASC, experiment_id ASC",
                    (protocol_id,),
                ).fetchall()
            experiments = tuple(self._decode_experiment(row) for row in rows)
            for experiment in experiments:
                self._validate_external_bindings(experiment)
                self._validate_parameter_space_row(connection, experiment.parameter_space)
            return experiments
        except ExperimentPersistenceError:
            raise
        except (sqlite3.Error, TypeError, ValueError, OverflowError, json.JSONDecodeError) as exc:
            raise ExperimentPersistenceError("stored experiments failed integrity checks") from exc
        finally:
            connection.close()

    def get_parameter_space(self, parameter_space_hash: str) -> ParameterSpace | None:
        connection = self._connect()
        try:
            row = connection.execute(
                "SELECT * FROM experiment_parameter_spaces WHERE parameter_space_hash = ?",
                (parameter_space_hash,),
            ).fetchone()
            if row is None:
                return None
            return self._decode_parameter_space(row)
        except ExperimentPersistenceError:
            raise
        except (sqlite3.Error, TypeError, ValueError, OverflowError, json.JSONDecodeError) as exc:
            raise ExperimentPersistenceError(
                "stored parameter space failed integrity checks"
            ) from exc
        finally:
            connection.close()

    def get_parameter_set(
        self,
        parameter_set_hash: str,
        parameter_space: ParameterSpace | None = None,
    ) -> ParameterSet | None:
        connection = self._connect()
        try:
            row = connection.execute(
                "SELECT * FROM experiment_parameter_sets WHERE parameter_set_hash = ?",
                (parameter_set_hash,),
            ).fetchone()
            if row is None:
                return None
            if parameter_space is None and row["parameter_space_hash"] is not None:
                parameter_space = self._decode_parameter_space(
                    connection.execute(
                        "SELECT * FROM experiment_parameter_spaces WHERE parameter_space_hash = ?",
                        (row["parameter_space_hash"],),
                    ).fetchone()
                )
            parameter_set = self._decode_parameter_set(row, parameter_space)
            if parameter_set.content_hash != parameter_set_hash:
                raise ExperimentPersistenceError("stored parameter set hash mismatch")
            return parameter_set
        except ExperimentPersistenceError:
            raise
        except (sqlite3.Error, TypeError, ValueError, OverflowError, json.JSONDecodeError) as exc:
            raise ExperimentPersistenceError(
                "stored parameter set failed integrity checks"
            ) from exc
        finally:
            connection.close()

    def get_candidate_set(self, experiment_id: str) -> ParameterCandidateSet | None:
        """Rebuild one candidate set with an explicit candidate-index ordering."""
        connection = self._connect()
        try:
            experiment_row = connection.execute(
                "SELECT * FROM experiments WHERE experiment_id = ?", (experiment_id,)
            ).fetchone()
            if experiment_row is None:
                return None
            experiment = self._decode_experiment(experiment_row)
            metadata = connection.execute(
                "SELECT * FROM experiment_candidate_sets WHERE experiment_id = ?",
                (experiment_id,),
            ).fetchone()
            if metadata is None:
                return None
            rows = connection.execute(
                "SELECT * FROM experiment_candidates WHERE experiment_id = ? "
                "ORDER BY candidate_index ASC",
                (experiment_id,),
            ).fetchall()
            metadata_payload = json.loads(
                metadata["canonical_json"], parse_constant=_reject_nonfinite
            )
            if metadata["canonical_json"] != self._canonical_payload(metadata_payload):
                raise ExperimentPersistenceError("stored candidate set is not canonical JSON")
            if not isinstance(metadata_payload, Mapping):
                raise ExperimentPersistenceError("stored candidate set payload is invalid")
            if metadata_payload.get("candidate_set_hash") != metadata["candidate_set_hash"]:
                raise ExperimentPersistenceError("stored candidate set metadata hash mismatch")
            if metadata["parameter_space_hash"] != experiment.parameter_space_hash:
                raise ExperimentPersistenceError("stored candidate set parameter space mismatch")
            if metadata_payload.get("candidate_count") != int(metadata["candidate_count"]):
                raise ExperimentPersistenceError("stored candidate set metadata count mismatch")
            if metadata_payload.get("parameter_space_hash") != experiment.parameter_space_hash:
                raise ExperimentPersistenceError("stored candidate set parameter space mismatch")
            if tuple(int(row["candidate_index"]) for row in rows) != tuple(range(len(rows))):
                raise ExperimentPersistenceError("stored candidate indexes are not contiguous")
            candidates = tuple(
                self._decode_candidate(connection, row, experiment.parameter_space) for row in rows
            )
            if len(candidates) != int(metadata["candidate_count"]):
                raise ExperimentPersistenceError("stored candidate count mismatch")
            candidate_set = ParameterCandidateSet(
                parameter_space=experiment.parameter_space,
                theoretical_candidate_count=int(metadata["theoretical_candidate_count"]),
                candidates=tuple(item.parameter_set for item in candidates),
            )
            if candidate_set.candidate_set_hash != metadata["candidate_set_hash"]:
                raise ExperimentPersistenceError("stored candidate set hash mismatch")
            if any(
                item.candidate_set_hash != candidate_set.candidate_set_hash for item in candidates
            ):
                raise ExperimentPersistenceError("stored candidate binding mismatch")
            if metadata["canonical_json"] != self._candidate_set_payload(candidate_set):
                raise ExperimentPersistenceError("stored candidate set content mismatch")
            return candidate_set
        except ExperimentPersistenceError:
            raise
        except (sqlite3.Error, TypeError, ValueError, OverflowError, json.JSONDecodeError) as exc:
            raise ExperimentPersistenceError(
                "stored candidate set failed integrity checks"
            ) from exc
        finally:
            connection.close()

    def list_candidates(self, experiment_id: str) -> tuple[ExperimentCandidateRecord, ...]:
        """Return candidates ordered by their persisted generation index."""
        connection = self._connect()
        try:
            experiment_row = connection.execute(
                "SELECT * FROM experiments WHERE experiment_id = ?", (experiment_id,)
            ).fetchone()
            if experiment_row is None:
                return ()
            experiment = self._decode_experiment(experiment_row)
            rows = connection.execute(
                "SELECT * FROM experiment_candidates WHERE experiment_id = ? "
                "ORDER BY candidate_index ASC",
                (experiment_id,),
            ).fetchall()
            if any(int(row["candidate_index"]) != index for index, row in enumerate(rows)):
                raise ExperimentPersistenceError("stored candidate indexes are not contiguous")
            return tuple(
                self._decode_candidate(connection, row, experiment.parameter_space) for row in rows
            )
        except ExperimentPersistenceError:
            raise
        except (sqlite3.Error, TypeError, ValueError, OverflowError, json.JSONDecodeError) as exc:
            raise ExperimentPersistenceError("stored candidates failed integrity checks") from exc
        finally:
            connection.close()

    def list_events(self, experiment_id: str) -> tuple[ExperimentEventRecord, ...]:
        connection = self._connect()
        try:
            rows = connection.execute(
                "SELECT * FROM experiment_events WHERE experiment_id = ? "
                "ORDER BY event_sequence ASC",
                (experiment_id,),
            ).fetchall()
            return tuple(self._decode_event(row) for row in rows)
        except ExperimentPersistenceError:
            raise
        except (sqlite3.Error, TypeError, ValueError, OverflowError, json.JSONDecodeError) as exc:
            raise ExperimentPersistenceError(
                "stored experiment events failed integrity checks"
            ) from exc
        finally:
            connection.close()

    def clear(self) -> None:
        """Remove experiment records for isolated tests without touching shared repositories."""
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute("DELETE FROM experiment_events")
            connection.execute("DELETE FROM experiment_candidates")
            connection.execute("DELETE FROM experiment_candidate_sets")
            connection.execute("DELETE FROM experiments")
            connection.execute("DELETE FROM experiment_parameter_sets")
            connection.execute("DELETE FROM experiment_parameter_spaces")
            connection.execute(
                "DELETE FROM schema_metadata WHERE schema_key = ?", (self._SCHEMA_KEY,)
            )
            connection.execute("COMMIT")
            self._initialize()
        except sqlite3.Error as exc:
            self._rollback(connection)
            raise ExperimentPersistenceError("could not clear experiment database") from exc
        finally:
            connection.close()

    def _validate_external_bindings(self, experiment: Experiment) -> None:
        try:
            protocol = self._protocols.get_protocol(experiment.protocol_id)
        except Exception as exc:
            raise ExperimentPersistenceError("could not verify research protocol binding") from exc
        if protocol is None:
            raise ExperimentPersistenceError("RESEARCH_PROTOCOL_NOT_FOUND")
        try:
            version = self._strategies.get(
                experiment.strategy_definition_id, experiment.base_strategy_version_id
            )
        except Exception as exc:
            raise ExperimentPersistenceError("could not verify strategy version binding") from exc
        if version is None:
            raise ExperimentPersistenceError("STRATEGY_VERSION_NOT_FOUND")
        if version.content_hash != experiment.base_strategy_version_hash:
            raise ExperimentPersistenceError("STRATEGY_VERSION_HASH_MISMATCH")

    def _validate_experiment_integrity(self, experiment: Experiment, payload: str) -> None:
        if sha256_hash(json.loads(payload)) != experiment.content_hash:
            raise ExperimentPersistenceError("experiment content hash mismatch")
        if experiment.parameter_space_hash != experiment.parameter_space.content_hash:
            raise ExperimentPersistenceError("parameter space hash mismatch")
        if experiment.objective_spec_hash != experiment.objective_specification.content_hash:
            raise ExperimentPersistenceError("objective specification hash mismatch")

    def _validate_candidate_set(
        self, experiment: Experiment, candidates: ParameterCandidateSet
    ) -> None:
        if candidates.parameter_space != experiment.parameter_space:
            raise ExperimentPersistenceError("candidate set parameter space mismatch")
        if candidates.candidate_count != len(candidates.candidates):
            raise ExperimentPersistenceError("candidate count mismatch")
        rebuilt = ParameterCandidateSet(
            parameter_space=candidates.parameter_space,
            theoretical_candidate_count=candidates.theoretical_candidate_count,
            candidates=tuple(candidates.candidates),
        )
        if rebuilt.candidate_set_hash != candidates.candidate_set_hash:
            raise ExperimentPersistenceError("candidate set hash mismatch")

    @classmethod
    def _canonical_payload(cls, payload: object) -> str:
        cls._assert_safe_payload(payload)
        try:
            return canonical_json(payload)
        except (TypeError, ValueError, OverflowError) as exc:
            raise ExperimentPersistenceError("payload is not canonical JSON") from exc

    @classmethod
    def _assert_safe_payload(cls, payload: object, key_path: str = "payload") -> None:
        if isinstance(payload, Mapping):
            for key, value in payload.items():
                key_text = str(key)
                if cls._SENSITIVE_KEY.search(key_text):
                    raise ExperimentPersistenceError("sensitive provenance fields are not allowed")
                cls._assert_safe_payload(value, f"{key_path}.{key_text}")
        elif isinstance(payload, (list, tuple)):
            for index, value in enumerate(payload):
                cls._assert_safe_payload(value, f"{key_path}[{index}]")
        elif isinstance(payload, str) and re.search(
            r"(?:TIINGO_API_KEY|API_KEY|BEGIN\s+PRIVATE\s+KEY)", payload, re.IGNORECASE
        ):
            raise ExperimentPersistenceError("sensitive provenance values are not allowed")

    def _insert_parameter_space(
        self, connection: sqlite3.Connection, parameter_space: ParameterSpace
    ) -> None:
        payload = self._canonical_payload(parameter_space.to_dict())
        row = connection.execute(
            "SELECT canonical_json FROM experiment_parameter_spaces WHERE parameter_space_hash = ?",
            (parameter_space.content_hash,),
        ).fetchone()
        if row is not None:
            if row["canonical_json"] != payload:
                raise ExperimentPersistenceError("parameter space hash collision or corruption")
            return
        connection.execute(
            "INSERT INTO experiment_parameter_spaces VALUES (?, ?, ?)",
            (parameter_space.content_hash, self.SCHEMA_VERSION, payload),
        )

    def _insert_parameter_set(
        self, connection: sqlite3.Connection, parameter_set: ParameterSet
    ) -> None:
        payload = self._canonical_payload(parameter_set.to_dict())
        parameter_space_hash = (
            parameter_set.parameter_space.content_hash
            if parameter_set.parameter_space is not None
            else None
        )
        row = connection.execute(
            "SELECT canonical_json, parameter_space_hash FROM experiment_parameter_sets "
            "WHERE parameter_set_hash = ?",
            (parameter_set.content_hash,),
        ).fetchone()
        if row is not None:
            if (
                row["canonical_json"] != payload
                or row["parameter_space_hash"] != parameter_space_hash
            ):
                raise ExperimentPersistenceError("parameter set hash collision or corruption")
            return
        connection.execute(
            "INSERT INTO experiment_parameter_sets VALUES (?, ?, ?)",
            (parameter_set.content_hash, parameter_space_hash, payload),
        )

    def _insert_candidate(
        self,
        connection: sqlite3.Connection,
        experiment_id: str,
        candidate_index: int,
        parameter_set: ParameterSet,
        candidate_set_hash: str,
    ) -> None:
        self._insert_parameter_set(connection, parameter_set)
        connection.execute(
            "INSERT INTO experiment_candidates VALUES (?, ?, ?, ?, ?)",
            (
                experiment_id,
                candidate_index,
                parameter_set.content_hash,
                candidate_set_hash,
                "pending",
            ),
        )

    def _insert_event(
        self,
        connection: sqlite3.Connection,
        experiment_id: str,
        event_type: str,
        payload: Mapping[str, Any],
        created_at: datetime,
        *,
        transition_key: str | None = None,
        from_status: ExperimentStatus | None = None,
        to_status: ExperimentStatus | None = None,
        provenance: Mapping[str, Any] | None = None,
    ) -> None:
        connection.execute(
            """
            INSERT INTO experiment_events(
                event_id, experiment_id, event_type, created_at, payload_json,
                transition_key, from_status, to_status, provenance_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                uuid4().hex,
                experiment_id,
                event_type,
                created_at.isoformat(),
                self._canonical_payload(payload),
                transition_key,
                from_status.value if from_status is not None else None,
                to_status.value if to_status is not None else None,
                self._canonical_payload({} if provenance is None else provenance),
            ),
        )

    @staticmethod
    def _update_experiment_row(
        connection: sqlite3.Connection,
        experiment_id: str,
        expected_status: ExperimentStatus,
        updated: Experiment,
        payload: str,
    ) -> None:
        cursor = connection.execute(
            "UPDATE experiments SET status = ?, content_hash = ?, experiment_json = ? "
            "WHERE experiment_id = ? AND status = ?",
            (
                updated.status.value,
                updated.content_hash,
                payload,
                experiment_id,
                expected_status.value,
            ),
        )
        if cursor.rowcount != 1:
            raise ExperimentPersistenceError(
                "experiment status changed during transition",
                code="EXPERIMENT_STALE_STATE",
            )

    @classmethod
    def _candidate_set_payload(cls, candidates: ParameterCandidateSet) -> str:
        return cls._canonical_payload(
            {
                **candidates.to_dict(),
                "candidate_set_hash": candidates.candidate_set_hash,
            }
        )

    @classmethod
    def _decode_experiment(cls, row: sqlite3.Row) -> Experiment:
        raw = row["experiment_json"]
        payload = (
            json.loads(raw, parse_constant=_reject_nonfinite) if isinstance(raw, str) else None
        )
        if not isinstance(raw, str) or raw != cls._canonical_payload(payload):
            raise ExperimentPersistenceError("stored experiment is not canonical JSON")
        try:
            experiment = Experiment.from_dict(payload)
        except (KeyError, TypeError, ValueError, OverflowError) as exc:
            raise ExperimentPersistenceError("stored experiment failed integrity checks") from exc
        if experiment.content_hash != row["content_hash"]:
            raise ExperimentPersistenceError("stored experiment hash mismatch")
        expected = {
            "experiment_id": experiment.experiment_id,
            "protocol_id": experiment.protocol_id,
            "strategy_definition_id": experiment.strategy_definition_id,
            "base_strategy_version_id": experiment.base_strategy_version_id,
            "base_strategy_version_hash": experiment.base_strategy_version_hash,
            "parameter_space_hash": experiment.parameter_space_hash,
            "objective_spec_hash": experiment.objective_spec_hash,
            "is_start_date": experiment.is_start_date.isoformat(),
            "is_end_date": experiment.is_end_date.isoformat(),
            "engine_version": experiment.engine_version,
            "analysis_version": experiment.analysis_version,
            "status": experiment.status.value,
            "created_at": experiment.created_at.isoformat(),
        }
        if any(row[key] != value for key, value in expected.items()):
            raise ExperimentPersistenceError("stored experiment columns do not match payload")
        return experiment

    @classmethod
    def _decode_parameter_space(cls, row: sqlite3.Row | None) -> ParameterSpace:
        if row is None:
            raise ExperimentPersistenceError("stored parameter space is missing")
        if int(row["schema_version"]) != cls.SCHEMA_VERSION:
            raise ExperimentPersistenceError("stored parameter space schema version is unsupported")
        raw = row["canonical_json"]
        payload = json.loads(raw, parse_constant=_reject_nonfinite)
        if raw != cls._canonical_payload(payload):
            raise ExperimentPersistenceError("stored parameter space is not canonical JSON")
        parameter_space = ParameterSpace.from_dict(payload)
        if parameter_space.content_hash != row["parameter_space_hash"]:
            raise ExperimentPersistenceError("stored parameter space hash mismatch")
        return parameter_space

    @classmethod
    def _decode_parameter_set(
        cls, row: sqlite3.Row, parameter_space: ParameterSpace | None
    ) -> ParameterSet:
        if (
            parameter_space is not None
            and row["parameter_space_hash"] != parameter_space.content_hash
        ):
            raise ExperimentPersistenceError("stored parameter set parameter space mismatch")
        raw = row["canonical_json"]
        payload = json.loads(raw, parse_constant=_reject_nonfinite)
        if raw != cls._canonical_payload(payload):
            raise ExperimentPersistenceError("stored parameter set is not canonical JSON")
        parameter_set = ParameterSet.from_dict(payload, parameter_space)
        if parameter_set.content_hash != row["parameter_set_hash"]:
            raise ExperimentPersistenceError("stored parameter set hash mismatch")
        return parameter_set

    def _decode_candidate(
        self,
        connection: sqlite3.Connection,
        row: sqlite3.Row,
        parameter_space: ParameterSpace,
    ) -> ExperimentCandidateRecord:
        parameter_row = connection.execute(
            "SELECT * FROM experiment_parameter_sets WHERE parameter_set_hash = ?",
            (row["parameter_set_hash"],),
        ).fetchone()
        if parameter_row is None:
            raise ExperimentPersistenceError("candidate parameter set is missing")
        parameter_set = self._decode_parameter_set(parameter_row, parameter_space)
        if parameter_set.content_hash != row["parameter_set_hash"]:
            raise ExperimentPersistenceError("candidate parameter binding mismatch")
        return ExperimentCandidateRecord(
            experiment_id=str(row["experiment_id"]),
            candidate_index=int(row["candidate_index"]),
            parameter_set_hash=str(row["parameter_set_hash"]),
            candidate_set_hash=str(row["candidate_set_hash"]),
            parameter_set=parameter_set,
            candidate_status=str(row["candidate_status"]),
        )

    @classmethod
    def _decode_event(cls, row: sqlite3.Row) -> ExperimentEventRecord:
        payload = json.loads(row["payload_json"], parse_constant=_reject_nonfinite)
        if row["payload_json"] != cls._canonical_payload(payload):
            raise ExperimentPersistenceError("stored experiment event is not canonical JSON")
        if not isinstance(payload, Mapping):
            raise ExperimentPersistenceError("stored experiment event payload is invalid")
        provenance_raw = row["provenance_json"] if "provenance_json" in row.keys() else "{}"
        provenance = json.loads(provenance_raw, parse_constant=_reject_nonfinite)
        if provenance_raw != cls._canonical_payload(provenance) or not isinstance(
            provenance, Mapping
        ):
            raise ExperimentPersistenceError("stored experiment event provenance is invalid")
        from_status = cls._decode_optional_status(row["from_status"])
        to_status = cls._decode_optional_status(row["to_status"])
        return ExperimentEventRecord(
            event_id=str(row["event_id"]),
            experiment_id=str(row["experiment_id"]),
            event_type=str(row["event_type"]),
            created_at=datetime.fromisoformat(str(row["created_at"])),
            payload=dict(payload),
            transition_key=(str(row["transition_key"]) if row["transition_key"] else None),
            from_status=from_status,
            to_status=to_status,
            provenance=dict(provenance),
        )

    @staticmethod
    def _coerce_status(value: ExperimentStatus | str, label: str) -> ExperimentStatus:
        try:
            return value if isinstance(value, ExperimentStatus) else ExperimentStatus(value)
        except (TypeError, ValueError) as exc:
            raise ExperimentPersistenceError(
                f"{label} is invalid", code="INVALID_EXPERIMENT_TRANSITION"
            ) from exc

    @staticmethod
    def _validate_transition_text(value: str, label: str) -> str:
        if not isinstance(value, str) or not value.strip():
            raise ExperimentPersistenceError(
                f"{label} must be non-empty", code="EXPERIMENT_TRANSITION_INTEGRITY_ERROR"
            )
        return value.strip()

    @staticmethod
    def _decode_optional_status(value: object) -> ExperimentStatus | None:
        if value is None:
            return None
        try:
            return ExperimentStatus(str(value))
        except ValueError as exc:
            raise ExperimentPersistenceError(
                "stored experiment event status is invalid",
                code="EXPERIMENT_TRANSITION_INTEGRITY_ERROR",
            ) from exc

    @staticmethod
    def _event_matches_transition(
        event: ExperimentEventRecord,
        *,
        expected: ExperimentStatus,
        target: ExperimentStatus,
        event_type: str,
        transition_key: str,
        provenance_json: str,
    ) -> bool:
        return (
            event.transition_key == transition_key
            and event.from_status == expected
            and event.to_status == target
            and event.event_type == event_type
            and canonical_json(event.provenance) == provenance_json
        )

    @staticmethod
    def _event_matches_result(
        event: ExperimentEventRecord,
        *,
        expected: ExperimentStatus,
        event_type: str,
        transition_key: str,
        provenance_json: str,
        payload_json: str,
    ) -> bool:
        return (
            event.transition_key == transition_key
            and event.from_status == expected
            and event.to_status is ExperimentStatus.COMPLETED
            and event.event_type == event_type
            and canonical_json(event.provenance) == provenance_json
            and canonical_json(event.payload) == payload_json
        )

    @staticmethod
    def _migrate_event_columns(connection: sqlite3.Connection) -> None:
        columns = {
            str(row["name"])
            for row in connection.execute("PRAGMA table_info(experiment_events)").fetchall()
        }
        additions = (
            ("transition_key", "TEXT"),
            ("from_status", "TEXT"),
            ("to_status", "TEXT"),
            ("provenance_json", "TEXT NOT NULL DEFAULT '{}'"),
        )
        for name, definition in additions:
            if name not in columns:
                connection.execute(
                    f"ALTER TABLE experiment_events ADD COLUMN {name} {definition}"
                )

    def _validate_parameter_space_row(
        self, connection: sqlite3.Connection, parameter_space: ParameterSpace
    ) -> None:
        self._decode_parameter_space(
            connection.execute(
                "SELECT * FROM experiment_parameter_spaces WHERE parameter_space_hash = ?",
                (parameter_space.content_hash,),
            ).fetchone()
        )

    @staticmethod
    def _rollback(connection: sqlite3.Connection) -> None:
        try:
            connection.execute("ROLLBACK")
        except sqlite3.Error:
            pass


def _reject_nonfinite(value: str) -> None:
    raise ValueError(f"non-finite JSON constant {value}")


__all__ = [
    "ExperimentCandidateRecord",
    "ExperimentEventRecord",
    "ExperimentPersistenceError",
    "ExperimentRepository",
]
