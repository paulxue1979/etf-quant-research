from __future__ import annotations

from datetime import date

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
    RuleGroupResult,
    StrategyDefinition,
    Threshold,
    ThresholdType,
    resolve_allocations,
)
from strategies.evaluation import ConditionResult, OperandValue
from strategies.exceptions import (
    EvaluationError,
    InvalidAllocationConfigurationError,
    MissingRuleEvaluationError,
    RuleEvaluationPropagationError,
)

AS_OF = date(2026, 9, 7)


def _allocation(symbol: str, weight: float, **bounds: float) -> Allocation:
    return Allocation(symbol, weight, **bounds)


def _condition() -> Condition:
    return Condition(
        Operand("QQQ", OperandType.PRICE, price_field=PriceField.ADJUSTED_CLOSE),
        ComparisonOperator.GREATER_THAN,
        Operand("QQQ", OperandType.MA, period=200, price_field=PriceField.ADJUSTED_CLOSE),
        Threshold(ThresholdType.RELATIVE, 0.04),
    )


def _result(passed: bool, rule_group_id: str = "group", when: date = AS_OF) -> RuleGroupResult:
    left = OperandValue(101.0, "QQQ", OperandType.PRICE, PriceField.ADJUSTED_CLOSE, when)
    right = OperandValue(100.0, "QQQ", OperandType.MA, PriceField.ADJUSTED_CLOSE, when)
    child = ConditionResult(
        condition_id=f"{rule_group_id}.children[0]",
        date=when,
        passed=passed,
        left_operand_result=left,
        right_operand_result=right,
        operator=ComparisonOperator.GREATER_THAN,
        threshold=Threshold(ThresholdType.RELATIVE, 0.0),
        effective_right_value=100.0,
        explanation=f"condition={'TRUE' if passed else 'FALSE'}",
    )
    return RuleGroupResult(
        rule_group_id=rule_group_id,
        date=when,
        operator=LogicalOperator.AND,
        passed=passed,
        child_results=(child,),
        explanation=f"group={'TRUE' if passed else 'FALSE'}",
    )


def _strategy(
    assets: tuple[str, ...] = ("QQQ", "TQQQ", "SGOV"),
    rules: tuple[AllocationRule, ...] = (),
    fallback: FallbackAllocation | None = None,
) -> StrategyDefinition:
    return StrategyDefinition(
        strategy_id="resolver-test",
        name="Resolver Test",
        description="Allocation resolver fixture",
        assets=tuple(AssetReference(symbol) for symbol in assets),
        price_field=PriceField.ADJUSTED_CLOSE,
        rules=rules,
        fallback=fallback or FallbackAllocation((_allocation("SGOV", 1.0),)),
        rebalance_policy=RebalancePolicy(RebalanceFrequency.DAILY),
    )


def _rule(
    rule_id: str,
    priority: int,
    allocations: tuple[Allocation, ...],
    *,
    condition: Condition | RuleGroup | None = None,
    remaining: RemainingAllocation | None = None,
) -> AllocationRule:
    return AllocationRule(rule_id, rule_id, priority, allocations, condition, remaining)


def test_resolves_single_two_three_and_four_asset_allocations() -> None:
    cases = [
        (("QQQ",), (_allocation("QQQ", 1.0),)),
        (("QQQ", "SPY"), (_allocation("QQQ", 0.6), _allocation("SPY", 0.4))),
        (
            ("QQQ", "TQQQ", "SGOV"),
            (_allocation("QQQ", 0.3), _allocation("TQQQ", 0.5), _allocation("SGOV", 0.2)),
        ),
        (
            ("QQQ", "TQQQ", "SPY", "SGOV"),
            (
                _allocation("QQQ", 0.25),
                _allocation("TQQQ", 0.25),
                _allocation("SPY", 0.25),
                _allocation("SGOV", 0.25),
            ),
        ),
    ]

    for assets, allocations in cases:
        strategy = _strategy(assets, fallback=FallbackAllocation(allocations))
        result = resolve_allocations(strategy, {}, AS_OF)
        assert tuple(result.weights) == assets
        assert sum(result.weights.values()) == pytest.approx(1.0)


def test_priority_selects_highest_matching_rule_and_reversal_changes_result() -> None:
    high = _rule(
        "high",
        100,
        (_allocation("QQQ", 0.8), _allocation("SGOV", 0.2)),
        condition=_condition(),
    )
    low = _rule(
        "low",
        90,
        (_allocation("QQQ", 0.2), _allocation("SGOV", 0.8)),
        condition=_condition(),
    )
    strategy = _strategy(rules=(low, high))

    result = resolve_allocations(strategy, {"high": _result(True), "low": _result(True)}, AS_OF)

    assert result.matched_rule_id == "high"
    assert result.weights["QQQ"] == pytest.approx(0.8)

    reversed_high = _rule(
        "high",
        80,
        (_allocation("QQQ", 0.8), _allocation("SGOV", 0.2)),
        condition=_condition(),
    )
    reversed_strategy = _strategy(rules=(low, reversed_high))
    reversed_result = resolve_allocations(
        reversed_strategy, {"high": _result(True), "low": _result(True)}, AS_OF
    )
    assert reversed_result.matched_rule_id == "low"
    assert reversed_result.weights["QQQ"] == pytest.approx(0.2)


def test_false_conditional_result_uses_fallback_and_true_result_does_not() -> None:
    rule = _rule(
        "risk-on",
        100,
        (_allocation("QQQ", 0.7), _allocation("SGOV", 0.3)),
        condition=_condition(),
    )
    strategy = _strategy(rules=(rule,))

    fallback_result = resolve_allocations(strategy, {"risk-on": _result(False)}, AS_OF)
    matched_result = resolve_allocations(strategy, {"risk-on": _result(True)}, AS_OF)

    assert fallback_result.used_fallback
    assert fallback_result.matched_rule_id == "fallback"
    assert fallback_result.weights == {"SGOV": 1.0}
    assert not matched_result.used_fallback
    assert matched_result.matched_rule_id == "risk-on"


def test_missing_and_error_rule_results_are_not_converted_to_false() -> None:
    rule = _rule("risk-on", 100, (_allocation("QQQ", 1.0),), condition=_condition())
    strategy = _strategy(rules=(rule,))

    with pytest.raises(MissingRuleEvaluationError):
        resolve_allocations(strategy, {}, AS_OF)

    cause = EvaluationError("upstream evaluation failed")
    with pytest.raises(RuleEvaluationPropagationError) as exc:
        resolve_allocations(strategy, {"risk-on": cause}, AS_OF)
    assert exc.value.rule_id == "risk-on"
    assert exc.value.cause is cause


def test_duplicate_priorities_and_unknown_rule_results_are_rejected() -> None:
    first = _rule("first", 100, (_allocation("QQQ", 1.0),))
    second = _rule("second", 100, (_allocation("SGOV", 1.0),))

    with pytest.raises(InvalidAllocationConfigurationError, match="duplicate priorities"):
        resolve_allocations(_strategy(rules=(first, second)), {}, AS_OF)
    with pytest.raises(InvalidAllocationConfigurationError, match="unknown rule IDs"):
        resolve_allocations(_strategy(rules=(first,)), {"unknown": _result(True)}, AS_OF)


def test_remaining_allocation_and_cash_buffer_are_distinct() -> None:
    partial = _rule(
        "partial",
        100,
        (_allocation("QQQ", 0.6), _allocation("TQQQ", 0.2)),
        remaining=RemainingAllocation("SGOV"),
    )
    result = resolve_allocations(_strategy(rules=(partial,)), {}, AS_OF)
    assert result.weights == {"QQQ": 0.6, "TQQQ": 0.2, "SGOV": pytest.approx(0.2)}
    assert result.remaining_weight == pytest.approx(0.2)
    assert result.cash_buffer == 0.0

    cash = _rule("cash", 100, (_allocation("QQQ", 0.6), _allocation("TQQQ", 0.2)))
    cash_result = resolve_allocations(_strategy(rules=(cash,)), {}, AS_OF)
    assert cash_result.weights == {"QQQ": 0.6, "TQQQ": 0.2}
    assert cash_result.cash_buffer == pytest.approx(0.2)


def test_zero_weights_full_remaining_and_explicit_full_sum_are_valid() -> None:
    zero = _rule(
        "zero",
        100,
        (_allocation("QQQ", 0.0), _allocation("TQQQ", 0.3), _allocation("SGOV", 0.7)),
    )
    result = resolve_allocations(_strategy(rules=(zero,)), {}, AS_OF)
    assert result.weights["QQQ"] == 0.0
    assert result.remaining_weight == 0.0

    full_remaining = _rule(
        "full",
        100,
        (_allocation("QQQ", 1.0),),
        remaining=RemainingAllocation("SGOV"),
    )
    full_result = resolve_allocations(_strategy(rules=(full_remaining,)), {}, AS_OF)
    assert full_result.weights == {"QQQ": 1.0, "SGOV": 0.0}


@pytest.mark.parametrize(
    "weights",
    [
        (0.8, 0.5),
        (-0.1,),
        (1.1,),
    ],
)
def test_invalid_weights_are_rejected_without_normalization(
    weights: tuple[float, ...],
) -> None:
    allocations: list[Allocation] = []
    for index, weight in enumerate(weights):
        if 0 <= weight <= 1:
            allocations.append(_allocation(("QQQ", "TQQQ")[index], weight))
            continue
        malformed = object.__new__(Allocation)
        object.__setattr__(malformed, "symbol", AssetReference(("QQQ", "TQQQ")[index]))
        object.__setattr__(malformed, "target_weight", weight)
        object.__setattr__(malformed, "minimum_weight", None)
        object.__setattr__(malformed, "maximum_weight", None)
        allocations.append(malformed)  # type: ignore[arg-type]
    rule = _rule("invalid", 100, tuple(allocations))

    with pytest.raises(InvalidAllocationConfigurationError):
        resolve_allocations(_strategy(rules=(rule,)), {}, AS_OF)


def test_malformed_assets_remaining_and_fallback_are_rejected() -> None:
    unknown = _rule("unknown", 100, (_allocation("SPY", 1.0),))
    with pytest.raises(InvalidAllocationConfigurationError, match="undeclared"):
        resolve_allocations(_strategy(rules=(unknown,)), {}, AS_OF)

    malformed_rule = object.__new__(AllocationRule)
    object.__setattr__(malformed_rule, "rule_id", "malformed")
    object.__setattr__(malformed_rule, "name", "Malformed")
    object.__setattr__(malformed_rule, "priority", 100)
    object.__setattr__(malformed_rule, "allocations", (_allocation("QQQ", 0.5),))
    object.__setattr__(malformed_rule, "condition", None)
    object.__setattr__(malformed_rule, "remaining", (RemainingAllocation("SGOV"),))
    with pytest.raises(InvalidAllocationConfigurationError, match="remaining"):
        resolve_allocations(_strategy(rules=(malformed_rule,)), {}, AS_OF)  # type: ignore[arg-type]

    no_fallback = object.__new__(StrategyDefinition)
    for field in ("strategy_id", "name", "description", "assets", "price_field", "rules"):
        object.__setattr__(no_fallback, field, getattr(_strategy(), field))
    object.__setattr__(no_fallback, "fallback", None)
    object.__setattr__(no_fallback, "rebalance_policy", _strategy().rebalance_policy)
    with pytest.raises(InvalidAllocationConfigurationError, match="no valid fallback"):
        resolve_allocations(no_fallback, {}, AS_OF)  # type: ignore[arg-type]


def test_date_mismatch_and_non_mapping_inputs_are_rejected() -> None:
    rule = _rule("dated", 100, (_allocation("QQQ", 1.0),), condition=_condition())
    strategy = _strategy(rules=(rule,))
    with pytest.raises(InvalidAllocationConfigurationError, match="expected"):
        resolve_allocations(strategy, {"dated": _result(True, when=date(2026, 9, 6))}, AS_OF)
    with pytest.raises(TypeError):
        resolve_allocations(strategy, [], AS_OF)  # type: ignore[arg-type]
    with pytest.raises(TypeError):
        resolve_allocations(strategy, {}, "2026-09-07")  # type: ignore[arg-type]


def test_output_is_immutable_explainable_ordered_and_deterministic() -> None:
    rule = _rule(
        "ordered",
        100,
        (_allocation("SGOV", 0.2), _allocation("QQQ", 0.8)),
    )
    strategy = _strategy(rules=(rule,))
    first = resolve_allocations(strategy, {}, AS_OF)
    second = resolve_allocations(strategy, {}, AS_OF)

    assert first == second
    assert tuple(first.weights) == ("QQQ", "SGOV")
    assert "Selected rule ordered" in first.explanation
    assert "cash_buffer=0" in first.explanation
    with pytest.raises(TypeError):
        first.weights["QQQ"] = 0.0  # type: ignore[index]


def test_bullish_and_defensive_relative_threshold_scenarios_consume_precomputed_results() -> None:
    bullish = _rule(
        "bullish",
        100,
        (_allocation("QQQ", 0.6), _allocation("TQQQ", 0.3), _allocation("SGOV", 0.1)),
        condition=_condition(),
    )
    defensive = _rule(
        "defensive",
        90,
        (_allocation("SGOV", 1.0),),
        condition=_condition(),
    )
    strategy = _strategy(rules=(bullish, defensive))

    result = resolve_allocations(
        strategy,
        {"bullish": _result(True), "defensive": _result(False)},
        AS_OF,
    )
    assert result.matched_rule_id == "bullish"
    assert result.weights["TQQQ"] == pytest.approx(0.3)
    assert result.date == AS_OF
