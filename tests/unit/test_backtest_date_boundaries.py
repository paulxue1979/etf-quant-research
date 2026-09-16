from __future__ import annotations

from datetime import UTC, date, datetime

import pytest

from analytics import analyze_backtest
from backend.app.backtest_models import (
    deserialize_backtest_result,
    serialize_backtest_result,
)
from backend.app.backtest_service import _warmup_start
from backtest import BacktestConfig, BacktestEngine, TargetAllocation
from backtest.exceptions import MissingMarketDataError
from data.models import (
    DataSource,
    HistoricalDataRequest,
    HistoricalDataSet,
    MarketDataPoint,
    PriceField,
)
from strategies import (
    Allocation,
    AllocationRule,
    AssetReference,
    ComparisonOperator,
    Condition,
    FallbackAllocation,
    LogicalOperator,
    Operand,
    OperandType,
    RebalanceFrequency,
    RebalancePolicy,
    RuleGroup,
    StrategyDefinition,
    StrategyVersion,
)


def _dataset(symbol: str, dates: tuple[date, ...]) -> HistoricalDataSet:
    points = tuple(
        MarketDataPoint(
            date=day,
            open=100.0,
            high=101.0,
            low=99.0,
            close=100.0,
            volume=100.0,
            adj_open=100.0,
            adj_high=101.0,
            adj_low=99.0,
            adj_close=100.0,
            adj_volume=100.0,
            div_cash=0.0,
            split_factor=1.0,
        )
        for day in dates
    )
    return HistoricalDataSet(
        request=HistoricalDataRequest(
            symbol=symbol,
            start_date=dates[0],
            end_date=dates[-1],
            price_field_used=PriceField.ADJUSTED_CLOSE,
        ),
        points=points,
        source=DataSource.API_FRESH,
    )


def _config(start: date, end: date) -> BacktestConfig:
    return BacktestConfig(
        strategy_version_id="boundary-test-v1",
        start_date=start,
        end_date=end,
        initial_capital=1_000.0,
        price_field_used=PriceField.ADJUSTED_CLOSE,
    )


def _run(data: dict[str, HistoricalDataSet], start: date, end: date):
    candidate_dates = [
        point.date
        for dataset in data.values()
        for point in dataset.points
        if start <= point.date <= end
    ]
    first_signal = min(candidate_dates, default=start)
    weight = 1.0 / len(data)
    return BacktestEngine().run(
        data,
        [TargetAllocation.from_weights(first_signal, {symbol: weight for symbol in data})],
        _config(start, end),
    )


def test_weekend_start_and_end_normalize_to_common_trading_dates() -> None:
    data = {
        "QQQ": _dataset("QQQ", (date(2025, 1, 3), date(2025, 1, 6), date(2025, 1, 7))),
    }

    result = _run(data, date(2025, 1, 4), date(2025, 1, 7))

    assert result.start_date == date(2025, 1, 6)
    assert result.end_date == date(2025, 1, 7)
    assert result.requested_start_date == date(2025, 1, 4)
    assert result.requested_end_date == date(2025, 1, 7)
    assert result.effective_start_date == date(2025, 1, 6)
    assert result.effective_end_date == date(2025, 1, 7)
    assert [point.date for point in result.equity_curve] == [date(2025, 1, 6), date(2025, 1, 7)]


@pytest.mark.parametrize(
    ("start", "end", "expected_start", "expected_end"),
    [
        (date(2025, 1, 1), date(2025, 1, 3), date(2025, 1, 2), date(2025, 1, 3)),
        (date(2024, 12, 30), date(2025, 1, 1), date(2024, 12, 30), date(2024, 12, 31)),
    ],
)
def test_holiday_start_and_end_derive_from_available_data(
    start: date, end: date, expected_start: date, expected_end: date
) -> None:
    data = {
        "QQQ": _dataset(
            "QQQ",
            (date(2024, 12, 30), date(2024, 12, 31), date(2025, 1, 2), date(2025, 1, 3)),
        ),
    }

    result = _run(data, start, end)

    assert (result.start_date, result.end_date) == (expected_start, expected_end)


def test_empty_effective_range_is_an_explicit_domain_error() -> None:
    data = {"QQQ": _dataset("QQQ", (date(2025, 1, 2), date(2025, 1, 3)))}

    with pytest.raises(MissingMarketDataError, match="effective trading range"):
        _run(data, date(2025, 1, 4), date(2025, 1, 5))


def test_multi_asset_uses_common_dates_without_forward_fill() -> None:
    data = {
        "QQQ": _dataset(
            "QQQ", (date(2025, 1, 2), date(2025, 1, 3), date(2025, 1, 6), date(2025, 1, 7))
        ),
        "TQQQ": _dataset("TQQQ", (date(2025, 1, 2), date(2025, 1, 3), date(2025, 1, 7))),
    }

    result = _run(data, date(2025, 1, 2), date(2025, 1, 7))

    assert [point.date for point in result.equity_curve] == [
        date(2025, 1, 2),
        date(2025, 1, 3),
        date(2025, 1, 7),
    ]
    assert all(point.date != date(2025, 1, 6) for point in result.equity_curve)


def test_single_asset_boundary_and_next_open_execution_are_preserved() -> None:
    data = {"QQQ": _dataset("QQQ", (date(2025, 1, 2), date(2025, 1, 3), date(2025, 1, 6)))}

    result = _run(data, date(2025, 1, 2), date(2025, 1, 6))

    assert result.orders[0].signal_date == date(2025, 1, 2)
    assert result.orders[0].date == date(2025, 1, 3)
    assert result.orders[0].date > result.orders[0].signal_date


def test_sma200_warmup_is_preserved_before_requested_start() -> None:
    condition = Condition(
        Operand("QQQ", OperandType.PRICE, price_field=PriceField.ADJUSTED_CLOSE),
        ComparisonOperator.GREATER_THAN,
        Operand("QQQ", OperandType.MA, period=200, price_field=PriceField.ADJUSTED_CLOSE),
    )
    definition = StrategyDefinition(
        strategy_id="warmup-test",
        name="Warmup Test",
        description="boundary test",
        assets=(AssetReference("QQQ"),),
        price_field=PriceField.ADJUSTED_CLOSE,
        rules=(
            AllocationRule(
                "risk-on",
                "Risk On",
                1,
                (Allocation("QQQ", 1.0),),
                RuleGroup(LogicalOperator.AND, (condition,)),
            ),
        ),
        fallback=FallbackAllocation((Allocation("QQQ", 1.0),)),
        rebalance_policy=RebalancePolicy(RebalanceFrequency.DAILY),
    )
    version = StrategyVersion(
        strategy_id=definition.strategy_id,
        version_id="warmup-test-v1",
        version_number=1,
        created_at=datetime(2026, 1, 1, tzinfo=UTC),
        configuration=definition,
    )

    requested_start = date(2025, 1, 6)
    assert _warmup_start(requested_start, version) == date(2023, 5, 7)


def test_analytics_and_serialization_expose_effective_boundary_provenance() -> None:
    data = {"QQQ": _dataset("QQQ", (date(2025, 1, 3), date(2025, 1, 6), date(2025, 1, 7)))}
    result = _run(data, date(2025, 1, 4), date(2025, 1, 7))

    analysis = analyze_backtest(result)
    payload = serialize_backtest_result(result)
    restored = deserialize_backtest_result(payload)

    assert (analysis.start_date, analysis.end_date) == (date(2025, 1, 6), date(2025, 1, 7))
    assert analysis.provenance["requested_start_date"] == "2025-01-04"
    assert analysis.provenance["effective_end_date"] == "2025-01-07"
    assert restored == result
    assert payload["requested_start_date"] == "2025-01-04"


def test_configuration_snapshot_keeps_requested_and_effective_ranges() -> None:
    data = {"QQQ": _dataset("QQQ", (date(2025, 1, 3), date(2025, 1, 6), date(2025, 1, 7)))}

    result = _run(data, date(2025, 1, 4), date(2025, 1, 7))

    assert result.configuration_snapshot["start_date"] == "2025-01-04"
    assert result.configuration_snapshot["end_date"] == "2025-01-07"
    assert result.configuration_snapshot["effective_start_date"] == "2025-01-06"
    assert result.configuration_snapshot["effective_end_date"] == "2025-01-07"
