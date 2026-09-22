"""PHASE 8D-4 finalization of an IS-only execution outcome."""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping
from dataclasses import replace
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from analytics.models import PerformanceAnalysisResult
from backend.app.backtest_models import BacktestRun, serialize_backtest_result
from backend.app.backtest_repository import BacktestPersistenceError, BacktestRepository
from backend.app.experiment_repository import ExperimentPersistenceError, ExperimentRepository
from backend.app.strategy_execution_provenance import strategy_execution_provenance
from backtest.integration import StrategyBacktestResult
from data.models import PriceField
from research.canonical import canonical_json, sha256_hash
from research.enums import ExperimentStatus
from research.exceptions import ExperimentFinalizationError, ExperimentResultConflictError
from research.execution import CandidateExecutionStatus, candidate_id_for
from research.execution_outcome import (
    ExperimentExecutionOutcome,
    ExperimentExecutionOutcomeStatus,
)
from research.experiment_result import ExperimentResult

if TYPE_CHECKING:
    from backend.app.candidate_execution_repository import CandidateExecutionRepository
    from backend.app.experiment_result_repository import ExperimentResultRepository


class ExperimentResultFinalizationService:
    """Persist one completed candidate execution as an immutable result.

    The service is deliberately an adapter around existing persistence and
    execution products.  It does not evaluate strategies, run backtests, or
    calculate analytics.  BacktestRun persistence remains an independent
    source-of-truth write. When the bound experiment is already RUNNING,
    result persistence and terminal lifecycle persistence are delegated to one
    SQLite transaction.
    """

    ACTOR = "phase-8d-4-finalizer"

    def __init__(
        self,
        *,
        backtest_repository: BacktestRepository,
        experiment_result_repository: ExperimentResultRepository,
        candidate_execution_repository: CandidateExecutionRepository,
        experiment_repository: ExperimentRepository | None = None,
        clock: Callable[[], datetime] | None = None,
        actor: str = ACTOR,
    ) -> None:
        self._backtests = backtest_repository
        self._results = experiment_result_repository
        self._executions = candidate_execution_repository
        self._experiments = experiment_repository or ExperimentRepository(self._executions.db_path)
        self._clock = clock or (lambda: datetime.now(UTC))
        if not isinstance(actor, str) or not actor.strip():
            raise ValueError("actor must be a non-empty string")
        self._actor = actor.strip()

    def finalize(
        self,
        outcome: ExperimentExecutionOutcome,
        *,
        terminalize_experiment: bool = True,
    ) -> ExperimentResult:
        """Finalize a successful outcome, safely supporting retries."""
        self._validate_outcome(outcome)

        existing = self._results.get_by_candidate(outcome.experiment_id, outcome.candidate_id)
        if existing is not None:
            self._validate_existing_result(existing, outcome)
            self._validate_backtest_reference(existing, outcome)
            execution = self._executions.get_execution(outcome.candidate_execution_id or "")
            if execution is None:
                raise ExperimentFinalizationError(
                    "CANDIDATE_EXECUTION_NOT_FOUND: candidate execution does not exist"
                )
            self._complete_execution_if_needed(execution, outcome)
            return existing

        execution = self._load_and_validate_execution(outcome)

        run = self._build_backtest_run(outcome)
        run = self._persist_or_reuse_backtest_run(run, outcome)
        result = ExperimentResult.from_outcome(
            outcome,
            backtest_run_id=run.backtest_run_id,
            created_at=self._now(),
        )
        experiment = self._experiments.get(outcome.experiment_id)
        if (
            terminalize_experiment
            and experiment is not None
            and experiment.status is ExperimentStatus.RUNNING
        ):
            try:
                stored = self._experiments.finalize_result(
                    result,
                    expected_status=ExperimentStatus.RUNNING,
                    transition_key=f"experiment-result:{result.experiment_result_id}",
                    provenance={
                        "source": self._actor,
                        "derived_strategy_version_id": result.derived_strategy_version_id,
                        "derived_strategy_version_hash": result.derived_strategy_version_hash,
                    },
                )
            except ExperimentPersistenceError as exc:
                raise ExperimentFinalizationError(
                    f"EXPERIMENT_RESULT_PERSISTENCE_ERROR: {exc}"
                ) from exc
        else:
            try:
                stored = self._results.create(result)
            except ExperimentResultConflictError:
                # Another finalizer won the candidate's unique result race.  The
                # winner is authoritative; a retry must return it after checking
                # that it represents the same immutable outcome.
                stored = self._results.get_by_candidate(outcome.experiment_id, outcome.candidate_id)
                if stored is None:
                    raise ExperimentFinalizationError(
                        "DUPLICATE_EXPERIMENT_RESULT: conflicting result could not be recovered"
                    )
                self._validate_existing_result(stored, outcome)
                self._validate_backtest_reference(stored, outcome)

        self._complete_execution_if_needed(execution, outcome)
        return stored

    def _validate_outcome(self, outcome: ExperimentExecutionOutcome) -> None:
        if not isinstance(outcome, ExperimentExecutionOutcome):
            raise ExperimentFinalizationError("INVALID_OUTCOME: outcome is invalid")
        if outcome.status is not ExperimentExecutionOutcomeStatus.COMPLETED:
            raise ExperimentFinalizationError(
                "INVALID_OUTCOME: only completed outcomes may be finalized"
            )
        if not outcome.succeeded:
            raise ExperimentFinalizationError("INVALID_OUTCOME: outcome did not succeed")
        if outcome.candidate_execution_id is None:
            raise ExperimentFinalizationError(
                "CANDIDATE_EXECUTION_ERROR: completed outcome has no execution identity"
            )
        if outcome.derived_strategy_version_id is None or (
            outcome.derived_strategy_version_hash is None
        ):
            raise ExperimentFinalizationError(
                "PROVENANCE_MISMATCH: completed outcome has no derived strategy identity"
            )
        if outcome.backtest_result is None or outcome.performance_analysis_result is None:
            raise ExperimentFinalizationError(
                "INVALID_OUTCOME: completed outcome is missing execution products"
            )
        integration = outcome.backtest_result
        analysis = outcome.performance_analysis_result
        if not isinstance(integration, StrategyBacktestResult):
            raise ExperimentFinalizationError("INVALID_OUTCOME: backtest product is invalid")
        if not isinstance(analysis, PerformanceAnalysisResult):
            raise ExperimentFinalizationError("INVALID_OUTCOME: analytics product is invalid")
        backtest = integration.backtest_result
        if integration.strategy_version_id != outcome.derived_strategy_version_id:
            raise ExperimentFinalizationError(
                "PROVENANCE_MISMATCH: derived strategy version id does not match backtest"
            )
        if backtest.strategy_version_id != outcome.derived_strategy_version_id:
            raise ExperimentFinalizationError(
                "PROVENANCE_MISMATCH: backtest strategy version does not match outcome"
            )
        if backtest.start_date != outcome.is_start or backtest.end_date != outcome.is_end:
            raise ExperimentFinalizationError(
                "IS_RANGE_VIOLATION: backtest result is outside the IS range"
            )
        if analysis.start_date != outcome.is_start or analysis.end_date != outcome.is_end:
            raise ExperimentFinalizationError(
                "IS_RANGE_VIOLATION: analytics result is outside the IS range"
            )
        if analysis.strategy_version_id != outcome.derived_strategy_version_id:
            raise ExperimentFinalizationError(
                "PROVENANCE_MISMATCH: analytics strategy version does not match outcome"
            )
        if analysis.price_field_used is not outcome.price_field_used:
            raise ExperimentFinalizationError(
                "PROVENANCE_MISMATCH: analytics price field does not match outcome"
            )
        try:
            snapshot = backtest.configuration_snapshot
            snapshot_price_field = PriceField(snapshot["price_field_used"])
            snapshot_start = snapshot["start_date"]
            snapshot_end = snapshot["end_date"]
        except (KeyError, TypeError, ValueError) as exc:
            raise ExperimentFinalizationError(
                "PROVENANCE_MISMATCH: backtest configuration snapshot is invalid"
            ) from exc
        if snapshot_price_field is not outcome.price_field_used:
            raise ExperimentFinalizationError(
                "PROVENANCE_MISMATCH: backtest price field does not match outcome"
            )
        if (
            snapshot_start != outcome.is_start.isoformat()
            or snapshot_end != outcome.is_end.isoformat()
        ):
            raise ExperimentFinalizationError(
                "IS_RANGE_VIOLATION: backtest configuration is outside the IS range"
            )
        if backtest.data_snapshot_reference != backtest.configuration_snapshot.get(
            "data_snapshot_reference", backtest.data_snapshot_reference
        ):
            raise ExperimentFinalizationError(
                "PROVENANCE_MISMATCH: backtest data snapshot is inconsistent"
            )

    def _load_and_validate_execution(self, outcome: ExperimentExecutionOutcome) -> Any:
        execution = self._executions.get_execution(outcome.candidate_execution_id or "")
        if execution is None:
            raise ExperimentFinalizationError(
                "CANDIDATE_EXECUTION_NOT_FOUND: candidate execution does not exist"
            )
        expected = {
            "experiment_id": outcome.experiment_id,
            "candidate_id": outcome.candidate_id,
            "candidate_index": outcome.candidate_index,
            "parameter_set_hash": outcome.parameter_set_hash,
            "base_strategy_version_id": outcome.base_strategy_version_id,
            "base_strategy_version_hash": outcome.base_strategy_version_hash,
            "derived_strategy_version_id": outcome.derived_strategy_version_id,
            "derived_strategy_version_hash": outcome.derived_strategy_version_hash,
            "parameter_binding_hash": outcome.binding_hash,
        }
        actual = {
            "experiment_id": execution.experiment_id,
            "candidate_id": execution.candidate_id,
            "candidate_index": execution.candidate_index,
            "parameter_set_hash": execution.parameter_set_hash,
            "base_strategy_version_id": execution.base_strategy_version_id,
            "base_strategy_version_hash": execution.base_strategy_version_hash,
            "derived_strategy_version_id": execution.derived_strategy_version_id,
            "derived_strategy_version_hash": execution.derived_strategy_version_hash,
            "parameter_binding_hash": execution.parameter_binding_hash,
        }
        if actual != expected:
            raise ExperimentFinalizationError(
                "PROVENANCE_MISMATCH: candidate execution identity does not match outcome"
            )
        experiment = self._experiments.get(outcome.experiment_id)
        if experiment is None:
            raise ExperimentFinalizationError("EXPERIMENT_NOT_FOUND: experiment does not exist")
        if execution.experiment_hash != experiment.content_hash:
            raise ExperimentFinalizationError(
                "PROVENANCE_MISMATCH: candidate execution experiment hash is stale"
            )
        base_configuration_hash = sha256_hash(experiment.backtest_configuration.snapshot({}))
        if any(
            (
                experiment.base_strategy_version_id != outcome.base_strategy_version_id,
                experiment.base_strategy_version_hash != outcome.base_strategy_version_hash,
                experiment.parameter_space_hash != outcome.parameter_space_hash,
                experiment.is_start_date != outcome.is_start,
                experiment.is_end_date != outcome.is_end,
                experiment.engine_version != outcome.engine_version,
                experiment.analysis_version != outcome.analysis_version,
                outcome.provenance.get("base_backtest_configuration_hash")
                not in (None, base_configuration_hash),
                not self._experiments.candidate_configuration_hash_matches(
                    experiment,
                    outcome.candidate_index,
                    outcome.backtest_configuration_hash,
                ),
            )
        ):
            raise ExperimentFinalizationError(
                "PROVENANCE_MISMATCH: outcome does not match frozen experiment"
            )
        materialized_configuration = outcome.provenance.get("backtest_configuration")
        if materialized_configuration is not None and (
            not isinstance(materialized_configuration, Mapping)
            or sha256_hash(materialized_configuration) != outcome.backtest_configuration_hash
        ):
            raise ExperimentFinalizationError(
                "PROVENANCE_MISMATCH: materialized backtest configuration hash is invalid"
            )
        candidates = self._experiments.list_candidates(outcome.experiment_id)
        candidate = next(
            (item for item in candidates if item.candidate_index == outcome.candidate_index),
            None,
        )
        if candidate is None:
            raise ExperimentFinalizationError(
                "CANDIDATE_NOT_FOUND: candidate does not exist in the frozen candidate set"
            )
        expected_candidate_id = candidate_id_for(
            outcome.experiment_id,
            candidate.candidate_index,
            candidate.parameter_set_hash,
        )
        if any(
            (
                candidate.experiment_id != outcome.experiment_id,
                expected_candidate_id != outcome.candidate_id,
                candidate.parameter_set_hash != outcome.parameter_set_hash,
                candidate.candidate_set_hash != outcome.candidate_set_hash,
            )
        ):
            raise ExperimentFinalizationError(
                "PROVENANCE_MISMATCH: outcome does not match frozen candidate"
            )
        if execution.status is CandidateExecutionStatus.FAILED:
            raise ExperimentFinalizationError(
                "CANDIDATE_FINALIZATION_ERROR: FAILED candidate cannot produce a result"
            )
        if execution.status is CandidateExecutionStatus.PENDING:
            raise ExperimentFinalizationError(
                "CANDIDATE_FINALIZATION_ERROR: candidate must be RUNNING or COMPLETED"
            )
        return execution

    def _build_backtest_run(self, outcome: ExperimentExecutionOutcome) -> BacktestRun:
        integration = outcome.backtest_result
        analysis = outcome.performance_analysis_result
        assert integration is not None and analysis is not None
        backtest = integration.backtest_result
        strategy_id = integration.strategy_id
        run_id = "backtest-experiment-" + sha256_hash(
            {
                "experiment_id": outcome.experiment_id,
                "candidate_id": outcome.candidate_id,
                "parameter_set_hash": outcome.parameter_set_hash,
                "binding_hash": outcome.binding_hash,
                "derived_strategy_version_id": outcome.derived_strategy_version_id,
                "derived_strategy_version_hash": outcome.derived_strategy_version_hash,
                "backtest_result": serialize_backtest_result(backtest),
                "performance_analysis": analysis.to_dict(),
            }
        )
        bound_analysis = replace(
            analysis,
            backtest_run_id=run_id,
            strategy_id=strategy_id,
        )
        provenance_payload = {
            "source": "ExperimentExecutionOutcome",
            "experiment_id": outcome.experiment_id,
            "candidate_id": outcome.candidate_id,
            "candidate_execution_id": outcome.candidate_execution_id,
            "data_snapshot_reference": outcome.provenance.get("data_snapshot_reference", {}),
            "execution_provenance": outcome.provenance,
        }
        provenance = json.loads(canonical_json(provenance_payload))
        return BacktestRun(
            backtest_run_id=run_id,
            strategy_id=strategy_id,
            strategy_version_id=outcome.derived_strategy_version_id or "",
            created_at=self._now(),
            strategy_version_content_hash=outcome.derived_strategy_version_hash or "",
            backtest_result=backtest,
            performance_analysis=bound_analysis,
            provenance=provenance,
            strategy_provenance=strategy_execution_provenance(integration.signal_records),
        )

    def _persist_or_reuse_backtest_run(
        self, run: BacktestRun, outcome: ExperimentExecutionOutcome
    ) -> BacktestRun:
        existing = self._backtests.get(run.backtest_run_id)
        if existing is not None:
            self._validate_backtest_run(existing, outcome)
            return existing
        try:
            return self._backtests.create(run)
        except BacktestPersistenceError:
            existing = self._backtests.get(run.backtest_run_id)
            if existing is None:
                raise ExperimentFinalizationError(
                    "TRANSIENT_PERSISTENCE_ERROR: backtest run could not be persisted"
                )
            self._validate_backtest_run(existing, outcome)
            return existing

    def _validate_backtest_run(self, run: BacktestRun, outcome: ExperimentExecutionOutcome) -> None:
        if run.strategy_version_id != outcome.derived_strategy_version_id:
            raise ExperimentFinalizationError(
                "PROVENANCE_MISMATCH: persisted backtest strategy version mismatch"
            )
        if run.strategy_version_content_hash != outcome.derived_strategy_version_hash:
            raise ExperimentFinalizationError(
                "PROVENANCE_MISMATCH: persisted derived strategy hash mismatch"
            )
        integration = outcome.backtest_result
        analysis = outcome.performance_analysis_result
        assert integration is not None and analysis is not None
        if run.backtest_result != integration.backtest_result:
            raise ExperimentFinalizationError(
                "BACKTEST_RUN_CONFLICT: persisted backtest run differs from outcome"
            )
        if (
            run.performance_analysis.to_dict()
            != replace(
                analysis, backtest_run_id=run.backtest_run_id, strategy_id=integration.strategy_id
            ).to_dict()
        ):
            raise ExperimentFinalizationError(
                "BACKTEST_RUN_CONFLICT: persisted analytics differs from outcome"
            )

    def _validate_existing_result(
        self, result: ExperimentResult, outcome: ExperimentExecutionOutcome
    ) -> None:
        expected = ExperimentResult.from_outcome(
            outcome,
            backtest_run_id=result.backtest_run_id,
            created_at=result.created_at,
        )
        if result.hash_payload() != expected.hash_payload():
            raise ExperimentFinalizationError(
                "DUPLICATE_EXPERIMENT_RESULT: existing result conflicts with outcome"
            )

    def _validate_backtest_reference(
        self, result: ExperimentResult, outcome: ExperimentExecutionOutcome
    ) -> None:
        run = self._backtests.get(result.backtest_run_id)
        if run is None:
            raise ExperimentFinalizationError(
                "BACKTEST_RUN_MISSING: experiment result references a missing backtest run"
            )
        self._validate_backtest_run(run, outcome)

    def _complete_execution_if_needed(
        self, execution: Any, outcome: ExperimentExecutionOutcome
    ) -> None:
        if execution.status is CandidateExecutionStatus.COMPLETED:
            return
        try:
            now = self._now()
            self._executions.mark_completed(
                execution.execution_id,
                self._actor,
                now,
                now,
            )
        except Exception as exc:
            raise ExperimentFinalizationError(
                "CANDIDATE_FINALIZATION_ERROR: result exists but candidate completion failed"
            ) from exc

    def _now(self) -> datetime:
        value = self._clock()
        if value.tzinfo is None or value.utcoffset() is None:
            raise ExperimentFinalizationError(
                "INVALID_CLOCK: finalizer clock must be timezone-aware"
            )
        return value


ResultFinalizationService = ExperimentResultFinalizationService

__all__ = ["ExperimentResultFinalizationService", "ResultFinalizationService"]
