from __future__ import annotations

from dataclasses import FrozenInstanceError, replace
from datetime import UTC, datetime
from math import inf, nan

import pytest

from backtest import RebalanceFrequency
from data.models import PriceField
from research import (
    BindingValueType,
    InvalidParameterBindingError,
    ParameterBinding,
    ParameterBindingSet,
    ParameterBindingTypeMismatchError,
    ParameterDefinition,
    ParameterSet,
    ParameterSpace,
    ParameterType,
    StrategyMaterializationError,
    UnsupportedParameterBindingTargetError,
    materialize_strategy_version,
)
from strategies import (
    Allocation,
    AllocationRule,
    AssetReference,
    ComparisonOperator,
    Condition,
    FallbackAllocation,
    LogicalOperator,
    Operand,
    OperandType,
    RebalancePolicy,
    RuleGroup,
    StrategyDefinition,
    StrategyStatus,
    StrategyVersion,
    Threshold,
    ThresholdType,
)


def _price(symbol: str = "TQQQ") -> Operand:
    return Operand(symbol, OperandType.PRICE, price_field=PriceField.ADJUSTED_CLOSE)


def _ma(period: int, symbol: str = "TQQQ") -> Operand:
    return Operand(symbol, OperandType.MA, period, PriceField.ADJUSTED_CLOSE)


def _definition() -> StrategyDefinition:
    condition = Condition(
        _price(),
        ComparisonOperator.GREATER_THAN,
        _ma(50),
        Threshold(ThresholdType.RELATIVE, 0.04),
    )
    return StrategyDefinition(
        strategy_id="materialization-test",
        name="Materialization Test",
        description="test strategy",
        assets=tuple(AssetReference(symbol) for symbol in ("TQQQ", "SGOV")),
        price_field=PriceField.ADJUSTED_CLOSE,
        rules=(
            AllocationRule(
                "risk-on",
                "Risk On",
                1,
                (Allocation("TQQQ", 0.5),),
                condition=RuleGroup(LogicalOperator.AND, (condition,)),
                remaining=None,
            ),
        ),
        fallback=FallbackAllocation((Allocation("SGOV", 1.0),)),
        rebalance_policy=RebalancePolicy(RebalanceFrequency.MONTHLY),
    )


def _version() -> StrategyVersion:
    definition = _definition()
    return StrategyVersion(
        strategy_id=definition.strategy_id,
        version_id="materialization-test-v1",
        version_number=1,
        created_at=datetime(2026, 9, 8, tzinfo=UTC),
        configuration=definition,
        status=StrategyStatus.ACTIVE,
    )


def _space(*definitions) -> ParameterSpace:
    return ParameterSpace(parameters=definitions)


def _binding(name: str, path: str, value_type: BindingValueType) -> ParameterBinding:
    return ParameterBinding(name, path, value_type, "phase-8d-1-v1")


def _period_binding(name: str = "period") -> ParameterBinding:
    return _binding(
        name,
        "rules[0].condition.children[0].right.period",
        BindingValueType.POSITIVE_INTEGER,
    )


def _float_space(name: str, minimum: float = -1.0, maximum: float = 1.0) -> ParameterSpace:
    return _space(
        ParameterDefinition(name, ParameterType.FLOAT, min=minimum, max=maximum, step=0.01)
    )


def test_binding_is_serializable_sorted_and_hash_stable() -> None:
    first = ParameterBindingSet(
        (
            _binding(
                "threshold",
                "rules[0].condition.children[0].threshold.value",
                BindingValueType.RELATIVE_THRESHOLD,
            ),
            _period_binding(),
        )
    )
    second = ParameterBindingSet(tuple(reversed(first.bindings)))

    assert tuple(item.parameter_name for item in first.bindings) == ("period", "threshold")
    assert first.binding_hash == second.binding_hash
    assert ParameterBindingSet.from_dict(first.to_dict()) == first
    with pytest.raises(FrozenInstanceError):
        first.bindings = ()  # type: ignore[misc]


def test_parameter_set_is_immutable_and_binding_identity_is_distinct() -> None:
    parameter_set = ParameterSet({"period": 20})
    with pytest.raises(TypeError):
        parameter_set.values["period"] = 30  # type: ignore[index]
    with pytest.raises(FrozenInstanceError):
        parameter_set.values = {}  # type: ignore[misc]

    first = _period_binding()
    second = ParameterBinding(
        first.parameter_name,
        first.target_path,
        first.value_type,
        "phase-8d-1-v2",
    )
    assert ParameterBindingSet((first,)).binding_hash != ParameterBindingSet((second,)).binding_hash


@pytest.mark.parametrize("name", ["period;import os", "__import__", "path/to/value", ""])
def test_malicious_parameter_names_are_rejected(name: str) -> None:
    with pytest.raises(InvalidParameterBindingError):
        ParameterBinding(name, "rules[0].condition.children[0].right.period", "integer", "v1")


@pytest.mark.parametrize(
    ("path", "value", "value_type"),
    [
        (
            "rules[0].condition.children[0].right.period",
            20,
            BindingValueType.POSITIVE_INTEGER,
        ),
        (
            "rules[0].condition.children[0].threshold.value",
            -0.03,
            BindingValueType.RELATIVE_THRESHOLD,
        ),
        ("rules[0].allocations[0].target_weight", 0.75, BindingValueType.ALLOCATION_WEIGHT),
        ("fallback.allocations[0].target_weight", 0.25, BindingValueType.ALLOCATION_WEIGHT),
    ],
)
def test_materializes_whitelisted_targets(
    path: str, value: object, value_type: BindingValueType
) -> None:
    name = "parameter"
    parameter_type = (
        ParameterType.INTEGER
        if value_type is BindingValueType.POSITIVE_INTEGER
        else ParameterType.FLOAT
    )
    definition = (
        ParameterDefinition(name, parameter_type, min=1, max=200, step=1)
        if parameter_type is ParameterType.INTEGER
        else ParameterDefinition(name, parameter_type, min=-1.0, max=1.0, step=0.01)
    )
    parameter_set = ParameterSet({name: value}, _space(definition))
    derived = materialize_strategy_version(
        _version(), parameter_set, (_binding(name, path, value_type),)
    )

    assert derived.configuration != _version().configuration
    assert derived.materialization_provenance is not None
    assert derived.materialization_provenance.base_strategy_version_id == "materialization-test-v1"
    assert derived.materialization_provenance.parameter_set_hash == parameter_set.content_hash
    assert (
        derived.materialization_provenance.derived_strategy_version_hash
        != _version().content_hash
    )


def test_materializes_direct_condition_and_preserves_signed_threshold() -> None:
    parameter_set = ParameterSet({"threshold": 0.04}, _float_space("threshold"))
    binding = _binding(
        "threshold",
        "rules[0].condition.children[0].threshold.value",
        BindingValueType.RELATIVE_THRESHOLD,
    )

    derived = materialize_strategy_version(_version(), parameter_set, (binding,))

    condition = derived.configuration.rules[0].condition
    assert isinstance(condition, RuleGroup)
    child = condition.children[0]
    assert isinstance(child, Condition)
    assert child.threshold is not None and child.threshold.value == 0.04


@pytest.mark.parametrize("value", [0, -1, True, 2.5, "20", nan, inf, -inf])
def test_period_binding_rejects_invalid_values(value: object) -> None:
    if isinstance(value, bool) or isinstance(value, str) or not isinstance(value, (int, float)):
        with pytest.raises(ValueError):
            ParameterSet({"period": value}, _space())
        return
    if not isinstance(value, int) and not value == value or (
        isinstance(value, float) and not abs(value) < inf
    ):
        with pytest.raises(ValueError):
            ParameterSet({"period": value}, _space())
        return
    parameter_set = ParameterSet({"period": value})
    with pytest.raises((ParameterBindingTypeMismatchError, ValueError)):
        materialize_strategy_version(
            _version(),
            parameter_set,
            (_period_binding(),),
        )


def test_materialization_requires_exact_binding_coverage() -> None:
    space = _space(ParameterDefinition("period", ParameterType.INTEGER, min=1, max=200, step=1))
    parameter_set = ParameterSet({"period": 20}, space)
    with pytest.raises(InvalidParameterBindingError):
        materialize_strategy_version(_version(), parameter_set, ())


@pytest.mark.parametrize(
    "path",
    [
        "rules[0].conditions[0].left.period",
        "rules[0].condition.left.__class__",
        "rules[0].condition.left.period + 1",
        "../../fallback.allocations[0].target_weight",
        "__import__('os').system('true')",
        "rules[-1].condition.left.period",
    ],
)
def test_unsafe_or_unsupported_paths_are_rejected(path: str) -> None:
    with pytest.raises(UnsupportedParameterBindingTargetError):
        ParameterBinding("period", path, BindingValueType.POSITIVE_INTEGER, "v1")


def test_period_binding_requires_an_ma_or_ema_operand() -> None:
    parameter_set = ParameterSet({"period": 20})
    binding = _binding(
        "period",
        "rules[0].condition.children[0].left.period",
        BindingValueType.POSITIVE_INTEGER,
    )

    with pytest.raises(UnsupportedParameterBindingTargetError):
        materialize_strategy_version(_version(), parameter_set, (binding,))


def test_duplicate_parameter_and_target_bindings_are_rejected() -> None:
    first = _period_binding()
    with pytest.raises(InvalidParameterBindingError):
        ParameterBindingSet(
            (
                first,
                _binding(
                    "period",
                    "rules[0].condition.children[0].left.period",
                    BindingValueType.POSITIVE_INTEGER,
                ),
            )
        )
    with pytest.raises(InvalidParameterBindingError):
        ParameterBindingSet(
            (first, _binding("other", first.target_path, BindingValueType.POSITIVE_INTEGER))
        )


def test_binding_type_mismatch_and_non_relative_threshold_are_rejected() -> None:
    space = _float_space("value")
    parameter_set = ParameterSet({"value": 0.05}, space)
    with pytest.raises(ParameterBindingTypeMismatchError):
        materialize_strategy_version(
            _version(), parameter_set,
            (
                _binding(
                    "value",
                    "rules[0].condition.children[0].threshold.value",
                    BindingValueType.ALLOCATION_WEIGHT,
                ),
            ),
        )


@pytest.mark.parametrize(
    ("path", "value_type"),
    [
        (
            "rules[0].condition.children[0].threshold.value",
            BindingValueType.FLOAT,
        ),
        (
            "rules[0].allocations[0].target_weight",
            BindingValueType.FLOAT,
        ),
    ],
)
def test_targets_require_their_explicit_binding_value_type(
    path: str, value_type: BindingValueType
) -> None:
    parameter_set = ParameterSet({"value": 0.05})
    with pytest.raises(ParameterBindingTypeMismatchError):
        materialize_strategy_version(
            _version(), parameter_set, (_binding("value", path, value_type),)
        )


def test_materialization_failure_has_no_partial_mutation() -> None:
    base = _version()
    space = _float_space("weight", minimum=0.0)
    invalid_allocation = Allocation("TQQQ", 0.5, minimum_weight=0.6)
    invalid_rule = replace(base.configuration.rules[0], allocations=(invalid_allocation,))
    invalid_definition = replace(base.configuration, rules=(invalid_rule,))
    base = replace(base, configuration=invalid_definition, content_hash=None)
    original_json = base.to_json()
    original_hash = base.content_hash
    parameter_set = ParameterSet({"weight": 0.4}, space)

    with pytest.raises(StrategyMaterializationError):
        materialize_strategy_version(
            base, parameter_set,
            (
                _binding(
                    "weight",
                    "rules[0].allocations[0].target_weight",
                    BindingValueType.ALLOCATION_WEIGHT,
                ),
            ),
        )

    assert base.to_json() == original_json
    assert base.content_hash == original_hash
    assert base.configuration.rules[0].allocations[0].target_weight == 0.5


def test_same_inputs_are_deterministic_and_distinct_parameter_sets_have_distinct_hashes() -> None:
    space = _space(ParameterDefinition("period", ParameterType.INTEGER, min=1, max=200, step=1))
    binding = _period_binding()
    first = materialize_strategy_version(
        _version(), ParameterSet({"period": 20}, space), (binding,)
    )
    second = materialize_strategy_version(
        _version(), ParameterSet({"period": 20}, space), (binding,)
    )
    other = materialize_strategy_version(
        _version(), ParameterSet({"period": 30}, space), (binding,)
    )

    assert first.to_json() == second.to_json()
    assert first.materialization_provenance == second.materialization_provenance
    assert first.materialization_provenance is not None
    assert other.materialization_provenance is not None
    assert (
        first.materialization_provenance.derived_strategy_version_hash
        != other.materialization_provenance.derived_strategy_version_hash
    )


def test_materialization_preserves_all_inputs_and_binding_changes_derived_identity() -> None:
    space = _space(ParameterDefinition("period", ParameterType.INTEGER, min=1, max=200, step=1))
    parameter_set = ParameterSet({"period": 20}, space)
    base = _version()
    base_json = base.to_json()
    parameter_json = parameter_set.canonical_json()

    first_binding = _period_binding()
    second_binding = ParameterBinding(
        first_binding.parameter_name,
        first_binding.target_path,
        first_binding.value_type,
        "phase-8d-1-v2",
    )
    first = materialize_strategy_version(base, parameter_set, (first_binding,))
    second = materialize_strategy_version(base, parameter_set, (second_binding,))

    assert base.to_json() == base_json
    assert parameter_set.canonical_json() == parameter_json
    assert first.materialization_provenance is not None
    assert second.materialization_provenance is not None
    assert (
        first.materialization_provenance.derived_strategy_version_hash
        != second.materialization_provenance.derived_strategy_version_hash
    )


def test_parameter_set_numeric_canonicalization_follows_existing_contract() -> None:
    integer = ParameterSet({"value": 1})
    decimal = ParameterSet({"value": 1.0})

    assert integer.canonical_json() != decimal.canonical_json()
    assert integer.content_hash != decimal.content_hash


def test_derived_strategy_version_round_trips_with_provenance() -> None:
    space = _space(ParameterDefinition("period", ParameterType.INTEGER, min=1, max=200, step=1))
    derived = materialize_strategy_version(
        _version(), ParameterSet({"period": 20}, space), (_period_binding(),)
    )

    restored = StrategyVersion.from_json(derived.to_json())

    assert restored == derived
    assert restored.materialization_provenance == derived.materialization_provenance
    assert restored.content_hash == derived.content_hash
