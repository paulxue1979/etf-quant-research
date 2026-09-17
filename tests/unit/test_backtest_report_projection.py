from __future__ import annotations

from dataclasses import replace
from datetime import date
from types import MappingProxyType

from backend.app.backtest_report_projection import (
    REPORT_SCHEMA_VERSION,
    BacktestReportProjectionService,
)
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
    assert report["trades"]["closed_trade_count"] == len(run.backtest_result.trades)


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
