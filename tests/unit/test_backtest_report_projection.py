from __future__ import annotations

from dataclasses import replace
from datetime import date
from types import MappingProxyType

from backend.app.backtest_report_projection import (
    REPORT_SCHEMA_VERSION,
    BacktestReportProjectionService,
)
from backtest.models import AllocationPoint, RebalanceCause
from tests.unit.test_backtest_repository import _run


def _provenance() -> MappingProxyType:
    return MappingProxyType(
        {
            "records": (
                {
                    "signal_date": "2026-01-02",
                    "matched_rule_id": "trend-on",
                    "allocation_source": "rule_match",
                    "target_allocation": {"QQQ": 0.6, "TQQQ": 0.3},
                    "execution_date": "2026-01-03",
                    "execution_status": "submitted",
                    "omission_reason": None,
                },
                {
                    "signal_date": "2026-01-04",
                    "matched_rule_id": None,
                    "allocation_source": "hold_previous",
                    "target_allocation": {"QQQ": 0.6, "TQQQ": 0.3},
                    "execution_date": None,
                    "execution_status": "omitted",
                    "omission_reason": "no future trading day in backtest range",
                },
            ),
            "source": "StrategyBacktestResult.signal_records",
        }
    )


def _transition_provenance() -> MappingProxyType:
    return MappingProxyType(
        {
            "records": (
                {
                    "signal_date": "2026-01-02",
                    "matched_rule_id": "buy",
                    "allocation_source": "rule_match",
                    "target_allocation": {"QQQ": 1.0},
                    "execution_date": "2026-01-03",
                    "execution_status": "submitted",
                    "omission_reason": None,
                },
                {
                    "signal_date": "2026-01-03",
                    "matched_rule_id": "buy",
                    "allocation_source": "rule_match",
                    "target_allocation": {"QQQ": 1.0},
                    "execution_date": "2026-01-04",
                    "execution_status": "submitted",
                    "omission_reason": None,
                },
                {
                    "signal_date": "2026-01-04",
                    "matched_rule_id": "sell",
                    "allocation_source": "rule_match",
                    "target_allocation": {},
                    "execution_date": None,
                    "execution_status": "omitted",
                    "omission_reason": "no future trading day in backtest range",
                },
            ),
            "source": "StrategyBacktestResult.signal_records",
        }
    )


def _hold_only_provenance() -> MappingProxyType:
    return MappingProxyType(
        {
            "records": (
                {
                    "signal_date": "2026-01-02",
                    "matched_rule_id": None,
                    "allocation_source": "hold_previous",
                    "target_allocation": {"QQQ": 1.0},
                    "execution_date": "2026-01-03",
                    "execution_status": "submitted",
                    "omission_reason": None,
                },
            ),
            "source": "StrategyBacktestResult.signal_records",
        }
    )


def test_projection_separates_capital_profit_and_strategy_metrics_without_recalculation() -> None:
    run = replace(_run(), strategy_provenance=_provenance())

    report = BacktestReportProjectionService().project(run)

    assert report["report_schema_version"] == REPORT_SCHEMA_VERSION
    assert report["identity"]["backtest_run_id"] == run.backtest_run_id
    assert report["summary"]["account"]["ending_value"] == run.backtest_result.final_equity
    assert report["capital"] == {
        "initial_capital": run.backtest_result.initial_capital,
        "cumulative_contributions": run.backtest_result.cumulative_contributions,
        "total_capital_invested": run.backtest_result.total_capital_invested,
    }
    assert report["profit"]["investment_profit"] == run.backtest_result.investment_profit
    assert report["performance"]["twr_total_return"] == (
        run.performance_analysis.total_return.to_dict()
    )
    assert report["investor_experience"]["xirr"] == run.performance_analysis.xirr.to_dict()


def test_projection_keeps_hold_previous_out_of_trade_count() -> None:
    run = replace(_run(), strategy_provenance=_provenance())

    report = BacktestReportProjectionService().project(run)

    provenance = report["strategy_provenance"]
    assert provenance["status"] == "available"
    assert provenance["record_count"] == 2
    assert provenance["allocation_sources"] == {
        "rule_match": 1,
        "hold_previous": 1,
    }
    assert provenance["submitted_count"] == 1
    assert provenance["omitted_count"] == 1
    assert [item["marker_type"] for item in provenance["markers"]] == ["signal", "execution"]
    assert report["trades"]["closed_trade_count"] == len(run.backtest_result.trades)


def test_projection_emits_only_transition_signals_and_real_execution_markers() -> None:
    run = replace(_run(), strategy_provenance=_transition_provenance())

    provenance = BacktestReportProjectionService().project(run)["strategy_provenance"]

    assert [
        (item["marker_type"], item["date"], item.get("matched_rule_id"))
        for item in provenance["markers"]
    ] == [
        ("signal", "2026-01-02", "buy"),
        ("execution", "2026-01-03", "buy"),
        ("signal", "2026-01-04", "sell"),
    ]
    assert provenance["markers"][1]["rebalance_cause"] == "target"
    assert provenance["markers"][1]["order_count"] == 1
    assert provenance["markers"][1]["fill_count"] == 1
    assert provenance["markers"][-1]["execution_status"] == "omitted"


def test_contribution_execution_is_not_promoted_to_strategy_signal() -> None:
    base = _run()
    contribution_order = replace(
        base.backtest_result.orders[0],
        rebalance_cause=RebalanceCause.CONTRIBUTION,
    )
    result = replace(base.backtest_result, orders=(contribution_order,))
    run = replace(base, backtest_result=result, strategy_provenance=_hold_only_provenance())

    markers = BacktestReportProjectionService().project(run)["strategy_provenance"]["markers"]

    assert [item["marker_type"] for item in markers] == ["execution"]
    assert markers[0]["rebalance_cause"] == "contribution"


def test_projection_keeps_multi_asset_target_actual_cash_and_sgov_distinct() -> None:
    base = _run()
    first_date = base.backtest_result.equity_curve[0].date
    result = replace(
        base.backtest_result,
        allocation_history=(
            AllocationPoint(first_date, "QQQ", 0.6, 0.59),
            AllocationPoint(first_date, "TQQQ", 0.3, 0.28),
            AllocationPoint(first_date, "SGOV", 0.1, 0.09),
        ),
        equity_curve=(
            replace(
                base.backtest_result.equity_curve[0],
                cash=400.0,
                asset_values=MappingProxyType({"QQQ": 5900.0, "TQQQ": 2800.0, "SGOV": 900.0}),
                total_equity=10_000.0,
            ),
            *base.backtest_result.equity_curve[1:],
        ),
    )
    provenance = MappingProxyType(
        {
            "records": (
                {
                    "signal_date": first_date.isoformat(),
                    "matched_rule_id": "mixed",
                    "allocation_source": "rule_match",
                    "target_allocation": {"QQQ": 0.6, "TQQQ": 0.3, "SGOV": 0.1},
                    "execution_date": "2026-01-03",
                    "execution_status": "submitted",
                    "omission_reason": None,
                },
            ),
            "source": "StrategyBacktestResult.signal_records",
        }
    )
    run = replace(base, backtest_result=result, strategy_provenance=provenance)

    allocations = BacktestReportProjectionService().project(run)["allocations"]

    assert allocations["target"]["asset_symbols"] == ["QQQ", "SGOV", "TQQQ"]
    assert allocations["target"]["timeline"][0]["cash_weight"] == 0.0
    assert allocations["actual"]["timeline"][0] == {
        "date": "2026-01-02",
        "asset_weights": {"QQQ": 0.59, "SGOV": 0.09, "TQQQ": 0.28},
        "target_asset_weights": {"QQQ": 0.6, "SGOV": 0.1, "TQQQ": 0.3},
        "cash_weight": 0.04,
    }
    assert allocations["cash_semantics"].endswith("SGOV remains an asset")


def test_legacy_run_degrades_to_explicitly_unavailable_strategy_provenance() -> None:
    report = BacktestReportProjectionService().project(_run())

    assert report["strategy_provenance"] == {
        "status": "not_available",
        "reason": "strategy execution provenance was not persisted for this legacy run",
    }
    assert report["holdings"] == {
        "status": "not_available",
        "reason": "canonical FIFO open-lot provenance is not persisted",
    }


def test_series_are_windowed_without_altering_full_period_summary_metrics() -> None:
    run = replace(_run(), strategy_provenance=_provenance())
    service = BacktestReportProjectionService()

    payload = service.series(
        run,
        include=("equity", "capital", "twr", "drawdown", "benchmark"),
        start=date(2026, 1, 3),
        end=date(2026, 1, 3),
    )

    assert payload["report_schema_version"] == REPORT_SCHEMA_VERSION
    assert payload["series"]["equity"]["points"] == [
        {"date": "2026-01-03", "value": run.backtest_result.equity_curve[1].total_equity}
    ]
    assert payload["series"]["capital"]["points"] == [
        {"date": "2026-01-03", "value": run.backtest_result.initial_capital}
    ]
    assert payload["series"]["drawdown"]["source"] == ("PerformanceAnalysisResult.drawdown_curve")
    assert payload["series"]["twr"]["status"] == "available"
    assert payload["series"]["benchmark"]["status"] == "not_available"
    assert payload["summary_period"] == {
        "start_date": "2026-01-02",
        "end_date": "2026-01-04",
    }
