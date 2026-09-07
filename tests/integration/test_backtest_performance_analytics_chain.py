from __future__ import annotations

from datetime import date, timedelta

import pytest

from analytics import analyze_backtest
from backtest import BacktestConfig, BacktestEngine, RebalancePolicy, TargetAllocation
from data.models import (
    DataSource,
    HistoricalDataRequest,
    HistoricalDataSet,
    MarketDataPoint,
    PriceField,
)


def _dataset() -> HistoricalDataSet:
    start = date(2025, 1, 2)
    opens = (10.0, 10.0, 12.0, 12.0)
    closes = (10.0, 11.0, 12.0, 12.0)
    points = tuple(
        MarketDataPoint(
            date=start + timedelta(days=index),
            open=open_price,
            high=max(open_price, close_price),
            low=min(open_price, close_price),
            close=close_price,
            volume=100.0,
            adj_open=open_price,
            adj_high=max(open_price, close_price),
            adj_low=min(open_price, close_price),
            adj_close=close_price,
            adj_volume=100.0,
            div_cash=0.0,
            split_factor=1.0,
        )
        for index, (open_price, close_price) in enumerate(zip(opens, closes, strict=True))
    )
    return HistoricalDataSet(
        request=HistoricalDataRequest("QQQ", points[0].date, points[-1].date),
        points=points,
        source=DataSource.API_FRESH,
    )


def test_backtest_result_flows_to_pure_performance_analytics_without_reexecution() -> None:
    data = _dataset()
    config = BacktestConfig(
        strategy_version_id="analytics-chain-v1",
        start_date=data.points[0].date,
        end_date=data.points[-1].date,
        initial_capital=1_000.0,
        price_field_used=PriceField.RAW_CLOSE,
        rebalance_policy=RebalancePolicy(),
    )
    backtest = BacktestEngine().run(
        {"QQQ": data},
        (
            TargetAllocation.from_weights(data.points[0].date, {"QQQ": 1.0}),
            TargetAllocation.from_weights(data.points[2].date, {"QQQ": 0.0}),
        ),
        config,
    )
    original_orders = backtest.orders
    original_fills = backtest.fills
    original_trades = backtest.trades

    analysis = analyze_backtest(backtest, backtest_run_id="chain-run-1")

    assert analysis.total_return.value == pytest.approx(0.20)
    assert analysis.price_field_used is PriceField.RAW_CLOSE
    assert analysis.strategy_version_id == "analytics-chain-v1"
    assert analysis.backtest_run_id == "chain-run-1"
    assert analysis.trade_metrics.number_of_closed_trades == 1
    assert backtest.orders == original_orders
    assert backtest.fills == original_fills
    assert backtest.trades == original_trades
