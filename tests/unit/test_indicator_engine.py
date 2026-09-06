from __future__ import annotations

from dataclasses import replace
from datetime import date, timedelta

import pytest

from data.exceptions import DataValidationError
from data.models import (
    DataSource,
    HistoricalDataRequest,
    HistoricalDataSet,
    MarketDataPoint,
    PriceField,
)
from indicators import exponential_moving_average, moving_average
from indicators.exceptions import IndicatorParameterError
from indicators.models import IndicatorKind


def _data(
    raw_prices: list[float],
    adjusted_prices: list[float] | None = None,
    *,
    start: date = date(2024, 1, 2),
) -> HistoricalDataSet:
    adjusted = adjusted_prices if adjusted_prices is not None else raw_prices
    if len(raw_prices) != len(adjusted):
        raise ValueError("test prices must have equal lengths")
    points = tuple(
        MarketDataPoint(
            date=start + timedelta(days=index),
            open=raw_price,
            high=raw_price,
            low=raw_price,
            close=raw_price,
            volume=100.0,
            adj_open=adjusted_price,
            adj_high=adjusted_price,
            adj_low=adjusted_price,
            adj_close=adjusted_price,
            adj_volume=100.0,
            div_cash=0.0,
            split_factor=1.0,
        )
        for index, (raw_price, adjusted_price) in enumerate(zip(raw_prices, adjusted, strict=True))
    )
    request = HistoricalDataRequest(
        symbol="QQQ",
        start_date=start,
        end_date=start + timedelta(days=max(len(points) - 1, 0)),
    )
    return HistoricalDataSet(request=request, points=points, source=DataSource.API_FRESH)


@pytest.mark.parametrize(
    ("period", "expected"),
    [(5, 198.0), (10, 195.5), (20, 190.5), (50, 175.5), (100, 150.5), (200, 100.5)],
)
def test_ma_standard_periods_use_complete_rolling_windows(period: int, expected: float) -> None:
    series = moving_average(
        _data(list(range(1, 201))), period=period, price_field=PriceField.RAW_CLOSE
    )

    assert series.kind is IndicatorKind.MOVING_AVERAGE
    assert series.name == f"ma_{period}"
    assert series.price_field_used is PriceField.RAW_CLOSE
    assert [point.value for point in series.points[: period - 1]] == [None] * (period - 1)
    assert series.points[-1].value == expected


@pytest.mark.parametrize(
    ("period", "expected"),
    [(7, 197.0), (30, 185.5), (120, 140.5)],
)
def test_custom_ma_periods(period: int, expected: float) -> None:
    series = moving_average(
        _data(list(range(1, 201))), period=period, price_field=PriceField.RAW_CLOSE
    )

    assert series.points[-1].value == expected


@pytest.mark.parametrize("calculator", [moving_average, exponential_moving_average])
def test_price_field_is_explicit_and_traceable(calculator) -> None:
    data = _data(list(range(1, 11)), [price * 10.0 for price in range(1, 11)])
    raw = calculator(data, period=7, price_field=PriceField.RAW_CLOSE)
    adjusted = calculator(data, period=7, price_field=PriceField.ADJUSTED_CLOSE)

    assert raw.points[-1].value == 7.0
    assert adjusted.points[-1].value == 70.0
    assert adjusted.price_field_used is PriceField.ADJUSTED_CLOSE
    assert [point.date for point in raw.points] == [point.date for point in data.points]


@pytest.mark.parametrize(
    ("period", "seed", "next_value"),
    [(5, 3.0, 4.0), (20, 10.5, 11.5), (50, 25.5, 26.5)],
)
def test_ema_standard_periods_use_documented_sma_seed(
    period: int, seed: float, next_value: float
) -> None:
    series = exponential_moving_average(
        _data(list(range(1, 201))), period=period, price_field=PriceField.RAW_CLOSE
    )

    assert series.kind is IndicatorKind.EXPONENTIAL_MOVING_AVERAGE
    assert series.name == f"ema_{period}"
    assert [point.value for point in series.points[: period - 1]] == [None] * (period - 1)
    assert series.points[period - 1].value == pytest.approx(seed)
    assert series.points[period].value == pytest.approx(next_value)


@pytest.mark.parametrize(
    ("period", "expected"),
    [(3, [None, None, 2.0, 3.0, 4.0]), (7, [None, None, None, None, None, None, 4.0, 5.0])],
)
def test_custom_ema_has_deterministic_values(period: int, expected: list[float | None]) -> None:
    prices = [float(value) for value in range(1, len(expected) + 1)]
    series = exponential_moving_average(
        _data(prices), period=period, price_field=PriceField.RAW_CLOSE
    )

    assert [point.value for point in series.points] == expected


@pytest.mark.parametrize("calculator", [moving_average, exponential_moving_average])
def test_short_series_returns_only_missing_values(calculator) -> None:
    series = calculator(_data([1.0, 2.0, 3.0]), period=5, price_field=PriceField.RAW_CLOSE)

    assert [point.value for point in series.points] == [None, None, None]


@pytest.mark.parametrize("calculator", [moving_average, exponential_moving_average])
@pytest.mark.parametrize("period", [0, -1, 2.5, True, "5"])
def test_invalid_periods_are_rejected(calculator, period) -> None:
    with pytest.raises(IndicatorParameterError, match="positive integer"):
        calculator(_data([1.0, 2.0, 3.0]), period=period, price_field=PriceField.RAW_CLOSE)


@pytest.mark.parametrize("calculator", [moving_average, exponential_moving_average])
def test_invalid_price_field_is_rejected(calculator) -> None:
    with pytest.raises(IndicatorParameterError, match="price_field"):
        calculator(_data([1.0, 2.0, 3.0]), period=2, price_field="raw_close")  # type: ignore[arg-type]


@pytest.mark.parametrize("calculator", [moving_average, exponential_moving_average])
def test_data_validation_rejects_empty_missing_unsorted_and_duplicate_input(calculator) -> None:
    empty = _data([])
    missing = _data([1.0, 2.0, 3.0])
    missing = replace(
        missing,
        points=(replace(missing.points[0], close=float("nan")), *missing.points[1:]),
    )
    unsorted = _data([1.0, 2.0])
    unsorted = replace(unsorted, points=(unsorted.points[1], unsorted.points[0]))
    duplicate = _data([1.0, 2.0])
    duplicate = replace(
        duplicate,
        points=(
            duplicate.points[0],
            replace(duplicate.points[1], date=duplicate.points[0].date),
        ),
    )

    for invalid in (empty, missing, unsorted, duplicate):
        with pytest.raises(DataValidationError):
            calculator(invalid, period=2, price_field=PriceField.RAW_CLOSE)


@pytest.mark.parametrize("calculator", [moving_average, exponential_moving_average])
def test_calculation_does_not_modify_input_or_read_future_prices(calculator) -> None:
    prices = [float(value) for value in range(1, 11)]
    data = _data(prices)
    original = data
    full_series = calculator(data, period=3, price_field=PriceField.RAW_CLOSE)

    assert data == original
    for index in range(len(prices)):
        prefix_series = calculator(
            _data(prices[: index + 1]), period=3, price_field=PriceField.RAW_CLOSE
        )
        assert full_series.points[index].value == prefix_series.points[-1].value
