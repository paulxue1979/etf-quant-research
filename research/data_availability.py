"""Non-mutating availability projection; never run or rank a strategy."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import replace
from datetime import date, timedelta
from typing import Any

from data.exceptions import DataEngineError
from data.models import HistoricalDataSet, Timeframe
from data.trading_calendar import TradingSessionCalendar, default_trading_calendar
from indicators.models import IndicatorHistoryRequirement, IndicatorKind
from indicators.preparation import prepare_strategy_inputs
from research.canonical import sha256_hash
from research.date_boundaries import validate_effective_range_within_requested
from strategies.strategy_evaluation import required_indicators, required_weekly_assets


def history_requirement_for_strategy(strategy_version: Any) -> IndicatorHistoryRequirement:
    requirements = required_indicators(strategy_version)
    if any(item.kind is IndicatorKind.PEAK_DRAWDOWN for item in requirements):
        return IndicatorHistoryRequirement.ALL_TIME_TO_DATE
    return IndicatorHistoryRequirement.FIXED_WARMUP


def warmup_start_for_strategy(start_date: date, strategy_version: Any) -> date:
    """Resolve the data-preparation boundary without changing evaluation dates."""
    if (
        history_requirement_for_strategy(strategy_version)
        is IndicatorHistoryRequirement.ALL_TIME_TO_DATE
    ):
        # A request predating ETF history lets the provider return every real bar.
        # It does not create observations before an asset's inception.
        return date(1900, 1, 1)
    requirements = required_indicators(strategy_version)
    daily_period = max(
        (item.period for item in requirements if item.timeframe is Timeframe.DAILY), default=1
    )
    weekly_period = max(
        (item.period for item in requirements if item.timeframe is Timeframe.WEEKLY), default=0
    )
    return start_date - timedelta(days=max(daily_period * 3 + 10, weekly_period * 7 + 21))


def project_data_availability(
    strategy_version: Any,
    *,
    requested_start: date,
    requested_end: date,
    data_by_symbol: Mapping[str, HistoricalDataSet | None],
    warmup_start: date | None = None,
    calendar: TradingSessionCalendar | None = None,
) -> dict[str, Any]:
    """Project bounded evidence, preserving gaps and completed-bar semantics.

    The result is conservative indicator-ready availability, not a promise of
    orders or a replacement for execution validation. Observed bounds are not
    provider inception metadata. No prices or returns are exposed in this view.
    """
    if requested_start > requested_end:
        raise ValueError("requested range is reversed")
    warmup = warmup_start or warmup_start_for_strategy(requested_start, strategy_version)
    if warmup > requested_start:
        raise ValueError("warm-up must not follow requested start")
    requirements = required_indicators(strategy_version)
    weekly_symbols = required_weekly_assets(strategy_version)
    symbols = tuple(sorted(asset.symbol for asset in strategy_version.configuration.assets))
    calendar = calendar or default_trading_calendar()
    sessions = set(calendar.sessions(requested_start, requested_end))
    info: list[dict[str, str]] = []
    warnings: list[dict[str, str]] = []
    blocking: list[dict[str, str]] = []

    def issue(target: list, code: str, message: str) -> None:
        target.append({"code": code, "message": message})

    assets: list[dict[str, Any]] = []
    bounded: dict[str, HistoricalDataSet] = {}
    common = set(sessions)
    snapshots: dict[str, str] = {}
    for symbol in symbols:
        dataset = data_by_symbol.get(symbol)
        points = (
            tuple(point for point in dataset.points if warmup <= point.date <= requested_end)
            if dataset is not None
            else ()
        )
        asset: dict[str, Any] = {
            "symbol": symbol,
            "first_available_date": points[0].date.isoformat() if points else None,
            "last_available_date": points[-1].date.isoformat() if points else None,
            "availability_scope": "bounded_observed_history",
            "inception_date": None,
            "requested_before_inception": None,
            "requested_before_observed_history": bool(points and requested_start < points[0].date),
            "requested_after_last_available": bool(
                points and max(sessions, default=requested_end) > points[-1].date
            ),
            "timeframes": ["daily", "weekly"] if symbol in weekly_symbols else ["daily"],
        }
        assets.append(asset)
        if not points or dataset is None:
            issue(
                blocking,
                "ACTIVE_ASSET_UNAVAILABLE",
                f"{symbol}: no verified prices in the requested preparation window.",
            )
            common.clear()
            continue
        if (
            dataset.request.symbol != symbol
            or dataset.price_field_used is not strategy_version.configuration.price_field
        ):
            issue(
                blocking,
                "DATA_IDENTITY_MISMATCH",
                f"{symbol}: data identity or price field differs from the strategy.",
            )
            common.clear()
            continue
        bounded[symbol] = replace(
            dataset,
            points=points,
            request=replace(dataset.request, start_date=warmup, end_date=requested_end),
        )
        snapshots[symbol] = sha256_hash(bounded[symbol].to_cache_payload()["points"])
        common.intersection_update(point.date for point in points)
        if asset["requested_before_observed_history"]:
            issue(
                warnings,
                "PRE_HISTORY",
                f"{symbol}: requested start precedes observed history; "
                "inception is not independently verified. No pre-history prices will be "
                "fabricated.",
            )
        if asset["requested_after_last_available"]:
            issue(
                warnings,
                "TRUNCATED_END",
                f"{symbol}: observed history ends before the requested final trading session.",
            )

    indicator_rows: list[dict[str, Any]] = []
    ready_dates: list[date] = []
    if len(bounded) == len(symbols):
        try:
            prepared = prepare_strategy_inputs(
                strategy_version,
                bounded,
                price_field=strategy_version.configuration.price_field,
                calendar=calendar,
                cutoff=requested_end,
            )
            for requirement, (_, series) in zip(requirements, prepared.indicators, strict=True):
                first = next(
                    (point.date for point in series.points if point.value is not None), None
                )
                indicator_rows.append(
                    {
                        **requirement.to_dict(),
                        "required_bars": (
                            None
                            if requirement.kind is IndicatorKind.PEAK_DRAWDOWN
                            else requirement.period
                        ),
                        **(
                            {
                                "peak_mode": "all_time_to_date",
                                "history_source": "available_history_before_requested_range",
                            }
                            if requirement.kind is IndicatorKind.PEAK_DRAWDOWN
                            else {}
                        ),
                        "first_valid_date": first.isoformat() if first else None,
                    }
                )
                if first is None:
                    issue(
                        blocking,
                        "INSUFFICIENT_HISTORY",
                        f"{requirement.symbol}: {requirement.timeframe.value.upper()} "
                        f"{requirement.kind.value.upper()}{requirement.period} has no valid value "
                        "before requested end.",
                    )
                else:
                    ready_dates.append(first)
                    if first > requested_start:
                        issue(
                            warnings,
                            "INDICATOR_NOT_READY_AT_START",
                            f"{requirement.symbol}: {requirement.timeframe.value.upper()} "
                            f"{requirement.kind.value.upper()}{requirement.period} first valid "
                            f"on {first}.",
                        )
            for symbol, weekly in prepared.weekly_market_data.items():
                if weekly.points:
                    ready_dates.append(weekly.points[0].available_on)
                else:
                    issue(
                        blocking,
                        "NO_COMPLETED_WEEK",
                        f"{symbol}: no completed weekly bar is available.",
                    )
        except (ValueError, DataEngineError) as exc:
            issue(
                blocking,
                "PREPARATION_FAILED",
                f"Market-data preparation failed ({type(exc).__name__}); check missing sessions "
                "and data integrity.",
            )
    if not common:
        issue(
            blocking,
            "NO_COMMON_TRADING_DATES",
            "Required active assets have no common trading dates inside the requested range.",
        )
    ready_start = max([requested_start, *ready_dates])
    eligible = sorted(day for day in common if day >= ready_start)
    if not eligible and common:
        issue(
            blocking,
            "NO_INDICATOR_READY_DATES",
            "No common trading session remains after indicator warm-up.",
        )
    start = eligible[0] if eligible and not blocking else None
    end = eligible[-1] if eligible and not blocking else None
    if start and end:
        validate_effective_range_within_requested(requested_start, requested_end, start, end)
    if sessions and (requested_start not in sessions or requested_end not in sessions):
        issue(
            info,
            "NON_SESSION_BOUNDARY",
            "Natural-calendar boundaries map to trading sessions within the requested range; "
            "this is not an error.",
        )
    return {
        "status": "BLOCKED" if blocking else "READY_WITH_WARNINGS" if warnings else "READY",
        "strategy_id": strategy_version.strategy_id,
        "strategy_version_id": strategy_version.version_id,
        "strategy_version_content_hash": strategy_version.content_hash,
        "price_field": strategy_version.configuration.price_field.value,
        "rebalance_policy": strategy_version.configuration.rebalance_policy.to_dict(),
        "execution_rule": "next_trading_day_open",
        "requested_start": requested_start.isoformat(),
        "requested_end": requested_end.isoformat(),
        "effective_start_expected": start.isoformat() if start else None,
        "effective_end_expected": end.isoformat() if end else None,
        "common_market_start": min(common).isoformat() if common else None,
        "common_market_end": max(common).isoformat() if common else None,
        "range_basis": "conservative_indicator_ready_common_sessions",
        "warmup_start": warmup.isoformat(),
        "assets": assets,
        "indicator_requirements": indicator_rows or [item.to_dict() for item in requirements],
        "info": info,
        "warnings": warnings,
        "blocking_issues": blocking,
        "data_snapshot_reference": snapshots,
    }
