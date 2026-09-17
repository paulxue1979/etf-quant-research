from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

import pytest

from backtest import BacktestConfig
from backtest import RebalanceFrequency as BacktestRebalanceFrequency
from backtest import RebalancePolicy as BacktestRebalancePolicy
from backtest.integration import run_strategy_backtest
from backtest.models import OrderSide
from data.models import (
    DataSource,
    HistoricalDataRequest,
    HistoricalDataSet,
    MarketDataPoint,
    PriceField,
)
from indicators.models import IndicatorKind, IndicatorPoint, IndicatorSeries
from strategies import (
    Allocation,
    AllocationRule,
    AllocationSpecification,
    AssetReference,
    ComparisonOperator,
    Condition,
    EvaluationContext,
    FallbackAllocation,
    NoMatchBehavior,
    Operand,
    OperandType,
    RebalanceFrequency,
    RebalancePolicy,
    StrategyDefinition,
    StrategyVersion,
    Threshold,
    ThresholdType,
    evaluate_strategy,
)

START = date(2026, 9, 7)


def _dates(count: int) -> tuple[date, ...]:
    return tuple(START + timedelta(days=index) for index in range(count))


def _dataset(closes: tuple[float, ...], opens: tuple[float, ...]) -> HistoricalDataSet:
    dates = _dates(len(closes))
    points = tuple(
        MarketDataPoint(
            date=as_of_date,
            open=open_price,
            high=max(open_price, close_price),
            low=min(open_price, close_price),
            close=close_price,
            volume=1_000.0,
            adj_open=open_price,
            adj_high=max(open_price, close_price),
            adj_low=min(open_price, close_price),
            adj_close=close_price,
            adj_volume=1_000.0,
            div_cash=0.0,
            split_factor=1.0,
        )
        for as_of_date, open_price, close_price in zip(dates, opens, closes, strict=True)
    )
    return HistoricalDataSet(
        HistoricalDataRequest(
            "QQQ",
            dates[0],
            dates[-1],
            price_field_used=PriceField.RAW_CLOSE,
        ),
        points,
        DataSource.API_FRESH,
    )


def _condition(operator: ComparisonOperator, threshold: float) -> Condition:
    return Condition(
        Operand("QQQ", OperandType.PRICE, price_field=PriceField.RAW_CLOSE),
        operator,
        Operand("QQQ", OperandType.MA, period=200, price_field=PriceField.RAW_CLOSE),
        Threshold(ThresholdType.RELATIVE, threshold),
    )


def _version() -> StrategyVersion:
    definition = StrategyDefinition(
        strategy_id="stateful-backtest",
        name="Stateful Backtest",
        description="Stateful strategy-to-backtest integration fixture",
        assets=(AssetReference("QQQ"),),
        price_field=PriceField.RAW_CLOSE,
        rules=(
            AllocationRule(
                "buy",
                "Buy",
                100,
                (Allocation("QQQ", 1.0),),
                _condition(ComparisonOperator.GREATER_THAN, 0.04),
            ),
            AllocationRule(
                "sell",
                "Sell",
                90,
                (Allocation("QQQ", 0.0),),
                _condition(ComparisonOperator.LESS_THAN, -0.03),
            ),
        ),
        fallback=FallbackAllocation((Allocation("QQQ", 1.0),), name="unused-fallback"),
        rebalance_policy=RebalancePolicy(RebalanceFrequency.ON_SIGNAL_CHANGE),
        no_match_behavior=NoMatchBehavior.HOLD_PREVIOUS_ALLOCATION,
        initial_allocation=AllocationSpecification(()),
    )
    return StrategyVersion(
        strategy_id=definition.strategy_id,
        version_id="stateful-backtest-v1",
        version_number=1,
        created_at=datetime(2026, 9, 7, tzinfo=UTC),
        configuration=definition,
    )


def _run(closes: tuple[float, ...], opens: tuple[float, ...], *, capital: float = 100.0):
    dataset = _dataset(closes, opens)
    dates = _dates(len(closes))
    indicator = IndicatorSeries(
        kind=IndicatorKind.MOVING_AVERAGE,
        period=200,
        price_field_used=PriceField.RAW_CLOSE,
        points=tuple(IndicatorPoint(as_of_date, 100.0) for as_of_date in dates),
    )
    version = _version()
    timeline = evaluate_strategy(
        version,
        EvaluationContext.from_components({"QQQ": dataset}, (("QQQ", indicator),)),
        dates[0],
        dates[-1],
    )
    result = run_strategy_backtest(
        version,
        timeline,
        {"QQQ": dataset},
        BacktestConfig(
            strategy_version_id=version.version_id,
            start_date=dates[0],
            end_date=dates[-1],
            initial_capital=capital,
            price_field_used=PriceField.RAW_CLOSE,
            rebalance_policy=BacktestRebalancePolicy(BacktestRebalanceFrequency.ON_SIGNAL_CHANGE),
        ),
    )
    return timeline, result


def test_stateful_golden_chain_rebalances_only_on_target_transitions_at_next_open() -> None:
    timeline, result = _run(
        (105.0, 102.0, 99.0, 96.0, 100.0, 105.0),
        (30.0, 33.0, 31.0, 29.0, 28.0, 35.0),
    )

    assert [dict(item.target_allocation.weights) for item in timeline.evaluations] == [
        {"QQQ": 1.0},
        {"QQQ": 1.0},
        {"QQQ": 1.0},
        {"QQQ": 0.0},
        {"QQQ": 0.0},
        {"QQQ": 1.0},
    ]
    order_timing = [
        (order.side, order.signal_date, order.date) for order in result.backtest_result.orders
    ]
    assert order_timing == [
        (OrderSide.BUY, _dates(6)[0], _dates(6)[1]),
        (OrderSide.SELL, _dates(6)[3], _dates(6)[4]),
    ]
    assert result.backtest_result.orders[0].requested_price == pytest.approx(33.0)
    assert result.backtest_result.orders[0].quantity == 3
    assert result.backtest_result.equity_curve[1].cash == pytest.approx(1.0)
    assert timeline.evaluations[1].target_allocation.weights == {"QQQ": 1.0}
    assert result.signal_records[-1].signal.matched_rule_id == "buy"
    assert result.signal_records[-1].submitted_to_backtest is False
    assert all(fill.date <= _dates(6)[-1] for fill in result.backtest_result.fills)


def test_consecutive_buy_and_sell_targets_keep_strategy_state_ahead_of_fills() -> None:
    timeline, result = _run(
        (105.0, 96.0, 100.0),
        (10.0, 20.0, 30.0),
        capital=1_000.0,
    )

    assert [dict(item.target_allocation.weights) for item in timeline.evaluations] == [
        {"QQQ": 1.0},
        {"QQQ": 0.0},
        {"QQQ": 0.0},
    ]
    order_timing = [
        (order.side, order.signal_date, order.date) for order in result.backtest_result.orders
    ]
    assert order_timing == [
        (OrderSide.BUY, _dates(3)[0], _dates(3)[1]),
        (OrderSide.SELL, _dates(3)[1], _dates(3)[2]),
    ]
