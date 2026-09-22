from __future__ import annotations

from datetime import UTC, date, datetime

import pytest

from backend.app.research import ComparisonInclude, compare_records
from backend.app.wealth_projection import project_wealth
from backtest import (
    BacktestConfig,
    BacktestEngine,
    ContributionFrequency,
    ContributionSchedule,
    RebalancePolicy,
    TargetAllocation,
)
from backtest.integration import run_strategy_backtest
from data.cache import DiskCache
from data.models import HistoricalDataRequest, HistoricalDataSet, PriceField
from indicators import moving_average
from strategies import EvaluationContext, StrategyVersion, evaluate_strategy
from tests.integration.test_five_etf_real_data_acceptance import FIVE_ETFS, _strategy
from tests.integration.test_wealth_accumulation_comparison import _record

START = date(2023, 10, 24)
END = date(2024, 12, 31)


def _cached_data() -> dict[str, HistoricalDataSet]:
    cache = DiskCache()
    data: dict[str, HistoricalDataSet] = {}
    for symbol in FIVE_ETFS:
        request = HistoricalDataRequest(
            symbol,
            START,
            END,
            price_field_used=PriceField.ADJUSTED_CLOSE,
        )
        dataset = cache.get(request)
        if dataset is None:
            pytest.skip(f"real local cache is unavailable for {symbol}")
        data[symbol] = dataset
    common_dates = set.intersection(
        *(set(point.date for point in dataset.points) for dataset in data.values())
    )
    return {
        symbol: HistoricalDataSet(
            request=dataset.request,
            points=tuple(point for point in dataset.points if point.date in common_dates),
            source=dataset.source,
        )
        for symbol, dataset in data.items()
    }


@pytest.mark.integration
def test_real_cache_qqq_lump_sum_dca_and_state_machine_wealth_comparison() -> None:
    data = _cached_data()
    qqq = data["QQQ"]
    allocation = (TargetAllocation.from_weights(qqq.points[0].date, {"QQQ": 1.0}),)
    common_config = {
        "strategy_version_id": "real-cache-qqq-v1",
        "start_date": START,
        "end_date": END,
        "price_field_used": PriceField.ADJUSTED_CLOSE,
        "rebalance_policy": RebalancePolicy(),
    }
    dca = BacktestEngine().run(
        {"QQQ": qqq},
        allocation,
        BacktestConfig(
            initial_capital=10_000,
            contribution_schedule=ContributionSchedule(
                ContributionFrequency.MONTHLY,
                "1000",
            ),
            **common_config,
        ),
    )
    lump_sum = BacktestEngine().run(
        {"QQQ": qqq},
        allocation,
        BacktestConfig(initial_capital=dca.total_capital_invested, **common_config),
    )
    comparison = compare_records(
        (_record(lump_sum, "qqq-lump-sum"), _record(dca, "qqq-dca")),
        include=ComparisonInclude(
            portfolio_value=True,
            capital_invested=True,
            investment_profit=True,
        ),
    ).to_dict()

    definition = _strategy()
    version = StrategyVersion(
        strategy_id=definition.strategy_id,
        version_id="real-cache-state-machine-v1",
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
    stateful = run_strategy_backtest(
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
            contribution_schedule=ContributionSchedule(
                ContributionFrequency.MONTHLY,
                "1000",
            ),
        ),
    )
    stateful_points = project_wealth(stateful.backtest_result)

    assert qqq.source.value == "cache"
    assert lump_sum.total_capital_invested == dca.total_capital_invested
    assert (
        "TOTAL_CAPITAL_EQUAL_BUT_TIMING_DIFFERS"
        in comparison["compatibility"]["portfolio_value"]["reason_codes"]
    )
    assert comparison["series"][0]["portfolio_value"]["points"][0]["value"] != 100
    assert (
        comparison["series"][1]["capital_invested"]["points"][-1]["value"]
        == dca.total_capital_invested
    )
    assert stateful.signal_records
    assert stateful.backtest_result.contribution_events
    assert stateful_points[-1].capital_invested == stateful.backtest_result.total_capital_invested
    assert stateful_points[-1].investment_profit == stateful.backtest_result.investment_profit
