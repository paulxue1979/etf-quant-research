from __future__ import annotations

from datetime import date

import pytest

from data.derived import derive_completed_weekly
from data.exceptions import DataValidationError
from data.models import (
    DataSource,
    HistoricalDataRequest,
    HistoricalDataSet,
    MarketDataPoint,
    PriceField,
    Timeframe,
)
from indicators.exponential_moving_average import exponential_moving_average
from indicators.moving_average import moving_average
from strategies.enums import OperandType
from strategies.evaluation import EvaluationContext
from strategies.models import Operand
from strategies.operand_evaluator import evaluate_operand


class StaticCalendar:
    calendar_id = "TEST-US-EQUITY"

    def __init__(self, sessions: tuple[date, ...]) -> None:
        self._sessions = set(sessions)

    def sessions(self, start: date, end: date) -> tuple[date, ...]:
        return tuple(sorted(day for day in self._sessions if start <= day <= end))


def _point(day: date, value: float) -> MarketDataPoint:
    return MarketDataPoint(
        date=day,
        open=value,
        high=value + 2,
        low=value - 2,
        close=value + 1,
        volume=value,
        adj_open=value + 10,
        adj_high=value + 12,
        adj_low=value + 8,
        adj_close=value + 11,
        adj_volume=value + 100,
        div_cash=0.5,
        split_factor=1.0,
    )


def _dataset(symbol: str, days: tuple[date, ...]) -> HistoricalDataSet:
    points = tuple(_point(day, float(index + 1)) for index, day in enumerate(days))
    return HistoricalDataSet(
        HistoricalDataRequest(
            symbol, days[0], days[-1], price_field_used=PriceField.ADJUSTED_CLOSE
        ),
        points,
        DataSource.CACHE,
    )


def test_completed_week_aggregates_raw_and_adjusted_fields() -> None:
    days = tuple(date(2024, 1, day) for day in range(8, 13))
    weekly = derive_completed_weekly(_dataset("SPY", days), calendar=StaticCalendar(days))
    bar = weekly.points[0]
    assert (bar.period_start, bar.period_end, bar.available_on) == (days[0], days[-1], days[-1])
    assert (bar.open, bar.high, bar.low, bar.close, bar.volume) == (1.0, 7.0, -1.0, 6.0, 15.0)
    assert (bar.adj_open, bar.adj_high, bar.adj_low, bar.adj_close) == (11.0, 17.0, 9.0, 16.0)
    assert bar.adj_volume == 515.0
    assert bar.div_cash == 2.5


def test_current_partial_week_is_excluded_even_when_future_rows_exist() -> None:
    days = tuple(date(2024, 1, day) for day in range(8, 13))
    data = _dataset("SPY", days)
    weekly = derive_completed_weekly(data, calendar=StaticCalendar(days), cutoff=date(2024, 1, 10))
    assert weekly.points == ()


def test_missing_expected_session_is_data_quality_error() -> None:
    expected = tuple(date(2024, 1, day) for day in range(8, 13))
    observed = tuple(day for day in expected if day != date(2024, 1, 10))
    with pytest.raises(DataValidationError, match="missing expected session"):
        derive_completed_weekly(
            _dataset("SPY", observed), calendar=StaticCalendar(expected), cutoff=expected[-1]
        )


def test_holiday_short_week_uses_actual_final_session() -> None:
    expected = tuple(date(2024, 3, day) for day in (25, 26, 27, 28))
    weekly = derive_completed_weekly(
        _dataset("SPY", expected), calendar=StaticCalendar(expected), cutoff=expected[-1]
    )
    assert weekly.points[0].period_end == date(2024, 3, 28)


def test_holiday_thursday_does_not_complete_before_open_friday() -> None:
    expected = tuple(date(2024, 11, day) for day in (25, 26, 27, 29))
    data = _dataset("SPY", expected)
    assert (
        derive_completed_weekly(
            data, calendar=StaticCalendar(expected), cutoff=date(2024, 11, 27)
        ).points
        == ()
    )
    assert (
        derive_completed_weekly(data, calendar=StaticCalendar(expected), cutoff=expected[-1])
        .points[0]
        .period_end
        == expected[-1]
    )


def test_weekly_indicator_reuses_existing_formula_and_as_of_lookup() -> None:
    first = tuple(date(2024, 1, day) for day in range(8, 13))
    second = tuple(date(2024, 1, day) for day in range(16, 20))
    third = tuple(date(2024, 1, day) for day in range(22, 27))
    all_days = first + second + third
    data = _dataset("QQQ", all_days)
    calendar = StaticCalendar(all_days)
    weekly = derive_completed_weekly(data, calendar=calendar)
    series = moving_average(weekly, period=2, price_field=PriceField.ADJUSTED_CLOSE)
    context = EvaluationContext.from_components(
        {"QQQ": data},
        [("QQQ", series)],
        weekly_market_data={"QQQ": weekly},
    )
    operand = Operand(
        "QQQ", OperandType.MA, 2, PriceField.ADJUSTED_CLOSE, timeframe=Timeframe.WEEKLY
    )
    resolved = evaluate_operand(operand, context, date(2024, 1, 24))
    assert resolved.source_date == date(2024, 1, 19)
    assert resolved.timeframe is Timeframe.WEEKLY
    assert resolved.value == series.points[1].value


def test_weekly_ema_uses_the_same_formula_engine() -> None:
    days = tuple(date(2024, 1, day) for day in range(8, 13))
    data = _dataset("QQQ", days)
    weekly = derive_completed_weekly(data, calendar=StaticCalendar(days))
    series = exponential_moving_average(weekly, period=1, price_field=PriceField.ADJUSTED_CLOSE)
    assert series.timeframe is Timeframe.WEEKLY
    assert series.points[0].value == weekly.points[0].adj_close


def test_partial_first_week_is_not_fabricated() -> None:
    days = tuple(date(2024, 1, day) for day in (10, 11, 12))
    calendar = StaticCalendar(tuple(date(2024, 1, day) for day in range(8, 13)))
    assert derive_completed_weekly(_dataset("QQQ", days), calendar=calendar).points == ()
