"""Daily all-history-to-date drawdown from the highest observed close."""

from __future__ import annotations

from data.models import HistoricalDataSet, PriceField
from data.validation import validate_historical_data
from indicators.models import IndicatorKind, IndicatorPoint, IndicatorSeries


def peak_drawdown(data: HistoricalDataSet, *, price_field: PriceField) -> IndicatorSeries:
    if data.price_field_used is not price_field:
        raise ValueError("peak drawdown price field must match the data request")
    validate_historical_data(data)
    peak = 0.0
    points: list[IndicatorPoint] = []
    for point in data.points:
        close = point.close_for(price_field)
        if close <= 0:
            raise ValueError("peak drawdown requires positive closing prices")
        if close > peak:
            peak = close
        points.append(IndicatorPoint(point.date, (close - peak) / peak))
    return IndicatorSeries(IndicatorKind.PEAK_DRAWDOWN, 1, price_field, tuple(points))
