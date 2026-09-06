"""Exponential moving-average calculation with explicit initialization."""

from __future__ import annotations

from data.models import HistoricalDataSet, PriceField
from indicators._common import build_series, prepare_prices
from indicators.models import IndicatorKind, IndicatorSeries


def exponential_moving_average(
    data: HistoricalDataSet,
    *,
    period: int,
    price_field: PriceField,
) -> IndicatorSeries:
    """Calculate EMA seeded by the first complete window's simple average.

    Values before the seed are `None`. The seed at index `period - 1` is the
    first `period` prices' simple average. Later values use
    `EMA_t = alpha * price_t + (1 - alpha) * EMA_(t - 1)`, where
    `alpha = 2 / (period + 1)`.
    """
    prices = prepare_prices(data, period, price_field)
    values: list[float | None] = [None] * len(prices)
    if len(prices) >= period:
        previous = sum(prices[:period]) / period
        values[period - 1] = previous
        alpha = 2.0 / (period + 1)
        for index in range(period, len(prices)):
            previous = alpha * prices[index] + (1.0 - alpha) * previous
            values[index] = previous
    return build_series(
        kind=IndicatorKind.EXPONENTIAL_MOVING_AVERAGE,
        period=period,
        price_field=price_field,
        data=data,
        values=values,
    )
