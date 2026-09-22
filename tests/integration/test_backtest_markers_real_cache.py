from __future__ import annotations

from datetime import UTC, datetime

import pytest

from analytics import analyze_backtest
from backend.app.backtest_marker_projection import BacktestMarkerProjectionService
from backend.app.backtest_models import BacktestRun
from backend.app.strategy_execution_provenance import strategy_execution_provenance
from backtest import BacktestConfig, ContributionFrequency, ContributionSchedule, RebalancePolicy
from backtest.integration import run_strategy_backtest
from data.models import PriceField
from indicators import moving_average
from strategies import EvaluationContext, StrategyVersion, evaluate_strategy
from tests.integration.test_five_etf_real_data_acceptance import _strategy
from tests.integration.test_wealth_accumulation_real_cache import END, START, _cached_data


@pytest.mark.integration
def test_real_cache_five_etf_signal_execution_contribution_and_allocation_markers() -> None:
    data = _cached_data()
    definition = _strategy()
    version = StrategyVersion(
        strategy_id=definition.strategy_id,
        version_id="real-cache-five-etf-markers-v1",
        version_number=1,
        created_at=datetime(2026, 9, 22, tzinfo=UTC),
        configuration=definition,
    )
    context = EvaluationContext.from_components(
        data,
        (
            (
                "QQQ",
                moving_average(data["QQQ"], period=20, price_field=PriceField.ADJUSTED_CLOSE),
            ),
        ),
    )
    timeline = evaluate_strategy(version, context, START, END)
    integration = run_strategy_backtest(
        version,
        timeline,
        data,
        BacktestConfig(
            strategy_version_id=version.version_id,
            start_date=START,
            end_date=END,
            initial_capital=10_000,
            price_field_used=PriceField.ADJUSTED_CLOSE,
            rebalance_policy=RebalancePolicy(),
            contribution_schedule=ContributionSchedule(ContributionFrequency.MONTHLY, "1000"),
        ),
    )
    run = BacktestRun(
        backtest_run_id="real-cache-five-etf-markers-run",
        strategy_id=version.strategy_id,
        strategy_version_id=version.version_id,
        created_at=datetime(2026, 9, 22, tzinfo=UTC),
        strategy_version_content_hash=version.content_hash or "",
        backtest_result=integration.backtest_result,
        performance_analysis=analyze_backtest(
            integration.backtest_result,
            backtest_run_id="real-cache-five-etf-markers-run",
            strategy_id=version.strategy_id,
        ),
        provenance={"source": "real-tiingo-cache"},
        strategy_provenance=strategy_execution_provenance(integration.signal_records),
    )

    payload = BacktestMarkerProjectionService().project(run)
    by_type = payload["counts"]["by_type"]

    assert {dataset.source.value for dataset in data.values()} == {"cache"}
    assert by_type["SIGNAL"] > 0
    assert by_type["EXECUTION"] > 0
    assert by_type["CONTRIBUTION"] > 0
    assert by_type["TARGET_ALLOCATION_TRANSITION"] > 0
    assert by_type["ACTUAL_ALLOCATION_TRANSITION"] > 0
    assert payload["counts"]["truncated"] is False
    assert all(
        marker["event_date"] == marker["details"]["effective_date"]
        for marker in payload["markers"]
        if marker["marker_type"] == "CONTRIBUTION"
    )
    assert all(
        marker["event_date"] == marker["details"]["execution_date"]
        for marker in payload["markers"]
        if marker["marker_type"] == "EXECUTION"
    )
    assert all(
        marker["details"]["related_signal_date"] < marker["event_date"]
        for marker in payload["markers"]
        if marker["marker_type"] == "EXECUTION"
        and marker["details"]["related_signal_date"] is not None
    )
