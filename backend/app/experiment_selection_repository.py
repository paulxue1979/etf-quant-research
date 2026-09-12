"""Atomic persistence for one immutable researcher selection per experiment.

This repository stores the PHASE 8E-1D experiment-level selection record.  It
is deliberately separate from the PHASE 7 protocol selection repository: a
selection here records a researcher choice from an experiment's IS results,
but does not hand the protocol to OOS or create a strategy freeze.
"""

from __future__ import annotations

import json
import re
import sqlite3
from collections.abc import Mapping
from pathlib import Path

from backend.app.backtest_repository import BacktestRepository
from backend.app.candidate_execution_repository import CandidateExecutionRepository
from backend.app.experiment_repository import ExperimentPersistenceError, ExperimentRepository
from backend.app.experiment_result_repository import ExperimentResultRepository
from backend.app.experiment_results_read_service import (
    ExperimentResultsReadModelError,
    ExperimentResultsReadService,
)
from backend.app.strategy_repository import StrategyRepository
from research.canonical import canonical_json, sha256_hash
from research.enums import ExperimentStatus
from research.exceptions import (
    ExperimentSelectionConflictError,
    ExperimentSelectionPersistenceError,
)
from research.execution import CandidateExecutionStatus, candidate_id_for
from research.experiment_selection import (
    ExperimentSelectionDecision,
    ExperimentSelectionEvidence,
)
from research.objective_evaluation import evaluate_candidate_objective


class ExperimentSelectionRepository:
    """Durable, append-only storage for an experiment's official selection."""

    SCHEMA_VERSION = 1
    _SCHEMA_KEY = "experiment_selection_repository"
    _SENSITIVE_KEY = re.compile(
        r"(?:api[_-]?key|access[_-]?token|authorization|credential|password|secret)",
        re.IGNORECASE,
    )

    def __init__(
        self,
        db_path: str | Path | None = None,
        *,
        experiment_repository: ExperimentRepository | None = None,
        result_repository: ExperimentResultRepository | None = None,
        execution_repository: CandidateExecutionRepository | None = None,
        backtest_repository: BacktestRepository | None = None,
        strategy_repository: StrategyRepository | None = None,
        results_read_service: ExperimentResultsReadService | None = None,
    ) -> None:
        self.db_path = (
            Path(db_path)
            if db_path is not None
            else Path(__file__).resolve().parents[2] / "data" / "strategy.db"
        )
        self._experiments = experiment_repository or ExperimentRepository(self.db_path)
        self._results = result_repository or ExperimentResultRepository(self.db_path)
        self._executions = execution_repository or CandidateExecutionRepository(self.db_path)
        self._backtests = backtest_repository or BacktestRepository(self.db_path)
        self._strategies = strategy_repository or StrategyRepository(self.db_path)
        self._read_service = results_read_service or ExperimentResultsReadService(
            experiment_repository=self._experiments,
            candidate_execution_repository=self._executions,
            experiment_result_repository=self._results,
            backtest_repository=self._backtests,
            strategy_repository=self._strategies,
        )
        try:
            self.db_path.parent.mkdir(parents=True, exist_ok=True)
            self._initialize()
        except OSError as exc:
            raise ExperimentSelectionPersistenceError(
                "could not prepare experiment selection database"
            ) from exc

    def _connect(self) -> sqlite3.Connection:
        try:
            connection = sqlite3.connect(self.db_path, timeout=30.0, isolation_level=None)
            connection.row_factory = sqlite3.Row
            connection.execute("PRAGMA foreign_keys = ON")
            connection.execute("PRAGMA busy_timeout = 30000")
            return connection
        except sqlite3.Error as exc:
            raise ExperimentSelectionPersistenceError(
                "could not open experiment selection database"
            ) from exc

    def _initialize(self) -> None:
        connection = self._connect()
        try:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS schema_metadata (
                    schema_key TEXT PRIMARY KEY,
                    schema_version INTEGER NOT NULL
                );
                CREATE TABLE IF NOT EXISTS research_experiment_selections (
                    selection_id TEXT PRIMARY KEY,
                    experiment_id TEXT NOT NULL UNIQUE,
                    protocol_id TEXT NOT NULL,
                    selected_candidate_id TEXT NOT NULL,
                    selected_candidate_index INTEGER NOT NULL CHECK(selected_candidate_index >= 0),
                    selected_parameter_set_hash TEXT NOT NULL,
                    selected_experiment_result_id TEXT NOT NULL,
                    selected_result_hash TEXT NOT NULL,
                    selected_derived_strategy_version_id TEXT NOT NULL,
                    selected_derived_strategy_content_hash TEXT NOT NULL,
                    objective_hash TEXT NOT NULL,
                    candidate_set_hash TEXT NOT NULL,
                    parameter_space_hash TEXT NOT NULL,
                    selection_method TEXT NOT NULL,
                    researcher_rationale TEXT NOT NULL,
                    evidence_json TEXT NOT NULL,
                    selection_hash TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    selection_json TEXT NOT NULL
                );
                CREATE UNIQUE INDEX IF NOT EXISTS uq_research_experiment_selection_experiment
                    ON research_experiment_selections(experiment_id);
                CREATE INDEX IF NOT EXISTS idx_research_experiment_selections_protocol
                    ON research_experiment_selections(
                        protocol_id, created_at ASC, selection_id ASC
                    );
                """
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
            elif int(row["schema_version"]) != self.SCHEMA_VERSION:
                raise ExperimentSelectionPersistenceError(
                    "unsupported experiment selection database schema version"
                )
        except ExperimentSelectionPersistenceError:
            raise
        except sqlite3.Error as exc:
            raise ExperimentSelectionPersistenceError(
                "could not initialize experiment selection database"
            ) from exc
        finally:
            connection.close()

    def create(self, decision: ExperimentSelectionDecision) -> ExperimentSelectionDecision:
        """Persist one validated decision and its lifecycle transition atomically."""
        self._validate_decision(decision)
        selection_payload = self._safe_json(decision.to_dict())
        evidence_payload = self._safe_json(decision.evidence.to_dict())
        expected_hash = sha256_hash(decision.semantic_payload())
        if decision.selection_hash != expected_hash:
            raise ExperimentSelectionPersistenceError(
                "selection hash does not match semantic payload", code="SELECTION_INTEGRITY_ERROR"
            )

        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            ExperimentResultRepository.ensure_schema(connection)
            experiment = self._load_experiment(connection, decision.experiment_id)
            existing_row = connection.execute(
                "SELECT * FROM research_experiment_selections WHERE experiment_id = ?",
                (decision.experiment_id,),
            ).fetchone()
            if existing_row is not None:
                existing = self._decode(existing_row)
                if existing.semantic_payload() != decision.semantic_payload():
                    raise ExperimentSelectionConflictError(
                        "experiment already has a different official selection"
                    )
                self._validate_existing_terminal_state(connection, experiment, existing)
                connection.execute("COMMIT")
                return existing

            if experiment.status is not ExperimentStatus.COMPLETED:
                raise ExperimentSelectionPersistenceError(
                    "selection requires a completed experiment",
                    code=(
                        "EXPERIMENT_NOT_COMPLETED"
                        if experiment.status is not ExperimentStatus.SELECTION_RECORDED
                        else "SELECTION_INTEGRITY_ERROR"
                    ),
                )
            self._validate_database_bindings(connection, experiment, decision)
            self._insert_selection(
                connection,
                decision,
                evidence_payload,
                selection_payload,
            )
            updated = experiment.with_status(ExperimentStatus.SELECTION_RECORDED)
            updated_payload = self._experiments._canonical_payload(updated.to_dict())
            self._experiments._validate_experiment_integrity(updated, updated_payload)
            self._experiments._update_experiment_row(
                connection,
                decision.experiment_id,
                ExperimentStatus.COMPLETED,
                updated,
                updated_payload,
            )
            event_payload = {
                "selection_id": decision.selection_id,
                "selection_hash": decision.selection_hash,
                "selected_candidate_id": decision.selected_candidate_id,
                "selected_experiment_result_id": decision.selected_experiment_result_id,
            }
            self._experiments._insert_event(
                connection,
                decision.experiment_id,
                "SELECTION_RECORDED",
                event_payload,
                decision.created_at,
                transition_key=f"experiment-selection:{decision.experiment_id}",
                from_status=ExperimentStatus.COMPLETED,
                to_status=ExperimentStatus.SELECTION_RECORDED,
                provenance={
                    "selection_id": decision.selection_id,
                    "selection_hash": decision.selection_hash,
                    "result_id": decision.selected_experiment_result_id,
                    "result_hash": decision.selected_result_hash,
                },
            )
            connection.execute("COMMIT")
            return decision
        except (ExperimentSelectionPersistenceError, ExperimentSelectionConflictError):
            self._rollback(connection)
            raise
        except ExperimentPersistenceError as exc:
            self._rollback(connection)
            raise ExperimentSelectionPersistenceError(
                str(exc), code=getattr(exc, "code", None) or "SELECTION_INTEGRITY_ERROR"
            ) from exc
        except sqlite3.IntegrityError as exc:
            self._rollback(connection)
            if "experiment_id" in str(exc):
                raise ExperimentSelectionConflictError(
                    "experiment already has a different official selection"
                ) from exc
            raise ExperimentSelectionPersistenceError(
                "could not persist experiment selection", code="SELECTION_INTEGRITY_ERROR"
            ) from exc
        except (sqlite3.Error, TypeError, ValueError, OverflowError, json.JSONDecodeError) as exc:
            self._rollback(connection)
            raise ExperimentSelectionPersistenceError(
                "could not persist experiment selection atomically",
                code="SELECTION_INTEGRITY_ERROR",
            ) from exc
        except Exception as exc:
            self._rollback(connection)
            raise ExperimentSelectionPersistenceError(
                "could not persist experiment selection atomically",
                code="SELECTION_INTEGRITY_ERROR",
            ) from exc
        finally:
            connection.close()

    persist = create
    record = create

    def get(self, selection_id: str) -> ExperimentSelectionDecision | None:
        connection = self._connect()
        try:
            row = connection.execute(
                "SELECT * FROM research_experiment_selections WHERE selection_id = ?",
                (selection_id,),
            ).fetchone()
            return None if row is None else self._decode(row)
        except ExperimentSelectionPersistenceError:
            raise
        except (sqlite3.Error, TypeError, ValueError, OverflowError, json.JSONDecodeError) as exc:
            raise ExperimentSelectionPersistenceError(
                "stored experiment selection failed integrity checks",
                code="PERSISTED_SELECTION_INTEGRITY_ERROR",
            ) from exc
        finally:
            connection.close()

    def get_by_experiment_id(self, experiment_id: str) -> ExperimentSelectionDecision | None:
        connection = self._connect()
        try:
            row = connection.execute(
                "SELECT * FROM research_experiment_selections WHERE experiment_id = ?",
                (experiment_id,),
            ).fetchone()
            return None if row is None else self._decode(row)
        except ExperimentSelectionPersistenceError:
            raise
        except (sqlite3.Error, TypeError, ValueError, OverflowError, json.JSONDecodeError) as exc:
            raise ExperimentSelectionPersistenceError(
                "stored experiment selection failed integrity checks",
                code="PERSISTED_SELECTION_INTEGRITY_ERROR",
            ) from exc
        finally:
            connection.close()

    get_by_experiment = get_by_experiment_id

    def list(self, protocol_id: str | None = None) -> tuple[ExperimentSelectionDecision, ...]:
        connection = self._connect()
        try:
            if protocol_id is None:
                rows = connection.execute(
                    "SELECT * FROM research_experiment_selections "
                    "ORDER BY created_at ASC, selection_id ASC"
                ).fetchall()
            else:
                rows = connection.execute(
                    "SELECT * FROM research_experiment_selections WHERE protocol_id = ? "
                    "ORDER BY created_at ASC, selection_id ASC",
                    (protocol_id,),
                ).fetchall()
            return tuple(self._decode(row) for row in rows)
        except ExperimentSelectionPersistenceError:
            raise
        except (sqlite3.Error, TypeError, ValueError, OverflowError, json.JSONDecodeError) as exc:
            raise ExperimentSelectionPersistenceError(
                "stored experiment selections failed integrity checks",
                code="PERSISTED_SELECTION_INTEGRITY_ERROR",
            ) from exc
        finally:
            connection.close()

    def _validate_database_bindings(
        self,
        connection: sqlite3.Connection,
        experiment: object,
        decision: ExperimentSelectionDecision,
    ) -> None:
        if decision.protocol_id != experiment.protocol_id:
            self._fail("selection protocol does not match experiment", "SELECTION_INTEGRITY_ERROR")
        if decision.objective_hash != experiment.objective_spec_hash:
            self._fail(
                "selection objective hash does not match experiment",
                "OBJECTIVE_HASH_MISMATCH",
            )
        if decision.parameter_space_hash != experiment.parameter_space_hash:
            self._fail(
                "selection parameter space hash does not match experiment",
                "PARAMETER_SPACE_HASH_MISMATCH",
            )

        candidate_row = connection.execute(
            "SELECT * FROM experiment_candidates WHERE experiment_id = ? AND candidate_index = ?",
            (experiment.experiment_id, decision.selected_candidate_index),
        ).fetchone()
        if candidate_row is None:
            self._fail("selected candidate was not found", "CANDIDATE_NOT_FOUND")
        expected_candidate_id = candidate_id_for(
            experiment.experiment_id,
            decision.selected_candidate_index,
            decision.selected_parameter_set_hash,
        )
        if any(
            (
                decision.selected_candidate_id != expected_candidate_id,
                candidate_row["parameter_set_hash"] != decision.selected_parameter_set_hash,
            )
        ):
            self._fail("selected candidate identity is inconsistent", "SELECTION_INTEGRITY_ERROR")
        candidate_set_row = connection.execute(
            "SELECT candidate_set_hash, parameter_space_hash FROM experiment_candidate_sets "
            "WHERE experiment_id = ?",
            (experiment.experiment_id,),
        ).fetchone()
        if candidate_set_row is None:
            self._fail("candidate set was not found", "CANDIDATE_SET_HASH_MISMATCH")
        if candidate_set_row["candidate_set_hash"] != decision.candidate_set_hash:
            self._fail("candidate set hash does not match selection", "CANDIDATE_SET_HASH_MISMATCH")
        if candidate_set_row["parameter_space_hash"] != decision.parameter_space_hash:
            self._fail(
                "candidate set parameter space hash does not match selection",
                "PARAMETER_SPACE_HASH_MISMATCH",
            )

        result = ExperimentResultRepository.get_by_candidate_in_transaction(
            connection, experiment.experiment_id, decision.selected_candidate_id
        )
        if result is None:
            self._fail("selected experiment result was not found", "RESULT_NOT_FOUND")
        if any(
            (
                result.experiment_result_id != decision.selected_experiment_result_id,
                result.result_hash != decision.selected_result_hash,
                result.candidate_index != decision.selected_candidate_index,
                result.parameter_set_hash != decision.selected_parameter_set_hash,
                result.candidate_set_hash != decision.candidate_set_hash,
                result.parameter_space_hash != decision.parameter_space_hash,
                result.base_strategy_version_id != experiment.base_strategy_version_id,
                result.base_strategy_version_hash != experiment.base_strategy_version_hash,
                result.is_start != experiment.is_start_date,
                result.is_end != experiment.is_end_date,
                result.engine_version != experiment.engine_version,
                result.analysis_version != experiment.analysis_version,
            )
        ):
            self._fail("experiment result binding does not match selection", "RESULT_HASH_MISMATCH")

        execution_row = connection.execute(
            "SELECT * FROM candidate_executions WHERE experiment_id = ? AND candidate_index = ?",
            (experiment.experiment_id, decision.selected_candidate_index),
        ).fetchone()
        if execution_row is None:
            self._fail("candidate execution was not found", "RESULT_NOT_FOUND")
        try:
            execution = CandidateExecutionRepository._decode_execution(execution_row)
        except Exception as exc:
            self._fail("candidate execution failed integrity checks", "SELECTION_INTEGRITY_ERROR")
            raise AssertionError from exc
        if execution.status is not CandidateExecutionStatus.COMPLETED:
            self._fail("selected candidate execution is not completed", "CANDIDATE_NOT_TERMINAL")
        if execution.candidate_id != decision.selected_candidate_id:
            self._fail(
                "candidate execution identity does not match selection",
                "SELECTION_INTEGRITY_ERROR",
            )
        derived = self._strategies.get_any_version(decision.selected_derived_strategy_version_id)
        if (
            derived is None
            or derived.content_hash != decision.selected_derived_strategy_content_hash
        ):
            self._fail(
                "derived strategy version is missing or has a hash mismatch",
                "DERIVED_STRATEGY_INTEGRITY_ERROR",
            )
        if result.derived_strategy_version_id != derived.version_id:
            self._fail(
                "result derived strategy identity does not match selection",
                "DERIVED_STRATEGY_INTEGRITY_ERROR",
            )

        try:
            model = self._read_service.get(experiment.experiment_id)
            candidate = next(
                item for item in model.candidates
                if item.candidate_id == decision.selected_candidate_id
            )
            objective = evaluate_candidate_objective(experiment, candidate)
            compatibility = self._compatibility(model)
        except (ExperimentResultsReadModelError, StopIteration, ValueError, TypeError) as exc:
            raise ExperimentSelectionPersistenceError(
                "selection evidence could not be revalidated",
                code="SELECTION_INTEGRITY_ERROR",
            ) from exc
        if objective.objective_hash != decision.objective_hash:
            self._fail(
                "objective evidence hash does not match selection",
                "OBJECTIVE_HASH_MISMATCH",
            )
        if objective.experiment_result_id != decision.selected_experiment_result_id:
            self._fail("objective evidence result does not match selection", "RESULT_HASH_MISMATCH")
        if decision.evidence.objective_evaluation_state != objective.overall_constraint_state:
            self._fail("objective evidence state changed", "OBJECTIVE_HASH_MISMATCH")
        if sha256_hash(compatibility.to_dict()) != decision.evidence.compatibility_hash:
            self._fail("compatibility evidence changed", "SELECTION_INTEGRITY_ERROR")
        self._validate_evidence(decision.evidence, experiment, result, model)

    def _compatibility(self, model: object) -> object:
        from backend.app.experiment_compatibility_service import ExperimentCompatibilityService

        return ExperimentCompatibilityService().diagnose(model)

    @classmethod
    def _insert_selection(
        cls,
        connection: sqlite3.Connection,
        decision: ExperimentSelectionDecision,
        evidence_payload: str,
        selection_payload: str,
    ) -> None:
        connection.execute(
            """
            INSERT INTO research_experiment_selections(
                selection_id, experiment_id, protocol_id, selected_candidate_id,
                selected_candidate_index, selected_parameter_set_hash,
                selected_experiment_result_id, selected_result_hash,
                selected_derived_strategy_version_id, selected_derived_strategy_content_hash,
                objective_hash, candidate_set_hash, parameter_space_hash,
                selection_method, researcher_rationale, evidence_json,
                selection_hash, created_at, selection_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            cls._values(decision, evidence_payload, selection_payload),
        )

    @staticmethod
    def _validate_evidence(
        evidence: ExperimentSelectionEvidence,
        experiment: object,
        result: object,
        model: object,
    ) -> None:
        summary = model.summary
        if any(
            (
                evidence.candidate_count != summary.candidate_count,
                evidence.completed_count != summary.completed_count,
                evidence.failed_count != summary.failed_count,
                evidence.selected_candidate_index < 0,
                evidence.selected_result_hash != result.result_hash,
                evidence.selected_parameter_set_hash != result.parameter_set_hash,
                evidence.selected_experiment_result_id != result.experiment_result_id,
                evidence.selected_derived_strategy_version_id != result.derived_strategy_version_id,
                evidence.selected_derived_strategy_content_hash
                != result.derived_strategy_version_hash,
                evidence.objective_hash != experiment.objective_spec_hash,
                evidence.candidate_set_hash != result.candidate_set_hash,
                evidence.parameter_space_hash != experiment.parameter_space_hash,
                evidence.is_start != result.is_start,
                evidence.is_end != result.is_end,
                evidence.backtest_configuration_hash != result.backtest_configuration_hash,
                evidence.engine_version != result.engine_version,
                evidence.analysis_version != result.analysis_version,
                dict(evidence.data_snapshot_reference) != dict(result.data_snapshot_reference),
            )
        ):
            raise ExperimentSelectionPersistenceError(
                "selection evidence does not match persisted sources",
                code="SELECTION_INTEGRITY_ERROR",
            )

    @staticmethod
    def _validate_existing_terminal_state(
        connection: sqlite3.Connection, experiment: object, decision: ExperimentSelectionDecision
    ) -> None:
        if experiment.status is not ExperimentStatus.SELECTION_RECORDED:
            raise ExperimentSelectionPersistenceError(
                "selection exists without selection-recorded experiment state",
                code="PERSISTED_SELECTION_INTEGRITY_ERROR",
            )
        event = connection.execute(
            "SELECT * FROM experiment_events WHERE experiment_id = ? AND transition_key = ?",
            (experiment.experiment_id, f"experiment-selection:{experiment.experiment_id}"),
        ).fetchone()
        if event is None:
            raise ExperimentSelectionPersistenceError(
                "selection exists without its lifecycle event",
                code="PERSISTED_SELECTION_INTEGRITY_ERROR",
            )
        if (
            event["event_type"] != "SELECTION_RECORDED"
            or event["from_status"] != ExperimentStatus.COMPLETED.value
            or event["to_status"] != ExperimentStatus.SELECTION_RECORDED.value
            or event["transition_key"] != f"experiment-selection:{experiment.experiment_id}"
        ):
            raise ExperimentSelectionPersistenceError(
                "selection lifecycle event does not match experiment state",
                code="PERSISTED_SELECTION_INTEGRITY_ERROR",
            )
        try:
            payload = json.loads(event["payload_json"], parse_constant=_reject_nonfinite)
            provenance = json.loads(event["provenance_json"], parse_constant=_reject_nonfinite)
            expected_payload = {
                "selection_id": decision.selection_id,
                "selection_hash": decision.selection_hash,
                "selected_candidate_id": decision.selected_candidate_id,
                "selected_experiment_result_id": decision.selected_experiment_result_id,
            }
            expected_provenance = {
                "selection_id": decision.selection_id,
                "selection_hash": decision.selection_hash,
                "result_id": decision.selected_experiment_result_id,
                "result_hash": decision.selected_result_hash,
            }
            if (
                canonical_json(payload) != canonical_json(expected_payload)
                or canonical_json(provenance) != canonical_json(expected_provenance)
            ):
                raise ValueError("selection lifecycle event provenance mismatch")
        except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
            raise ExperimentSelectionPersistenceError(
                "selection lifecycle event provenance is invalid",
                code="PERSISTED_SELECTION_INTEGRITY_ERROR",
            ) from exc

    def _load_experiment(self, connection: sqlite3.Connection, experiment_id: str) -> object:
        row = connection.execute(
            "SELECT * FROM experiments WHERE experiment_id = ?", (experiment_id,)
        ).fetchone()
        if row is None:
            raise ExperimentSelectionPersistenceError(
                "experiment was not found", code="EXPERIMENT_NOT_FOUND"
            )
        try:
            experiment = self._experiments._decode_experiment(row)
            self._experiments._validate_external_bindings(experiment)
            self._experiments._validate_parameter_space_row(connection, experiment.parameter_space)
            return experiment
        except ExperimentPersistenceError as exc:
            raise ExperimentSelectionPersistenceError(
                str(exc), code="SELECTION_INTEGRITY_ERROR"
            ) from exc

    @classmethod
    def _validate_decision(cls, decision: object) -> None:
        if not isinstance(decision, ExperimentSelectionDecision):
            raise ExperimentSelectionPersistenceError(
                "experiment selection decision is invalid", code="SELECTION_INTEGRITY_ERROR"
            )
        try:
            if decision.selection_id != f"selection-{decision.selection_hash[:32]}":
                raise ValueError("selection identity does not match selection hash")
            cls._safe_json(decision.to_dict())
        except (TypeError, ValueError, OverflowError) as exc:
            raise ExperimentSelectionPersistenceError(
                "experiment selection decision is not canonical", code="SELECTION_INTEGRITY_ERROR"
            ) from exc

    @classmethod
    def _values(
        cls,
        decision: ExperimentSelectionDecision,
        evidence_payload: str,
        selection_payload: str,
    ) -> tuple[object, ...]:
        return (
            decision.selection_id,
            decision.experiment_id,
            decision.protocol_id,
            decision.selected_candidate_id,
            decision.selected_candidate_index,
            decision.selected_parameter_set_hash,
            decision.selected_experiment_result_id,
            decision.selected_result_hash,
            decision.selected_derived_strategy_version_id,
            decision.selected_derived_strategy_content_hash,
            decision.objective_hash,
            decision.candidate_set_hash,
            decision.parameter_space_hash,
            decision.selection_method.value,
            decision.researcher_rationale,
            evidence_payload,
            decision.selection_hash,
            decision.created_at.isoformat(),
            selection_payload,
        )

    @classmethod
    def _decode(cls, row: sqlite3.Row) -> ExperimentSelectionDecision:
        try:
            raw = row["selection_json"]
            payload = json.loads(raw, parse_constant=_reject_nonfinite)
            if raw != cls._safe_json(payload):
                raise ValueError("stored selection is not canonical JSON")
            decision = ExperimentSelectionDecision.from_dict(payload)
            if decision.selection_hash != sha256_hash(decision.semantic_payload()):
                raise ValueError("stored selection hash mismatch")
            evidence_raw = row["evidence_json"]
            evidence_payload = json.loads(evidence_raw, parse_constant=_reject_nonfinite)
            if evidence_raw != cls._safe_json(evidence_payload):
                raise ValueError("stored evidence is not canonical JSON")
            if evidence_payload != decision.evidence.to_dict():
                raise ValueError("stored evidence does not match selection payload")
            expected = {
                "selection_id": decision.selection_id,
                "experiment_id": decision.experiment_id,
                "protocol_id": decision.protocol_id,
                "selected_candidate_id": decision.selected_candidate_id,
                "selected_candidate_index": decision.selected_candidate_index,
                "selected_parameter_set_hash": decision.selected_parameter_set_hash,
                "selected_experiment_result_id": decision.selected_experiment_result_id,
                "selected_result_hash": decision.selected_result_hash,
                "selected_derived_strategy_version_id": (
                    decision.selected_derived_strategy_version_id
                ),
                "selected_derived_strategy_content_hash": (
                    decision.selected_derived_strategy_content_hash
                ),
                "objective_hash": decision.objective_hash,
                "candidate_set_hash": decision.candidate_set_hash,
                "parameter_space_hash": decision.parameter_space_hash,
                "selection_method": decision.selection_method.value,
                "researcher_rationale": decision.researcher_rationale,
                "selection_hash": decision.selection_hash,
                "created_at": decision.created_at.isoformat(),
            }
            if any(row[key] != value for key, value in expected.items()):
                raise ValueError("stored selection columns do not match payload")
            return decision
        except ExperimentSelectionPersistenceError:
            raise
        except (KeyError, TypeError, ValueError, OverflowError, json.JSONDecodeError) as exc:
            raise ExperimentSelectionPersistenceError(
                "stored experiment selection failed integrity checks",
                code="PERSISTED_SELECTION_INTEGRITY_ERROR",
            ) from exc

    @classmethod
    def _safe_json(cls, payload: object) -> str:
        cls._assert_safe(payload)
        try:
            return canonical_json(payload)
        except (TypeError, ValueError, OverflowError) as exc:
            raise ExperimentSelectionPersistenceError(
                "selection payload is not canonical JSON", code="SELECTION_INTEGRITY_ERROR"
            ) from exc

    @classmethod
    def _assert_safe(cls, payload: object) -> None:
        if isinstance(payload, Mapping):
            for key, value in payload.items():
                if cls._SENSITIVE_KEY.search(str(key)):
                    raise ExperimentSelectionPersistenceError(
                        "sensitive selection fields are not allowed",
                        code="SELECTION_INTEGRITY_ERROR",
                    )
                cls._assert_safe(value)
        elif isinstance(payload, (list, tuple)):
            for value in payload:
                cls._assert_safe(value)
        elif isinstance(payload, str) and re.search(
            r"(?:TIINGO_API_KEY|API_KEY|BEGIN\s+PRIVATE\s+KEY|SECRET_SENTINEL_8E1E)",
            payload,
            re.IGNORECASE,
        ):
            raise ExperimentSelectionPersistenceError(
                "sensitive selection values are not allowed", code="SELECTION_INTEGRITY_ERROR"
            )

    @staticmethod
    def _fail(message: str, code: str) -> None:
        raise ExperimentSelectionPersistenceError(message, code=code)

    @staticmethod
    def _rollback(connection: sqlite3.Connection) -> None:
        try:
            connection.execute("ROLLBACK")
        except sqlite3.Error:
            pass


def _reject_nonfinite(value: str) -> None:
    raise ValueError(f"non-finite JSON constant {value}")


__all__ = ["ExperimentSelectionRepository"]
