"""Simple moving-average calculation with full-window semantics."""

from __future__ import annotations

from data.models import HistoricalDataSet, PriceField
from indicators._common import build_series, prepare_prices
from indicators.models import IndicatorKind, IndicatorSeries


def moving_average(
    data: HistoricalDataSet,
    *,
    period: int,
    price_field: PriceField,
) -> IndicatorSeries:
    """Calculate MA using only the current and prior `period - 1` observations.

    The value is `None` until a complete window exists. No partial-window value
    is fabricated.
    """
    prices = prepare_prices(data, period, price_field)
    values: list[float | None] = []
    for index in range(len(prices)):
        if index + 1 < period:
            values.append(None)
            continue
        window = prices[index - period + 1 : index + 1]
        values.append(sum(window) / period)
    return build_series(
        kind=IndicatorKind.MOVING_AVERAGE,
        period=period,
        price_field=price_field,
        data=data,
        values=values,
    )
