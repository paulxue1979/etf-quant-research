from __future__ import annotations

from dataclasses import FrozenInstanceError
from datetime import UTC, datetime
from math import inf, nan

import pytest

from data.models import PriceField
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
    RebalanceFrequency,
    RebalancePolicy,
    RemainingAllocation,
    RuleGroup,
    StrategyDefinition,
    StrategyStatus,
    StrategyVersion,
    Threshold,
    ThresholdType,
)
from strategies.exceptions import (
    InvalidAllocationError,
    InvalidAssetError,
    InvalidOperandError,
    InvalidRebalancePolicyError,
    InvalidRuleError,
    InvalidRuleGroupError,
    InvalidStrategyError,
    InvalidStrategyVersionError,
    InvalidThresholdError,
)


def _price(symbol: str = "TQQQ") -> Operand:
    return Operand(symbol, OperandType.PRICE, price_field=PriceField.ADJUSTED_CLOSE)


def _ma(period: int, symbol: str = "TQQQ") -> Operand:
    return Operand(symbol, OperandType.MA, period, PriceField.ADJUSTED_CLOSE)


def _ema(period: int, symbol: str = "TQQQ") -> Operand:
    return Operand(symbol, OperandType.EMA, period, PriceField.ADJUSTED_CLOSE)


def _constant(value: object, symbol: str = "TQQQ") -> Operand:
    return Operand(symbol, OperandType.CONSTANT, value=value)  # type: ignore[arg-type]


def _condition() -> Condition:
    return Condition(
        left=_price(),
        operator=ComparisonOperator.GREATER_THAN,
        right=_ma(50),
        threshold=Threshold(ThresholdType.RELATIVE, 0.04),
    )


def _allocation(symbol: str, weight: float) -> Allocation:
    return Allocation(symbol, weight)


def _definition() -> StrategyDefinition:
    risk_on = AllocationRule(
        rule_id="risk-on",
        name="Risk On",
        priority=100,
        condition=RuleGroup(
            LogicalOperator.AND,
            (_condition(), Condition(_ma(20), ComparisonOperator.GREATER_THAN, _ma(50))),
        ),
        allocations=(
            _allocation("QQQ", 0.30),
            _allocation("TQQQ", 0.50),
            _allocation("SGOV", 0.20),
        ),
    )
    return StrategyDefinition(
        strategy_id="qqq-tqqq-sgov",
        name="Dynamic Allocation",
        description="A test strategy",
        assets=tuple(AssetReference(symbol) for symbol in ("QQQ", "TQQQ", "SGOV")),
        price_field=PriceField.ADJUSTED_CLOSE,
        rules=(risk_on,),
        fallback=FallbackAllocation((_allocation("SGOV", 1.0),)),
        rebalance_policy=RebalancePolicy(RebalanceFrequency.MONTHLY, 0.05),
    )


def test_asset_reference_normalizes_and_is_immutable() -> None:
    asset = AssetReference(" qqq ")

    assert asset.symbol == "QQQ"
    assert asset.to_dict() == {"symbol": "QQQ"}
    with pytest.raises(FrozenInstanceError):
        asset.symbol = "SPY"  # type: ignore[misc]


@pytest.mark.parametrize("symbol", ["", "A B", "$QQQ", "123", "A" * 17, None])
def test_asset_reference_rejects_invalid_symbols(symbol: object) -> None:
    with pytest.raises(InvalidAssetError):
        AssetReference(symbol)  # type: ignore[arg-type]


def test_operand_supports_price_ma_and_ema_with_traceable_fields() -> None:
    assert _price().to_dict() == {
        "type": "price",
        "asset": "TQQQ",
        "price_field": "adjusted_close",
    }
    assert _ma(50).to_dict()["period"] == 50
    assert _ema(20).to_dict()["type"] == "ema"
    assert _ma(50).symbol == "TQQQ"
    assert _ma(50).type is OperandType.MA


@pytest.mark.parametrize("period", [0, -1, True, 2.5, "50"])
def test_operand_rejects_invalid_indicator_period(period: object) -> None:
    with pytest.raises(InvalidOperandError, match="positive integer"):
        Operand("QQQ", OperandType.MA, period=period)  # type: ignore[arg-type]


def test_price_operand_rejects_period_and_invalid_price_field() -> None:
    with pytest.raises(InvalidOperandError, match="must not have a period"):
        Operand("QQQ", OperandType.PRICE, period=1)
    with pytest.raises(InvalidOperandError, match="price_field"):
        Operand("QQQ", OperandType.PRICE, price_field="close")  # type: ignore[arg-type]


@pytest.mark.parametrize("value", [1, 0, -2, 0.125])
def test_constant_operand_accepts_finite_values(value: int | float) -> None:
    operand = _constant(value)

    assert operand.value == float(value)
    assert operand.to_dict() == {"type": "constant", "asset": "TQQQ", "value": float(value)}


@pytest.mark.parametrize("value", [None, nan, inf, -inf, "1.0", True])
def test_constant_operand_requires_finite_numeric_value(value: object) -> None:
    with pytest.raises(InvalidOperandError, match="finite numeric value"):
        _constant(value)


def test_constant_operand_rejects_indicator_only_fields_and_non_constants_reject_value() -> None:
    with pytest.raises(InvalidOperandError, match="must not have a period"):
        Operand("QQQ", OperandType.CONSTANT, period=1, value=1)
    with pytest.raises(InvalidOperandError, match="must not have a price_field"):
        Operand("QQQ", OperandType.CONSTANT, price_field=PriceField.RAW_CLOSE, value=1)
    with pytest.raises(InvalidOperandError, match="only CONSTANT"):
        Operand("QQQ", OperandType.PRICE, value=1)


def test_constant_operand_round_trips_without_changing_legacy_payloads() -> None:
    operand = _constant(0.25)

    assert Operand.from_dict(operand.to_dict()) == operand
    assert "value" not in _price().to_dict()
    assert "value" not in _ma(50).to_dict()
    assert "value" not in _ema(20).to_dict()


@pytest.mark.parametrize(
    ("operator", "wire_value"),
    [
        (ComparisonOperator.GREATER_THAN, "greater_than"),
        (ComparisonOperator.GREATER_OR_EQUAL, "greater_or_equal"),
        (ComparisonOperator.LESS_THAN, "less_than"),
        (ComparisonOperator.LESS_OR_EQUAL, "less_or_equal"),
        (ComparisonOperator.EQUAL, "equal"),
    ],
)
def test_condition_supports_all_comparison_operators(
    operator: ComparisonOperator, wire_value: str
) -> None:
    condition = Condition(_ma(20), operator, _ma(50))

    assert condition.operator is operator
    assert condition.to_dict()["operator"] == wire_value


def test_condition_supports_signed_relative_and_future_absolute_threshold() -> None:
    relative = _condition()
    negative = Condition(
        _price(),
        ComparisonOperator.LESS_THAN,
        _ma(50),
        Threshold(ThresholdType.RELATIVE, -0.03),
    )
    absolute = Condition(
        _price(),
        ComparisonOperator.GREATER_THAN,
        _ma(50),
        Threshold(ThresholdType.ABSOLUTE, 5),
    )

    assert relative.to_dict()["threshold"] == {"type": "relative", "value": 0.04}
    assert negative.to_dict()["threshold"] == {"type": "relative", "value": -0.03}
    assert absolute.threshold is not None and absolute.threshold.type is ThresholdType.ABSOLUTE


@pytest.mark.parametrize("value", [nan, inf, -inf, "0.04", True])
def test_threshold_rejects_non_finite_or_non_numeric_values(value: object) -> None:
    with pytest.raises(InvalidThresholdError, match="finite number"):
        Threshold(ThresholdType.RELATIVE, value)  # type: ignore[arg-type]


def test_condition_round_trips_without_evaluating() -> None:
    condition = _condition()

    restored = Condition.from_dict(condition.to_dict())

    assert restored == condition
    assert restored.left.asset.symbol == "TQQQ"
    assert restored.threshold is not None and restored.threshold.value == 0.04


def test_rule_groups_support_and_or_nesting_and_require_children() -> None:
    nested = RuleGroup(
        LogicalOperator.OR,
        (
            RuleGroup(
                LogicalOperator.AND,
                (_condition(), Condition(_ma(20), ComparisonOperator.GREATER_THAN, _ma(50))),
            ),
            _condition(),
        ),
    )

    assert RuleGroup.from_dict(nested.to_dict()) == nested
    with pytest.raises(InvalidRuleGroupError, match="must not be empty"):
        RuleGroup(LogicalOperator.AND, ())
    with pytest.raises(InvalidRuleGroupError, match="Condition or RuleGroup"):
        RuleGroup(LogicalOperator.AND, (_condition(), object()))  # type: ignore[arg-type]


@pytest.mark.parametrize("weight", [-0.01, 1.01, nan, inf, True])
def test_allocation_rejects_invalid_weights(weight: object) -> None:
    with pytest.raises(InvalidAllocationError, match="target_weight"):
        Allocation("QQQ", weight)  # type: ignore[arg-type]


def test_allocations_and_rules_support_multi_asset_targets() -> None:
    allocations = (_allocation("qqq", 0.30), _allocation("TQQQ", 0.50), _allocation("SGOV", 0.20))
    rule = AllocationRule("risk-on", "Risk On", 100, allocations, _condition())
    fallback = FallbackAllocation((_allocation("SGOV", 1.0),))

    assert [item.symbol.symbol for item in rule.allocations] == ["QQQ", "TQQQ", "SGOV"]
    assert rule.condition == _condition()
    assert FallbackAllocation.from_dict(fallback.to_dict()) == fallback


def test_allocation_bounds_and_remaining_round_trip() -> None:
    allocation = Allocation("QQQ", 0.3, minimum_weight=0.1, maximum_weight=0.5)
    rule = AllocationRule(
        "partial",
        "Partial",
        1,
        (allocation,),
        remaining=RemainingAllocation("SGOV"),
    )

    assert Allocation.from_dict(allocation.to_dict()) == allocation
    assert AllocationRule.from_dict(rule.to_dict()) == rule


def test_rule_and_fallback_require_structural_values() -> None:
    with pytest.raises(InvalidRuleError, match="allocations"):
        AllocationRule("rule", "Rule", 1, ())
    with pytest.raises(InvalidRuleError, match="priority"):
        AllocationRule("rule", "Rule", True, (_allocation("QQQ", 1.0),))
    with pytest.raises(InvalidAllocationError, match="must not be empty"):
        FallbackAllocation(())


@pytest.mark.parametrize("frequency", list(RebalanceFrequency))
def test_rebalance_policy_supports_all_frequencies(frequency: RebalanceFrequency) -> None:
    policy = RebalancePolicy(frequency, rebalance_threshold=0.05)

    assert policy.frequency is frequency
    assert policy.threshold == policy.rebalance_threshold == 0.05
    assert RebalancePolicy.from_dict(policy.to_dict()) == policy


@pytest.mark.parametrize("threshold", [-0.01, 1.01, nan, inf, True])
def test_rebalance_policy_rejects_invalid_threshold(threshold: object) -> None:
    with pytest.raises(InvalidRebalancePolicyError, match="threshold"):
        RebalancePolicy(RebalanceFrequency.DAILY, threshold=threshold)  # type: ignore[arg-type]


def test_strategy_definition_round_trips_and_preserves_selected_price_field() -> None:
    definition = _definition()

    restored = StrategyDefinition.from_json(definition.to_json())

    assert restored == definition
    assert restored.price_field is PriceField.ADJUSTED_CLOSE
    assert restored.rules[0].condition is not None
    first_condition = restored.rules[0].condition.children[0]  # type: ignore[union-attr]
    assert first_condition.to_dict()["threshold"] == {"type": "relative", "value": 0.04}


def test_strategy_definition_rejects_duplicate_assets_but_does_not_evaluate_weights() -> None:
    with pytest.raises(InvalidStrategyError, match="duplicate symbols"):
        StrategyDefinition(
            "strategy",
            "Strategy",
            "",
            (AssetReference("QQQ"), AssetReference("qqq")),
            PriceField.RAW_CLOSE,
            (),
            FallbackAllocation((_allocation("QQQ", 1.0),)),
            RebalancePolicy(RebalanceFrequency.DAILY),
        )


def test_strategy_version_hashes_configuration_and_is_immutable() -> None:
    configuration = _definition()
    version = StrategyVersion(
        strategy_id=configuration.strategy_id,
        version_id="v1",
        version_number=1,
        created_at=datetime(2026, 9, 6, tzinfo=UTC),
        configuration=configuration,
        status=StrategyStatus.ACTIVE,
    )

    restored = StrategyVersion.from_json(version.to_json())

    assert version.content_hash is not None
    assert len(version.content_hash) == 64
    assert restored == version
    assert restored.content_hash == version.content_hash
    with pytest.raises(FrozenInstanceError):
        version.version_number = 2  # type: ignore[misc]


def test_strategy_version_rejects_mismatched_identity_or_hash() -> None:
    configuration = _definition()
    common = {
        "version_id": "v1",
        "version_number": 1,
        "created_at": datetime(2026, 9, 6),
        "configuration": configuration,
    }
    with pytest.raises(InvalidStrategyVersionError, match="must match"):
        StrategyVersion(strategy_id="other", **common)
    with pytest.raises(InvalidStrategyVersionError, match="does not match"):
        StrategyVersion(strategy_id=configuration.strategy_id, content_hash="bad", **common)
