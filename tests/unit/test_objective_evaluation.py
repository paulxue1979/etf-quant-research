from __future__ import annotations

from dataclasses import replace
from datetime import UTC, date, datetime
from math import inf, nan

import pytest

from backtest import BacktestConfig, RebalanceFrequency, RebalancePolicy
from data.models import PriceField
from research import (
    Experiment,
    ExperimentMethod,
    ExperimentProvenance,
    ExperimentStatus,
    MetricDirection,
    ObjectiveSpecification,
    ParameterSpace,
)
from research.enums import ConstraintOperator
from research.experiment_read_model import ExperimentCandidateView, ExperimentResultStatus
from research.objective_evaluation import (
    ConstraintEvaluation,
    ObjectiveEvaluationError,
    ObjectiveEvaluationReasonCode,
    ObjectiveEvaluationState,
    evaluate_candidate_objective,
)

HASH_A = "a" * 64
HASH_B = "b" * 64
IS_START = date(2020, 1, 2)
IS_END = date(2020, 12, 31)


def _metric(value: float | None, *, reason: str | None = None) -> dict[str, object]:
    return {
        "value": value,
        "status": "available" if value is not None else "not_evaluable",
        "reason": reason,
    }


def _objective() -> ObjectiveSpecification:
    return ObjectiveSpecification(
        primary_metric="cagr",
        secondary_metrics=("max_drawdown", "sharpe_ratio", "total_return"),
        metric_directions={
            "cagr": MetricDirection.MAXIMIZE,
            "max_drawdown": MetricDirection.MINIMIZE,
            "sharpe_ratio": MetricDirection.MAXIMIZE,
            "total_return": MetricDirection.MAXIMIZE,
        },
        hard_constraints={
            "cagr": 0.10,
            "max_drawdown": -0.20,
            "sharpe_ratio": 1.00,
            "total_return": 0.25,
        },
    )


def _experiment(objective: ObjectiveSpecification | None = None) -> Experiment:
    objective = objective or _objective()
    config = BacktestConfig(
        strategy_version_id="base-v1",
        start_date=IS_START,
        end_date=IS_END,
        initial_capital=10_000.0,
        price_field_used=PriceField.ADJUSTED_CLOSE,
        rebalance_policy=RebalancePolicy(RebalanceFrequency.MONTHLY),
    )
    space = ParameterSpace(parameters=(), max_candidates=10)
    return Experiment(
        experiment_id="experiment-1",
        protocol_id="protocol-1",
        strategy_definition_id="strategy-1",
        base_strategy_version_id="base-v1",
        base_strategy_version_hash=HASH_A,
        parameter_space=space,
        objective_specification=objective,
        is_start_date=IS_START,
        is_end_date=IS_END,
        backtest_configuration=config,
        engine_version="phase-3.0",
        analysis_version="phase-4i.0",
        status=ExperimentStatus.COMPLETED,
        created_at=datetime(2026, 9, 11, tzinfo=UTC),
        method=ExperimentMethod.MANUAL,
        provenance=ExperimentProvenance(
            protocol_id="protocol-1",
            strategy_definition_id="strategy-1",
            base_strategy_version_id="base-v1",
            base_strategy_version_hash=HASH_A,
            parameter_space_hash=space.content_hash,
            objective_spec_hash=objective.content_hash,
            is_start_date=IS_START,
            is_end_date=IS_END,
            price_field_used=PriceField.ADJUSTED_CLOSE,
            initial_capital=10_000.0,
            commission={"rate": 0.0, "per_order": 0.0},
            slippage=0.0,
            execution_rule="next_trading_day_open",
            fractional_shares=False,
            rebalance_policy={"frequency": "monthly", "threshold": None},
            engine_version="phase-3.0",
            analysis_version="phase-4i.0",
            data_snapshot_reference={"symbol": "QQQ", "frequency": "daily"},
        ),
    )


def _candidate(
    experiment: Experiment,
    *,
    index: int = 0,
    result_status: ExperimentResultStatus = ExperimentResultStatus.COMPLETED,
    values: dict[str, dict[str, object]] | None = None,
    objective_hash: str | None = None,
    result_hash: str | None = HASH_B,
    execution_status: str = "completed",
) -> ExperimentCandidateView:
    summary = values or {
        "cagr": _metric(0.12),
        "max_drawdown": _metric(-0.25),
        "sharpe_ratio": _metric(1.25),
        "total_return": _metric(0.30),
    }
    completed = result_status is ExperimentResultStatus.COMPLETED
    return ExperimentCandidateView(
        experiment_id=experiment.experiment_id,
        protocol_id=experiment.protocol_id,
        candidate_id=f"candidate-{index}",
        candidate_index=index,
        parameter_set={"period": 20 + index},
        parameter_set_hash=HASH_A,
        candidate_set_hash=HASH_B,
        parameter_space_hash=experiment.parameter_space_hash,
        objective_spec_hash=objective_hash or experiment.objective_spec_hash,
        execution_status=execution_status,
        result_status=result_status,
        experiment_result_id=f"result-{index}" if completed else None,
        result_hash=result_hash if completed else None,
        backtest_run_id=f"backtest-{index}" if completed else None,
        base_strategy_version_id="base-v1",
        base_strategy_version_hash=HASH_A,
        derived_strategy_version_id=f"derived-{index}" if completed else None,
        derived_strategy_version_hash=HASH_B if completed else None,
        binding_hash=HASH_A if completed else None,
        is_start=IS_START if completed else None,
        is_end=IS_END if completed else None,
        price_field_used=PriceField.ADJUSTED_CLOSE if completed else None,
        backtest_configuration_hash=HASH_A if completed else None,
        engine_version="phase-3.0" if completed else None,
        analysis_version="phase-4i.0" if completed else None,
        data_snapshot_reference={"symbol": "QQQ", "frequency": "daily"},
        performance_summary=summary if completed else {},
    )


def test_all_hard_constraints_pass_without_ranking_fields() -> None:
    experiment = _experiment()
    evaluation = evaluate_candidate_objective(experiment, _candidate(experiment))

    assert evaluation.overall_constraint_state is ObjectiveEvaluationState.PASS
    assert [item.metric for item in evaluation.constraints] == [
        "cagr",
        "max_drawdown",
        "sharpe_ratio",
        "total_return",
    ]
    assert all(item.state is ObjectiveEvaluationState.PASS for item in evaluation.constraints)
    assert evaluation.constraints[0].operator is ConstraintOperator.GREATER_OR_EQUAL
    assert evaluation.constraints[1].operator is ConstraintOperator.LESS_OR_EQUAL
    assert "rank" not in evaluation.to_dict()
    assert "score" not in evaluation.to_dict()
    assert "winner" not in evaluation.to_dict()


def test_one_and_multiple_failures_are_deterministic() -> None:
    experiment = _experiment()
    values = {
        "cagr": _metric(0.05),
        "max_drawdown": _metric(-0.10),
        "sharpe_ratio": _metric(0.50),
        "total_return": _metric(0.10),
    }
    first = evaluate_candidate_objective(experiment, _candidate(experiment, values=values))
    second = evaluate_candidate_objective(experiment, _candidate(experiment, values=values))

    assert first.overall_constraint_state is ObjectiveEvaluationState.FAIL
    assert sum(item.state is ObjectiveEvaluationState.FAIL for item in first.constraints) == 4
    assert first.canonical_json() == second.canonical_json()


def test_single_failed_constraint_is_reported_without_a_score() -> None:
    experiment = _experiment()
    values = {
        "cagr": _metric(0.05),
        "max_drawdown": _metric(-0.25),
        "sharpe_ratio": _metric(1.25),
        "total_return": _metric(0.30),
    }
    evaluation = evaluate_candidate_objective(experiment, _candidate(experiment, values=values))

    assert evaluation.overall_constraint_state is ObjectiveEvaluationState.FAIL
    failed_metrics = [
        item.metric
        for item in evaluation.constraints
        if item.state is ObjectiveEvaluationState.FAIL
    ]
    assert failed_metrics == [
        "cagr"
    ]
    assert "score" not in evaluation.to_dict()


def test_pass_and_not_evaluable_is_not_evaluable() -> None:
    experiment = _experiment()
    values = {
        "cagr": _metric(0.12),
        "max_drawdown": _metric(None, reason="insufficient observations"),
        "sharpe_ratio": _metric(1.25),
        "total_return": _metric(0.30),
    }
    evaluation = evaluate_candidate_objective(experiment, _candidate(experiment, values=values))

    assert evaluation.overall_constraint_state is ObjectiveEvaluationState.NOT_EVALUABLE
    assert (
        evaluation.constraints[1].reason_code
        is ObjectiveEvaluationReasonCode.METRIC_NOT_EVALUABLE
    )
    assert evaluation.constraints[1].metric_value is None


def test_fail_and_not_evaluable_remains_fail() -> None:
    experiment = _experiment()
    values = {
        "cagr": _metric(0.05),
        "max_drawdown": _metric(None, reason="insufficient observations"),
        "sharpe_ratio": _metric(1.25),
        "total_return": _metric(0.30),
    }
    evaluation = evaluate_candidate_objective(experiment, _candidate(experiment, values=values))

    assert evaluation.overall_constraint_state is ObjectiveEvaluationState.FAIL


def test_failed_candidate_is_not_evaluable_not_constraint_failure() -> None:
    experiment = _experiment()
    candidate = _candidate(
        experiment,
        result_status=ExperimentResultStatus.FAILED,
        execution_status="failed",
    )
    evaluation = evaluate_candidate_objective(experiment, candidate)

    assert evaluation.overall_constraint_state is ObjectiveEvaluationState.NOT_EVALUABLE
    assert all(
        item.state is ObjectiveEvaluationState.NOT_EVALUABLE for item in evaluation.constraints
    )
    assert all(
        item.reason_code is ObjectiveEvaluationReasonCode.EXECUTION_FAILED
        for item in evaluation.constraints
    )


@pytest.mark.parametrize("metric", ["cagr", "max_drawdown", "sharpe_ratio", "total_return"])
def test_supported_metrics_use_frozen_threshold_and_direction(metric: str) -> None:
    experiment = _experiment()
    evaluation = evaluate_candidate_objective(experiment, _candidate(experiment))
    item = next(result for result in evaluation.constraints if result.metric == metric)

    assert item.threshold == experiment.objective_specification.hard_constraints[metric]
    assert item.metric_status.value == "available"


def test_unsupported_metric_is_not_silently_ignored_even_for_failed_candidate() -> None:
    objective = ObjectiveSpecification(
        primary_metric="unknown_metric",
        metric_directions={"unknown_metric": MetricDirection.MAXIMIZE},
        hard_constraints={"unknown_metric": 1.0},
    )
    experiment = _experiment(objective)

    with pytest.raises(ObjectiveEvaluationError) as error:
        evaluate_candidate_objective(
            experiment,
            _candidate(
                experiment,
                result_status=ExperimentResultStatus.FAILED,
                execution_status="failed",
            ),
        )
    assert error.value.code == "OBJECTIVE_METRIC_UNSUPPORTED"


@pytest.mark.parametrize("non_finite_threshold", [nan, inf, -inf])
def test_persisted_non_finite_threshold_is_rejected(non_finite_threshold: float) -> None:
    experiment = _experiment()
    object.__setattr__(
        experiment.objective_specification,
        "hard_constraints",
        {"cagr": non_finite_threshold},
    )
    with pytest.raises((ValueError, ObjectiveEvaluationError)):
        evaluate_candidate_objective(experiment, _candidate(experiment))


def test_objective_hash_binding_is_required() -> None:
    experiment = _experiment()
    candidate = _candidate(experiment, objective_hash=HASH_B)

    with pytest.raises(ObjectiveEvaluationError) as error:
        evaluate_candidate_objective(experiment, candidate)
    assert error.value.code == "OBJECTIVE_HASH_MISMATCH"


def test_result_hash_binding_is_preserved_and_validated() -> None:
    experiment = _experiment()
    evaluation = evaluate_candidate_objective(experiment, _candidate(experiment))
    assert evaluation.result_hash == HASH_B

    with pytest.raises(ObjectiveEvaluationError) as error:
        evaluate_candidate_objective(experiment, _candidate(experiment, result_hash="bad"))
    assert error.value.code == "EXPERIMENT_RESULT_INTEGRITY_ERROR"


def test_is_only_summary_ignores_oos_payload_and_parameter_variation() -> None:
    experiment = _experiment()
    first = _candidate(experiment, index=0)
    second = replace(
        _candidate(experiment, index=1),
        performance_summary={
            **dict(first.performance_summary),
            "oos": {"cagr": _metric(-0.99)},
        },
    )

    evaluation = evaluate_candidate_objective(experiment, second)

    assert evaluation.overall_constraint_state is ObjectiveEvaluationState.PASS
    assert evaluation.candidate_id == "candidate-1"
    assert "oos" not in evaluation.to_dict()


def test_security_sentinel_is_rejected_from_evaluation_reason() -> None:
    with pytest.raises(ValueError, match="unsafe"):
        ConstraintEvaluation(
            metric="cagr",
            operator=ConstraintOperator.GREATER_OR_EQUAL,
            threshold=0.1,
            metric_value=None,
            metric_status="not_evaluable",
            state=ObjectiveEvaluationState.NOT_EVALUABLE,
            reason_code=ObjectiveEvaluationReasonCode.METRIC_NOT_EVALUABLE,
            reason="SECRET_SENTINEL_8E1C must never be exposed",
        )


def test_objective_evaluation_has_no_oos_or_selection_side_effects() -> None:
    experiment = _experiment()
    before = experiment.canonical_json()
    evaluation = evaluate_candidate_objective(experiment, _candidate(experiment))

    assert experiment.canonical_json() == before
    assert set(evaluation.to_dict()) == {
        "experiment_id",
        "candidate_id",
        "objective_hash",
        "experiment_result_id",
        "result_hash",
        "constraint_results",
        "overall_constraint_state",
    }
