"""Derived market-data contracts with explicit completed-period provenance."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date, timedelta

from data.exceptions import DataValidationError
from data.models import DataSource, HistoricalDataSet, MarketDataPoint, PriceField, Timeframe
from data.trading_calendar import (
    TradingSessionCalendar,
    default_trading_calendar,
    grouped_week_sessions,
)
from data.validation import validate_historical_data

WEEKLY_AGGREGATION_VERSION = "phase-11c-v1"


@dataclass(frozen=True)
class DerivedWeeklyBar:
    """One completed exchange week; ``available_on`` is its final session."""

    symbol: str
    period_start: date
    period_end: date
    available_on: date
    open: float
    high: float
    low: float
    close: float
    volume: float
    adj_open: float
    adj_high: float
    adj_low: float
    adj_close: float
    adj_volume: float
    div_cash: float
    split_factor: float
    source_frequency: str = "daily"
    price_field_used: PriceField = PriceField.ADJUSTED_CLOSE
    aggregation_version: str = WEEKLY_AGGREGATION_VERSION

    @property
    def date(self) -> date:
        """Indicator point compatibility date: the completed period end."""
        return self.available_on

    @property
    def timeframe(self) -> Timeframe:
        return Timeframe.WEEKLY

    def price_for(self, field: PriceField) -> float:
        if field is PriceField.RAW_CLOSE:
            return self.close
        if field is PriceField.ADJUSTED_CLOSE:
            return self.adj_close
        raise ValueError(f"Unsupported price field: {field}")

    def open_for(self, field: PriceField) -> float:
        if field is PriceField.RAW_CLOSE:
            return self.open
        if field is PriceField.ADJUSTED_CLOSE:
            return self.adj_open
        raise ValueError(f"Unsupported price field: {field}")


@dataclass(frozen=True)
class DerivedWeeklyDataSet:
    """Completed weekly bars derived only from a bounded daily snapshot."""

    symbol: str
    points: tuple[DerivedWeeklyBar, ...]
    source_request: object
    source: DataSource
    calendar_id: str = "XNYS"
    aggregation_version: str = WEEKLY_AGGREGATION_VERSION
    price_field_used: PriceField = PriceField.ADJUSTED_CLOSE

    @property
    def timeframe(self) -> Timeframe:
        return Timeframe.WEEKLY

    @property
    def points_by_date(self) -> Mapping[date, DerivedWeeklyBar]:
        return {point.available_on: point for point in self.points}

    def latest_as_of(self, as_of: date) -> DerivedWeeklyBar | None:
        eligible = [point for point in self.points if point.available_on <= as_of]
        return eligible[-1] if eligible else None


def derive_completed_weekly(
    data: HistoricalDataSet,
    *,
    calendar: TradingSessionCalendar | None = None,
    cutoff: date | None = None,
) -> DerivedWeeklyDataSet:
    """Aggregate complete exchange weeks and reject silent missing sessions.

    The cutoff is an effective information boundary.  Rows after it are ignored,
    even when a cache returned a larger snapshot.  A request-boundary partial week
    is omitted; a missing session inside a completed requested week is an error.
    """
    validate_historical_data(data)
    calendar = calendar or default_trading_calendar()
    effective_cutoff = min(cutoff or data.request.end_date, data.request.end_date)
    if effective_cutoff < data.request.start_date:
        return _empty_weekly(data, calendar)
    observed = tuple(point for point in data.points if point.date <= effective_cutoff)
    if not observed:
        return _empty_weekly(data, calendar)
    observed_dates = {point.date for point in observed}
    calendar_sessions = set(
        calendar.sessions(
            data.request.start_date - timedelta(days=7), effective_cutoff + timedelta(days=7)
        )
    )
    if any(point.date not in calendar_sessions for point in observed):
        raise DataValidationError("daily data contains a non-trading session")
    sessions = tuple(sorted(calendar_sessions))
    by_week = grouped_week_sessions(sessions)
    point_by_date = {point.date: point for point in observed}
    first_observed = observed[0].date
    bars: list[DerivedWeeklyBar] = []
    for expected in by_week.values():
        if not expected:
            continue
        # A request can begin after a week's first session.  That week is not a
        # reliable full bar and is intentionally omitted rather than fabricated.
        if expected[0] < first_observed:
            continue
        complete = effective_cutoff >= expected[-1]
        if not complete:
            continue
        missing = tuple(day for day in expected if day not in observed_dates)
        if missing:
            raise DataValidationError(
                "completed trading week is missing expected session(s): "
                + ", ".join(day.isoformat() for day in missing)
            )
        bars.append(
            _aggregate_week(
                data.request.symbol, tuple(point_by_date[day] for day in expected), expected
            )
        )
    return DerivedWeeklyDataSet(
        symbol=data.request.symbol,
        points=tuple(sorted(bars, key=lambda item: item.available_on)),
        source_request=data.request,
        source=data.source,
        calendar_id=calendar.calendar_id,
        price_field_used=data.price_field_used,
    )


def _aggregate_week(
    symbol: str, points: tuple[MarketDataPoint, ...], sessions: tuple[date, ...]
) -> DerivedWeeklyBar:
    return DerivedWeeklyBar(
        symbol=symbol,
        period_start=sessions[0],
        period_end=sessions[-1],
        available_on=sessions[-1],
        open=points[0].open,
        high=max(point.high for point in points),
        low=min(point.low for point in points),
        close=points[-1].close,
        volume=sum(point.volume for point in points),
        adj_open=points[0].adj_open,
        adj_high=max(point.adj_high for point in points),
        adj_low=min(point.adj_low for point in points),
        adj_close=points[-1].adj_close,
        adj_volume=sum(point.adj_volume for point in points),
        div_cash=sum(point.div_cash for point in points),
        split_factor=points[-1].split_factor,
    )


def _empty_weekly(
    data: HistoricalDataSet, calendar: TradingSessionCalendar
) -> DerivedWeeklyDataSet:
    return DerivedWeeklyDataSet(
        symbol=data.request.symbol,
        points=(),
        source_request=data.request,
        source=data.source,
        calendar_id=calendar.calendar_id,
        price_field_used=data.price_field_used,
    )


__all__ = [
    "DerivedWeeklyBar",
    "DerivedWeeklyDataSet",
    "WEEKLY_AGGREGATION_VERSION",
    "derive_completed_weekly",
]
