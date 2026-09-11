"""Read-only composition service for PHASE 8E-1A experiment results."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from backend.app.backtest_repository import BacktestRepository
from backend.app.candidate_execution_repository import CandidateExecutionRepository
from backend.app.experiment_repository import ExperimentPersistenceError, ExperimentRepository
from backend.app.experiment_result_repository import ExperimentResultRepository
from backend.app.strategy_repository import StrategyRepository
from research.canonical import canonical_json, sha256_hash
from research.enums import ExperimentStatus
from research.execution import CandidateExecutionStatus, candidate_id_for
from research.experiment_read_model import (
    ExperimentCandidateView,
    ExperimentResultsReadModel,
    ExperimentResultsSummary,
    ExperimentResultStatus,
)


class ExperimentResultsReadModelError(RuntimeError):
    """Safe, structured error for read-model resolution failures."""

    def __init__(self, message: str, *, code: str, details: Mapping[str, Any] | None = None):
        super().__init__(message)
        self.code = code
        self.details = dict(details or {})


@dataclass(frozen=True)
class _ResolvedResult:
    result: Any
    execution: Any
    backtest: Any
    strategy: Any


class ExperimentResultsReadService:
    """Compose canonical persisted sources without recalculating analytics."""

    def __init__(
        self,
        experiment_repository: ExperimentRepository,
        candidate_execution_repository: CandidateExecutionRepository,
        experiment_result_repository: ExperimentResultRepository,
        backtest_repository: BacktestRepository,
        strategy_repository: StrategyRepository,
    ) -> None:
        self._experiments = experiment_repository
        self._executions = candidate_execution_repository
        self._results = experiment_result_repository
        self._backtests = backtest_repository
        self._strategies = strategy_repository

    def get(
        self, experiment_id: str, *, protocol_id: str | None = None
    ) -> ExperimentResultsReadModel:
        experiment = self._experiments.get(experiment_id)
        if experiment is None:
            raise ExperimentResultsReadModelError(
                "experiment was not found", code="EXPERIMENT_NOT_FOUND"
            )
        if protocol_id is not None and experiment.protocol_id != protocol_id:
            raise ExperimentResultsReadModelError(
                "experiment does not belong to the requested protocol",
                code="EXPERIMENT_PROTOCOL_MISMATCH",
            )

        try:
            candidates = self._experiments.list_candidates(experiment_id)
            candidate_set = self._experiments.get_candidate_set(experiment_id)
        except ExperimentPersistenceError as exc:
            raise ExperimentResultsReadModelError(
                "experiment candidate data failed integrity checks",
                code="EXPERIMENT_RESULT_INTEGRITY_ERROR",
            ) from exc
        if candidate_set is None and candidates:
            raise self._integrity("candidate set is missing")
        candidate_set_hash = candidate_set.candidate_set_hash if candidate_set else None
        views: list[ExperimentCandidateView] = []
        for expected_index, candidate in enumerate(candidates):
            self._validate_candidate(experiment, candidate, candidate_set_hash, expected_index)
            views.append(self._build_candidate_view(experiment, candidate))

        summary = self._summary(experiment.status, views)
        if experiment.status is ExperimentStatus.COMPLETED and not summary.complete:
            raise self._integrity("completed experiment has incomplete candidate outcomes")
        return ExperimentResultsReadModel(
            experiment_id=experiment.experiment_id,
            protocol_id=experiment.protocol_id,
            experiment_status=experiment.status.value,
            is_start=experiment.is_start_date,
            is_end=experiment.is_end_date,
            parameter_space_hash=experiment.parameter_space_hash,
            objective_spec_hash=experiment.objective_spec_hash,
            base_strategy_version_id=experiment.base_strategy_version_id,
            base_strategy_version_hash=experiment.base_strategy_version_hash,
            engine_version=experiment.engine_version,
            analysis_version=experiment.analysis_version,
            candidates=tuple(views),
            summary=summary,
        )

    def get_candidate(
        self, experiment_id: str, candidate_id: str, *, protocol_id: str | None = None
    ) -> ExperimentCandidateView:
        model = self.get(experiment_id, protocol_id=protocol_id)
        for candidate in model.candidates:
            if candidate.candidate_id == candidate_id:
                return candidate
        raise ExperimentResultsReadModelError(
            "candidate was not found", code="CANDIDATE_NOT_FOUND"
        )

    def _build_candidate_view(self, experiment: Any, candidate: Any) -> ExperimentCandidateView:
        execution = self._executions.get_execution_by_candidate(
            experiment.experiment_id, candidate.candidate_index
        )
        result = self._results.get_by_candidate(
            experiment.experiment_id,
            candidate_id_for(
                experiment.experiment_id,
                candidate.candidate_index,
                candidate.parameter_set_hash,
            ),
        )
        base = dict(
            experiment_id=experiment.experiment_id,
            protocol_id=experiment.protocol_id,
            candidate_id=candidate_id_for(
                experiment.experiment_id, candidate.candidate_index, candidate.parameter_set_hash
            ),
            candidate_index=candidate.candidate_index,
            parameter_set=candidate.parameter_set.to_dict(),
            parameter_set_hash=candidate.parameter_set_hash,
            candidate_set_hash=candidate.candidate_set_hash,
            parameter_space_hash=experiment.parameter_space_hash,
            objective_spec_hash=experiment.objective_spec_hash,
            execution_status=execution.status.value if execution else None,
        )
        if execution is None:
            if result is not None:
                return self._inconsistent(base, "result exists without candidate execution")
            return ExperimentCandidateView(
                **base, result_status=ExperimentResultStatus.MISSING_RESULT
            )
        if execution.experiment_id != experiment.experiment_id or execution.candidate_id != base[
            "candidate_id"
        ] or execution.candidate_index != candidate.candidate_index:
            return self._inconsistent(base, "candidate execution identity does not match candidate")
        if execution.status is CandidateExecutionStatus.FAILED:
            if result is not None:
                return self._inconsistent(base, "failed execution has an experiment result")
            status = (
                ExperimentResultStatus.NOT_EVALUABLE
                if execution.failure_code == "INDICATOR_NOT_EVALUABLE"
                else ExperimentResultStatus.FAILED
            )
            return ExperimentCandidateView(
                **base,
                result_status=status,
                failure_code=execution.failure_code,
                failure_summary=self._safe_failure(execution.failure_message),
                created_at=execution.created_at,
                completed_at=execution.failed_at,
            )
        if execution.status is not CandidateExecutionStatus.COMPLETED:
            if result is not None:
                return self._inconsistent(base, "non-terminal execution has an experiment result")
            return ExperimentCandidateView(
                **base, result_status=ExperimentResultStatus.MISSING_RESULT
            )
        if result is None:
            return ExperimentCandidateView(
                **base, result_status=ExperimentResultStatus.MISSING_RESULT
            )
        try:
            resolved = self._resolve_result(experiment, candidate, execution, result)
        except ExperimentResultsReadModelError as exc:
            return self._inconsistent(base, str(exc), code=exc.code)
        strategy_provenance = resolved.strategy.materialization_provenance
        return ExperimentCandidateView(
            **base,
            result_status=ExperimentResultStatus.COMPLETED,
            experiment_result_id=result.experiment_result_id,
            result_hash=result.result_hash,
            backtest_run_id=result.backtest_run_id,
            base_strategy_version_id=result.base_strategy_version_id,
            base_strategy_version_hash=result.base_strategy_version_hash,
            derived_strategy_version_id=result.derived_strategy_version_id,
            derived_strategy_version_hash=result.derived_strategy_version_hash,
            binding_hash=result.binding_hash,
            materialization_spec_hash=(
                strategy_provenance.materialization_spec_hash if strategy_provenance else None
            ),
            is_start=result.is_start,
            is_end=result.is_end,
            warmup_start=result.warmup_start,
            warmup_end=result.warmup_end,
            price_field_used=result.price_field_used,
            backtest_configuration_hash=result.backtest_configuration_hash,
            engine_version=result.engine_version,
            analysis_version=result.analysis_version,
            data_snapshot_reference=result.data_snapshot_reference,
            configuration_snapshot=resolved.backtest.backtest_result.configuration_snapshot,
            performance_summary=result.performance_summary,
            created_at=execution.created_at,
            completed_at=execution.completed_at,
        )

    def _resolve_result(
        self, experiment: Any, candidate: Any, execution: Any, result: Any
    ) -> _ResolvedResult:
        expected_id = candidate_id_for(
            experiment.experiment_id, candidate.candidate_index, candidate.parameter_set_hash
        )
        if any(
            (
            result.experiment_id != experiment.experiment_id,
            result.candidate_id != expected_id,
            result.candidate_index != candidate.candidate_index,
            result.parameter_set_hash != candidate.parameter_set_hash,
            result.candidate_set_hash != candidate.candidate_set_hash,
            result.parameter_space_hash != experiment.parameter_space_hash,
            result.base_strategy_version_id != experiment.base_strategy_version_id,
            result.base_strategy_version_hash != experiment.base_strategy_version_hash,
            result.is_start != experiment.is_start_date,
            result.is_end != experiment.is_end_date,
            result.engine_version != experiment.engine_version,
            result.analysis_version != experiment.analysis_version,
            result.binding_hash != execution.parameter_binding_hash,
            execution.derived_strategy_version_id != result.derived_strategy_version_id,
            execution.derived_strategy_version_hash != result.derived_strategy_version_hash,
            )
        ):
            raise self._integrity("experiment result identity does not match candidate")
        strategy = self._strategies.get_any_version(result.derived_strategy_version_id)
        if strategy is None or strategy.content_hash != result.derived_strategy_version_hash:
            raise ExperimentResultsReadModelError(
                "derived strategy version is missing or has a hash mismatch",
                code="STRATEGY_VERSION_INTEGRITY_ERROR",
            )
        provenance = strategy.materialization_provenance
        if (
            provenance is None
            or provenance.base_strategy_version_id != experiment.base_strategy_version_id
        ):
            raise self._integrity("derived strategy provenance is inconsistent")
        if (
            provenance.parameter_set_hash != result.parameter_set_hash
            or provenance.binding_hash != result.binding_hash
        ):
            raise self._integrity("derived strategy binding provenance is inconsistent")
        backtest = self._backtests.get(result.backtest_run_id)
        if backtest is None:
            raise ExperimentResultsReadModelError(
                "backtest run was not found", code="BACKTEST_RUN_NOT_FOUND"
            )
        analysis = backtest.performance_analysis
        if any(
            (
            backtest.strategy_version_id != result.derived_strategy_version_id,
            backtest.strategy_version_content_hash != result.derived_strategy_version_hash,
            backtest.backtest_result.start_date != result.is_start,
            backtest.backtest_result.end_date != result.is_end,
            analysis.start_date != result.is_start,
            analysis.end_date != result.is_end,
            analysis.price_field_used is not result.price_field_used,
            backtest.backtest_result.engine_version != result.engine_version,
            self._analytics_payload(analysis.to_dict())
            != self._analytics_payload(dict(result.performance_summary)),
            )
        ):
            raise self._integrity("backtest run identity does not match experiment result")
        snapshot = dict(backtest.backtest_result.configuration_snapshot)
        frozen_snapshot = experiment.backtest_configuration.snapshot({})
        snapshot.pop("data_snapshot_reference", None)
        frozen_snapshot.pop("data_snapshot_reference", None)
        derived_version_id = snapshot.pop("strategy_version_id", None)
        frozen_snapshot.pop("strategy_version_id", None)
        if any(
            (
                derived_version_id != result.derived_strategy_version_id,
                snapshot != frozen_snapshot,
                result.backtest_configuration_hash
                != sha256_hash(experiment.backtest_configuration.snapshot({})),
            )
        ):
            raise self._integrity("backtest configuration identity does not match result")
        return _ResolvedResult(result, execution, backtest, strategy)

    @staticmethod
    def _validate_candidate(
        experiment: Any, candidate: Any, candidate_set_hash: str | None, index: int
    ) -> None:
        if any(
            (
            candidate.experiment_id != experiment.experiment_id,
            candidate.candidate_index != index,
            candidate.parameter_set_hash != candidate.parameter_set.content_hash,
            candidate_set_hash is not None and candidate.candidate_set_hash != candidate_set_hash,
            )
        ):
            raise ExperimentResultsReadModelError(
                "candidate binding is inconsistent", code="EXPERIMENT_RESULT_INTEGRITY_ERROR"
            )
        expected = candidate_id_for(experiment.experiment_id, index, candidate.parameter_set_hash)
        if not expected:
            raise ExperimentResultsReadModelError(
                "candidate identity is invalid", code="EXPERIMENT_RESULT_INTEGRITY_ERROR"
            )

    @staticmethod
    def _summary(
        status: ExperimentStatus, views: list[ExperimentCandidateView]
    ) -> ExperimentResultsSummary:
        completed = sum(item.result_status is ExperimentResultStatus.COMPLETED for item in views)
        failed = sum(item.result_status is ExperimentResultStatus.FAILED for item in views)
        not_evaluable = sum(
            item.result_status is ExperimentResultStatus.NOT_EVALUABLE for item in views
        )
        running = sum(
            item.execution_status == CandidateExecutionStatus.RUNNING.value for item in views
        )
        pending = sum(
            item.execution_status == CandidateExecutionStatus.PENDING.value for item in views
        )
        results = sum(item.experiment_result_id is not None for item in views)
        complete = all(
            item.execution_status in {
                CandidateExecutionStatus.COMPLETED.value,
                CandidateExecutionStatus.FAILED.value,
            }
            and item.result_status in {
                ExperimentResultStatus.COMPLETED,
                ExperimentResultStatus.FAILED,
                ExperimentResultStatus.NOT_EVALUABLE,
            }
            for item in views
        )
        return ExperimentResultsSummary(
            candidate_count=len(views),
            completed_count=completed,
            failed_count=failed,
            not_evaluable_count=not_evaluable,
            running_count=running,
            pending_count=pending,
            result_count=results,
            complete=complete,
            completeness_status="complete" if complete else "partial",
        )

    @staticmethod
    def _safe_failure(message: str | None) -> str:
        if not message:
            return "candidate execution failed"
        first_line = message.splitlines()[0].strip()[:256]
        lowered = first_line.lower()
        if any(
            marker in lowered
            for marker in ("api_key", "authorization", "traceback", "/users/", "\\")
        ):
            return "candidate execution failed"
        return first_line or "candidate execution failed"

    @staticmethod
    def _inconsistent(
        base: dict[str, Any],
        message: str,
        *,
        code: str = "EXPERIMENT_RESULT_INTEGRITY_ERROR",
    ) -> ExperimentCandidateView:
        return ExperimentCandidateView(
            **base,
            result_status=ExperimentResultStatus.INCONSISTENT,
            failure_code=code,
            failure_summary=message[:256],
        )

    @staticmethod
    def _integrity(message: str) -> ExperimentResultsReadModelError:
        return ExperimentResultsReadModelError(message, code="EXPERIMENT_RESULT_INTEGRITY_ERROR")

    @staticmethod
    def _analytics_payload(payload: Mapping[str, Any]) -> str:
        """Compare backend analytics while ignoring only post-run identity fields."""
        normalized = dict(payload)
        normalized.pop("backtest_run_id", None)
        normalized.pop("strategy_id", None)
        return canonical_json(normalized)


__all__ = ["ExperimentResultsReadModelError", "ExperimentResultsReadService"]
