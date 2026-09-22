"""Application service for deterministic, IS-only optimization research."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from backend.app.experiment_repository import ExperimentRepository
from backend.app.optimization_research_repository import OptimizationResearchRepository
from research.optimization_analysis import (
    METRIC_REGISTRY,
    CandidateFilter,
    OptimizationAnalysisError,
    ParameterFilter,
    analyze_stability,
    apply_candidate_filter,
    build_heatmap,
    build_sensitivity,
    calculate_pareto,
)


class OptimizationResearchService:
    """Load one frozen experiment universe and apply read-only analysis."""

    def __init__(
        self,
        experiment_repository: ExperimentRepository,
        optimization_repository: OptimizationResearchRepository,
    ) -> None:
        self._experiments = experiment_repository
        self._optimization = optimization_repository

    def metric_registry(self) -> dict[str, Any]:
        return {
            "schema_version": "phase-11i.1",
            "metrics": [item.to_dict() for item in METRIC_REGISTRY.values()],
            "max_drawdown_semantics": "canonical_negative_value_closer_to_zero_is_better",
            "turnover_semantics": "analytics_canonical_realized_turnover",
        }

    def results(
        self,
        protocol_id: str,
        experiment_id: str,
        candidate_filter: CandidateFilter,
        metric_ids: Sequence[str] | None = None,
    ) -> dict[str, Any]:
        experiment, candidates = self._load(protocol_id, experiment_id)
        selected_metric_ids = tuple(metric_ids) if metric_ids else tuple(METRIC_REGISTRY)
        for metric_id in selected_metric_ids:
            if metric_id not in METRIC_REGISTRY:
                raise OptimizationAnalysisError(
                    "metric_id is not in the research metric allowlist", code="METRIC_NOT_ALLOWED"
                )
        filtered, exclusions = apply_candidate_filter(
            candidates, candidate_filter, experiment.parameter_space
        )
        return {
            "schema_version": "phase-11i.1",
            "experiment_id": experiment.experiment_id,
            "protocol_id": experiment.protocol_id,
            "is_start": experiment.is_start_date.isoformat(),
            "is_end": experiment.is_end_date.isoformat(),
            "is_only": True,
            "parameter_space": experiment.parameter_space.to_dict(),
            "parameter_space_hash": experiment.parameter_space_hash,
            "source_count": len(candidates),
            "filtered_count": len(filtered),
            "applied_filter": candidate_filter.to_dict(),
            "candidates": [item.to_dict(selected_metric_ids) for item in filtered],
            "exclusions": {key: list(value) for key, value in sorted(exclusions.items())},
        }

    def heatmap(
        self,
        protocol_id: str,
        experiment_id: str,
        *,
        x_parameter: str,
        y_parameter: str,
        metric_id: str,
        fixed_parameter_values: Mapping[str, Any],
        candidate_filter: CandidateFilter,
    ) -> dict[str, Any]:
        experiment, candidates = self._load(protocol_id, experiment_id)
        return build_heatmap(
            candidates,
            experiment.parameter_space,
            x_parameter=x_parameter,
            y_parameter=y_parameter,
            metric_id=metric_id,
            fixed_parameter_values=fixed_parameter_values,
            candidate_filter=candidate_filter,
        )

    def pareto(
        self,
        protocol_id: str,
        experiment_id: str,
        *,
        objective_ids: Sequence[str],
        candidate_filter: CandidateFilter,
    ) -> dict[str, Any]:
        experiment, candidates = self._load(protocol_id, experiment_id)
        return calculate_pareto(
            candidates,
            experiment.parameter_space,
            objective_ids=objective_ids,
            candidate_filter=candidate_filter,
        )

    def stability(
        self,
        protocol_id: str,
        experiment_id: str,
        *,
        center_candidate_id: str,
        metric_id: str,
        candidate_filter: CandidateFilter,
    ) -> dict[str, Any]:
        experiment, candidates = self._load(protocol_id, experiment_id)
        return analyze_stability(
            candidates,
            experiment.parameter_space,
            center_candidate_id=center_candidate_id,
            metric_id=metric_id,
            candidate_filter=candidate_filter,
        )

    def sensitivity(
        self,
        protocol_id: str,
        experiment_id: str,
        *,
        parameter: str,
        metric_ids: Sequence[str],
        fixed_parameter_values: Mapping[str, Any],
        candidate_filter: CandidateFilter,
    ) -> dict[str, Any]:
        experiment, candidates = self._load(protocol_id, experiment_id)
        return build_sensitivity(
            candidates,
            experiment.parameter_space,
            parameter=parameter,
            metric_ids=metric_ids,
            fixed_parameter_values=fixed_parameter_values,
            candidate_filter=candidate_filter,
        )

    def _load(self, protocol_id: str, experiment_id: str):
        experiment = self._experiments.get(experiment_id)
        if experiment is None:
            raise OptimizationAnalysisError("experiment was not found", code="EXPERIMENT_NOT_FOUND")
        if experiment.protocol_id != protocol_id:
            raise OptimizationAnalysisError(
                "experiment does not belong to the requested protocol",
                code="EXPERIMENT_PROTOCOL_MISMATCH",
            )
        return experiment, self._optimization.list_candidate_summaries(experiment)


def candidate_filter_from_dict(payload: Mapping[str, Any] | None) -> CandidateFilter:
    data = payload or {}
    parameters = {
        name: ParameterFilter(
            exact=value.get("exact"),
            minimum=value.get("minimum"),
            maximum=value.get("maximum"),
        )
        for name, value in data.get("parameters", {}).items()
    }
    return CandidateFilter(
        statuses=tuple(data.get("statuses", ())),
        candidate_ids=tuple(data.get("candidate_ids", ())),
        minimum_trade_count=data.get("minimum_trade_count"),
        maximum_turnover=data.get("maximum_turnover"),
        maximum_drawdown_magnitude=data.get("maximum_drawdown_magnitude"),
        minimum_cagr=data.get("minimum_cagr"),
        minimum_sharpe=data.get("minimum_sharpe"),
        minimum_exposure=data.get("minimum_exposure"),
        maximum_exposure=data.get("maximum_exposure"),
        parameters=parameters,
    )


__all__ = ["OptimizationResearchService", "candidate_filter_from_dict"]
