from __future__ import annotations

from datetime import date, timedelta

import pytest

from analytics.benchmark import BenchmarkEvaluationService
from analytics.performance import analyze_backtest
from backend.app.backtest_models import BacktestRun
from backend.app.backtest_report_projection import BacktestReportProjectionService
from backtest import (
    BacktestConfig,
    BacktestEngine,
    ContributionFrequency,
    ContributionSchedule,
    RebalancePolicy,
)
from data.models import (
    DataSource,
    HistoricalDataRequest,
    HistoricalDataSet,
    MarketDataPoint,
    PriceField,
)


def _flat_weekday_data() -> HistoricalDataSet:
    start = date(2026, 1, 2)
    end = date(2026, 3, 31)
    dates = []
    current = start
    while current <= end:
        if current.weekday() < 5:
            dates.append(current)
        current += timedelta(days=1)
    points = tuple(
        MarketDataPoint(
            date=item,
            open=100.0,
            high=100.0,
            low=100.0,
            close=100.0,
            volume=1_000.0,
            adj_open=100.0,
            adj_high=100.0,
            adj_low=100.0,
            adj_close=100.0,
            adj_volume=1_000.0,
            div_cash=0.0,
            split_factor=1.0,
        )
        for item in dates
    )
    return HistoricalDataSet(
        request=HistoricalDataRequest(
            "QQQ",
            start,
            end,
            price_field_used=PriceField.ADJUSTED_CLOSE,
        ),
        points=points,
        source=DataSource.CACHE,
    )


def test_report_2_0_flat_market_monthly_dca_integrity() -> None:
    dataset = _flat_weekday_data()
    config = BacktestConfig(
        strategy_version_id="report-2-v1",
        start_date=dataset.request.start_date,
        end_date=dataset.request.end_date,
        initial_capital=100_000,
        price_field_used=PriceField.ADJUSTED_CLOSE,
        rebalance_policy=RebalancePolicy(),
        contribution_schedule=ContributionSchedule(
            ContributionFrequency.MONTHLY,
            "1000",
        ),
    )
    result = BacktestEngine().run({"QQQ": dataset}, (), config)
    analysis = analyze_backtest(result)
    owner_timeline = tuple(point.date for point in result.equity_curve)
    benchmark = BenchmarkEvaluationService().evaluate(
        "QQQ",
        dataset,
        config,
        required_dates=owner_timeline,
    )
    run = BacktestRun.create(
        strategy_id="report-2",
        strategy_version_id="report-2-v1",
        strategy_version_content_hash="report-2-content-hash",
        backtest_result=result,
        performance_analysis=analysis,
        provenance={"source": "PHASE 9G-G synthetic acceptance"},
        strategy_provenance={"source": "StrategyBacktestResult.signal_records", "records": []},
        benchmark_evaluation=benchmark.to_dict(),
    )
    projection = BacktestReportProjectionService()
    report = projection.project(run)
    series = projection.series(
        run,
        include=("equity", "capital", "twr", "drawdown", "benchmark_twr"),
    )
    holdings = projection.holdings(run)

    assert result.orders == result.fills == result.trades == ()
    assert result.final_equity == pytest.approx(result.total_capital_invested)
    assert result.investment_profit == pytest.approx(0.0)
    assert analysis.total_return.value == pytest.approx(0.0)
    assert analysis.xirr.value == pytest.approx(0.0, abs=1e-8)
    assert report["identity"]["strategy_version_id"] == "report-2-v1"
    assert report["configuration"]["price_field_used"] == "adjusted_close"
    assert report["contribution_report"]["status"] == "available"
    assert report["contribution_report"]["event_count"] >= 2
    assert report["contribution_report"]["integrity"]["status"] == "consistent"
    assert series["series"]["capital"]["points"][-1]["value"] == pytest.approx(
        result.total_capital_invested
    )
    assert series["series"]["twr"]["points"][-1]["value"] - 1 == pytest.approx(
        report["performance"]["twr_total_return"]["value"]
    )
    assert min(item["value"] for item in series["series"]["drawdown"]["points"]) == (
        pytest.approx(report["performance"]["max_drawdown"]["value"])
    )
    assert report["summary"]["benchmark"]["status"] == "available"
    assert report["summary"]["benchmark"]["provenance"]["common_timeline_enforced"] is True
    assert tuple(point.date for point in benchmark.backtest_result.equity_curve) == owner_timeline
    assert holdings["status"] == "available"
    assert holdings["total"] == 0
