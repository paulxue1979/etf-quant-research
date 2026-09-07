from __future__ import annotations

from dataclasses import FrozenInstanceError
from datetime import UTC, date, datetime

import pytest

from data.models import PriceField
from strategies import (
    Allocation,
    AllocationRule,
    AllocationSource,
    AssetReference,
    ComparisonOperator,
    Condition,
    FallbackAllocation,
    LogicalOperator,
    Operand,
    OperandType,
    RebalanceFrequency,
    RebalancePolicy,
    RuleGroupResult,
    StrategyDefinition,
    StrategyStatus,
    StrategyVersion,
    TargetAllocationResult,
    build_signal,
)
from strategies.evaluation import ConditionResult, OperandValue
from strategies.exceptions import (
    EvaluationError,
    InvalidSignalInputError,
    SignalEvaluationPropagationError,
    SignalInputConsistencyError,
)

AS_OF = date(2026, 9, 7)


def _allocation(symbol: str, weight: float) -> Allocation:
    return Allocation(symbol, weight)


def _version(price_field: PriceField = PriceField.ADJUSTED_CLOSE) -> StrategyVersion:
    condition = Condition(
        Operand("QQQ", OperandType.PRICE, price_field=price_field),
        ComparisonOperator.GREATER_THAN,
        Operand("QQQ", OperandType.MA, period=200, price_field=price_field),
    )
    definition = StrategyDefinition(
        strategy_id="signal-test",
        name="Signal Test",
        description="Signal engine fixture",
        assets=tuple(AssetReference(symbol) for symbol in ("QQQ", "TQQQ", "SGOV")),
        price_field=price_field,
        rules=(
            AllocationRule(
                "risk-on",
                "Risk On",
                100,
                (_allocation("QQQ", 0.7), _allocation("TQQQ", 0.2), _allocation("SGOV", 0.1)),
                condition,
            ),
        ),
        fallback=FallbackAllocation((_allocation("SGOV", 1.0),), name="risk-off"),
        rebalance_policy=RebalancePolicy(RebalanceFrequency.DAILY),
    )
    return StrategyVersion(
        strategy_id=definition.strategy_id,
        version_id="signal-v1",
        version_number=1,
        created_at=datetime(2026, 9, 7, tzinfo=UTC),
        configuration=definition,
        status=StrategyStatus.ACTIVE,
    )


def _group(
    passed: bool = True,
    *,
    when: date = AS_OF,
    field: PriceField | None = PriceField.ADJUSTED_CLOSE,
    nested: bool = False,
) -> RuleGroupResult:
    child = ConditionResult(
        condition_id="root.children[0]",
        date=when,
        passed=passed,
        left_operand_result=OperandValue(104, "QQQ", OperandType.PRICE, field, when),
        right_operand_result=OperandValue(100, "QQQ", OperandType.MA, field, when),
        operator=ComparisonOperator.GREATER_THAN,
        threshold=None,
        effective_right_value=100,
        explanation="condition result",
    )
    result: ConditionResult | RuleGroupResult = child
    if nested:
        result = RuleGroupResult(
            "root.children[0]", when, LogicalOperator.OR, passed, (child,), "nested group"
        )
    return RuleGroupResult("root", when, LogicalOperator.AND, passed, (result,), "root group")


def _target(
    *, when: date = AS_OF, fallback: bool = False, rule_id: str | None = "risk-on"
) -> TargetAllocationResult:
    allocations = (
        (_allocation("SGOV", 1.0),)
        if fallback
        else (_allocation("QQQ", 0.7), _allocation("TQQQ", 0.2), _allocation("SGOV", 0.1))
    )
    return TargetAllocationResult(
        when,
        "risk-off" if fallback else rule_id,
        fallback,
        allocations,
        0.0,
        0.0,
        "allocation result",
    )


def test_builds_traceable_multi_asset_signal_and_preserves_nested_tree() -> None:
    version, group, target = _version(), _group(nested=True), _target()
    before = (version, group, target)

    signal = build_signal(version, group, target, source_data_reference="batch-2026-09-07")

    assert signal.date == AS_OF
    assert signal.strategy_version_id == "signal-v1"
    assert signal.matched_rule_id == "risk-on"
    assert signal.allocation_source is AllocationSource.RULE_MATCH
    assert signal.price_field_used is PriceField.ADJUSTED_CLOSE
    assert signal.condition_results is group
    assert signal.target_allocation is target
    assert signal.rule_group_result.child_results[0].rule_group_id == "root.children[0]"
    assert tuple(signal.target_allocation.weights) == ("QQQ", "TQQQ", "SGOV")
    assert "risk-on" in signal.explanation
    assert (version, group, target) == before


def test_fallback_has_distinct_provenance_and_no_matched_rule() -> None:
    signal = build_signal(_version(), _group(False), _target(fallback=True))

    assert signal.matched_rule_id is None
    assert signal.allocation_source is AllocationSource.FALLBACK
    assert signal.target_allocation.used_fallback
    assert "fallback" in signal.explanation


def test_fallback_provenance_must_match_the_configured_fallback() -> None:
    target = TargetAllocationResult(
        AS_OF,
        "unknown-fallback",
        True,
        (_allocation("SGOV", 1.0),),
        0.0,
        0.0,
        "malformed fallback provenance",
    )

    with pytest.raises(SignalInputConsistencyError, match="configured fallback"):
        build_signal(_version(), _group(False), target)


@pytest.mark.parametrize("field", [PriceField.RAW_CLOSE, PriceField.ADJUSTED_CLOSE])
def test_price_field_propagates_without_switching(field: PriceField) -> None:
    signal = build_signal(_version(field), _group(field=field), _target())

    assert signal.price_field_used is field


def test_date_price_field_and_rule_provenance_mismatches_are_rejected() -> None:
    with pytest.raises(SignalInputConsistencyError, match="date"):
        build_signal(_version(), _group(when=date(2026, 9, 8)), _target())
    with pytest.raises(SignalInputConsistencyError, match="price fields"):
        build_signal(_version(), _group(field=PriceField.RAW_CLOSE), _target())
    with pytest.raises(SignalInputConsistencyError, match="unknown rule"):
        build_signal(_version(), _group(), _target(rule_id="unknown"))
    with pytest.raises(SignalInputConsistencyError, match="non-passing"):
        build_signal(_version(), _group(False), _target())


def test_error_and_missing_inputs_propagate_without_fallback() -> None:
    error = EvaluationError("upstream failure")
    with pytest.raises(SignalEvaluationPropagationError) as exc:
        build_signal(_version(), error, _target(fallback=True))
    assert exc.value.cause is error
    with pytest.raises(SignalEvaluationPropagationError):
        build_signal(_version(), _group(), error)
    with pytest.raises(InvalidSignalInputError):
        build_signal(_version(), None, _target())  # type: ignore[arg-type]
    with pytest.raises(InvalidSignalInputError):
        build_signal(_version(), _group(), None)  # type: ignore[arg-type]


def test_signal_is_immutable_deterministic_and_prefix_consistent() -> None:
    version, group, target = _version(), _group(), _target()
    signals = [
        build_signal(version, group, target, source_data_reference="batch-1")
        for _ in range(3)
    ]

    assert signals[0] == signals[1] == signals[2]
    assert signals[0].to_dict() == signals[1].to_dict()
    with pytest.raises(FrozenInstanceError):
        signals[0].date = date(2026, 9, 8)  # type: ignore[misc]
    serialized = signals[0].to_dict()
    assert serialized["condition_results"]["date"] == AS_OF.isoformat()
    assert serialized["target_allocation"]["allocations"][0]["symbol"] == "QQQ"
    assert serialized["source_data_reference"] == "batch-1"
