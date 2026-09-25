"""Deterministic exchange-session access for derived market data."""

from __future__ import annotations

from collections.abc import Iterable
from datetime import date
from functools import lru_cache
from typing import Protocol


class TradingSessionCalendar(Protocol):
    """Small domain seam used by weekly aggregation and deterministic tests."""

    calendar_id: str

    def sessions(self, start: date, end: date) -> tuple[date, ...]: ...


class ExchangeTradingCalendar:
    """Offline regular-session calendar backed by ``exchange_calendars``."""

    def __init__(self, calendar_id: str = "XNYS") -> None:
        try:
            import exchange_calendars as xcals
        except ImportError as exc:  # pragma: no cover - packaging failure
            raise RuntimeError("exchange-calendars is required for weekly market data") from exc
        self.calendar_id = calendar_id.strip().upper()
        self._calendar_factory = xcals

    def sessions(self, start: date, end: date) -> tuple[date, ...]:
        if start > end:
            return ()
        # Build the calendar for the requested range.  exchange-calendars' default
        # XNYS instance is a rolling window (currently about 20 years), which is
        # too narrow for indicator warm-up on older research ranges.
        calendar = self._calendar_factory.get_calendar(
            self.calendar_id,
            start=start,
            end=end,
        )
        # The factory normalizes weekend/holiday bounds to actual sessions.  Use
        # those normalized labels for the second API call so a non-session request
        # date cannot itself become out of bounds.
        effective_start = max(start, calendar.first_session.date())
        effective_end = min(end, calendar.last_session.date())
        if effective_start > effective_end:
            return ()
        # exchange_calendars returns UTC-normalized session labels; converting through
        # ``date`` keeps the rest of the domain independent of pandas/time zones.
        labels = calendar.sessions_in_range(effective_start.isoformat(), effective_end.isoformat())
        return tuple(label.date() for label in labels)


@lru_cache(maxsize=4)
def default_trading_calendar(calendar_id: str = "XNYS") -> ExchangeTradingCalendar:
    """Return the process-local deterministic calendar instance."""
    return ExchangeTradingCalendar(calendar_id)


def grouped_week_sessions(
    sessions: Iterable[date],
) -> dict[tuple[int, int], tuple[date, ...]]:
    """Group session labels by ISO calendar week without resampling observations."""
    grouped: dict[tuple[int, int], list[date]] = {}
    for session in sessions:
        key = session.isocalendar()[:2]
        grouped.setdefault(key, []).append(session)
    return {key: tuple(sorted(value)) for key, value in grouped.items()}


__all__ = [
    "ExchangeTradingCalendar",
    "TradingSessionCalendar",
    "default_trading_calendar",
    "grouped_week_sessions",
]
