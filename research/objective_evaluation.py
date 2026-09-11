"""Read-only objective and hard-constraint evaluation for PHASE 8E-1C."""

from __future__ import annotations

import math
import re
from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from types import MappingProxyType
from typing import Any

from analytics.models import MetricStatus
from research.canonical import canonical_json
from research.enums import ConstraintOperator, MetricDirection
from research.experiment_read_model import ExperimentCandidateView, ExperimentResultStatus
from research.experiments import Experiment

_HASH = re.compile(r"^[0-9a-f]{64}$")
_UNSAFE_TEXT = re.compile(
    r"(?:TIINGO_API_KEY|API_KEY|SECRET_SENTINEL|BEGIN\s+PRIVATE\s+KEY|authorization\s*:|"
    r"bearer\s+|traceback|/users/|/private/|\\)",
    re.IGNORECASE,
)


class ObjectiveEvaluationState(StrEnum):
    """State of one constraint or the aggregate hard-constraint result."""

    PASS = "pass"
    FAIL = "fail"
    NOT_EVALUABLE = "not_evaluable"


class ObjectiveEvaluationReasonCode(StrEnum):
    """Stable, non-sensitive reasons for a non-pass evaluation."""

    CONSTRAINT_SATISFIED = "CONSTRAINT_SATISFIED"
    CONSTRAINT_FAILED = "CONSTRAINT_FAILED"
    METRIC_NOT_EVALUABLE = "METRIC_NOT_EVALUABLE"
    RESULT_NOT_AVAILABLE = "RESULT_NOT_AVAILABLE"
    EXECUTION_FAILED = "EXECUTION_FAILED"
    EXPERIMENT_RESULT_INTEGRITY_ERROR = "EXPERIMENT_RESULT_INTEGRITY_ERROR"


class ObjectiveEvaluationError(RuntimeError):
    """Structured error for invalid frozen objective/result bindings."""

    def __init__(self, message: str, *, code: str):
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class ConstraintEvaluation:
    """Immutable evaluation of one frozen hard constraint."""

    metric: str
    operator: ConstraintOperator
    threshold: float
    metric_value: float | None
    metric_status: MetricStatus
    state: ObjectiveEvaluationState
    reason_code: ObjectiveEvaluationReasonCode
    reason: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "operator", ConstraintOperator(self.operator))
        object.__setattr__(self, "state", ObjectiveEvaluationState(self.state))
        object.__setattr__(self, "reason_code", ObjectiveEvaluationReasonCode(self.reason_code))
        if not isinstance(self.metric, str) or not self.metric.strip():
            raise ValueError("metric must be a non-empty string")
        if not math.isfinite(float(self.threshold)):
            raise ValueError("threshold must be finite")
        if self.metric_value is not None and not math.isfinite(float(self.metric_value)):
            raise ValueError("metric_value must be finite or None")
        if not isinstance(self.metric_status, MetricStatus):
            object.__setattr__(self, "metric_status", MetricStatus(self.metric_status))
        if not isinstance(self.reason, str) or not self.reason.strip():
            raise ValueError("reason must be non-empty")
        if _UNSAFE_TEXT.search(self.reason):
            raise ValueError("reason contains unsafe text")
        object.__setattr__(self, "threshold", float(self.threshold))
        if self.metric_value is not None:
            object.__setattr__(self, "metric_value", float(self.metric_value))
        object.__setattr__(self, "reason", self.reason.strip()[:256])

    def to_dict(self) -> dict[str, Any]:
        return {
            "metric": self.metric,
            "operator": self.operator.value,
            "threshold": self.threshold,
            "metric_value": self.metric_value,
            "metric_status": self.metric_status.value,
            "state": self.state.value,
            "reason_code": self.reason_code.value,
            "reason": self.reason,
        }

    def canonical_json(self) -> str:
        """Return deterministic JSON for audit and replay comparisons."""
        return canonical_json(self.to_dict())


@dataclass(frozen=True)
class CandidateObjectiveEvaluation:
    """Immutable, non-ranking objective interpretation for one candidate."""

    experiment_id: str
    candidate_id: str
    objective_hash: str
    experiment_result_id: str | None
    result_hash: str | None
    constraint_results: tuple[ConstraintEvaluation, ...]
    overall_constraint_state: ObjectiveEvaluationState

    def __post_init__(self) -> None:
        for label in ("experiment_id", "candidate_id"):
            value = getattr(self, label)
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"{label} must be non-empty")
            object.__setattr__(self, label, value.strip())
        if not isinstance(self.objective_hash, str) or not _HASH.fullmatch(self.objective_hash):
            raise ValueError("objective_hash must be a SHA-256 hex digest")
        for label in ("experiment_result_id", "result_hash"):
            value = getattr(self, label)
            if value is not None and (not isinstance(value, str) or not value.strip()):
                raise ValueError(f"{label} must be non-empty or None")
        if self.result_hash is not None and not _HASH.fullmatch(self.result_hash):
            raise ValueError("result_hash must be a SHA-256 hex digest or None")
        constraints = tuple(self.constraint_results)
        if not all(isinstance(item, ConstraintEvaluation) for item in constraints):
            raise TypeError("constraint_results must contain ConstraintEvaluation values")
        if tuple(item.metric for item in constraints) != tuple(
            sorted(item.metric for item in constraints)
        ):
            raise ValueError("constraint_results must use deterministic metric order")
        object.__setattr__(self, "constraint_results", constraints)
        object.__setattr__(
            self,
            "overall_constraint_state",
            ObjectiveEvaluationState(self.overall_constraint_state),
        )

    @property
    def constraints(self) -> tuple[ConstraintEvaluation, ...]:
        """Compatibility alias for callers using the shorter domain name."""
        return self.constraint_results

    def to_dict(self) -> dict[str, Any]:
        return {
            "experiment_id": self.experiment_id,
            "candidate_id": self.candidate_id,
            "objective_hash": self.objective_hash,
            "experiment_result_id": self.experiment_result_id,
            "result_hash": self.result_hash,
            "constraint_results": [item.to_dict() for item in self.constraint_results],
            "overall_constraint_state": self.overall_constraint_state.value,
        }

    def canonical_json(self) -> str:
        """Return deterministic JSON without ranking or selection fields."""
        return canonical_json(self.to_dict())


# These are the canonical analytics identifiers emitted by PerformanceAnalysisResult.
_METRIC_PATHS: Mapping[str, tuple[str, ...]] = MappingProxyType(
    {
        "total_return": ("total_return",),
        "cagr": ("cagr",),
        "annualized_volatility": ("annualized_volatility",),
        "sharpe_ratio": ("sharpe_ratio",),
        "sortino_ratio": ("sortino_ratio",),
        "max_drawdown": ("max_drawdown",),
        "max_drawdown_duration": ("max_drawdown_duration",),
        "recovery_duration": ("recovery_duration",),
        "calmar_ratio": ("calmar_ratio",),
        "win_rate": ("trade_metrics", "win_rate"),
        "profit_factor": ("trade_metrics", "profit_factor"),
        "average_trade_return": ("trade_metrics", "average_trade_return"),
        "best_trade": ("trade_metrics", "best_trade"),
        "worst_trade": ("trade_metrics", "worst_trade"),
        "average_holding_period": ("trade_metrics", "average_holding_period"),
        "turnover": ("trade_metrics", "turnover"),
    }
)


def evaluate_candidate_objective(
    experiment: Experiment, candidate: ExperimentCandidateView
) -> CandidateObjectiveEvaluation:
    """Evaluate frozen hard constraints against one persisted IS result view.

    The objective is always resolved from ``experiment``. No caller-provided
    objective, threshold, metric value, OOS payload, or ranking input is accepted.
    """
    if not isinstance(experiment, Experiment):
        raise TypeError("experiment must be an Experiment")
    if not isinstance(candidate, ExperimentCandidateView):
        raise TypeError("candidate must be an ExperimentCandidateView")
    objective = experiment.objective_specification
    try:
        objective_hash = objective.content_hash
    except (TypeError, ValueError) as exc:
        raise ObjectiveEvaluationError(
            "frozen objective cannot be canonically hashed", code="OBJECTIVE_HASH_MISMATCH"
        ) from exc
    if experiment.objective_spec_hash != objective_hash:
        raise ObjectiveEvaluationError(
            "frozen objective hash does not match its content", code="OBJECTIVE_HASH_MISMATCH"
        )
    if candidate.experiment_id != experiment.experiment_id:
        raise ObjectiveEvaluationError(
            "candidate is bound to a different experiment", code="EXPERIMENT_RESULT_INTEGRITY_ERROR"
        )
    if candidate.objective_spec_hash != objective_hash:
        raise ObjectiveEvaluationError(
            "candidate objective hash does not match the frozen experiment",
            code="OBJECTIVE_HASH_MISMATCH",
        )
    _validate_objective(objective.hard_constraints, objective.metric_directions)
    if candidate.result_status is not ExperimentResultStatus.COMPLETED:
        return _unavailable_evaluation(experiment, candidate)
    if not candidate.experiment_result_id or not candidate.result_hash:
        raise ObjectiveEvaluationError(
            "completed candidate has no immutable experiment result binding",
            code="EXPERIMENT_RESULT_NOT_AVAILABLE",
        )
    if not _HASH.fullmatch(candidate.result_hash):
        raise ObjectiveEvaluationError(
            "experiment result hash is invalid", code="EXPERIMENT_RESULT_INTEGRITY_ERROR"
        )

    evaluations = tuple(
        _evaluate_constraint(metric, threshold, objective.metric_directions[metric], candidate)
        for metric, threshold in objective.hard_constraints.items()
    )
    return CandidateObjectiveEvaluation(
        experiment_id=experiment.experiment_id,
        candidate_id=candidate.candidate_id,
        objective_hash=objective_hash,
        experiment_result_id=candidate.experiment_result_id,
        result_hash=candidate.result_hash,
        constraint_results=evaluations,
        overall_constraint_state=_overall_state(evaluations),
    )


def _validate_objective(
    constraints: Mapping[str, object], directions: Mapping[str, MetricDirection]
) -> None:
    """Validate persisted objective inputs before evaluating any candidate state."""
    for metric, threshold in constraints.items():
        if metric not in _METRIC_PATHS:
            raise ObjectiveEvaluationError(
                f"unsupported objective metric: {metric}", code="OBJECTIVE_METRIC_UNSUPPORTED"
            )
        if metric not in directions:
            raise ObjectiveEvaluationError(
                f"objective direction is missing: {metric}", code="OBJECTIVE_INTEGRITY_ERROR"
            )
        _validated_threshold(metric, threshold)


def evaluate_objective(
    experiment: Experiment, candidate: ExperimentCandidateView
) -> CandidateObjectiveEvaluation:
    """Short alias for the public PHASE 8E-1C evaluator."""
    return evaluate_candidate_objective(experiment, candidate)


def _evaluate_constraint(
    metric: str,
    threshold: object,
    direction: MetricDirection,
    candidate: ExperimentCandidateView,
) -> ConstraintEvaluation:
    if isinstance(threshold, bool) or not isinstance(threshold, (int, float)):
        raise ObjectiveEvaluationError(
            f"threshold for {metric} is invalid", code="OBJECTIVE_THRESHOLD_INVALID"
        )
    threshold_value = float(threshold)
    if not math.isfinite(threshold_value):
        raise ObjectiveEvaluationError(
            f"threshold for {metric} is non-finite", code="OBJECTIVE_THRESHOLD_INVALID"
        )
    metric_value, metric_status, metric_reason = _metric_value(
        candidate.performance_summary, metric
    )
    operator = (
        ConstraintOperator.GREATER_OR_EQUAL
        if direction is MetricDirection.MAXIMIZE
        else ConstraintOperator.LESS_OR_EQUAL
    )
    if metric_status is MetricStatus.NOT_EVALUABLE:
        return ConstraintEvaluation(
            metric=metric,
            operator=operator,
            threshold=threshold_value,
            metric_value=None,
            metric_status=metric_status,
            state=ObjectiveEvaluationState.NOT_EVALUABLE,
            reason_code=ObjectiveEvaluationReasonCode.METRIC_NOT_EVALUABLE,
            reason=metric_reason,
        )
    passed = _compare(metric_value, operator, threshold_value)
    return ConstraintEvaluation(
        metric=metric,
        operator=operator,
        threshold=threshold_value,
        metric_value=metric_value,
        metric_status=metric_status,
        state=ObjectiveEvaluationState.PASS if passed else ObjectiveEvaluationState.FAIL,
        reason_code=(
            ObjectiveEvaluationReasonCode.CONSTRAINT_SATISFIED
            if passed
            else ObjectiveEvaluationReasonCode.CONSTRAINT_FAILED
        ),
        reason=(
            "metric satisfies frozen hard constraint"
            if passed
            else "metric does not satisfy frozen hard constraint"
        ),
    )


def _metric_value(
    summary: Mapping[str, Any], metric: str
) -> tuple[float | None, MetricStatus, str]:
    path = _METRIC_PATHS.get(metric)
    if path is None:
        raise ObjectiveEvaluationError(
            f"unsupported objective metric: {metric}", code="OBJECTIVE_METRIC_UNSUPPORTED"
        )
    current: Any = summary
    for component in path:
        if not isinstance(current, Mapping) or component not in current:
            raise ObjectiveEvaluationError(
                f"metric is missing from experiment result: {metric}",
                code="EXPERIMENT_RESULT_INTEGRITY_ERROR",
            )
        current = current[component]
    if not isinstance(current, Mapping):
        raise ObjectiveEvaluationError(
            f"metric payload is invalid: {metric}", code="EXPERIMENT_RESULT_INTEGRITY_ERROR"
        )
    status = current.get("status")
    try:
        metric_status = MetricStatus(status)
    except (TypeError, ValueError) as exc:
        raise ObjectiveEvaluationError(
            f"metric status is invalid: {metric}", code="EXPERIMENT_RESULT_INTEGRITY_ERROR"
        ) from exc
    if metric_status is MetricStatus.NOT_EVALUABLE:
        return None, metric_status, _safe_metric_reason(current.get("reason"))
    value = current.get("value")
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not math.isfinite(float(value))
    ):
        raise ObjectiveEvaluationError(
            f"metric value is invalid: {metric}", code="EXPERIMENT_RESULT_INTEGRITY_ERROR"
        )
    return float(value), metric_status, "metric is available"


def _safe_metric_reason(value: object) -> str:
    if not isinstance(value, str) or not value.strip() or _UNSAFE_TEXT.search(value):
        return "metric is not evaluable"
    return value.strip().splitlines()[0][:256]


def _compare(value: float | None, operator: ConstraintOperator, threshold: float) -> bool:
    if value is None:
        return False
    if operator is ConstraintOperator.LESS_THAN:
        return value < threshold
    if operator is ConstraintOperator.LESS_OR_EQUAL:
        return value <= threshold
    if operator is ConstraintOperator.GREATER_THAN:
        return value > threshold
    if operator is ConstraintOperator.GREATER_OR_EQUAL:
        return value >= threshold
    if operator is ConstraintOperator.EQUAL:
        return value == threshold
    return value != threshold


def _unavailable_evaluation(
    experiment: Experiment, candidate: ExperimentCandidateView
) -> CandidateObjectiveEvaluation:
    if (
        candidate.execution_status == "failed"
        or candidate.result_status is ExperimentResultStatus.FAILED
    ):
        code = ObjectiveEvaluationReasonCode.EXECUTION_FAILED
        reason = "candidate execution failed; constraints are not evaluable"
    elif candidate.result_status is ExperimentResultStatus.INCONSISTENT:
        code = ObjectiveEvaluationReasonCode.EXPERIMENT_RESULT_INTEGRITY_ERROR
        reason = "candidate result is inconsistent; constraints are not evaluable"
    else:
        code = ObjectiveEvaluationReasonCode.RESULT_NOT_AVAILABLE
        reason = "candidate experiment result is not available"
    evaluations = tuple(
        ConstraintEvaluation(
            metric=metric,
            operator=(
                ConstraintOperator.GREATER_OR_EQUAL
                if direction is MetricDirection.MAXIMIZE
                else ConstraintOperator.LESS_OR_EQUAL
            ),
            threshold=_validated_threshold(metric, threshold),
            metric_value=None,
            metric_status=MetricStatus.NOT_EVALUABLE,
            state=ObjectiveEvaluationState.NOT_EVALUABLE,
            reason_code=code,
            reason=reason,
        )
        for metric, threshold in experiment.objective_specification.hard_constraints.items()
        for direction in (experiment.objective_specification.metric_directions[metric],)
    )
    return CandidateObjectiveEvaluation(
        experiment_id=experiment.experiment_id,
        candidate_id=candidate.candidate_id,
        objective_hash=experiment.objective_spec_hash,
        experiment_result_id=candidate.experiment_result_id,
        result_hash=candidate.result_hash,
        constraint_results=evaluations,
        overall_constraint_state=ObjectiveEvaluationState.NOT_EVALUABLE,
    )


def _validated_threshold(metric: str, value: object) -> float:
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not math.isfinite(float(value))
    ):
        raise ObjectiveEvaluationError(
            f"threshold for {metric} is invalid", code="OBJECTIVE_THRESHOLD_INVALID"
        )
    return float(value)


def _overall_state(evaluations: tuple[ConstraintEvaluation, ...]) -> ObjectiveEvaluationState:
    if any(item.state is ObjectiveEvaluationState.FAIL for item in evaluations):
        return ObjectiveEvaluationState.FAIL
    if any(item.state is ObjectiveEvaluationState.NOT_EVALUABLE for item in evaluations):
        return ObjectiveEvaluationState.NOT_EVALUABLE
    return ObjectiveEvaluationState.PASS


__all__ = [
    "CandidateObjectiveEvaluation",
    "ConstraintEvaluation",
    "ObjectiveEvaluationError",
    "ObjectiveEvaluationReasonCode",
    "ObjectiveEvaluationState",
    "evaluate_candidate_objective",
    "evaluate_objective",
]
