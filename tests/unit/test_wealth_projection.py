from __future__ import annotations

from dataclasses import replace
from datetime import date
from decimal import Decimal

import pytest

from backend.app.wealth_projection import project_wealth, wealth_series_points
from backtest.models import (
    ContributionEvent,
    ContributionFrequency,
    EquityPoint,
    ExternalCashFlow,
)
from tests.unit.test_backtest_lab_api import _record


def _result_with(
    *,
    initial_capital: float = 10_000,
    values: tuple[float, ...] = (10_000, 11_000, 10_500),
    events: tuple[ContributionEvent, ...] = (),
    flows: tuple[ExternalCashFlow, ...] = (),
):
    source = _record("wealth-projection").run.backtest_result
    days = (date(2026, 1, 2), date(2026, 1, 5), date(2026, 1, 6))
    equity = tuple(
        EquityPoint(
            date=day,
            cash=value,
            asset_values={},
            total_equity=value,
        )
        for day, value in zip(days, values, strict=True)
    )
    cumulative = float(sum((event.amount for event in events), Decimal("0")))
    return replace(
        source,
        start_date=days[0],
        end_date=days[-1],
        initial_capital=initial_capital,
        final_equity=values[-1],
        equity_curve=equity,
        contribution_events=events,
        external_cash_flows=flows,
        cumulative_contributions=cumulative,
        total_capital_invested=None,
        investment_profit=None,
    )


def _event(requested: date, effective: date, amount: str) -> ContributionEvent:
    return ContributionEvent(
        frequency=ContributionFrequency.ONE_TIME,
        amount=Decimal(amount),
        requested_date=requested,
        effective_date=effective,
    )


def test_no_contribution_keeps_initial_capital_flat_and_profit_can_be_negative() -> None:
    result = _result_with(values=(10_000, 9_500, 10_500))

    points = project_wealth(result)

    assert [point.capital_invested for point in points] == [10_000, 10_000, 10_000]
    assert [point.investment_profit for point in points] == [0, -500, 500]
    assert points[-1].capital_invested == result.total_capital_invested
    assert points[-1].investment_profit == result.investment_profit


def test_effective_dates_create_steps_without_using_requested_holiday_dates() -> None:
    events = (
        _event(date(2026, 1, 3), date(2026, 1, 5), "1000"),
        _event(date(2026, 1, 6), date(2026, 1, 6), "500"),
    )
    result = _result_with(values=(10_000, 11_000, 11_500), events=events)

    points = project_wealth(result)

    assert [point.date for point in points] == ["2026-01-02", "2026-01-05", "2026-01-06"]
    assert [point.capital_invested for point in points] == [10_000, 11_000, 11_500]
    assert [point.investment_profit for point in points] == [0, 0, 0]


def test_multiple_same_day_contributions_are_counted_once_each() -> None:
    events = (
        _event(date(2026, 1, 3), date(2026, 1, 5), "250"),
        _event(date(2026, 1, 4), date(2026, 1, 5), "750"),
    )
    result = _result_with(values=(10_000, 11_000, 11_200), events=events)

    first = project_wealth(result)
    second = project_wealth(result)

    assert first == second
    assert [point.capital_invested for point in first] == [10_000, 11_000, 11_000]
    assert [point.investment_profit for point in first] == [0, 0, 200]


@pytest.mark.parametrize("cost_impact", [25.0, 75.0])
def test_cost_impact_is_read_from_portfolio_value_without_second_deduction(
    cost_impact: float,
) -> None:
    result = _result_with(values=(10_000, 10_000 - cost_impact, 10_000 - cost_impact))

    points = project_wealth(result)

    assert points[-1].investment_profit == -cost_impact


def test_external_cash_flow_records_do_not_duplicate_contribution_events() -> None:
    event = _event(date(2026, 1, 5), date(2026, 1, 5), "400")
    flow = ExternalCashFlow(
        date=date(2026, 1, 5),
        amount=Decimal("400"),
    )
    result = _result_with(
        values=(10_000, 10_400, 10_400),
        events=(event,),
        flows=(flow,),
    )

    points = project_wealth(result)

    assert [point.capital_invested for point in points] == [10_000, 10_400, 10_400]
    assert [point.investment_profit for point in points] == [0, 0, 0]


def test_series_projection_preserves_canonical_dates_and_rejects_unknown_fields() -> None:
    result = _result_with()

    assert wealth_series_points(result, "portfolio_value") == (
        {"date": "2026-01-02", "value": 10_000.0},
        {"date": "2026-01-05", "value": 11_000.0},
        {"date": "2026-01-06", "value": 10_500.0},
    )
    try:
        wealth_series_points(result, "return_percent")
    except ValueError as error:
        assert str(error) == "unsupported wealth projection field"
    else:
        raise AssertionError("unknown wealth projection fields must be rejected")
