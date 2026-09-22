from __future__ import annotations

from analytics.performance import analyze_backtest
from backend.app.backtest_models import ANALYSIS_VERSION, BacktestRun
from backend.app.backtest_repository import BacktestRunRecord
from backend.app.research import ComparisonInclude, compare_records
from backend.app.wealth_projection import project_wealth
from backtest import (
    BacktestConfig,
    BacktestEngine,
    ContributionFrequency,
    ContributionSchedule,
    RebalancePolicy,
)
from backtest.integration import run_strategy_backtest
from data.models import PriceField
from indicators import moving_average
from strategies import EvaluationContext, evaluate_strategy
from tests.integration.test_report_2_0_integrity import _flat_weekday_data
from tests.integration.test_strategy_backtest_chain import START, _conditional_version, _dataset


def _record(result, strategy_id: str) -> BacktestRunRecord:
    run = BacktestRun.create(
        strategy_id=strategy_id,
        strategy_version_id=result.strategy_version_id,
        strategy_version_content_hash=f"{strategy_id}-content-hash",
        backtest_result=result,
        performance_analysis=analyze_backtest(result),
        provenance={"source": "PHASE 11J realistic acceptance"},
    )
    return BacktestRunRecord(run=run, analysis_version=ANALYSIS_VERSION)


def test_flat_market_lump_sum_and_dca_have_equal_final_capital_but_different_timing() -> None:
    dataset = _flat_weekday_data()
    shared = {
        "strategy_version_id": "wealth-flat-v1",
        "start_date": dataset.request.start_date,
        "end_date": dataset.request.end_date,
        "price_field_used": PriceField.ADJUSTED_CLOSE,
        "rebalance_policy": RebalancePolicy(),
    }
    lump_sum = BacktestEngine().run(
        {"QQQ": dataset},
        (),
        BacktestConfig(initial_capital=100_000, **shared),
    )
    dca = BacktestEngine().run(
        {"QQQ": dataset},
        (),
        BacktestConfig(
            initial_capital=97_000,
            contribution_schedule=ContributionSchedule(
                ContributionFrequency.MONTHLY,
                "1000",
            ),
            **shared,
        ),
    )

    payload = compare_records(
        (_record(lump_sum, "lump-sum"), _record(dca, "dca")),
        include=ComparisonInclude(
            portfolio_value=True,
            capital_invested=True,
            investment_profit=True,
        ),
    ).to_dict()

    assert lump_sum.total_capital_invested == dca.total_capital_invested == 100_000
    assert (
        payload["compatibility"]["portfolio_value"]["dimensions"]["effective_cash_flow_sequence"][
            "status"
        ]
        == "MISMATCH"
    )
    assert (
        "TOTAL_CAPITAL_EQUAL_BUT_TIMING_DIFFERS"
        in payload["compatibility"]["portfolio_value"]["reason_codes"]
    )
    assert payload["series"][0]["capital_invested"]["points"][0]["value"] == 100_000
    assert [point["value"] for point in payload["series"][1]["capital_invested"]["points"]][
        -1
    ] == 100_000
    assert payload["series"][0]["investment_profit"]["points"][-1]["value"] == 0
    assert payload["series"][1]["investment_profit"]["points"][-1]["value"] == 0


def test_state_machine_multi_asset_run_with_contribution_uses_same_projection() -> None:
    version = _conditional_version()
    data = {
        "QQQ": _dataset("QQQ", (100.0, 100.0, 120.0, 80.0, 90.0)),
        "TQQQ": _dataset("TQQQ", (10.0, 10.0, 10.0, 10.0, 10.0)),
        "SGOV": _dataset("SGOV", (1.0, 1.0, 1.0, 1.0, 1.0)),
    }
    context = EvaluationContext.from_components(
        data,
        (("QQQ", moving_average(data["QQQ"], period=2, price_field=PriceField.RAW_CLOSE)),),
    )
    timeline = evaluate_strategy(version, context, START, data["QQQ"].request.end_date)
    result = run_strategy_backtest(
        version,
        timeline,
        data,
        BacktestConfig(
            strategy_version_id=version.version_id,
            start_date=START,
            end_date=data["QQQ"].request.end_date,
            initial_capital=9_000,
            price_field_used=PriceField.RAW_CLOSE,
            rebalance_policy=RebalancePolicy(),
            contribution_schedule=ContributionSchedule(
                ContributionFrequency.MONTHLY,
                "1000",
            ),
        ),
    )

    points = project_wealth(result.backtest_result)

    assert result.signal_records
    assert result.backtest_result.contribution_events
    assert points[0].capital_invested == 10_000
    assert points[-1].capital_invested == result.backtest_result.total_capital_invested
    assert points[-1].investment_profit == result.backtest_result.investment_profit
