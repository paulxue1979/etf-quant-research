from __future__ import annotations

from datetime import date, timedelta

import pytest

from analytics import analyze_backtest
from analytics.benchmark import BenchmarkEvaluationService
from backtest import (
    BacktestConfig,
    BacktestEngine,
    ContributionFrequency,
    ContributionSchedule,
    RebalancePolicy,
    TargetAllocation,
)
from data.models import (
    DataSource,
    HistoricalDataRequest,
    HistoricalDataSet,
    MarketDataPoint,
    PriceField,
)


def _data(
    symbol: str = "QQQ", closes: tuple[float, ...] = (100, 110, 100, 120)
) -> HistoricalDataSet:
    start = date(2026, 1, 2)
    points = tuple(
        MarketDataPoint(
            date=start + timedelta(days=index),
            open=value,
            high=value,
            low=value,
            close=value,
            volume=100,
            adj_open=value,
            adj_high=value,
            adj_low=value,
            adj_close=value,
            adj_volume=100,
            div_cash=0,
            split_factor=1,
        )
        for index, value in enumerate(closes)
    )
    return HistoricalDataSet(
        HistoricalDataRequest(
            symbol,
            points[0].date,
            points[-1].date,
            price_field_used=PriceField.RAW_CLOSE,
        ),
        points,
        DataSource.CACHE,
    )


def _run(*, contribution: ContributionSchedule | None = None):
    dataset = _data()
    config = BacktestConfig(
        strategy_version_id="9gb-v1",
        start_date=dataset.points[0].date,
        end_date=dataset.points[-1].date,
        initial_capital=1_000,
        price_field_used=PriceField.RAW_CLOSE,
        rebalance_policy=RebalancePolicy(),
        contribution_schedule=contribution,
    )
    return BacktestEngine().run(
        {"QQQ": dataset},
        (TargetAllocation.from_weights(dataset.points[0].date, {"QQQ": 1.0}),),
        config,
    )


def test_twr_wealth_is_canonical_and_drawdown_uses_twr() -> None:
    analysis = analyze_backtest(_run())

    assert [point.value for point in analysis.twr_wealth_curve] == pytest.approx(
        [1.0, 1.0, 0.91, 1.09]
    )
    assert analysis.drawdown_curve
    assert min(point.value for point in analysis.drawdown_curve) == pytest.approx(
        analysis.max_drawdown.value
    )


def test_xirr_is_cash_flow_based_and_not_cagr() -> None:
    analysis = analyze_backtest(
        _run(
            contribution=ContributionSchedule(
                ContributionFrequency.ONE_TIME,
                100,
                requested_date=date(2026, 1, 3),
            )
        )
    )
    assert analysis.xirr.is_evaluable
    assert analysis.xirr.value != analysis.cagr.value


def test_exposure_distinguishes_cash_from_security_and_is_dynamic() -> None:
    dataset = _data()
    config = BacktestConfig(
        strategy_version_id="exposure-v1",
        start_date=dataset.points[0].date,
        end_date=dataset.points[-1].date,
        initial_capital=1_000,
        price_field_used=PriceField.RAW_CLOSE,
        rebalance_policy=RebalancePolicy(),
    )
    cash_run = BacktestEngine().run({"QQQ": dataset}, (), config)
    asset_run = BacktestEngine().run(
        {"QQQ": dataset},
        (TargetAllocation.from_weights(dataset.points[0].date, {"QQQ": 1.0}),),
        config,
    )
    assert analyze_backtest(cash_run).exposure_summary["average_cash_weight"] == pytest.approx(1.0)
    assert analyze_backtest(asset_run).exposure_summary["average_cash_weight"] < 1.0


def test_turnover_excludes_external_contribution_notional() -> None:
    analysis = analyze_backtest(
        _run(
            contribution=ContributionSchedule(
                ContributionFrequency.ONE_TIME,
                100,
                requested_date=date(2026, 1, 3),
            )
        )
    )
    assert analysis.turnover.is_evaluable
    assert analysis.turnover_provenance["contribution_cash_is_not_traded"] is True


def test_benchmark_reuses_execution_contract_and_is_fair_for_dca() -> None:
    strategy = _run(
        contribution=ContributionSchedule(
            ContributionFrequency.ONE_TIME,
            100,
            requested_date=date(2026, 1, 3),
        )
    )
    dataset = _data()
    config = BacktestConfig(
        strategy_version_id="benchmark-owner",
        start_date=dataset.points[0].date,
        end_date=dataset.points[-1].date,
        initial_capital=1_000,
        price_field_used=PriceField.RAW_CLOSE,
        rebalance_policy=RebalancePolicy(),
        contribution_schedule=ContributionSchedule(
            ContributionFrequency.ONE_TIME,
            100,
            requested_date=date(2026, 1, 3),
        ),
    )
    artifact = BenchmarkEvaluationService().evaluate("QQQ", dataset, config)
    assert artifact.status == "available"
    assert artifact.provenance["price_field_used"] == "raw_close"
    assert artifact.provenance["same_contribution_schedule"] is True
    assert artifact.backtest_result.orders[0].date > artifact.backtest_result.orders[0].signal_date
    assert strategy.total_capital_invested == pytest.approx(1_100)
