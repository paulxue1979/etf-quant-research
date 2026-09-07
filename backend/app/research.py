"""Read-only comparison views over persisted backtest research artifacts."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from analytics.models import MetricValue
from backend.app.backtest_repository import BacktestRunRecord

MetricAccessor = Callable[[BacktestRunRecord], MetricValue]


def _trade_metric(name: str) -> MetricAccessor:
    return lambda record: getattr(record.run.performance_analysis.trade_metrics, name)


SORTABLE_METRICS: dict[str, MetricAccessor] = {
    "cagr": lambda record: record.run.performance_analysis.cagr,
    "sharpe_ratio": lambda record: record.run.performance_analysis.sharpe_ratio,
    "sortino_ratio": lambda record: record.run.performance_analysis.sortino_ratio,
    "max_drawdown": lambda record: record.run.performance_analysis.max_drawdown,
    "total_return": lambda record: record.run.performance_analysis.total_return,
    "annualized_volatility": lambda record: record.run.performance_analysis.annualized_volatility,
    "calmar_ratio": lambda record: record.run.performance_analysis.calmar_ratio,
    "win_rate": _trade_metric("win_rate"),
    "profit_factor": _trade_metric("profit_factor"),
    "average_trade_return": _trade_metric("average_trade_return"),
    "best_trade": _trade_metric("best_trade"),
    "worst_trade": _trade_metric("worst_trade"),
    "average_holding_period": _trade_metric("average_holding_period"),
    "turnover": _trade_metric("turnover"),
}


def metric_payload(record: BacktestRunRecord) -> dict[str, dict[str, Any]]:
    """Expose existing analytics without deriving or normalizing any metric."""
    analysis = record.run.performance_analysis
    trade = analysis.trade_metrics
    return {
        "total_return": analysis.total_return.to_dict(),
        "cagr": analysis.cagr.to_dict(),
        "annualized_volatility": analysis.annualized_volatility.to_dict(),
        "sharpe_ratio": analysis.sharpe_ratio.to_dict(),
        "sortino_ratio": analysis.sortino_ratio.to_dict(),
        "max_drawdown": analysis.max_drawdown.to_dict(),
        "calmar_ratio": analysis.calmar_ratio.to_dict(),
        "win_rate": trade.win_rate.to_dict(),
        "profit_factor": trade.profit_factor.to_dict(),
        "average_trade_return": trade.average_trade_return.to_dict(),
        "best_trade": trade.best_trade.to_dict(),
        "worst_trade": trade.worst_trade.to_dict(),
        "average_holding_period": trade.average_holding_period.to_dict(),
        "turnover": trade.turnover.to_dict(),
    }


def summary_payload(record: BacktestRunRecord) -> dict[str, Any]:
    """Build a display-focused read-only summary of one persisted run."""
    run = record.run
    result = run.backtest_result
    analysis = run.performance_analysis
    return {
        "backtest_run_id": run.backtest_run_id,
        "strategy_id": run.strategy_id,
        "strategy_version_id": run.strategy_version_id,
        "strategy_version_content_hash": run.strategy_version_content_hash,
        "created_at": run.created_at.isoformat(),
        "start_date": result.start_date.isoformat(),
        "end_date": result.end_date.isoformat(),
        "initial_capital": result.initial_capital,
        "final_equity": result.final_equity,
        "price_field_used": analysis.price_field_used.value,
        "engine_version": result.engine_version,
        "analysis_version": record.analysis_version,
        "configuration_snapshot": dict(result.configuration_snapshot),
        "data_snapshot_reference": dict(result.data_snapshot_reference),
        "provenance": dict(run.provenance),
        "metrics": metric_payload(record),
    }


def sorted_records(
    records: tuple[BacktestRunRecord, ...], *, sort_by: str, order: str
) -> tuple[BacktestRunRecord, ...]:
    """Sort one existing field while keeping non-evaluable metrics after valid values."""
    descending = order == "desc"
    if sort_by == "created_at":
        return tuple(sorted(records, key=lambda record: record.run.created_at, reverse=descending))

    accessor = SORTABLE_METRICS[sort_by]
    available: list[BacktestRunRecord] = []
    unavailable: list[BacktestRunRecord] = []
    for record in records:
        metric = accessor(record)
        (available if metric.is_evaluable else unavailable).append(record)
    available.sort(key=lambda record: float(accessor(record).value), reverse=descending)
    return tuple(available + unavailable)


@dataclass(frozen=True)
class ComparisonResult:
    """Transient comparison response; it never creates a persisted comparison record."""

    records: tuple[BacktestRunRecord, ...]
    comparable: bool
    incompatibility_reasons: tuple[dict[str, Any], ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "comparable": self.comparable,
            "incompatibility_reasons": list(self.incompatibility_reasons),
            "runs": [summary_payload(record) for record in self.records],
            "series": [
                {
                    "backtest_run_id": record.run.backtest_run_id,
                    "equity_curve": [
                        {"date": point.date.isoformat(), "total_equity": point.total_equity}
                        for point in record.run.backtest_result.equity_curve
                    ],
                    "drawdown_curve": [
                        point.to_dict() for point in record.run.performance_analysis.drawdown_curve
                    ],
                }
                for record in self.records
            ],
            "provenance_notice": (
                "Data provenance recorded; complete immutable market-data snapshot "
                "versioning is not yet implemented."
            ),
        }


def compare_records(records: tuple[BacktestRunRecord, ...]) -> ComparisonResult:
    """Compare stored research artifacts without rerunning a backtest or analytics."""
    if not 2 <= len(records) <= 6:
        raise ValueError("comparisons require between 2 and 6 backtest runs")

    reference = records[0]
    fields = (
        ("date_range", _date_range),
        ("initial_capital", lambda record: record.run.backtest_result.initial_capital),
        ("price_field", lambda record: record.run.performance_analysis.price_field_used.value),
        ("commission", lambda record: _config(record).get("commission", {}).get("rate")),
        (
            "per_order_commission",
            lambda record: _config(record).get("commission", {}).get("per_order"),
        ),
        ("slippage", lambda record: _config(record).get("slippage")),
        ("execution_rule", lambda record: _config(record).get("execution_rule")),
        ("fractional_shares", lambda record: _config(record).get("fractional_shares")),
        ("engine_version", lambda record: record.run.backtest_result.engine_version),
        ("analysis_version", lambda record: record.analysis_version),
    )
    reasons: list[dict[str, Any]] = []
    for field, value_for in fields:
        baseline = value_for(reference)
        values = [
            {"backtest_run_id": record.run.backtest_run_id, "value": value_for(record)}
            for record in records
        ]
        if any(item["value"] != baseline for item in values[1:]):
            reasons.append(
                {
                    "code": f"{field}_mismatch",
                    "field": field,
                    "reference_backtest_run_id": reference.run.backtest_run_id,
                    "reference_value": baseline,
                    "values": values,
                }
            )
    return ComparisonResult(
        records=records,
        comparable=not reasons,
        incompatibility_reasons=tuple(reasons),
    )


def _config(record: BacktestRunRecord) -> dict[str, Any]:
    return dict(record.run.backtest_result.configuration_snapshot)


def _date_range(record: BacktestRunRecord) -> dict[str, str]:
    result = record.run.backtest_result
    return {"start_date": result.start_date.isoformat(), "end_date": result.end_date.isoformat()}


__all__ = [
    "ComparisonResult",
    "SORTABLE_METRICS",
    "compare_records",
    "sorted_records",
    "summary_payload",
]
