"""Shared validation and result construction for PHASE 2 indicators."""

from __future__ import annotations

from collections.abc import Sequence

from data.models import HistoricalDataSet, PriceField
from data.validation import validate_historical_data
from indicators.exceptions import IndicatorParameterError
from indicators.models import IndicatorKind, IndicatorPoint, IndicatorSeries


def prepare_prices(
    data: HistoricalDataSet,
    period: int,
    price_field: PriceField,
) -> tuple[float, ...]:
    """Validate input and extract one explicitly chosen historical price series."""
    _validate_period(period)
    if not isinstance(price_field, PriceField):
        raise IndicatorParameterError("price_field must be a PriceField value")
    validate_historical_data(data)
    return tuple(point.price_for(price_field) for point in data.points)


def build_series(
    *,
    kind: IndicatorKind,
    period: int,
    price_field: PriceField,
    data: HistoricalDataSet,
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
    )


def _validate_period(period: int) -> None:
    if isinstance(period, bool) or not isinstance(period, int) or period <= 0:
        raise IndicatorParameterError("period must be a positive integer")
