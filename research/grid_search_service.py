"""Sequential, recoverable PHASE 11H grid execution orchestration."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

from backend.app.candidate_execution_repository import CandidateExecutionRepository
from backend.app.experiment_repository import ExperimentRepository
from backend.app.experiment_result_repository import ExperimentResultRepository
from backend.app.grid_search_repository import GridSearchRepository
from backend.app.strategy_repository import StrategyRepository
from research.enums import ExperimentStatus
from research.execution import CandidateExecutionStatus, candidate_id_for
from research.execution_outcome import ExperimentExecutionOutcomeStatus
from research.execution_service import ExperimentExecutionService
from research.grid_search import GridSearchDefinition, GridSearchPreflight, preflight_grid_search
from research.result_finalization_service import ExperimentResultFinalizationService


class GridSearchServiceError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


class GridSearchService:
    """Coordinate existing candidate execution without implementing backtest logic."""

    ACTOR = "phase-11h-grid-search"

    def __init__(
        self,
        *,
        experiment_repository: ExperimentRepository,
        strategy_repository: StrategyRepository,
        execution_repository: CandidateExecutionRepository,
        result_repository: ExperimentResultRepository,
        grid_repository: GridSearchRepository,
        execution_service: ExperimentExecutionService,
        finalization_service: ExperimentResultFinalizationService,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._experiments = experiment_repository
        self._strategies = strategy_repository
        self._executions = execution_repository
        self._results = result_repository
        self._grid = grid_repository
        self._executor = execution_service
        self._finalizer = finalization_service
        self._clock = clock or (lambda: datetime.now(UTC))

    def preflight(
        self, experiment_id: str, definition: GridSearchDefinition
    ) -> GridSearchPreflight:
        experiment = self._experiments.get(experiment_id)
        if experiment is None:
            raise GridSearchServiceError("EXPERIMENT_NOT_FOUND", "experiment was not found")
        base = self._strategies.get(
            experiment.strategy_definition_id, experiment.base_strategy_version_id
        )
        if base is None:
            raise GridSearchServiceError(
                "STRATEGY_VERSION_NOT_FOUND", "base strategy version was not found"
            )
        try:
            return preflight_grid_search(experiment, definition, base)
        except Exception as exc:
            raise GridSearchServiceError("GRID_PREFLIGHT_INVALID", str(exc)) from exc

    def template(self, experiment_id: str) -> dict[str, Any]:
        """Return frozen inputs needed by a client to build an explicit definition."""
        experiment = self._experiments.get(experiment_id)
        if experiment is None:
            raise GridSearchServiceError("EXPERIMENT_NOT_FOUND", "experiment was not found")
        base = self._strategies.get(
            experiment.strategy_definition_id, experiment.base_strategy_version_id
        )
        if base is None:
            raise GridSearchServiceError(
                "STRATEGY_VERSION_NOT_FOUND", "base strategy version was not found"
            )
        frozen_bindings = (
            list(experiment.provenance.parameter_bindings or ())
            if experiment.provenance is not None
            else []
        )
        definition = {
            "schema_version": "phase-11h.1",
            "experiment_id": experiment.experiment_id,
            "experiment_hash": experiment.content_hash,
            "strategy_bindings": frozen_bindings,
            "backtest_bindings": [],
            "fixed_parameters": {
                "strategy_definition_id": experiment.strategy_definition_id,
                "base_strategy_version_id": experiment.base_strategy_version_id,
                "price_field": experiment.backtest_configuration.price_field_used.value,
                "is_start": experiment.is_start_date.isoformat(),
                "is_end": experiment.is_end_date.isoformat(),
                "assets": [item.symbol for item in base.configuration.assets],
            },
            "constraints": [],
            "max_candidates": min(experiment.parameter_space.max_candidates, 100),
            "theoretical_guard": 1_000_000,
        }
        return {
            "definition": definition,
            "parameter_space": experiment.parameter_space.to_dict(),
            "fixed_parameters": definition["fixed_parameters"],
            "tunable_parameters": [
                item.to_dict() for item in experiment.parameter_space.parameters
            ],
        }

    def prepare(self, experiment_id: str, definition: GridSearchDefinition) -> dict[str, Any]:
        preflight = self.preflight(experiment_id, definition)
        if not preflight.can_execute:
            return preflight.to_dict()
        candidate_set = preflight.candidate_set
        self._experiments.attach_candidate_set(
            experiment_id,
            candidate_set,
            transition_key=f"grid-candidates:{definition.definition_hash}",
        )
        self._grid.save_preflight(preflight)
        return preflight.to_dict()

    def execute(self, experiment_id: str) -> dict[str, Any]:
        definition = self._grid.get_definition(experiment_id)
        experiment = self._experiments.get(experiment_id)
        if definition is None or experiment is None:
            raise GridSearchServiceError(
                "GRID_NOT_PREPARED", "grid definition and candidates must be prepared first"
            )
        if experiment.status in {ExperimentStatus.CANCELLED, ExperimentStatus.CANCEL_REQUESTED}:
            self._finish_cancellation(experiment_id, experiment.status)
            return self._grid.progress(experiment_id)
        if experiment.status is ExperimentStatus.COMPLETED:
            self._grid.set_status(experiment_id, "completed")
            return self._grid.progress(experiment_id)
        if experiment.status is ExperimentStatus.CANDIDATES_GENERATED:
            experiment = self._experiments.transition_status(
                experiment_id,
                ExperimentStatus.CANDIDATES_GENERATED,
                ExperimentStatus.RUNNING,
                "GRID_EXECUTION_STARTED",
                f"grid-execute:{definition.definition_hash}",
                {"definition_hash": definition.definition_hash, "execution_mode": "sequential"},
            )
        if experiment.status is not ExperimentStatus.RUNNING:
            raise GridSearchServiceError(
                "GRID_NOT_EXECUTABLE", f"experiment status is {experiment.status.value}"
            )
        self._grid.set_status(experiment_id, "running")
        candidates = self._experiments.list_candidates(experiment_id)
        for candidate in candidates:
            if self._grid.cancel_requested(experiment_id):
                self._request_experiment_cancel(experiment_id)
                break
            candidate_id = candidate_id_for(
                experiment_id, candidate.candidate_index, candidate.parameter_set_hash
            )
            if self._results.get_by_candidate(experiment_id, candidate_id) is not None:
                continue
            existing = self._executions.get_execution_by_candidate(
                experiment_id, candidate.candidate_index
            )
            if existing is not None:
                if existing.status is CandidateExecutionStatus.COMPLETED:
                    self._executions.recover_unfinalized_completion(
                        experiment_id,
                        candidate.candidate_index,
                        actor=self.ACTOR,
                        now=self._now(),
                    )
                elif existing.status is CandidateExecutionStatus.FAILED and (
                    not existing.failure_retryable or existing.retry_count > existing.max_retries
                ):
                    continue
                elif existing.status is CandidateExecutionStatus.RUNNING and (
                    existing.lease_expires_at is not None
                    and existing.lease_expires_at > self._now()
                ):
                    raise GridSearchServiceError(
                        "CANDIDATE_ALREADY_RUNNING",
                        f"candidate {candidate.candidate_index} has an active lease",
                    )
            outcome = self._executor.execute(
                experiment_id=experiment_id,
                candidate_index=candidate.candidate_index,
                parameter_bindings=definition.strategy_bindings,
                backtest_parameter_bindings=definition.backtest_bindings,
                claimed_by=self.ACTOR,
            )
            if outcome.status is ExperimentExecutionOutcomeStatus.FAILED:
                continue
            try:
                self._finalizer.finalize(outcome, terminalize_experiment=False)
            except Exception as exc:
                self._grid.set_status(experiment_id, "failed")
                raise GridSearchServiceError(
                    "GRID_RESULT_FINALIZATION_FAILED",
                    f"candidate {candidate.candidate_index} result could not be finalized",
                ) from exc

        if self._grid.cancel_requested(experiment_id):
            self._finish_cancellation(experiment_id, self._experiments.get(experiment_id).status)
            return self._grid.progress(experiment_id)

        executions = tuple(
            self._executions.get_execution_by_candidate(experiment_id, item.candidate_index)
            for item in candidates
        )
        retryable = any(
            item is not None
            and item.status is CandidateExecutionStatus.FAILED
            and bool(item.failure_retryable)
            and item.retry_count <= item.max_retries
            for item in executions
        )
        if retryable:
            return self._grid.progress(experiment_id)
        result_count = len(self._results.list(experiment_id))
        if result_count:
            self._experiments.complete_grid_experiment(
                experiment_id,
                transition_key=f"grid-complete:{definition.definition_hash}",
                provenance={"definition_hash": definition.definition_hash},
            )
            self._grid.set_status(experiment_id, "completed")
        else:
            self._experiments.transition_status(
                experiment_id,
                ExperimentStatus.RUNNING,
                ExperimentStatus.INVALID,
                "GRID_EXECUTION_FAILED",
                f"grid-failed:{definition.definition_hash}",
                {"reason": "all candidates failed", "definition_hash": definition.definition_hash},
            )
            self._grid.set_status(experiment_id, "failed")
        return self._grid.progress(experiment_id)

    def cancel(self, experiment_id: str) -> dict[str, Any]:
        if not self._grid.request_cancel(experiment_id):
            progress = self._grid.progress(experiment_id)
            if progress["status"] not in {"completed", "failed", "cancelled"}:
                raise GridSearchServiceError("GRID_NOT_CANCELLABLE", "grid cannot be cancelled")
            return progress
        experiment = self._experiments.get(experiment_id)
        if experiment is None:
            raise GridSearchServiceError("EXPERIMENT_NOT_FOUND", "experiment was not found")
        self._request_experiment_cancel(experiment_id)
        current = self._experiments.get(experiment_id)
        if current is not None and current.status is ExperimentStatus.CANCEL_REQUESTED:
            running = any(
                execution is not None and execution.status is CandidateExecutionStatus.RUNNING
                for execution in (
                    self._executions.get_execution_by_candidate(
                        experiment_id, candidate.candidate_index
                    )
                    for candidate in self._experiments.list_candidates(experiment_id)
                )
            )
            if not running:
                self._finish_cancellation(experiment_id, current.status)
        return self._grid.progress(experiment_id)

    def progress(self, experiment_id: str) -> dict[str, Any]:
        return self._grid.progress(experiment_id)

    def _request_experiment_cancel(self, experiment_id: str) -> None:
        experiment = self._experiments.get(experiment_id)
        if experiment is None or experiment.status in {
            ExperimentStatus.CANCEL_REQUESTED,
            ExperimentStatus.CANCELLED,
        }:
            return
        if experiment.status not in {
            ExperimentStatus.CANDIDATES_GENERATED,
            ExperimentStatus.RUNNING,
        }:
            return
        self._experiments.transition_status(
            experiment_id,
            experiment.status,
            ExperimentStatus.CANCEL_REQUESTED,
            "GRID_CANCELLATION_REQUESTED",
            f"grid-cancel-request:{experiment.content_hash}",
            {"current_candidate_policy": "finish_atomically_then_stop"},
        )

    def _finish_cancellation(self, experiment_id: str, status: ExperimentStatus | None) -> None:
        if status is ExperimentStatus.CANCELLED:
            self._grid.set_status(experiment_id, "cancelled")
            return
        if status is not ExperimentStatus.CANCEL_REQUESTED:
            self._request_experiment_cancel(experiment_id)
        current = self._experiments.get(experiment_id)
        if current is not None and current.status is ExperimentStatus.CANCEL_REQUESTED:
            self._experiments.transition_status(
                experiment_id,
                ExperimentStatus.CANCEL_REQUESTED,
                ExperimentStatus.CANCELLED,
                "GRID_CANCELLED",
                f"grid-cancelled:{current.content_hash}",
                {"pending_candidates_started": False},
            )
        self._grid.set_status(experiment_id, "cancelled")

    def _now(self) -> datetime:
        value = self._clock()
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("clock must be timezone-aware")
        return value


__all__ = ["GridSearchService", "GridSearchServiceError"]
