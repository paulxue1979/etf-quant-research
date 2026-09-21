"""Shared validation and result construction for PHASE 2 indicators."""

from __future__ import annotations

from collections.abc import Sequence

from data.derived import DerivedWeeklyDataSet
from data.models import HistoricalDataSet, PriceField, Timeframe
from data.validation import validate_historical_data
from indicators.exceptions import IndicatorParameterError
from indicators.models import IndicatorKind, IndicatorPoint, IndicatorSeries


def prepare_prices(
    data: HistoricalDataSet | DerivedWeeklyDataSet,
    period: int,
    price_field: PriceField,
) -> tuple[float, ...]:
    """Validate input and extract one explicitly chosen historical price series."""
    _validate_period(period)
    if not isinstance(price_field, PriceField):
        raise IndicatorParameterError("price_field must be a PriceField value")
    if isinstance(data, HistoricalDataSet):
        validate_historical_data(data)
    elif not isinstance(data, DerivedWeeklyDataSet):
        raise IndicatorParameterError("data must be a historical or derived weekly data set")
    return tuple(point.price_for(price_field) for point in data.points)


def build_series(
    *,
    kind: IndicatorKind,
    period: int,
    price_field: PriceField,
    data: HistoricalDataSet | DerivedWeeklyDataSet,
    values: Sequence[float | None],
) -> IndicatorSeries:
    """Preserve the input date index exactly when returning indicator values."""
    return IndicatorSeries(
        kind=kind,
        period=period,
        price_field_used=price_field,
        points=tuple(
            IndicatorPoint(date=point.date, value=value)
            for point, value in zip(data.points, values)
        ),
        timeframe=(Timeframe.DAILY if isinstance(data, HistoricalDataSet) else Timeframe.WEEKLY),
    )


def _validate_period(period: int) -> None:
    if isinstance(period, bool) or not isinstance(period, int) or period <= 0:
        raise IndicatorParameterError("period must be a positive integer")
