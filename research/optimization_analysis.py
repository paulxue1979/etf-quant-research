"""Deterministic, IS-only analysis over persisted optimization summaries.

This module never executes a strategy, backtest, selection, freeze, or OOS run.
It projects canonical analytics already persisted by the experiment pipeline.
"""

from __future__ import annotations

import math
import statistics
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum
from types import MappingProxyType
from typing import Any

from research.candidates import parameter_values
from research.canonical import canonical_json
from research.enums import MetricDirection
from research.experiments import ParameterSpace

OPTIMIZATION_ANALYSIS_SCHEMA_VERSION = "phase-11i.1"


class OptimizationAnalysisError(ValueError):
    """A safe validation or integrity error for optimization research."""

    def __init__(self, message: str, *, code: str = "INVALID_OPTIMIZATION_REQUEST") -> None:
        super().__init__(message)
        self.code = code


class MetricAvailability(StrEnum):
    AVAILABLE = "available"
    UNAVAILABLE = "unavailable"
    NOT_APPLICABLE = "not_applicable"
    FAILED = "failed"


class MetricFormat(StrEnum):
    PERCENT = "percent"
    RATIO = "ratio"
    INTEGER = "integer"
    CURRENCY = "currency"
    DAYS = "days"


@dataclass(frozen=True)
class ResearchMetricDefinition:
    metric_id: str
    display_name: str
    source_path: tuple[str, ...]
    direction: MetricDirection
    format: MetricFormat
    category: str
    nullable: bool = True
    heatmap_eligible: bool = True
    pareto_eligible: bool = True

    def to_dict(self) -> dict[str, Any]:
        return {
            "metric_id": self.metric_id,
            "display_name": self.display_name,
            "source_path": list(self.source_path),
            "direction": self.direction.value,
            "format": self.format.value,
            "category": self.category,
            "nullable": self.nullable,
            "heatmap_eligible": self.heatmap_eligible,
            "pareto_eligible": self.pareto_eligible,
        }


_METRICS = (
    ResearchMetricDefinition(
        "cagr", "CAGR", ("cagr",), MetricDirection.MAXIMIZE, MetricFormat.PERCENT, "strategy"
    ),
    ResearchMetricDefinition(
        "total_return",
        "Total TWR Return",
        ("total_return",),
        MetricDirection.MAXIMIZE,
        MetricFormat.PERCENT,
        "strategy",
    ),
    ResearchMetricDefinition(
        "max_drawdown",
        "Max Drawdown",
        ("max_drawdown",),
        MetricDirection.MAXIMIZE,
        MetricFormat.PERCENT,
        "risk",
    ),
    ResearchMetricDefinition(
        "sharpe_ratio",
        "Sharpe Ratio",
        ("sharpe_ratio",),
        MetricDirection.MAXIMIZE,
        MetricFormat.RATIO,
        "risk_adjusted",
    ),
    ResearchMetricDefinition(
        "sortino_ratio",
        "Sortino Ratio",
        ("sortino_ratio",),
        MetricDirection.MAXIMIZE,
        MetricFormat.RATIO,
        "risk_adjusted",
    ),
    ResearchMetricDefinition(
        "calmar_ratio",
        "Calmar Ratio",
        ("calmar_ratio",),
        MetricDirection.MAXIMIZE,
        MetricFormat.RATIO,
        "risk_adjusted",
    ),
    ResearchMetricDefinition(
        "annualized_volatility",
        "Annualized Volatility",
        ("annualized_volatility",),
        MetricDirection.MINIMIZE,
        MetricFormat.PERCENT,
        "risk",
    ),
    ResearchMetricDefinition(
        "turnover",
        "Realized Turnover",
        ("turnover",),
        MetricDirection.MINIMIZE,
        MetricFormat.PERCENT,
        "trading",
    ),
    ResearchMetricDefinition(
        "trade_count",
        "Closed Trades",
        ("trade_metrics", "number_of_closed_trades"),
        MetricDirection.MAXIMIZE,
        MetricFormat.INTEGER,
        "trading",
        pareto_eligible=False,
    ),
    ResearchMetricDefinition(
        "average_holding_period",
        "Average Holding Period",
        ("trade_metrics", "average_holding_period"),
        MetricDirection.MAXIMIZE,
        MetricFormat.DAYS,
        "trading",
        pareto_eligible=False,
    ),
    ResearchMetricDefinition(
        "average_gross_exposure",
        "Average Gross Exposure",
        ("exposure_summary", "average_gross_exposure"),
        MetricDirection.MINIMIZE,
        MetricFormat.PERCENT,
        "exposure",
    ),
    ResearchMetricDefinition(
        "time_invested_fraction",
        "Time Invested",
        ("exposure_summary", "time_invested_fraction"),
        MetricDirection.MAXIMIZE,
        MetricFormat.PERCENT,
        "exposure",
    ),
    ResearchMetricDefinition(
        "xirr",
        "XIRR",
        ("xirr",),
        MetricDirection.MAXIMIZE,
        MetricFormat.PERCENT,
        "investor_experience",
    ),
    ResearchMetricDefinition(
        "final_equity",
        "Final Portfolio Value",
        ("final_equity",),
        MetricDirection.MAXIMIZE,
        MetricFormat.CURRENCY,
        "investor_experience",
        pareto_eligible=False,
    ),
)
METRIC_REGISTRY: Mapping[str, ResearchMetricDefinition] = MappingProxyType(
    {item.metric_id: item for item in _METRICS}
)


def metric_definition(metric_id: str) -> ResearchMetricDefinition:
    try:
        return METRIC_REGISTRY[metric_id]
    except (KeyError, TypeError) as exc:
        raise OptimizationAnalysisError(
            "metric_id is not in the research metric allowlist", code="METRIC_NOT_ALLOWED"
        ) from exc


@dataclass(frozen=True)
class ProjectedMetric:
    metric_id: str
    value: float | None
    status: MetricAvailability
    reason: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "metric_id": self.metric_id,
            "value": self.value,
            "status": self.status.value,
            "reason": self.reason,
        }


@dataclass(frozen=True)
class OptimizationCandidateSummary:
    experiment_id: str
    candidate_id: str
    candidate_index: int
    parameter_set_hash: str
    candidate_set_hash: str
    parameter_values: Mapping[str, Any]
    execution_status: str
    result_status: str
    experiment_result_id: str | None
    result_hash: str | None
    derived_strategy_version_id: str | None
    derived_strategy_version_hash: str | None
    backtest_configuration_hash: str | None
    backtest_run_id: str | None
    is_start: str | None
    is_end: str | None
    engine_version: str | None
    analysis_version: str | None
    performance_summary: Mapping[str, Any]
    failure_code: str | None = None
    failure_summary: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "parameter_values", MappingProxyType(dict(sorted(self.parameter_values.items())))
        )
        object.__setattr__(
            self, "performance_summary", MappingProxyType(dict(self.performance_summary))
        )

    def metric(self, metric_id: str) -> ProjectedMetric:
        definition = metric_definition(metric_id)
        if self.result_status == "failed" or self.execution_status == "failed":
            return ProjectedMetric(
                metric_id,
                None,
                MetricAvailability.FAILED,
                self.failure_code or "CANDIDATE_FAILED",
            )
        if self.result_status != "completed":
            return ProjectedMetric(
                metric_id, None, MetricAvailability.UNAVAILABLE, "RESULT_NOT_COMPLETED"
            )
        raw: object = self.performance_summary
        for key in definition.source_path:
            if not isinstance(raw, Mapping) or key not in raw:
                return ProjectedMetric(
                    metric_id, None, MetricAvailability.UNAVAILABLE, "METRIC_NOT_PERSISTED"
                )
            raw = raw[key]
        if isinstance(raw, Mapping):
            status = raw.get("status")
            if status == "not_evaluable":
                reason = raw.get("reason")
                return ProjectedMetric(
                    metric_id,
                    None,
                    MetricAvailability.NOT_APPLICABLE,
                    str(reason) if reason else "METRIC_NOT_EVALUABLE",
                )
            if status != "available" or "value" not in raw:
                return ProjectedMetric(
                    metric_id, None, MetricAvailability.UNAVAILABLE, "INVALID_METRIC_CONTRACT"
                )
            raw = raw["value"]
        if isinstance(raw, bool) or not isinstance(raw, (int, float)) or not math.isfinite(raw):
            return ProjectedMetric(
                metric_id, None, MetricAvailability.UNAVAILABLE, "INVALID_METRIC_VALUE"
            )
        return ProjectedMetric(metric_id, float(raw), MetricAvailability.AVAILABLE)

    def to_dict(self, metric_ids: Sequence[str] | None = None) -> dict[str, Any]:
        selected = tuple(metric_ids) if metric_ids is not None else tuple(METRIC_REGISTRY)
        return {
            "experiment_id": self.experiment_id,
            "candidate_id": self.candidate_id,
            "candidate_index": self.candidate_index,
            "parameter_set_hash": self.parameter_set_hash,
            "candidate_set_hash": self.candidate_set_hash,
            "parameter_values": dict(self.parameter_values),
            "execution_status": self.execution_status,
            "result_status": self.result_status,
            "experiment_result_id": self.experiment_result_id,
            "result_hash": self.result_hash,
            "derived_strategy_version_id": self.derived_strategy_version_id,
            "derived_strategy_version_hash": self.derived_strategy_version_hash,
            "backtest_configuration_hash": self.backtest_configuration_hash,
            "backtest_run_id": self.backtest_run_id,
            "is_start": self.is_start,
            "is_end": self.is_end,
            "engine_version": self.engine_version,
            "analysis_version": self.analysis_version,
            "failure_code": self.failure_code,
            "failure_summary": self.failure_summary,
            "metrics": {metric_id: self.metric(metric_id).to_dict() for metric_id in selected},
        }


@dataclass(frozen=True)
class ParameterFilter:
    exact: Any | None = None
    minimum: float | None = None
    maximum: float | None = None


@dataclass(frozen=True)
class CandidateFilter:
    statuses: tuple[str, ...] = ()
    candidate_ids: tuple[str, ...] = ()
    minimum_trade_count: int | None = None
    maximum_turnover: float | None = None
    maximum_drawdown_magnitude: float | None = None
    minimum_cagr: float | None = None
    minimum_sharpe: float | None = None
    minimum_exposure: float | None = None
    maximum_exposure: float | None = None
    parameters: Mapping[str, ParameterFilter] = MappingProxyType({})

    def __post_init__(self) -> None:
        allowed_statuses = {"pending", "running", "completed", "failed"}
        if not set(self.statuses).issubset(allowed_statuses):
            raise OptimizationAnalysisError("status filter contains an unsupported value")
        if self.minimum_trade_count is not None and (
            isinstance(self.minimum_trade_count, bool) or self.minimum_trade_count < 0
        ):
            raise OptimizationAnalysisError("minimum_trade_count must be non-negative")
        for name in (
            "maximum_turnover",
            "maximum_drawdown_magnitude",
            "minimum_cagr",
            "minimum_sharpe",
            "minimum_exposure",
            "maximum_exposure",
        ):
            value = getattr(self, name)
            if value is not None and (isinstance(value, bool) or not math.isfinite(float(value))):
                raise OptimizationAnalysisError(f"{name} must be finite")
        object.__setattr__(self, "parameters", MappingProxyType(dict(self.parameters)))

    def to_dict(self) -> dict[str, Any]:
        return {
            "statuses": list(self.statuses),
            "candidate_ids": list(self.candidate_ids),
            "minimum_trade_count": self.minimum_trade_count,
            "maximum_turnover": self.maximum_turnover,
            "maximum_drawdown_magnitude": self.maximum_drawdown_magnitude,
            "minimum_cagr": self.minimum_cagr,
            "minimum_sharpe": self.minimum_sharpe,
            "minimum_exposure": self.minimum_exposure,
            "maximum_exposure": self.maximum_exposure,
            "parameters": {
                key: {"exact": value.exact, "minimum": value.minimum, "maximum": value.maximum}
                for key, value in sorted(self.parameters.items())
            },
        }


def apply_candidate_filter(
    candidates: Sequence[OptimizationCandidateSummary],
    candidate_filter: CandidateFilter,
    parameter_space: ParameterSpace,
) -> tuple[tuple[OptimizationCandidateSummary, ...], dict[str, tuple[str, ...]]]:
    definitions = {item.name: item for item in parameter_space.parameters}
    if not set(candidate_filter.parameters).issubset(definitions):
        raise OptimizationAnalysisError("parameter filter contains an unknown parameter")
    included: list[OptimizationCandidateSummary] = []
    excluded: dict[str, tuple[str, ...]] = {}
    for candidate in sorted(candidates, key=lambda item: (item.candidate_index, item.candidate_id)):
        reasons: list[str] = []
        status = candidate.result_status if candidate.result_status else candidate.execution_status
        if candidate_filter.statuses and status not in candidate_filter.statuses:
            reasons.append("STATUS_FILTER")
        if (
            candidate_filter.candidate_ids
            and candidate.candidate_id not in candidate_filter.candidate_ids
        ):
            reasons.append("CANDIDATE_ID_FILTER")
        thresholds = (
            (
                "trade_count",
                candidate_filter.minimum_trade_count,
                lambda value, limit: value >= limit,
            ),
            ("turnover", candidate_filter.maximum_turnover, lambda value, limit: value <= limit),
            ("cagr", candidate_filter.minimum_cagr, lambda value, limit: value >= limit),
            ("sharpe_ratio", candidate_filter.minimum_sharpe, lambda value, limit: value >= limit),
            (
                "average_gross_exposure",
                candidate_filter.minimum_exposure,
                lambda value, limit: value >= limit,
            ),
            (
                "average_gross_exposure",
                candidate_filter.maximum_exposure,
                lambda value, limit: value <= limit,
            ),
        )
        for metric_id, limit, predicate in thresholds:
            if limit is None:
                continue
            metric = candidate.metric(metric_id)
            if metric.status is not MetricAvailability.AVAILABLE:
                reasons.append(f"{metric_id.upper()}_UNAVAILABLE")
            elif not predicate(metric.value, limit):
                reasons.append(f"{metric_id.upper()}_FILTER")
        if candidate_filter.maximum_drawdown_magnitude is not None:
            metric = candidate.metric("max_drawdown")
            if metric.status is not MetricAvailability.AVAILABLE:
                reasons.append("MAX_DRAWDOWN_UNAVAILABLE")
            elif abs(metric.value) > candidate_filter.maximum_drawdown_magnitude:
                reasons.append("MAX_DRAWDOWN_FILTER")
        for name, constraint in sorted(candidate_filter.parameters.items()):
            value = candidate.parameter_values[name]
            if constraint.exact is not None and canonical_json(value) != canonical_json(
                constraint.exact
            ):
                reasons.append(f"PARAMETER_FILTER:{name}")
            if constraint.minimum is not None and (
                isinstance(value, bool)
                or not isinstance(value, (int, float))
                or value < constraint.minimum
            ):
                reasons.append(f"PARAMETER_MINIMUM:{name}")
            if constraint.maximum is not None and (
                isinstance(value, bool)
                or not isinstance(value, (int, float))
                or value > constraint.maximum
            ):
                reasons.append(f"PARAMETER_MAXIMUM:{name}")
        if reasons:
            excluded[candidate.candidate_id] = tuple(dict.fromkeys(reasons))
        else:
            included.append(candidate)
    return tuple(included), excluded


def _domains(parameter_space: ParameterSpace) -> dict[str, tuple[Any, ...]]:
    return {item.name: parameter_values(item) for item in parameter_space.parameters}


def _coordinate(values: Mapping[str, Any], names: Sequence[str]) -> tuple[str, ...]:
    return tuple(canonical_json(values[name]) for name in names)


def _validate_slice(
    parameter_space: ParameterSpace,
    varying: set[str],
    fixed_values: Mapping[str, Any],
) -> None:
    definitions = {item.name: item for item in parameter_space.parameters}
    if not varying.issubset(definitions):
        raise OptimizationAnalysisError("analysis references an unknown parameter")
    expected = set(definitions) - varying
    if set(fixed_values) != expected:
        raise OptimizationAnalysisError(
            "fixed_parameter_values must cover every non-axis parameter exactly",
            code="AMBIGUOUS_SLICE",
        )
    for name, value in fixed_values.items():
        definitions[name].validate_value(value)


def build_heatmap(
    candidates: Sequence[OptimizationCandidateSummary],
    parameter_space: ParameterSpace,
    *,
    x_parameter: str,
    y_parameter: str,
    metric_id: str,
    fixed_parameter_values: Mapping[str, Any],
    candidate_filter: CandidateFilter = CandidateFilter(),
) -> dict[str, Any]:
    definition = metric_definition(metric_id)
    if not definition.heatmap_eligible:
        raise OptimizationAnalysisError("metric is not heatmap eligible")
    if x_parameter == y_parameter:
        raise OptimizationAnalysisError("heatmap axes must be different parameters")
    _validate_slice(parameter_space, {x_parameter, y_parameter}, fixed_parameter_values)
    domains = _domains(parameter_space)
    included, exclusions = apply_candidate_filter(candidates, candidate_filter, parameter_space)
    included_ids = {item.candidate_id for item in included}
    names = tuple(item.name for item in parameter_space.parameters)
    candidate_index: dict[tuple[str, ...], list[OptimizationCandidateSummary]] = {}
    for candidate in candidates:
        candidate_index.setdefault(_coordinate(candidate.parameter_values, names), []).append(
            candidate
        )
    cells: list[dict[str, Any]] = []
    for y_value in domains[y_parameter]:
        for x_value in domains[x_parameter]:
            values = {**fixed_parameter_values, x_parameter: x_value, y_parameter: y_value}
            matches = candidate_index.get(_coordinate(values, names), [])
            if len(matches) > 1:
                raise OptimizationAnalysisError(
                    "heatmap cell maps to multiple candidates", code="AMBIGUOUS_HEATMAP_CELL"
                )
            if not matches:
                cells.append(
                    {
                        "x_value": x_value,
                        "y_value": y_value,
                        "candidate_id": None,
                        "candidate_index": None,
                        "parameter_set_hash": None,
                        "metric": ProjectedMetric(
                            metric_id,
                            None,
                            MetricAvailability.UNAVAILABLE,
                            "PRUNED_OR_NOT_GENERATED",
                        ).to_dict(),
                        "cell_status": "pruned",
                        "supporting_metrics": {},
                    }
                )
                continue
            candidate = matches[0]
            metric = candidate.metric(metric_id)
            status = metric.status.value
            reason = metric.reason
            if candidate.candidate_id not in included_ids:
                status = "excluded"
                reason = ",".join(exclusions[candidate.candidate_id])
            cells.append(
                {
                    "x_value": x_value,
                    "y_value": y_value,
                    "candidate_id": candidate.candidate_id,
                    "candidate_index": candidate.candidate_index,
                    "parameter_set_hash": candidate.parameter_set_hash,
                    "metric": {**metric.to_dict(), "status": status, "reason": reason},
                    "cell_status": status,
                    "supporting_metrics": {
                        key: candidate.metric(key).to_dict()
                        for key in ("cagr", "max_drawdown", "turnover", "trade_count")
                    },
                }
            )
    return {
        "schema_version": OPTIMIZATION_ANALYSIS_SCHEMA_VERSION,
        "experiment_id": candidates[0].experiment_id if candidates else None,
        "metric": definition.to_dict(),
        "x_parameter": x_parameter,
        "y_parameter": y_parameter,
        "x_values": list(domains[x_parameter]),
        "y_values": list(domains[y_parameter]),
        "fixed_parameter_values": dict(sorted(fixed_parameter_values.items())),
        "applied_filter": candidate_filter.to_dict(),
        "source_count": len(candidates),
        "filtered_count": len(included),
        "cells": cells,
        "aggregation": None,
        "interpolation": False,
    }


def calculate_pareto(
    candidates: Sequence[OptimizationCandidateSummary],
    parameter_space: ParameterSpace,
    *,
    objective_ids: Sequence[str],
    candidate_filter: CandidateFilter = CandidateFilter(),
) -> dict[str, Any]:
    if not 2 <= len(objective_ids) <= 5 or len(set(objective_ids)) != len(objective_ids):
        raise OptimizationAnalysisError("Pareto analysis requires two to five unique objectives")
    definitions = tuple(metric_definition(item) for item in objective_ids)
    if not all(item.pareto_eligible for item in definitions):
        raise OptimizationAnalysisError("one or more metrics are not Pareto eligible")
    filtered, filter_exclusions = apply_candidate_filter(
        candidates, candidate_filter, parameter_space
    )
    vectors: list[tuple[OptimizationCandidateSummary, tuple[float, ...]]] = []
    analysis_exclusions: dict[str, tuple[str, ...]] = {}
    for candidate in filtered:
        metrics = tuple(candidate.metric(item.metric_id) for item in definitions)
        reasons = tuple(
            f"{metric.metric_id}:{metric.status.value}:{metric.reason or 'NO_VALUE'}"
            for metric in metrics
            if metric.status is not MetricAvailability.AVAILABLE
        )
        if reasons:
            analysis_exclusions[candidate.candidate_id] = reasons
        else:
            vectors.append((candidate, tuple(metric.value for metric in metrics)))

    def dominates(left: tuple[float, ...], right: tuple[float, ...]) -> bool:
        no_worse: list[bool] = []
        strictly_better: list[bool] = []
        for index, definition in enumerate(definitions):
            if definition.direction is MetricDirection.MAXIMIZE:
                no_worse.append(left[index] >= right[index])
                strictly_better.append(left[index] > right[index])
            else:
                no_worse.append(left[index] <= right[index])
                strictly_better.append(left[index] < right[index])
        return all(no_worse) and any(strictly_better)

    frontier: list[OptimizationCandidateSummary] = []
    dominated: list[OptimizationCandidateSummary] = []
    for candidate, vector in vectors:
        if any(
            other.candidate_id != candidate.candidate_id and dominates(other_vector, vector)
            for other, other_vector in vectors
        ):
            dominated.append(candidate)
        else:
            frontier.append(candidate)
    metric_ids = tuple(item.metric_id for item in definitions)

    def candidate_key(candidate: OptimizationCandidateSummary) -> tuple[int, str]:
        return candidate.candidate_index, candidate.candidate_id

    def result_item(candidate: OptimizationCandidateSummary) -> dict[str, Any]:
        return {
            "candidate_id": candidate.candidate_id,
            "candidate_index": candidate.candidate_index,
            "parameter_values": dict(candidate.parameter_values),
            "metrics": {
                metric_id: candidate.metric(metric_id).to_dict() for metric_id in metric_ids
            },
        }

    return {
        "schema_version": OPTIMIZATION_ANALYSIS_SCHEMA_VERSION,
        "experiment_id": candidates[0].experiment_id if candidates else None,
        "objectives": [definition.to_dict() for definition in definitions],
        "source_count": len(candidates),
        "filtered_count": len(filtered),
        "eligible_count": len(vectors),
        "excluded_count": len(filter_exclusions) + len(analysis_exclusions),
        "frontier": [result_item(candidate) for candidate in sorted(frontier, key=candidate_key)],
        "dominated": [result_item(candidate) for candidate in sorted(dominated, key=candidate_key)],
        "exclusions": {
            **{key: list(value) for key, value in sorted(filter_exclusions.items())},
            **{key: list(value) for key, value in sorted(analysis_exclusions.items())},
        },
        "applied_filter": candidate_filter.to_dict(),
    }


def analyze_stability(
    candidates: Sequence[OptimizationCandidateSummary],
    parameter_space: ParameterSpace,
    *,
    center_candidate_id: str,
    metric_id: str,
    candidate_filter: CandidateFilter = CandidateFilter(),
) -> dict[str, Any]:
    definition = metric_definition(metric_id)
    filtered, exclusions = apply_candidate_filter(candidates, candidate_filter, parameter_space)
    centers = {item.candidate_id: item for item in filtered}
    if center_candidate_id not in centers:
        reason = exclusions.get(center_candidate_id, ("CANDIDATE_NOT_FOUND",))
        raise OptimizationAnalysisError(
            "center candidate is outside the filtered research universe: " + ",".join(reason),
            code="CENTER_CANDIDATE_NOT_ELIGIBLE",
        )
    center = centers[center_candidate_id]
    domains = _domains(parameter_space)
    names = tuple(item.name for item in parameter_space.parameters)
    index = {_coordinate(item.parameter_values, names): item for item in candidates}
    neighbors: list[dict[str, Any]] = []
    available_values: list[float] = []
    deterioration: list[float] = []
    center_metric = center.metric(metric_id)
    for name in names:
        domain = domains[name]
        value_key = canonical_json(center.parameter_values[name])
        position = tuple(canonical_json(value) for value in domain).index(value_key)
        for target_position in (position - 1, position + 1):
            if not 0 <= target_position < len(domain):
                continue
            expected_values = dict(center.parameter_values)
            expected_values[name] = domain[target_position]
            neighbor = index.get(_coordinate(expected_values, names))
            if neighbor is None:
                metric = ProjectedMetric(
                    metric_id, None, MetricAvailability.UNAVAILABLE, "PRUNED_OR_NOT_GENERATED"
                )
                candidate_id = None
                candidate_index = None
                status = "pruned"
            else:
                metric = neighbor.metric(metric_id)
                candidate_id = neighbor.candidate_id
                candidate_index = neighbor.candidate_index
                status = metric.status.value
                if metric.status is MetricAvailability.AVAILABLE:
                    available_values.append(metric.value)
                    if center_metric.status is MetricAvailability.AVAILABLE:
                        delta = (
                            center_metric.value - metric.value
                            if definition.direction is MetricDirection.MAXIMIZE
                            else metric.value - center_metric.value
                        )
                        deterioration.append(delta)
            neighbors.append(
                {
                    "changed_parameter": name,
                    "from_value": center.parameter_values[name],
                    "to_value": domain[target_position],
                    "candidate_id": candidate_id,
                    "candidate_index": candidate_index,
                    "status": status,
                    "metric": metric.to_dict(),
                }
            )
    stats: dict[str, float | int | None] = {
        "neighbor_count_expected": len(neighbors),
        "neighbor_count_available": len(available_values),
        "center_value": center_metric.value,
        "neighbor_median": None,
        "neighbor_min": None,
        "neighbor_max": None,
        "neighbor_mean": None,
        "neighbor_std": None,
        "neighbor_mad": None,
        "worst_neighbor_deterioration": None,
        "median_neighbor_deterioration": None,
    }
    if available_values:
        median = statistics.median(available_values)
        stats.update(
            {
                "neighbor_median": median,
                "neighbor_min": min(available_values),
                "neighbor_max": max(available_values),
                "neighbor_mean": statistics.fmean(available_values),
                "neighbor_std": statistics.pstdev(available_values),
                "neighbor_mad": statistics.median(
                    abs(value - median) for value in available_values
                ),
            }
        )
    if deterioration:
        stats["worst_neighbor_deterioration"] = max(deterioration)
        stats["median_neighbor_deterioration"] = statistics.median(deterioration)
    return {
        "schema_version": OPTIMIZATION_ANALYSIS_SCHEMA_VERSION,
        "experiment_id": center.experiment_id,
        "center_candidate_id": center.candidate_id,
        "metric": definition.to_dict(),
        "center_metric": center_metric.to_dict(),
        "statistics": stats,
        "neighbors": neighbors,
        "topology": "one_parameter_one_canonical_step",
        "topology_uses_filtered_universe": False,
        "applied_filter": candidate_filter.to_dict(),
    }


def build_sensitivity(
    candidates: Sequence[OptimizationCandidateSummary],
    parameter_space: ParameterSpace,
    *,
    parameter: str,
    metric_ids: Sequence[str],
    fixed_parameter_values: Mapping[str, Any],
    candidate_filter: CandidateFilter = CandidateFilter(),
) -> dict[str, Any]:
    if not metric_ids or len(set(metric_ids)) != len(metric_ids):
        raise OptimizationAnalysisError("sensitivity requires unique metric IDs")
    definitions = tuple(metric_definition(metric_id) for metric_id in metric_ids)
    _validate_slice(parameter_space, {parameter}, fixed_parameter_values)
    domains = _domains(parameter_space)
    included, exclusions = apply_candidate_filter(candidates, candidate_filter, parameter_space)
    included_ids = {item.candidate_id for item in included}
    names = tuple(item.name for item in parameter_space.parameters)
    index = {_coordinate(item.parameter_values, names): item for item in candidates}
    points: list[dict[str, Any]] = []
    for value in domains[parameter]:
        values = {**fixed_parameter_values, parameter: value}
        candidate = index.get(_coordinate(values, names))
        if candidate is None:
            points.append(
                {
                    "parameter_value": value,
                    "candidate_id": None,
                    "candidate_index": None,
                    "status": "pruned",
                    "metrics": {
                        item.metric_id: ProjectedMetric(
                            item.metric_id,
                            None,
                            MetricAvailability.UNAVAILABLE,
                            "PRUNED_OR_NOT_GENERATED",
                        ).to_dict()
                        for item in definitions
                    },
                }
            )
            continue
        metrics = {item.metric_id: candidate.metric(item.metric_id) for item in definitions}
        status = "available"
        if candidate.candidate_id not in included_ids:
            status = "excluded"
            reason = ",".join(exclusions[candidate.candidate_id])
            metrics = {
                key: ProjectedMetric(key, value.value, value.status, reason)
                for key, value in metrics.items()
            }
        elif any(item.status is MetricAvailability.FAILED for item in metrics.values()):
            status = "failed"
        elif any(item.status is not MetricAvailability.AVAILABLE for item in metrics.values()):
            status = "unavailable"
        points.append(
            {
                "parameter_value": value,
                "candidate_id": candidate.candidate_id,
                "candidate_index": candidate.candidate_index,
                "status": status,
                "metrics": {key: metric.to_dict() for key, metric in metrics.items()},
            }
        )
    return {
        "schema_version": OPTIMIZATION_ANALYSIS_SCHEMA_VERSION,
        "experiment_id": candidates[0].experiment_id if candidates else None,
        "parameter": parameter,
        "parameter_values": list(domains[parameter]),
        "metrics": [item.to_dict() for item in definitions],
        "fixed_parameter_values": dict(sorted(fixed_parameter_values.items())),
        "points": points,
        "applied_filter": candidate_filter.to_dict(),
        "interpolation": False,
    }


__all__ = [
    "CandidateFilter",
    "METRIC_REGISTRY",
    "MetricAvailability",
    "OptimizationAnalysisError",
    "OptimizationCandidateSummary",
    "ParameterFilter",
    "ProjectedMetric",
    "ResearchMetricDefinition",
    "analyze_stability",
    "apply_candidate_filter",
    "build_heatmap",
    "build_sensitivity",
    "calculate_pareto",
    "metric_definition",
]
