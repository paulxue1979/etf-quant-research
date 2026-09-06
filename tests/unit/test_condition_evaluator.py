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
    Operand,
    OperandType,
    Threshold,
    ThresholdType,
    evaluate_condition,
)
from strategies.exceptions import (
    InvalidConditionConfigurationError,
    ZeroReferenceValueError,
)

START = date(2024, 1, 2)


def _data(
    symbol: str, prices: list[float], adjusted: list[float] | None = None
) -> HistoricalDataSet:
    adjusted_prices = adjusted if adjusted is not None else prices
    points = tuple(
        MarketDataPoint(
            date=START + timedelta(days=index),
            open=price,
            high=price,
            low=price,
            close=price,
            volume=100.0,
            adj_open=adjusted_price,
            adj_high=adjusted_price,
            adj_low=adjusted_price,
            adj_close=adjusted_price,
            adj_volume=100.0,
            div_cash=0.0,
            split_factor=1.0,
        )
        for index, (price, adjusted_price) in enumerate(zip(prices, adjusted_prices, strict=True))
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
                (symbol, moving_average(dataset, period=3, price_field=PriceField.RAW_CLOSE)),
                (symbol, moving_average(dataset, period=4, price_field=PriceField.RAW_CLOSE)),
                (
                    symbol,
                    exponential_moving_average(dataset, period=3, price_field=PriceField.RAW_CLOSE),
                ),
            )
        )
    return EvaluationContext.from_components(
        {dataset.request.symbol: dataset for dataset in datasets}, indicators
    )


def _price(symbol: str = "QQQ", field: PriceField = PriceField.RAW_CLOSE) -> Operand:
    return Operand(symbol, OperandType.PRICE, price_field=field)


def _ma(period: int, symbol: str = "QQQ") -> Operand:
    return Operand(symbol, OperandType.MA, period, PriceField.RAW_CLOSE)


def _ema(period: int, symbol: str = "QQQ") -> Operand:
    return Operand(symbol, OperandType.EMA, period, PriceField.RAW_CLOSE)


def _constant(value: float) -> Operand:
    return Operand("QQQ", OperandType.CONSTANT, value=value)


@pytest.mark.parametrize(
    ("operator", "passed"),
    [
        (ComparisonOperator.GREATER_THAN, True),
        (ComparisonOperator.GREATER_OR_EQUAL, True),
        (ComparisonOperator.LESS_THAN, False),
        (ComparisonOperator.LESS_OR_EQUAL, False),
        (ComparisonOperator.EQUAL, False),
    ],
)
def test_comparison_operators_are_deterministic(operator: ComparisonOperator, passed: bool) -> None:
    result = evaluate_condition(
        Condition(_constant(2), operator, _constant(1)),
        _context(_data("QQQ", [1, 2, 3, 4])),
        START,
        condition_id="operators",
    )

    assert result.passed is passed
    assert result.condition_id == "operators"
    assert result.effective_right_value == 1.0


def test_equal_without_relative_threshold_uses_exact_semantics() -> None:
    result = evaluate_condition(
        Condition(_constant(2), ComparisonOperator.EQUAL, _constant(2)),
        _context(_data("QQQ", [1, 2, 3, 4])),
        START,
    )

    assert result.passed


@pytest.mark.parametrize(
    ("operator", "threshold", "passed"),
    [
        (ComparisonOperator.GREATER_THAN, 0.04, True),
        (ComparisonOperator.LESS_THAN, -0.03, True),
        (ComparisonOperator.GREATER_OR_EQUAL, 0.0, True),
        (ComparisonOperator.LESS_OR_EQUAL, 0.0, True),
    ],
)
def test_relative_threshold_uses_right_times_one_plus_threshold(
    operator: ComparisonOperator, threshold: float, passed: bool
) -> None:
    right = 100.0
    left = {0.04: 105.0, -0.03: 96.0, 0.0: 100.0}[threshold]
    result = evaluate_condition(
        Condition(
            _constant(left),
            operator,
            _constant(right),
            Threshold(ThresholdType.RELATIVE, threshold),
        ),
        _context(_data("QQQ", [1, 2, 3, 4])),
        START,
    )

    assert result.passed is passed
    assert result.effective_right_value == pytest.approx(right * (1 + threshold))
    assert "relative threshold" in result.explanation


def test_price_ma_and_indicator_comparisons_include_effective_value_and_explanation() -> None:
    context = _context(_data("QQQ", [90, 100, 100, 110, 120]))
    as_of_date = START + timedelta(days=4)
    result = evaluate_condition(
        Condition(
            _price(),
            ComparisonOperator.GREATER_THAN,
            _ma(3),
            Threshold(ThresholdType.RELATIVE, 0.04),
        ),
        context,
        as_of_date,
        condition_id="price-over-ma",
    )
    indicator_result = evaluate_condition(
        Condition(_ema(3), ComparisonOperator.GREATER_THAN, _ma(4)), context, as_of_date
    )

    assert result.left_operand_result.value == 120.0
    assert result.right_operand_result.value == pytest.approx(110.0)
    assert result.effective_right_value == pytest.approx(114.4)
    assert result.passed
    assert "PRICE(QQQ)=120" in result.explanation
    assert "MA(QQQ)=110" in result.explanation
    assert indicator_result.passed


def test_multi_asset_condition_uses_each_assets_own_values() -> None:
    context = _context(_data("QQQ", [10, 20, 30, 40]), _data("SPY", [30, 40, 50, 60]))
    result = evaluate_condition(
        Condition(_ma(3, "QQQ"), ComparisonOperator.LESS_THAN, _ma(3, "SPY")),
        context,
        START + timedelta(days=3),
    )

    assert result.left_operand_result.asset == "QQQ"
    assert result.right_operand_result.asset == "SPY"
    assert result.passed


def test_mixed_raw_and_adjusted_price_fields_are_rejected() -> None:
    context = _context(_data("QQQ", [10, 20, 30], [100, 200, 300]))

    with pytest.raises(InvalidConditionConfigurationError) as exc:
        evaluate_condition(
            Condition(
                _price("QQQ", PriceField.RAW_CLOSE),
                ComparisonOperator.GREATER_THAN,
                _price("QQQ", PriceField.ADJUSTED_CLOSE),
            ),
            context,
            START + timedelta(days=2),
        )

    assert exc.value.code == "INVALID_CONDITION_CONFIGURATION"


def test_zero_reference_relative_equal_relative_and_absolute_thresholds_are_rejected() -> None:
    context = _context(_data("QQQ", [1, 2, 3, 4]))
    with pytest.raises(ZeroReferenceValueError) as zero_reference:
        evaluate_condition(
            Condition(
                _constant(1),
                ComparisonOperator.GREATER_THAN,
                _constant(0),
                Threshold(ThresholdType.RELATIVE, 0.01),
            ),
            context,
            START,
        )
    with pytest.raises(InvalidConditionConfigurationError):
        evaluate_condition(
            Condition(
                _constant(1),
                ComparisonOperator.EQUAL,
                _constant(1),
                Threshold(ThresholdType.RELATIVE, 0.0),
            ),
            context,
            START,
        )
    with pytest.raises(InvalidConditionConfigurationError):
        evaluate_condition(
            Condition(
                _constant(2),
                ComparisonOperator.GREATER_THAN,
                _constant(1),
                Threshold(ThresholdType.ABSOLUTE, 1),
            ),
            context,
            START,
        )

    assert zero_reference.value.code == "ZERO_REFERENCE_VALUE"


def test_prefix_consistency_and_input_immutability() -> None:
    full = _data("QQQ", [10, 20, 30, 40, 500])
    prefix = _data("QQQ", [10, 20, 30, 40])
    as_of_date = START + timedelta(days=3)
    condition = Condition(_price(), ComparisonOperator.GREATER_THAN, _ma(3))
    full_context = _context(full)
    prefix_context = _context(prefix)
    before = (full_context.market_data, full_context.indicators, full.points)

    full_result = evaluate_condition(condition, full_context, as_of_date)
    prefix_result = evaluate_condition(condition, prefix_context, as_of_date)

    assert full_result == prefix_result
    assert (full_context.market_data, full_context.indicators, full.points) == before
