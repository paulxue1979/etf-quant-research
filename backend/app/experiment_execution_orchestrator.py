"""PHASE 8D-5 orchestration for safe, idempotent candidate execution APIs."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from backend.app.backtest_repository import BacktestRepository
from backend.app.candidate_execution_repository import CandidateExecutionRepository
from backend.app.experiment_repository import ExperimentRepository
from backend.app.experiment_result_repository import ExperimentResultRepository
from backend.app.research_protocol import ResearchProtocolRepository
from backend.app.strategy_repository import StrategyRepository
from data.cache import DiskCache
from data.config import TiingoSettings
from data.service import HistoricalDataService
from data.tiingo import TiingoClient
from research.exceptions import ExperimentFinalizationError
from research.execution import CandidateExecutionStatus, candidate_id_for
from research.execution_outcome import ExperimentExecutionOutcomeStatus
from research.execution_service import ExperimentExecutionService
from research.materialization import ParameterBindingSet
from research.result_finalization_service import ExperimentResultFinalizationService


class ExperimentExecutionApiError(RuntimeError):
    """A safe, structured error suitable for the HTTP boundary."""

    def __init__(self, status_code: int, code: str, message: str) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.message = message


class ExperimentExecutionOrchestrator:
    """Connect persistence, 8D-3 execution, and 8D-4 finalization only."""

    ACTOR = "phase-8d-5-api"

    def __init__(
        self,
        *,
        experiment_repository: ExperimentRepository,
        execution_repository: CandidateExecutionRepository,
        result_repository: ExperimentResultRepository,
        execution_service: ExperimentExecutionService,
        finalization_service: ExperimentResultFinalizationService,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._experiments = experiment_repository
        self._executions = execution_repository
        self._results = result_repository
        self._execution_service = execution_service
        self._finalizer = finalization_service
        self._clock = clock or (lambda: datetime.now(UTC))

    def execute(self, experiment_id: str, candidate_id: str) -> dict[str, Any]:
        experiment, candidate_index, candidate = self._resolve_candidate(
            experiment_id, candidate_id
        )
        expected_id = candidate_id_for(
            experiment_id, candidate_index, candidate.parameter_set_hash
        )
        existing_result = self._results.get_by_candidate(experiment_id, expected_id)
        if existing_result is not None:
            self._validate_result_identity(existing_result, experiment, candidate, expected_id)
            execution = self._ensure_completed_result_execution(
                experiment_id, candidate_index, existing_result
            )
            return self._response(execution, existing_result)

        bindings = self._frozen_bindings(experiment)

        execution = self._executions.get_execution_by_candidate(experiment_id, candidate_index)
        now = self._now()
        if execution is not None:
            if execution.candidate_id != expected_id:
                raise ExperimentExecutionApiError(
                    409, "CANDIDATE_EXECUTION_CONFLICT", "candidate execution identity conflicts"
                )
            if execution.status is CandidateExecutionStatus.RUNNING:
                if execution.lease_expires_at is not None and execution.lease_expires_at > now:
                    raise ExperimentExecutionApiError(
                        409, "ALREADY_RUNNING", "candidate execution is already running"
                    )
            elif execution.status is CandidateExecutionStatus.COMPLETED:
                raise ExperimentExecutionApiError(
                    409,
                    "RESULT_MISSING",
                    "candidate execution is complete but its immutable result is missing",
                )
            elif execution.status is CandidateExecutionStatus.FAILED and (
                not execution.failure_retryable or execution.retry_count > execution.max_retries
            ):
                raise ExperimentExecutionApiError(
                    409, "RETRY_NOT_ALLOWED", "candidate execution has no retry remaining"
                )

        outcome = self._execution_service.execute(
            experiment_id=experiment_id,
            candidate_index=candidate_index,
            parameter_bindings=bindings,
            claimed_by=self.ACTOR,
        )
        if outcome.status is not ExperimentExecutionOutcomeStatus.COMPLETED:
            status_code = 503 if outcome.failure_code in {
                "MISSING_MARKET_DATA",
                "CANDIDATE_EXECUTION_PERSISTENCE_ERROR",
            } else 422
            raise ExperimentExecutionApiError(
                status_code,
                outcome.failure_code or "CANDIDATE_EXECUTION_FAILED",
                "candidate execution did not complete",
            )
        try:
            result = self._finalizer.finalize(outcome)
        except ExperimentFinalizationError as exc:
            message = str(exc)
            code = message.split(":", 1)[0] if ":" in message else "FINALIZATION_ERROR"
            raise ExperimentExecutionApiError(
                409, code, "candidate result finalization conflicted"
            ) from exc
        execution = self._executions.get_execution(outcome.candidate_execution_id or "")
        if execution is None:
            raise ExperimentExecutionApiError(
                503, "EXECUTION_NOT_FOUND", "candidate execution could not be recovered"
            )
        return self._response(execution, result)

    def get_execution(self, experiment_id: str, candidate_id: str) -> dict[str, Any]:
        _, candidate_index, candidate = self._resolve_candidate(experiment_id, candidate_id)
        execution = self._executions.get_execution_by_candidate(experiment_id, candidate_index)
        if execution is None:
            raise ExperimentExecutionApiError(
                404, "EXECUTION_NOT_FOUND", "candidate execution was not found"
            )
        if execution.candidate_id != candidate_id or execution.parameter_set_hash != (
            candidate.parameter_set_hash
        ):
            raise ExperimentExecutionApiError(
                409, "CANDIDATE_EXECUTION_CONFLICT", "candidate execution identity conflicts"
            )
        return execution.to_dict()

    def get_result(self, experiment_id: str, candidate_id: str) -> dict[str, Any]:
        _, _, candidate = self._resolve_candidate(experiment_id, candidate_id)
        result = self._results.get_by_candidate(experiment_id, candidate_id)
        if result is None:
            raise ExperimentExecutionApiError(
                404, "RESULT_NOT_FOUND", "experiment result was not found"
            )
        if result.parameter_set_hash != candidate.parameter_set_hash:
            raise ExperimentExecutionApiError(
                409, "RESULT_CONFLICT", "stored result does not match the candidate"
            )
        return result.to_dict()

    def _resolve_candidate(self, experiment_id: str, candidate_id: str):
        experiment = self._experiments.get(experiment_id)
        if experiment is None:
            raise ExperimentExecutionApiError(
                404, "EXPERIMENT_NOT_FOUND", "experiment was not found"
            )
        candidates = self._experiments.list_candidates(experiment_id)
        for candidate in candidates:
            expected = candidate_id_for(
                experiment_id, candidate.candidate_index, candidate.parameter_set_hash
            )
            if expected == candidate_id:
                return experiment, candidate.candidate_index, candidate
        raise ExperimentExecutionApiError(404, "CANDIDATE_NOT_FOUND", "candidate was not found")

    def _frozen_bindings(self, experiment: Any) -> ParameterBindingSet:
        provenance = experiment.provenance
        if provenance is None or provenance.parameter_bindings is None:
            raise ExperimentExecutionApiError(
                422,
                "FROZEN_BINDINGS_MISSING",
                "experiment does not contain frozen parameter bindings",
            )
        try:
            return ParameterBindingSet.from_dict(
                {"bindings": list(provenance.parameter_bindings)}
            )
        except Exception as exc:
            raise ExperimentExecutionApiError(
                422, "FROZEN_BINDINGS_INVALID", "experiment frozen parameter bindings are invalid"
            ) from exc

    def _validate_result_identity(
        self, result: Any, experiment: Any, candidate: Any, candidate_id: str
    ) -> None:
        if any(
            (
                result.experiment_id != experiment.experiment_id,
                result.candidate_id != candidate_id,
                result.candidate_index != candidate.candidate_index,
                result.parameter_set_hash != candidate.parameter_set_hash,
                result.parameter_space_hash != experiment.parameter_space_hash,
                result.candidate_set_hash != candidate.candidate_set_hash,
                result.base_strategy_version_id != experiment.base_strategy_version_id,
                result.base_strategy_version_hash != experiment.base_strategy_version_hash,
            )
        ):
            raise ExperimentExecutionApiError(
                409, "RESULT_CONFLICT", "stored result provenance conflicts with the experiment"
            )

    def _ensure_completed_result_execution(
        self, experiment_id: str, candidate_index: int, result: Any
    ) -> Any:
        execution = self._executions.get_execution_by_candidate(experiment_id, candidate_index)
        if execution is None:
            raise ExperimentExecutionApiError(
                409, "EXECUTION_NOT_FOUND", "result exists without its candidate execution"
            )
        if execution.status is CandidateExecutionStatus.COMPLETED:
            return execution
        now = self._now()
        if execution.status is CandidateExecutionStatus.RUNNING and (
            execution.lease_expires_at is not None and execution.lease_expires_at <= now
        ):
            self._executions.recover_expired_executions(now)
            execution = self._executions.get_execution(execution.execution_id)
        if execution is None:
            raise ExperimentExecutionApiError(
                503, "EXECUTION_NOT_FOUND", "execution recovery failed"
            )
        if execution.status is CandidateExecutionStatus.RUNNING:
            raise ExperimentExecutionApiError(
                409, "ALREADY_RUNNING", "candidate execution is already running"
            )
        if execution.status is CandidateExecutionStatus.PENDING:
            execution = self._executions.claim_candidate(
                experiment_id,
                candidate_index,
                self.ACTOR,
                now,
                now + timedelta(minutes=5),
            )
        raise ExperimentExecutionApiError(
            409, "EXECUTION_STATE_CONFLICT", "stored result cannot repair candidate execution state"
        )

    @staticmethod
    def _response(execution: Any, result: Any) -> dict[str, Any]:
        return {
            "candidate_id": result.candidate_id,
            "execution": execution.to_dict(),
            "result": result.to_dict(),
        }

    def _now(self) -> datetime:
        value = self._clock()
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("clock must return a timezone-aware datetime")
        return value


def create_default_experiment_execution_orchestrator() -> ExperimentExecutionOrchestrator:
    """Build the local SQLite/Tiingo-backed API orchestration graph lazily."""
    database = Path(__file__).resolve().parents[2] / "data" / "strategy.db"
    experiments = ExperimentRepository(database)
    protocols = ResearchProtocolRepository(database)
    strategies = StrategyRepository(database)
    executions = CandidateExecutionRepository(database)
    results = ExperimentResultRepository(database)
    backtests = BacktestRepository(database)
    settings = TiingoSettings.from_environment()
    data_service = HistoricalDataService(TiingoClient(settings), DiskCache())
    execution_service = ExperimentExecutionService(
        experiment_repository=experiments,
        protocol_repository=protocols,
        strategy_repository=strategies,
        candidate_execution_repository=executions,
        data_service=data_service,
    )
    finalizer = ExperimentResultFinalizationService(
        backtest_repository=backtests,
        experiment_result_repository=results,
        candidate_execution_repository=executions,
    )
    return ExperimentExecutionOrchestrator(
        experiment_repository=experiments,
        execution_repository=executions,
        result_repository=results,
        execution_service=execution_service,
        finalization_service=finalizer,
    )


__all__ = [
    "ExperimentExecutionApiError",
    "ExperimentExecutionOrchestrator",
    "create_default_experiment_execution_orchestrator",
]
