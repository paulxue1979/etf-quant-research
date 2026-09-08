from __future__ import annotations

from dataclasses import FrozenInstanceError
from datetime import UTC, date, datetime
from math import inf, nan

import pytest

from backtest import BacktestConfig, RebalanceFrequency, RebalancePolicy
from data.models import PriceField
from research import (
    ConstraintOperator,
    Experiment,
    ExperimentMethod,
    ExperimentProvenance,
    ExperimentStatus,
    InvalidExperimentError,
    InvalidObjectiveSpecificationError,
    InvalidParameterConstraintError,
    InvalidParameterDefinitionError,
    InvalidParameterSetError,
    InvalidParameterSpaceError,
    MetricDirection,
    ObjectiveSpecification,
    ParameterConstraint,
    ParameterDefinition,
    ParameterSet,
    ParameterSpace,
    ParameterType,
)
from research.canonical import canonical_json


def _int_definition(name: str = "ma_short") -> ParameterDefinition:
    return ParameterDefinition(name, ParameterType.INTEGER, min=5, max=50, step=5)


def _float_definition(name: str = "threshold") -> ParameterDefinition:
    return ParameterDefinition(name, ParameterType.FLOAT, min=0.0, max=0.1, step=0.01, precision=2)


def _space() -> ParameterSpace:
    return ParameterSpace(
        parameters=(_float_definition(), _int_definition(), _int_definition("ma_long")),
        constraints=(ParameterConstraint("ma_short", ConstraintOperator.LESS_THAN, "ma_long"),),
        max_candidates=100,
    )


def _objective() -> ObjectiveSpecification:
    return ObjectiveSpecification(
        primary_metric="cagr",
        secondary_metrics=("max_drawdown",),
        metric_directions={
            "cagr": MetricDirection.MAXIMIZE,
            "max_drawdown": MetricDirection.MINIMIZE,
        },
        hard_constraints={"max_drawdown": -0.3},
        tie_break_rules=("max_drawdown",),
    )


def _config() -> BacktestConfig:
    return BacktestConfig(
        strategy_version_id="strategy-v1",
        start_date=date(2020, 1, 2),
        end_date=date(2020, 12, 31),
        initial_capital=10_000,
        price_field_used=PriceField.ADJUSTED_CLOSE,
        rebalance_policy=RebalancePolicy(RebalanceFrequency.MONTHLY),
    )


def _provenance(space: ParameterSpace, objective: ObjectiveSpecification) -> ExperimentProvenance:
    return ExperimentProvenance(
        protocol_id="protocol-1",
        strategy_definition_id="strategy-1",
        base_strategy_version_id="strategy-v1",
        base_strategy_version_hash="a" * 64,
        parameter_space_hash=space.content_hash,
        objective_spec_hash=objective.content_hash,
        is_start_date=date(2020, 1, 2),
        is_end_date=date(2020, 12, 31),
        price_field_used=PriceField.ADJUSTED_CLOSE,
        initial_capital=10_000,
        commission={"rate": 0.0, "per_order": 0.0},
        slippage=0.0,
        execution_rule="next_trading_day_open",
        fractional_shares=False,
        rebalance_policy={"frequency": "monthly", "threshold": None},
        engine_version="phase-3.0",
        analysis_version="phase-4i.0",
        data_snapshot_reference={"symbol": "QQQ", "frequency": "daily"},
    )


def _experiment() -> Experiment:
    space = _space()
    objective = _objective()
    return Experiment(
        experiment_id="experiment-1",
        protocol_id="protocol-1",
        strategy_definition_id="strategy-1",
        base_strategy_version_id="strategy-v1",
        base_strategy_version_hash="a" * 64,
        parameter_space=space,
        objective_specification=objective,
        is_start_date=date(2020, 1, 2),
        is_end_date=date(2020, 12, 31),
        backtest_configuration=_config(),
        engine_version="phase-3.0",
        analysis_version="phase-4i.0",
        status=ExperimentStatus.DRAFT,
        created_at=datetime(2026, 9, 8, tzinfo=UTC),
        method=ExperimentMethod.GRID,
        parameter_set=ParameterSet(
            {"ma_short": 20, "ma_long": 50, "threshold": 0.03},
            parameter_space=space,
        ),
        provenance=_provenance(space, objective),
    )


@pytest.mark.parametrize(
    ("ptype", "kwargs"),
    [
        (ParameterType.INTEGER, {"min": 1, "max": 10, "step": 1}),
        (ParameterType.FLOAT, {"min": 0.0, "max": 1.0, "step": 0.1, "precision": 1}),
        (ParameterType.ENUM, {"allowed_values": ("raw_close", "adjusted_close")}),
        (ParameterType.DISCRETE, {"allowed_values": (5, 10, 20)}),
    ],
)
def test_parameter_definition_supports_initial_types(ptype, kwargs) -> None:
    definition = ParameterDefinition("parameter", ptype, **kwargs)

    assert definition.type is ptype
    assert ParameterDefinition.from_dict(definition.to_dict()) == definition


@pytest.mark.parametrize(
    "kwargs",
    [
        {"min": 10, "max": 1, "step": 1},
        {"min": 1, "max": 10, "step": 0},
        {"min": 0.0, "max": 1.0, "step": inf},
        {"allowed_values": ()},
    ],
)
def test_parameter_definition_rejects_invalid_domains(kwargs) -> None:
    ptype = ParameterType.ENUM if "allowed_values" in kwargs else ParameterType.INTEGER
    with pytest.raises(InvalidParameterDefinitionError):
        ParameterDefinition("parameter", ptype, **kwargs)


def test_parameter_definition_rejects_non_finite_values() -> None:
    with pytest.raises(InvalidParameterDefinitionError):
        ParameterDefinition("threshold", ParameterType.FLOAT, min=nan, max=1, step=0.1)


def test_parameter_set_is_immutable_and_validates_values() -> None:
    values = ParameterSet(
        {"ma_short": 20, "ma_long": 50, "threshold": 0.03}, parameter_space=_space()
    )

    assert values.content_hash == ParameterSet.from_dict(values.to_dict(), _space()).content_hash
    with pytest.raises(FrozenInstanceError):
        values.values = {}  # type: ignore[misc]
    with pytest.raises(InvalidParameterSetError):
        ParameterSet({"ma_short": 21, "ma_long": 50, "threshold": 0.03}, parameter_space=_space())
    with pytest.raises(InvalidParameterSetError):
        ParameterSet({"ma_short": 20, "ma_long": inf, "threshold": 0.03}, parameter_space=_space())


def test_constraint_is_restricted_and_never_accepts_executable_payload() -> None:
    constraint = ParameterConstraint("short", ConstraintOperator.LESS_THAN, "long")
    assert ParameterConstraint.from_dict(constraint.to_dict()) == constraint
    with pytest.raises(InvalidParameterConstraintError, match="executable"):
        ParameterConstraint.from_dict(
            {"left": "short", "operator": "less_than", "right": "long", "expression": "eval('x')"}
        )


def test_parameter_space_is_sorted_hash_ready_and_validates_references() -> None:
    space = _space()
    assert tuple(item.name for item in space.parameters) == ("ma_long", "ma_short", "threshold")
    assert space.content_hash == ParameterSpace.from_dict(space.to_dict()).content_hash
    with pytest.raises(InvalidParameterSpaceError, match="defined parameters"):
        ParameterSpace(
            (_int_definition(),), (ParameterConstraint("ma_short", "less_than", "missing"),)
        )
    with pytest.raises(InvalidParameterSpaceError, match="positive"):
        ParameterSpace((_int_definition(),), max_candidates=0)


def test_objective_is_strict_and_hash_deterministic() -> None:
    objective = _objective()
    restored = ObjectiveSpecification.from_dict(objective.to_dict())
    assert restored.content_hash == objective.content_hash
    assert canonical_json({"b": 1, "a": 2}) == canonical_json({"a": 2, "b": 1})
    with pytest.raises(InvalidObjectiveSpecificationError, match="cover"):
        ObjectiveSpecification(
            "cagr", metric_directions={"cagr": "maximize"}, secondary_metrics=("sharpe",)
        )


def test_provenance_round_trip_and_experiment_binding() -> None:
    experiment = _experiment()
    restored = Experiment.from_dict(experiment.to_dict())

    assert restored.content_hash == experiment.content_hash
    assert restored.provenance is not None
    assert restored.provenance.data_snapshot_reference["symbol"] == "QQQ"
    assert experiment.parameter_space_hash == experiment.parameter_space.content_hash


def test_experiment_requires_protocol_binding_and_is_date_range() -> None:
    with pytest.raises(InvalidExperimentError, match="protocol_id"):
        Experiment(**{**_experiment().__dict__, "protocol_id": ""})
    with pytest.raises(InvalidExperimentError, match="IS dates"):
        Experiment(**{**_experiment().__dict__, "is_start_date": date(2021, 1, 1)})


def test_experiment_status_transitions_are_immutable_and_separate_from_protocol() -> None:
    experiment = _experiment()
    defined = experiment.with_status(ExperimentStatus.DEFINED)
    frozen = defined.with_status(ExperimentStatus.SPACE_FROZEN)

    assert experiment.status is ExperimentStatus.DRAFT
    assert frozen.status is ExperimentStatus.SPACE_FROZEN
    with pytest.raises(InvalidExperimentError, match="invalid experiment transition"):
        experiment.with_status(ExperimentStatus.RUNNING)


def test_experiment_does_not_expose_best_or_recommendation_fields() -> None:
    fields = set(_experiment().to_dict())

    assert not fields.intersection({"best_strategy", "optimal_strategy", "recommended_strategy"})
