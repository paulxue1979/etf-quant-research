from __future__ import annotations

from dataclasses import replace
from datetime import UTC, date, datetime, timedelta

import pytest

from backtest import BacktestConfig, RebalanceFrequency, RebalancePolicy
from backtest.integration import run_strategy_backtest
from data.models import (
    DataSource,
    HistoricalDataRequest,
    HistoricalDataSet,
    MarketDataPoint,
    PriceField,
)
from indicators import moving_average
from strategies import (
    Allocation,
    AllocationRule,
    AllocationSource,
    AssetReference,
    ComparisonOperator,
    Condition,
    EvaluationContext,
    FallbackAllocation,
    LogicalOperator,
    Operand,
    OperandType,
    RemainingAllocation,
    RuleGroup,
    StrategyDefinition,
    StrategyEvaluationStatus,
    StrategyVersion,
    evaluate_strategy,
)
from strategies import (
    RebalanceFrequency as StrategyRebalanceFrequency,
)
from strategies import (
    RebalancePolicy as StrategyRebalancePolicy,
)

START = date(2026, 2, 2)


def _dataset(symbol: str, prices: tuple[float, ...]) -> HistoricalDataSet:
    points = tuple(
        MarketDataPoint(
            date=START + timedelta(days=index),
            open=price,
            high=price,
            low=price,
            close=price,
            volume=100.0,
            adj_open=price,
            adj_high=price,
            adj_low=price,
            adj_close=price,
            adj_volume=100.0,
            div_cash=0.0,
            split_factor=1.0,
        )
        for index, price in enumerate(prices)
    )
    return HistoricalDataSet(
        HistoricalDataRequest(
            symbol,
            points[0].date,
            points[-1].date,
            price_field_used=PriceField.RAW_CLOSE,
        ),
        points,
        DataSource.API_FRESH,
    )


def _conditional_version() -> StrategyVersion:
    field = PriceField.RAW_CLOSE
    condition = RuleGroup(
        LogicalOperator.AND,
        (
            Condition(
                Operand("QQQ", OperandType.PRICE, price_field=field),
                ComparisonOperator.GREATER_THAN,
                Operand("QQQ", OperandType.MA, period=2, price_field=field),
            ),
        ),
    )
    definition = StrategyDefinition(
        strategy_id="full-chain",
        name="Full Chain",
        description="PHASE 4C through 4H deterministic integration fixture",
        assets=tuple(AssetReference(symbol) for symbol in ("QQQ", "TQQQ", "SGOV")),
        price_field=field,
        rules=(
            AllocationRule(
                "risk-on",
                "Risk On",
                100,
                (Allocation("QQQ", 0.6), Allocation("TQQQ", 0.3)),
                condition,
                RemainingAllocation("SGOV"),
            ),
        ),
        fallback=FallbackAllocation((Allocation("SGOV", 1.0),)),
        rebalance_policy=StrategyRebalancePolicy(StrategyRebalanceFrequency.DAILY),
    )
    return StrategyVersion(
        strategy_id=definition.strategy_id,
        version_id="full-chain-v1",
        version_number=1,
        created_at=datetime(2026, 2, 1, tzinfo=UTC),
        configuration=definition,
    )


def test_condition_to_rule_to_signal_to_backtest_full_chain() -> None:
    version = _conditional_version()
    data = {
        "QQQ": _dataset("QQQ", (100.0, 100.0, 120.0, 80.0, 90.0)),
        "TQQQ": _dataset("TQQQ", (10.0, 10.0, 10.0, 10.0, 10.0)),
        "SGOV": _dataset("SGOV", (1.0, 1.0, 1.0, 1.0, 1.0)),
    }
    context = EvaluationContext.from_components(
        data,
        (
            (
                "QQQ",
                moving_average(data["QQQ"], period=2, price_field=PriceField.RAW_CLOSE),
            ),
        ),
    )
    timeline = evaluate_strategy(version, context, START, START + timedelta(days=4))
    config = BacktestConfig(
        strategy_version_id=version.version_id,
        start_date=START,
        end_date=START + timedelta(days=4),
        initial_capital=10_000.0,
        price_field_used=PriceField.RAW_CLOSE,
        rebalance_policy=RebalancePolicy(RebalanceFrequency.DAILY),
    )

    result = run_strategy_backtest(version, timeline, data, config)

    assert [item.status for item in timeline.evaluations] == [
        StrategyEvaluationStatus.NOT_EVALUABLE,
        StrategyEvaluationStatus.EVALUATED,
        StrategyEvaluationStatus.EVALUATED,
        StrategyEvaluationStatus.EVALUATED,
        StrategyEvaluationStatus.EVALUATED,
    ]
    assert result.signal_records[0].signal.allocation_source is AllocationSource.FALLBACK
    assert result.signal_records[0].signal.matched_rule_id is None
    assert result.signal_records[0].target_allocation.as_mapping() == {"SGOV": 1.0}
    assert result.signal_records[1].signal.allocation_source is AllocationSource.RULE_MATCH
    assert result.signal_records[1].target_allocation.as_mapping() == pytest.approx({
        "QQQ": 0.6,
        "TQQQ": 0.3,
        "SGOV": 0.1,
    })
    assert {order.signal_date for order in result.backtest_result.orders} == {
        START + timedelta(days=1),
        START + timedelta(days=2),
        START + timedelta(days=3),
    }
    assert {order.date for order in result.backtest_result.orders} == {
        START + timedelta(days=2),
        START + timedelta(days=3),
        START + timedelta(days=4),
    }
    assert all(
        order.date == order.signal_date + timedelta(days=1)
        for order in result.backtest_result.orders
    )
    assert result.signal_records[-1].submitted_to_backtest is False
    assert all(
        point.total_equity == pytest.approx(point.cash + sum(point.asset_values.values()))
        for point in result.backtest_result.equity_curve
    )


def test_on_signal_change_is_delegated_to_phase3() -> None:
    version = _conditional_version()
    definition = version.configuration
    definition = definition.__class__(
        **{
            **definition.__dict__,
            "rebalance_policy": StrategyRebalancePolicy(
                StrategyRebalanceFrequency.ON_SIGNAL_CHANGE
            ),
        }
    )
    version = replace(version, configuration=definition, content_hash=None)
    data = {
        symbol: _dataset(symbol, (10.0, 10.0, 10.0, 10.0))
        for symbol in ("QQQ", "TQQQ", "SGOV")
    }
    context = EvaluationContext.from_components(
        data,
        (
            (
                "QQQ",
                moving_average(data["QQQ"], period=2, price_field=PriceField.RAW_CLOSE),
            ),
        ),
    )
    timeline = evaluate_strategy(version, context, START, START + timedelta(days=3))
    config = BacktestConfig(
        strategy_version_id=version.version_id,
        start_date=START,
        end_date=START + timedelta(days=3),
        initial_capital=1_000.0,
        price_field_used=PriceField.RAW_CLOSE,
        rebalance_policy=RebalancePolicy(RebalanceFrequency.ON_SIGNAL_CHANGE),
    )

    result = run_strategy_backtest(version, timeline, data, config)

    assert len(result.signal_records) == 3
    assert len(result.backtest_result.fills) == 1
    assert result.backtest_result.fills[0].date == START + timedelta(days=2)
