"""Shared timeframe-aware indicator preparation for all research execution paths."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date
from typing import Any

from data.derived import DerivedWeeklyDataSet, derive_completed_weekly
from data.models import HistoricalDataSet, PriceField, Timeframe
from data.trading_calendar import TradingSessionCalendar
from indicators.exponential_moving_average import exponential_moving_average
from indicators.models import IndicatorKind, IndicatorSeries
from indicators.moving_average import moving_average
from strategies.strategy_evaluation import required_indicators, required_weekly_assets


@dataclass(frozen=True)
class PreparedStrategyInputs:
    """All market representations needed by one strategy evaluation."""

    indicators: tuple[tuple[str, IndicatorSeries], ...]
    weekly_market_data: Mapping[str, DerivedWeeklyDataSet]


def prepare_strategy_inputs(
    strategy_version: Any,
    data: Mapping[str, HistoricalDataSet],
    *,
    price_field: PriceField,
    calendar: TradingSessionCalendar | None = None,
    cutoff: date | None = None,
) -> PreparedStrategyInputs:
    """Prepare daily and completed-week inputs without duplicating formulas."""
    weekly_symbols = required_weekly_assets(strategy_version)
    weekly: dict[str, DerivedWeeklyDataSet] = {}
    for symbol in weekly_symbols:
        dataset = data.get(symbol)
        if dataset is None:
            raise ValueError(f"market data is missing for weekly asset {symbol}")
        if dataset.price_field_used is not price_field:
            raise ValueError(f"market data price field does not match for {symbol}")
        weekly[symbol] = derive_completed_weekly(dataset, calendar=calendar, cutoff=cutoff)

    prepared: list[tuple[str, IndicatorSeries]] = []
    for requirement in required_indicators(strategy_version):
        dataset = (
            weekly.get(requirement.symbol)
            if requirement.timeframe is Timeframe.WEEKLY
            else data.get(requirement.symbol)
        )
        if dataset is None:
            raise ValueError(f"indicator data is missing for {requirement.symbol}")
        if requirement.price_field is not price_field:
            raise ValueError(f"indicator price field does not match for {requirement.symbol}")
        if requirement.kind is IndicatorKind.MOVING_AVERAGE:
            series = moving_average(dataset, period=requirement.period, price_field=price_field)
        elif requirement.kind is IndicatorKind.EXPONENTIAL_MOVING_AVERAGE:
            series = exponential_moving_average(
                dataset, period=requirement.period, price_field=price_field
            )
        else:  # pragma: no cover - guarded by strategy validation
            raise ValueError(f"unsupported indicator {requirement.kind.value}")
        prepared.append((requirement.asset.symbol, series))
    return PreparedStrategyInputs(tuple(prepared), weekly)


__all__ = ["PreparedStrategyInputs", "prepare_strategy_inputs"]
