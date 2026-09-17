"""Deterministic normalization of external capital contribution schedules."""

from __future__ import annotations

from datetime import date

from backtest.models import ContributionEvent, ContributionFrequency, ContributionSchedule


def normalize_contribution_schedule(
    schedule: ContributionSchedule,
    trading_dates: tuple[date, ...],
    *,
    investment_start: date,
    investment_end: date,
) -> tuple[ContributionEvent, ...]:
    if not isinstance(schedule, ContributionSchedule):
        raise TypeError("schedule must be a ContributionSchedule")
    if investment_start > investment_end:
        raise ValueError("investment_start must be on or before investment_end")
    dates = tuple(sorted(set(trading_dates)))
    if not dates:
        return ()

    if schedule.frequency is ContributionFrequency.ONE_TIME:
        requested_dates = (schedule.requested_date,)
    else:
        requested: list[date] = []
        year, month = investment_start.year, investment_start.month
        while (year, month) <= (investment_end.year, investment_end.month):
            requested.append(date(year, month, 1))
            if month == 12:
                year, month = year + 1, 1
            else:
                month += 1
        requested_dates = tuple(requested)

    result: list[ContributionEvent] = []
    for requested_date in requested_dates:
        if requested_date is None:
            continue
        if (
            schedule.frequency is ContributionFrequency.ONE_TIME
            and requested_date < investment_start
        ):
            continue
        effective_date = next((day for day in dates if day >= requested_date), None)
        if (
            effective_date is None
            or effective_date < investment_start
            or effective_date > investment_end
        ):
            continue
        result.append(
            ContributionEvent(
                frequency=schedule.frequency,
                amount=schedule.amount,
                requested_date=requested_date,
                effective_date=effective_date,
                currency=schedule.currency,
            )
        )
    return tuple(result)


__all__ = ["normalize_contribution_schedule"]
