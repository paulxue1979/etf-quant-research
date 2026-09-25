from __future__ import annotations

from datetime import UTC, date, datetime

from backend.app.backtest_lab import BacktestRequest
from backend.app.backtest_repository import BacktestRepository
from backend.app.backtest_service import BacktestService
from backend.app.strategy_repository import StrategyRepository
from data.models import (
    DataSource,
    HistoricalDataRequest,
    HistoricalDataSet,
    MarketDataPoint,
    PriceField,
    Timeframe,
)
from data.trading_calendar import default_trading_calendar
from indicators.preparation import prepare_strategy_inputs
from research.execution_service import ExperimentExecutionService
from research.oos_execution_service import OosExecutionService
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


def _weekly_ma200_version() -> StrategyVersion:
    condition = Condition(
        Operand(
            "QQQ",
            OperandType.PRICE,
            price_field=PriceField.ADJUSTED_CLOSE,
            timeframe=Timeframe.WEEKLY,
        ),
        ComparisonOperator.GREATER_THAN,
        Operand(
            "QQQ",
            OperandType.MA,
            period=200,
            price_field=PriceField.ADJUSTED_CLOSE,
            timeframe=Timeframe.WEEKLY,
        ),
    )
    definition = StrategyDefinition(
        strategy_id="long-history-weekly",
        name="Long History Weekly",
        description="Weekly calendar coverage fixture",
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
    return StrategyVersion(
        strategy_id=definition.strategy_id,
        version_id="long-history-weekly-v1",
        version_number=1,
        created_at=datetime(2026, 9, 25, tzinfo=UTC),
        configuration=definition,
    )


def _dataset(
    start: date,
    end: date,
    *,
    first_observed: date = date(1999, 3, 10),
) -> HistoricalDataSet:
    sessions = default_trading_calendar().sessions(start, end)
    points = tuple(
        MarketDataPoint(
            date=day,
            open=100.0 + index,
            high=101.0 + index,
            low=99.0 + index,
            close=100.0 + index,
            volume=100.0,
            adj_open=100.0 + index,
            adj_high=101.0 + index,
            adj_low=99.0 + index,
            adj_close=100.0 + index,
            adj_volume=100.0,
            div_cash=0.0,
            split_factor=1.0,
        )
        for index, day in enumerate(day for day in sessions if day >= first_observed)
    )
    return HistoricalDataSet(
        request=HistoricalDataRequest(
            symbol="QQQ",
            start_date=start,
            end_date=end,
            price_field_used=PriceField.ADJUSTED_CLOSE,
        ),
        points=points,
        source=DataSource.CACHE,
    )


def test_xnys_calendar_covers_weekly_ma200_warmup_before_default_window() -> None:
    calendar = default_trading_calendar()

    sessions = calendar.sessions(date(2003, 2, 3), date(2003, 2, 10))

    assert sessions[0] == date(2003, 2, 3)
    assert sessions[-1] == date(2003, 2, 10)


def test_2007_weekly_ma200_preparation_does_not_hit_calendar_boundary() -> None:
    version = _weekly_ma200_version()
    data = {"QQQ": _dataset(date(2003, 2, 10), date(2007, 1, 31))}

    prepared = prepare_strategy_inputs(
        version,
        data,
        price_field=PriceField.ADJUSTED_CLOSE,
        cutoff=date(2007, 1, 31),
    )

    assert prepared.weekly_market_data["QQQ"].points
    assert prepared.indicators[0][1].points


def test_2000_range_preserves_pre_listing_gap_and_indicator_unavailable_state() -> None:
    version = _weekly_ma200_version()
    data = {"QQQ": _dataset(date(1996, 2, 10), date(2001, 1, 2))}

    prepared = prepare_strategy_inputs(
        version,
        data,
        price_field=PriceField.ADJUSTED_CLOSE,
        cutoff=date(2001, 1, 2),
    )
    weekly = prepared.weekly_market_data["QQQ"]
    indicator = prepared.indicators[0][1]

    assert weekly.points[0].period_start >= date(1999, 3, 10)
    assert all(point.value is None for point in indicator.points)


def test_weekly_ma200_first_value_requires_200_completed_real_bars() -> None:
    version = _weekly_ma200_version()
    data = {"QQQ": _dataset(date(1996, 2, 10), date(2004, 1, 2))}

    prepared = prepare_strategy_inputs(
        version,
        data,
        price_field=PriceField.ADJUSTED_CLOSE,
        cutoff=date(2004, 1, 2),
    )
    weekly = prepared.weekly_market_data["QQQ"]
    indicator = prepared.indicators[0][1]
    first_value_index = next(
        index for index, point in enumerate(indicator.points) if point.value is not None
    )

    assert first_value_index == 199
    assert indicator.points[first_value_index].date == weekly.points[199].available_on


def test_backtest_experiment_and_oos_wrappers_share_long_history_preparation(tmp_path) -> None:
    version = _weekly_ma200_version()
    data = {"QQQ": _dataset(date(2003, 2, 10), date(2007, 1, 31))}

    experiment_inputs = ExperimentExecutionService._calculate_inputs(
        object.__new__(ExperimentExecutionService), version, data
    )
    oos_inputs = OosExecutionService._prepare_inputs(
        version,
        data,
        PriceField.ADJUSTED_CLOSE,
        oos_end=date(2007, 1, 31),
    )
    strategies = StrategyRepository(tmp_path / "strategy.sqlite3")
    stored = strategies.create(version.configuration)
    backtests = BacktestRepository(tmp_path / "backtest.sqlite3")

    class DataService:
        def get_history(self, request: HistoricalDataRequest) -> HistoricalDataSet:
            return data[request.symbol]

    service = BacktestService(
        strategy_repository=strategies,
        backtest_repository=backtests,
        data_service=DataService(),
    )
    request = BacktestRequest(
        strategy_id=stored.strategy_id,
        strategy_version_id=stored.version_id,
        start_date=date(2007, 1, 1),
        end_date=date(2007, 1, 31),
        initial_capital=10_000.0,
        price_field_used=PriceField.ADJUSTED_CLOSE,
    )

    outcome = service.run(request)

    assert experiment_inputs.weekly_market_data["QQQ"].points
    assert oos_inputs.weekly_market_data["QQQ"].points
    assert outcome.backtest_result.start_date == date(2007, 1, 3)
