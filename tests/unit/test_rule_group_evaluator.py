from __future__ import annotations

from datetime import date, timedelta

import pytest

from data.models import (
    DataSource,
    HistoricalDataRequest,
    HistoricalDataSet,
    MarketDataPoint,
    PriceField,
)
from indicators import exponential_moving_average, moving_average
from strategies import (
    ComparisonOperator,
    Condition,
    EvaluationContext,
    LogicalOperator,
    Operand,
    OperandType,
    RuleGroup,
    Threshold,
    ThresholdType,
    evaluate_rule_group,
)
from strategies.exceptions import (
    InvalidRuleGroupError,
    MissingIndicatorError,
    RuleGroupEvaluationError,
)

START = date(2024, 1, 2)


def _data(symbol: str, prices: list[float]) -> HistoricalDataSet:
    points = tuple(
        MarketDataPoint(
            date=START + timedelta(days=index),
            open=price,
            high=price,
            low=price,
            close=price,
            volume=100.0,
            adj_open=price,
            adj_high=price,
            adj_low=price,
            adj_close=price,
            adj_volume=100.0,
            div_cash=0.0,
            split_factor=1.0,
        )
        for index, price in enumerate(prices)
    )
    return HistoricalDataSet(
        HistoricalDataRequest(symbol, START, START + timedelta(days=len(points) - 1)),
        points,
        DataSource.API_FRESH,
    )


def _context(*datasets: HistoricalDataSet) -> EvaluationContext:
    indicators = []
    for dataset in datasets:
        symbol = dataset.request.symbol
        indicators.extend(
            (
                (
                    symbol,
                    moving_average(dataset, period=2, price_field=PriceField.RAW_CLOSE),
                ),
                (
                    symbol,
                    moving_average(dataset, period=3, price_field=PriceField.RAW_CLOSE),
                ),
                (
                    symbol,
                    exponential_moving_average(
                        dataset, period=2, price_field=PriceField.RAW_CLOSE
                    ),
                ),
            )
        )
    return EvaluationContext.from_components(
        {dataset.request.symbol: dataset for dataset in datasets}, indicators
    )


def _constant(value: float) -> Operand:
    return Operand("QQQ", OperandType.CONSTANT, value=value)


def _price(symbol: str = "QQQ") -> Operand:
    return Operand(symbol, OperandType.PRICE, price_field=PriceField.RAW_CLOSE)


def _ma(period: int, symbol: str = "QQQ") -> Operand:
    return Operand(symbol, OperandType.MA, period, PriceField.RAW_CLOSE)


def _ema(period: int, symbol: str = "QQQ") -> Operand:
    return Operand(symbol, OperandType.EMA, period, PriceField.RAW_CLOSE)


def _condition(left: Operand, right: Operand, passed: bool = True) -> Condition:
    return Condition(
        left,
        ComparisonOperator.GREATER_THAN if passed else ComparisonOperator.LESS_THAN,
        right,
    )


def test_and_and_or_basic_semantics() -> None:
    context = _context(_data("QQQ", [10, 20, 30]))
    true_condition = _condition(_constant(2), _constant(1))
    false_condition = _condition(_constant(1), _constant(2))

    assert evaluate_rule_group(
        RuleGroup(LogicalOperator.AND, (true_condition, true_condition)), context, START
    ).passed
    assert not evaluate_rule_group(
        RuleGroup(LogicalOperator.AND, (true_condition, false_condition, true_condition)),
        context,
        START,
    ).passed
    assert not evaluate_rule_group(
        RuleGroup(LogicalOperator.AND, (false_condition, false_condition)), context, START
    ).passed
    assert evaluate_rule_group(
        RuleGroup(LogicalOperator.OR, (false_condition, true_condition)), context, START
    ).passed
    assert not evaluate_rule_group(
        RuleGroup(LogicalOperator.OR, (false_condition, false_condition)), context, START
    ).passed
    assert evaluate_rule_group(
        RuleGroup(LogicalOperator.OR, (true_condition, true_condition)), context, START
    ).passed


def test_nested_groups_preserve_recursive_results_and_ids() -> None:
    context = _context(_data("QQQ", [10, 20, 30]))
    first = _condition(_constant(2), _constant(1))
    second = _condition(_constant(1), _constant(2))
    nested = RuleGroup(LogicalOperator.OR, (first, second))
    root = RuleGroup(LogicalOperator.AND, (nested, first))

    result = evaluate_rule_group(root, context, START, rule_group_id="root")

    assert result.passed
    assert result.rule_group_id == "root"
    assert result.child_results[0].rule_group_id == "root.children[0]"
    nested_result = result.child_results[0]
    assert nested_result.child_results[0].condition_id == "root.children[0].children[0]"
    assert result.child_results[1].condition_id == "root.children[1]"
    assert "RuleGroup(root.children[0])" in result.explanation
    assert "TRUE" in result.explanation


def test_three_level_nested_group_and_single_child_groups() -> None:
    context = _context(_data("QQQ", [10, 20, 30]))
    condition = _condition(_constant(2), _constant(1))
    group = RuleGroup(
        LogicalOperator.AND,
        (RuleGroup(LogicalOperator.OR, (RuleGroup(LogicalOperator.AND, (condition,)),)),),
    )

    result = evaluate_rule_group(group, context, START)

    assert result.passed
    assert result.child_results[0].child_results[0].child_results[0].passed


def test_thresholds_and_indicator_comparisons_are_delegated_to_phase_4c() -> None:
    context = _context(_data("QQQ", [90, 100, 110]))
    threshold_condition = Condition(
        _price(),
        ComparisonOperator.GREATER_THAN,
        _ma(2),
        Threshold(ThresholdType.RELATIVE, 0.04),
    )
    indicator_condition = Condition(
        _ema(2),
        ComparisonOperator.GREATER_THAN,
        _ma(3),
        Threshold(ThresholdType.RELATIVE, -0.03),
    )

    result = evaluate_rule_group(
        RuleGroup(
            LogicalOperator.AND,
            (threshold_condition, RuleGroup(LogicalOperator.OR, (indicator_condition,))),
        ),
        context,
        START + timedelta(days=2),
    )

    assert result.passed
    assert result.child_results[0].effective_right_value == pytest.approx(109.2)


def test_multi_asset_children_use_their_own_values() -> None:
    context = _context(
        _data("QQQ", [10, 20, 30]),
        _data("SPY", [30, 40, 50]),
        _data("TQQQ", [5, 10, 15]),
    )
    group = RuleGroup(
        LogicalOperator.AND,
        (
            Condition(_ma(2, "QQQ"), ComparisonOperator.LESS_THAN, _ma(2, "SPY")),
            Condition(_price("TQQQ"), ComparisonOperator.LESS_THAN, _price("SPY")),
        ),
    )

    result = evaluate_rule_group(group, context, START + timedelta(days=2))

    assert result.passed
    assert result.child_results[0].left_operand_result.asset == "QQQ"
    assert result.child_results[1].left_operand_result.asset == "TQQQ"


def test_full_evaluation_propagates_error_after_evaluating_later_children() -> None:
    context = _context(_data("QQQ", [10, 20, 30]))
    valid = _condition(_constant(2), _constant(1))
    missing_indicator = Condition(
        _ma(99), ComparisonOperator.GREATER_THAN, _constant(0)
    )
    later = _condition(_constant(3), _constant(1))

    with pytest.raises(RuleGroupEvaluationError) as exc:
        evaluate_rule_group(
            RuleGroup(LogicalOperator.AND, (valid, missing_indicator, later)),
            context,
            START,
            rule_group_id="error-root",
        )

    error = exc.value
    assert error.code == "RULE_GROUP_EVALUATION_ERROR"
    assert len(error.child_errors) == 1
    assert isinstance(error.child_errors[0], MissingIndicatorError)
    assert [item.condition_id for item in error.evaluated_children] == [
        "error-root.children[0]",
        "error-root.children[2]",
    ]


def test_nested_error_does_not_mask_later_sibling() -> None:
    context = _context(_data("QQQ", [10, 20, 30]))
    bad = Condition(_ma(99), ComparisonOperator.GREATER_THAN, _constant(0))
    good = _condition(_constant(2), _constant(1))
    nested = RuleGroup(LogicalOperator.OR, (bad, good))

    with pytest.raises(RuleGroupEvaluationError) as exc:
        evaluate_rule_group(RuleGroup(LogicalOperator.AND, (nested, good)), context, START)

    assert len(exc.value.child_errors) == 1
    assert len(exc.value.evaluated_children) == 1
    assert exc.value.evaluated_children[0].condition_id == "rule-group.children[1]"


def test_empty_or_malformed_groups_are_rejected() -> None:
    context = _context(_data("QQQ", [10, 20, 30]))
    with pytest.raises(InvalidRuleGroupError):
        RuleGroup(LogicalOperator.AND, ())

    malformed = object.__new__(RuleGroup)
    object.__setattr__(malformed, "operator", "unsupported")
    object.__setattr__(malformed, "children", (_condition(_constant(2), _constant(1)),))
    with pytest.raises(InvalidRuleGroupError):
        evaluate_rule_group(malformed, context, START)


def test_date_and_input_immutability_determinism_and_prefix_consistency() -> None:
    full = _data("QQQ", [10, 20, 30, 40])
    prefix = _data("QQQ", [10, 20, 30])
    condition = Condition(_price(), ComparisonOperator.GREATER_THAN, _ma(2))
    group = RuleGroup(LogicalOperator.AND, (condition,))
    full_context = _context(full)
    prefix_context = _context(prefix)
    before = (group, full_context.market_data, full_context.indicators, full.points)
    as_of_date = START + timedelta(days=2)

    first = evaluate_rule_group(group, full_context, as_of_date)
    second = evaluate_rule_group(group, full_context, as_of_date)
    prefix_result = evaluate_rule_group(group, prefix_context, as_of_date)

    assert first == second == prefix_result
    assert (group, full_context.market_data, full_context.indicators, full.points) == before


def test_invalid_date_and_rule_group_id_are_rejected() -> None:
    context = _context(_data("QQQ", [10, 20, 30]))
    group = RuleGroup(LogicalOperator.AND, (_condition(_constant(2), _constant(1)),))
    with pytest.raises(TypeError):
        evaluate_rule_group(group, context, "2024-01-02")  # type: ignore[arg-type]
    with pytest.raises(ValueError):
        evaluate_rule_group(group, context, START, rule_group_id=" ")
