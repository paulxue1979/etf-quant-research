"""Strict data-quality validation for normalized EOD price data."""

from __future__ import annotations

import math
from datetime import date

from data.exceptions import DataValidationError
from data.models import HistoricalDataSet, MarketDataPoint


def validate_historical_data(data: HistoricalDataSet) -> None:
    """Validate without reordering, filling, or otherwise repairing market data."""
    issues: list[str] = []
    if not data.points:
        issues.append("data set is empty")

    seen_dates: set[date] = set()
    previous_date: date | None = None
    for index, point in enumerate(data.points):
        _validate_point(point, index, issues)
        if isinstance(point.date, date):
            if point.date in seen_dates:
                issues.append(f"duplicate date at row {index}")
            seen_dates.add(point.date)
            if previous_date is not None and point.date <= previous_date:
                issues.append(f"dates are not strictly ascending at row {index}")
            previous_date = point.date

    if issues:
        raise DataValidationError("Data quality validation failed: " + "; ".join(issues))


def _validate_point(point: MarketDataPoint, index: int, issues: list[str]) -> None:
    if not isinstance(point.date, date):
        issues.append(f"invalid date at row {index}")

    raw_values = (point.open, point.high, point.low, point.close, point.volume)
    adjusted_values = (
        point.adj_open,
        point.adj_high,
        point.adj_low,
        point.adj_close,
        point.adj_volume,
    )
    if not all(_is_finite(value) for value in raw_values):
        issues.append(f"raw OHLCV contains an invalid value at row {index}")
    if not all(_is_finite(value) for value in adjusted_values):
        issues.append(f"adjusted OHLCV contains an invalid value at row {index}")
    if not _is_finite(point.div_cash):
        issues.append(f"div_cash is invalid at row {index}")
    if not _is_finite(point.split_factor) or point.split_factor <= 0:
        issues.append(f"split_factor must be positive at row {index}")

    if _is_finite(point.volume) and point.volume < 0:
        issues.append(f"volume must not be negative at row {index}")
    if _is_finite(point.adj_volume) and point.adj_volume < 0:
        issues.append(f"adj_volume must not be negative at row {index}")

    _validate_ohlc(
        high=point.high,
        low=point.low,
        open_price=point.open,
        close=point.close,
        label="raw",
        index=index,
        issues=issues,
    )
    _validate_ohlc(
        high=point.adj_high,
        low=point.adj_low,
        open_price=point.adj_open,
        close=point.adj_close,
        label="adjusted",
        index=index,
        issues=issues,
    )


def _validate_ohlc(
    *,
    high: float,
    low: float,
    open_price: float,
    close: float,
    label: str,
    index: int,
    issues: list[str],
) -> None:
    if not all(_is_finite(value) for value in (high, low, open_price, close)):
        return
    if high < low:
        issues.append(f"{label} high is below low at row {index}")
    if high < open_price or high < close:
        issues.append(f"{label} high is below open or close at row {index}")
    if low > open_price or low > close:
        issues.append(f"{label} low is above open or close at row {index}")


def _is_finite(value: object) -> bool:
    return (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and math.isfinite(float(value))
    )
