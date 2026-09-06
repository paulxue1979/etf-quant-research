from __future__ import annotations

from datetime import date, timedelta
from math import inf, nan

import pytest

from data.models import (
    DataSource,
    HistoricalDataRequest,
    HistoricalDataSet,
    MarketDataPoint,
    PriceField,
)
from indicators import exponential_moving_average, moving_average
from indicators.models import IndicatorKind, IndicatorPoint, IndicatorSeries
from strategies import EvaluationContext, Operand, OperandType, evaluate_operand
from strategies.exceptions import (
    InvalidEvaluationValueError,
    MissingIndicatorError,
    MissingMarketDataError,
    MissingOperandValueError,
)

START = date(2024, 1, 2)


def _data(
    symbol: str,
    prices: list[float],
    adjusted_prices: list[float] | None = None,
) -> HistoricalDataSet:
    adjusted = adjusted_prices if adjusted_prices is not None else prices
    points = tuple(
        MarketDataPoint(
            date=START + timedelta(days=index),
            open=raw,
            high=raw,
            low=raw,
            close=raw,
            volume=100.0,
            adj_open=adjusted_value,
            adj_high=adjusted_value,
            adj_low=adjusted_value,
            adj_close=adjusted_value,
            adj_volume=100.0,
            div_cash=0.0,
            split_factor=1.0,
        )
        for index, (raw, adjusted_value) in enumerate(zip(prices, adjusted, strict=True))
    )
    return HistoricalDataSet(
        request=HistoricalDataRequest(
            symbol=symbol,
            start_date=START,
            end_date=START + timedelta(days=len(points) - 1),
        ),
        points=points,
        source=DataSource.API_FRESH,
    )


def _context(*datasets: HistoricalDataSet) -> EvaluationContext:
    market_data = {dataset.request.symbol: dataset for dataset in datasets}
    indicators = []
    for dataset in datasets:
        indicators.extend(
            (
                (
                    dataset.request.symbol,
                    moving_average(dataset, period=3, price_field=PriceField.RAW_CLOSE),
                ),
                (
                    dataset.request.symbol,
                    exponential_moving_average(
                        dataset, period=3, price_field=PriceField.RAW_CLOSE
                    ),
                ),
            )
        )
    return EvaluationContext.from_components(market_data, indicators)


def test_evaluates_explicit_raw_and_adjusted_prices() -> None:
    context = _context(_data("QQQ", [10, 20, 30], [100, 200, 300]))
    as_of_date = START + timedelta(days=2)

    raw = evaluate_operand(
        Operand("QQQ", OperandType.PRICE, price_field=PriceField.RAW_CLOSE), context, as_of_date
    )
    adjusted = evaluate_operand(
        Operand("QQQ", OperandType.PRICE, price_field=PriceField.ADJUSTED_CLOSE),
        context,
        as_of_date,
    )

    assert raw.value == 30.0
    assert raw.price_field_used is PriceField.RAW_CLOSE
    assert adjusted.value == 300.0
    assert adjusted.price_field_used is PriceField.ADJUSTED_CLOSE


def test_evaluates_ma_and_ema_on_the_requested_date() -> None:
    context = _context(_data("QQQ", [10, 20, 30, 40]))
    as_of_date = START + timedelta(days=3)

    ma = evaluate_operand(
        Operand("QQQ", OperandType.MA, 3, PriceField.RAW_CLOSE), context, as_of_date
    )
    ema = evaluate_operand(
        Operand("QQQ", OperandType.EMA, 3, PriceField.RAW_CLOSE), context, as_of_date
    )

    assert ma.value == 30.0
    assert ema.value == 30.0
    assert ma.operand_type is OperandType.MA
    assert ema.operand_type is OperandType.EMA


def test_indicator_evaluation_does_not_require_redundant_market_data() -> None:
    dataset = _data("QQQ", [10, 20, 30, 40])
    series = moving_average(dataset, period=3, price_field=PriceField.RAW_CLOSE)
    context = EvaluationContext.from_components({}, (("QQQ", series),))

    result = evaluate_operand(
        Operand("QQQ", OperandType.MA, 3, PriceField.RAW_CLOSE),
        context,
        START + timedelta(days=3),
    )

    assert result.value == 30.0


@pytest.mark.parametrize("value", [1.25, 0, -0.03])
def test_evaluates_constant_without_using_asset_or_price_field(value: float) -> None:
    result = evaluate_operand(
        Operand("QQQ", OperandType.CONSTANT, value=value),
        _context(_data("QQQ", [10, 20, 30])),
        START,
    )

    assert result.value == value
    assert result.asset is None
    assert result.price_field_used is None


def test_evaluates_assets_from_their_own_market_and_indicator_series() -> None:
    qqq = _data("QQQ", [10, 20, 30])
    spy = _data("SPY", [100, 200, 300])
    context = _context(qqq, spy)

    result = evaluate_operand(
        Operand("SPY", OperandType.MA, 3, PriceField.RAW_CLOSE), context, START + timedelta(days=2)
    )

    assert result.asset == "SPY"
    assert result.value == 200.0


def test_missing_market_data_date_indicator_and_early_indicator_value_are_explicit() -> None:
    context = _context(_data("QQQ", [10, 20, 30]))

    with pytest.raises(MissingMarketDataError) as missing_market:
        evaluate_operand(
            Operand("SPY", OperandType.PRICE, price_field=PriceField.RAW_CLOSE), context, START
        )
    with pytest.raises(MissingOperandValueError):
        evaluate_operand(
            Operand("QQQ", OperandType.PRICE, price_field=PriceField.RAW_CLOSE),
            context,
            START + timedelta(days=10),
        )
    with pytest.raises(MissingOperandValueError):
        evaluate_operand(
            Operand("QQQ", OperandType.MA, 3, PriceField.RAW_CLOSE), context, START
        )
    with pytest.raises(MissingIndicatorError):
        evaluate_operand(
            Operand("QQQ", OperandType.MA, 5, PriceField.RAW_CLOSE),
            context,
            START + timedelta(days=2),
        )

    assert missing_market.value.code == "MISSING_MARKET_DATA"


@pytest.mark.parametrize("value", [nan, inf, -inf])
def test_rejects_non_finite_indicator_values(value: float) -> None:
    dataset = _data("QQQ", [10, 20, 30])
    series = IndicatorSeries(
        kind=IndicatorKind.MOVING_AVERAGE,
        period=3,
        price_field_used=PriceField.RAW_CLOSE,
        points=(
            IndicatorPoint(START, None),
            IndicatorPoint(START + timedelta(days=1), None),
            IndicatorPoint(START + timedelta(days=2), value),
        ),
    )
    context = EvaluationContext.from_components({"QQQ": dataset}, (("QQQ", series),))

    with pytest.raises(InvalidEvaluationValueError) as exc:
        evaluate_operand(
            Operand("QQQ", OperandType.MA, 3, PriceField.RAW_CLOSE),
            context,
            START + timedelta(days=2),
        )

    assert exc.value.code == "INVALID_NUMERIC_VALUE"


def test_prefix_consistency_and_input_immutability() -> None:
    full_data = _data("QQQ", [10, 20, 30, 40, 500])
    prefix_data = _data("QQQ", [10, 20, 30, 40])
    as_of_date = START + timedelta(days=3)
    full_context = _context(full_data)
    prefix_context = _context(prefix_data)
    operand = Operand("QQQ", OperandType.EMA, 3, PriceField.RAW_CLOSE)
    before = (full_context.market_data, full_context.indicators, full_data.points)

    full_result = evaluate_operand(operand, full_context, as_of_date)
    prefix_result = evaluate_operand(operand, prefix_context, as_of_date)

    assert full_result == prefix_result
    assert (full_context.market_data, full_context.indicators, full_data.points) == before
