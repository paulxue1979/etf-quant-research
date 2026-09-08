from __future__ import annotations

import multiprocessing
from datetime import UTC, date, datetime

import pytest

from backtest import BacktestConfig, RebalanceFrequency, RebalancePolicy
from data.models import PriceField
from research import (
    ConstraintOperator,
    Experiment,
    ExperimentNotFrozenError,
    ExperimentStatus,
    InvalidParameterConstraintError,
    MetricDirection,
    ObjectiveSpecification,
    ParameterConstraint,
    ParameterDefinition,
    ParameterSet,
    ParameterSpace,
    ParameterSpaceTooLargeError,
    ParameterType,
    generate_candidates,
)


def _space(*, max_candidates: int = 12) -> ParameterSpace:
    return ParameterSpace(
        parameters=(
            ParameterDefinition("c", ParameterType.FLOAT, min=0.0, max=0.02, step=0.02),
            ParameterDefinition("b", ParameterType.DISCRETE, allowed_values=(50, 20)),
            ParameterDefinition("a", ParameterType.DISCRETE, allowed_values=(20, 5, 10)),
        ),
        max_candidates=max_candidates,
    )


def _experiment(
    space: ParameterSpace | None = None, *, status: ExperimentStatus = ExperimentStatus.SPACE_FROZEN
) -> Experiment:
    return Experiment(
        experiment_id="experiment-8b",
        protocol_id="protocol-8b",
        strategy_definition_id="strategy-8b",
        base_strategy_version_id="strategy-version-8b",
        base_strategy_version_hash="a" * 64,
        parameter_space=space or _space(),
        objective_specification=ObjectiveSpecification(
            primary_metric="cagr",
            metric_directions={"cagr": MetricDirection.MAXIMIZE},
        ),
        is_start_date=date(2020, 1, 2),
        is_end_date=date(2020, 12, 31),
        backtest_configuration=BacktestConfig(
            strategy_version_id="strategy-version-8b",
            start_date=date(2020, 1, 2),
            end_date=date(2020, 12, 31),
            initial_capital=10_000,
            price_field_used=PriceField.ADJUSTED_CLOSE,
            rebalance_policy=RebalancePolicy(RebalanceFrequency.MONTHLY),
        ),
        engine_version="phase-3.0",
        analysis_version="phase-4i.0",
        status=status,
        created_at=datetime(2026, 9, 8, tzinfo=UTC),
    )


def _candidate_signature() -> tuple[tuple[tuple[tuple[str, object], ...], ...], str]:
    generated = generate_candidates(_experiment())
    return (
        tuple(tuple(candidate.values.items()) for candidate in generated.candidates),
        generated.candidate_set_hash,
    )


def _emit_cross_process_signature(queue: multiprocessing.queues.Queue) -> None:
    queue.put(_candidate_signature())


def test_cartesian_product_uses_canonical_parameter_and_value_order() -> None:
    generated = generate_candidates(_experiment())

    assert generated.theoretical_candidate_count == 12
    assert generated.candidate_count == 12
    assert tuple(candidate.values for candidate in generated.candidates[:4]) == (
        {"a": 5, "b": 20, "c": 0.0},
        {"a": 5, "b": 20, "c": 0.02},
        {"a": 5, "b": 50, "c": 0.0},
        {"a": 5, "b": 50, "c": 0.02},
    )


def test_same_space_produces_same_candidates_order_and_hashes() -> None:
    first = generate_candidates(_experiment())
    second = generate_candidates(_experiment())

    assert first == second
    assert first.candidate_set_hash == second.candidate_set_hash
    assert tuple(item.content_hash for item in first.candidates) == tuple(
        item.content_hash for item in second.candidates
    )


def test_parameter_set_hash_contract_distinguishes_integer_and_float_values() -> None:
    integer = ParameterSet({"value": 1})
    decimal = ParameterSet({"value": 1.0})

    assert integer.content_hash == ParameterSet({"value": 1}).content_hash
    assert integer.content_hash != decimal.content_hash


def test_constraints_filter_invalid_combinations_without_reordering_survivors() -> None:
    space = ParameterSpace(
        parameters=(
            ParameterDefinition("short", ParameterType.DISCRETE, allowed_values=(10, 20)),
            ParameterDefinition("long", ParameterType.DISCRETE, allowed_values=(10, 20)),
        ),
        constraints=(ParameterConstraint("short", ConstraintOperator.LESS_THAN, "long"),),
        max_candidates=4,
    )

    generated = generate_candidates(_experiment(space))

    assert generated.theoretical_candidate_count == 4
    assert tuple(candidate.values for candidate in generated.candidates) == (
        {"long": 20, "short": 10},
    )


def test_constraints_that_eliminate_every_combination_return_zero_candidates() -> None:
    space = ParameterSpace(
        parameters=(
            ParameterDefinition("short", ParameterType.DISCRETE, allowed_values=(20,)),
            ParameterDefinition("long", ParameterType.DISCRETE, allowed_values=(10,)),
        ),
        constraints=(ParameterConstraint("short", ConstraintOperator.LESS_THAN, "long"),),
    )

    generated = generate_candidates(_experiment(space))

    assert generated.theoretical_candidate_count == 1
    assert generated.candidate_count == 0


def test_theoretical_count_limit_rejects_without_silent_truncation() -> None:
    with pytest.raises(ParameterSpaceTooLargeError) as raised:
        generate_candidates(_experiment(_space(max_candidates=11)))

    assert raised.value.code == "PARAMETER_SPACE_TOO_LARGE"


def test_generation_requires_a_space_frozen_experiment() -> None:
    with pytest.raises(ExperimentNotFrozenError) as raised:
        generate_candidates(_experiment(status=ExperimentStatus.DEFINED))

    assert raised.value.code == "EXPERIMENT_NOT_SPACE_FROZEN"


def test_empty_parameter_space_generates_one_explicit_empty_parameter_set() -> None:
    generated = generate_candidates(_experiment(ParameterSpace(parameters=())))

    assert generated.theoretical_candidate_count == 1
    assert generated.candidate_count == 1
    assert generated.candidates[0].values == {}


def test_incompatible_safe_constraint_values_raise_a_domain_error() -> None:
    space = ParameterSpace(
        parameters=(
            ParameterDefinition("number", ParameterType.DISCRETE, allowed_values=(1,)),
            ParameterDefinition("label", ParameterType.ENUM, allowed_values=("a",)),
        ),
        constraints=(ParameterConstraint("number", ConstraintOperator.LESS_THAN, "label"),),
    )

    with pytest.raises(InvalidParameterConstraintError, match="support"):
        generate_candidates(_experiment(space))


def test_candidate_order_and_hash_are_stable_in_an_independent_process() -> None:
    expected = _candidate_signature()
    context = multiprocessing.get_context("spawn")
    queue = context.Queue()
    process = context.Process(target=_emit_cross_process_signature, args=(queue,))
    process.start()
    process.join(timeout=10)

    assert process.exitcode == 0
    assert queue.get(timeout=1) == expected
