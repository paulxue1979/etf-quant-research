from datetime import UTC, date, datetime, timedelta

from data.models import (
    DataSource,
    HistoricalDataRequest,
    HistoricalDataSet,
    MarketDataPoint,
    PriceField,
)
from research.data_availability import project_data_availability, warmup_start_for_strategy
from strategies import (
    Allocation,
    AssetReference,
    FallbackAllocation,
    RebalanceFrequency,
    RebalancePolicy,
    StrategyDefinition,
    StrategyVersion,
)


class Calendar:
    calendar_id = "TEST"

    def __init__(self, sessions: tuple[date, ...]) -> None:
        self._sessions = sessions

    def sessions(self, start: date, end: date) -> tuple[date, ...]:
        return tuple(day for day in self._sessions if start <= day <= end)


def _version() -> StrategyVersion:
    definition = StrategyDefinition(
        strategy_id="availability-test",
        name="Availability test",
        description="test",
        assets=(AssetReference("QQQ"),),
        price_field=PriceField.ADJUSTED_CLOSE,
        rules=(),
        fallback=FallbackAllocation((Allocation("QQQ", 1.0),), name="fallback"),
        rebalance_policy=RebalancePolicy(RebalanceFrequency.DAILY),
    )
    return StrategyVersion(
        strategy_id=definition.strategy_id,
        version_id="availability-v1",
        version_number=1,
        created_at=datetime(2026, 1, 1, tzinfo=UTC),
        configuration=definition,
    )


def _dataset(start: date, days: int) -> HistoricalDataSet:
    points = tuple(
        MarketDataPoint(
            date=start + timedelta(days=index),
            open=100,
            high=101,
            low=99,
            close=100,
            volume=100,
            adj_open=100,
            adj_high=101,
            adj_low=99,
            adj_close=100,
            adj_volume=100,
            div_cash=0,
            split_factor=1,
        )
        for index in range(days)
    )
    return HistoricalDataSet(
        HistoricalDataRequest(
            "QQQ", start, points[-1].date, price_field_used=PriceField.ADJUSTED_CLOSE
        ),
        points,
        DataSource.CACHE,
    )


def test_warmup_uses_shared_engine_contract() -> None:
    assert warmup_start_for_strategy(date(2026, 1, 1), _version()) == date(2025, 12, 11)


def test_projection_distinguishes_requested_and_effective_trading_range() -> None:
    sessions = (date(2026, 1, 5), date(2026, 1, 6), date(2026, 1, 7))
    result = project_data_availability(
        _version(),
        requested_start=date(2026, 1, 3),
        requested_end=date(2026, 1, 7),
        data_by_symbol={"QQQ": _dataset(date(2025, 12, 19), 20)},
        calendar=Calendar(sessions),
    )
    assert result["status"] == "READY"
    assert result["effective_start_expected"] == "2026-01-05"
    assert result["effective_end_expected"] == "2026-01-07"
    assert any(item["code"] == "NON_SESSION_BOUNDARY" for item in result["info"])


def test_projection_blocks_missing_required_asset_without_fabricating_data() -> None:
    result = project_data_availability(
        _version(),
        requested_start=date(2026, 1, 1),
        requested_end=date(2026, 1, 7),
        data_by_symbol={"QQQ": None},
        calendar=Calendar((date(2026, 1, 2),)),
    )
    assert result["status"] == "BLOCKED"
    assert any(item["code"] == "ACTIVE_ASSET_UNAVAILABLE" for item in result["blocking_issues"])
    assert result["effective_start_expected"] is None
