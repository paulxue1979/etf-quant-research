"""Read-only comparison views over persisted backtest research artifacts."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from analytics.models import MetricValue
from backend.app.backtest_repository import BacktestRunRecord
from backtest.models import canonical_decimal

MetricAccessor = Callable[[BacktestRunRecord], MetricValue]
COMPARISON_SCHEMA_VERSION = "2.0"


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
class ComparisonInclude:
    """Optional comparison capabilities requested by the caller."""

    twr: bool = True
    drawdown: bool = True
    portfolio_value: bool = False
    metrics: bool = True

    def to_dict(self) -> dict[str, bool]:
        return {
            "twr": self.twr,
            "drawdown": self.drawdown,
            "portfolio_value": self.portfolio_value,
            "metrics": self.metrics,
        }


class CompatibilityStatus(StrEnum):
    COMPARABLE = "COMPARABLE"
    WARNING = "WARNING"
    INCOMPATIBLE = "INCOMPATIBLE"
    UNKNOWN = "UNKNOWN"


@dataclass(frozen=True)
class ComparisonResult:
    """Versioned comparison projection over immutable persisted runs."""

    records: tuple[BacktestRunRecord, ...]
    include: ComparisonInclude
    strategy_metadata: Mapping[str, Mapping[str, Any]]

    def to_dict(self) -> dict[str, Any]:
        legacy_reasons = _legacy_incompatibilities(self.records)
        return {
            "comparison_schema_version": COMPARISON_SCHEMA_VERSION,
            "ordering": "request_order",
            "include": self.include.to_dict(),
            "compatibility": _compatibility(self.records),
            "comparable": not legacy_reasons,
            "incompatibility_reasons": legacy_reasons,
            "runs": [
                _comparison_run_payload(
                    record,
                    self.strategy_metadata.get(record.run.strategy_version_id),
                    include_metrics=self.include.metrics,
                )
                for record in self.records
            ],
            "series": [_series_payload(record, self.include) for record in self.records],
            "provenance_notice": (
                "Data provenance recorded; complete immutable market-data snapshot "
                "versioning is not yet implemented."
            ),
        }


def compare_records(
    records: tuple[BacktestRunRecord, ...],
    *,
    include: ComparisonInclude | None = None,
    strategy_metadata: Mapping[str, Mapping[str, Any]] | None = None,
) -> ComparisonResult:
    """Compare stored research artifacts without rerunning a backtest or analytics."""
    if not 2 <= len(records) <= 10:
        raise ValueError("comparisons require between 2 and 10 backtest runs")
    return ComparisonResult(
        records=records,
        include=include or ComparisonInclude(),
        strategy_metadata=strategy_metadata or {},
    )


def _legacy_incompatibilities(
    records: tuple[BacktestRunRecord, ...],
) -> list[dict[str, Any]]:
    """Preserve the PHASE 6 compatibility fields for existing consumers."""

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
    return reasons


def _comparison_run_payload(
    record: BacktestRunRecord,
    metadata: Mapping[str, Any] | None,
    *,
    include_metrics: bool,
) -> dict[str, Any]:
    run = record.run
    result = run.backtest_result
    version_number = metadata.get("version_number") if metadata else None
    strategy_name = str(metadata.get("strategy_name")) if metadata else run.strategy_id
    version_label = f"v{version_number}" if version_number is not None else run.strategy_version_id
    payload = summary_payload(record)
    payload.update(
        {
            "strategy_name": strategy_name,
            "strategy_version": version_label,
            "strategy_metadata_status": ("available" if metadata else "fallback_to_strategy_id"),
            "short_display_label": (
                f"{strategy_name} · {version_label} · {run.backtest_run_id[-8:]}"
            ),
            "asset_universe": sorted(str(symbol) for symbol in result.data_snapshot_reference),
            "metrics": _comparison_metrics(record) if include_metrics else {},
            "metrics_status": "included" if include_metrics else "excluded",
            "comparison_provenance": _comparison_provenance(record),
        }
    )
    for key in ("configuration_snapshot", "data_snapshot_reference", "provenance"):
        payload[key] = _sanitize(payload[key])
    return payload


def _comparison_metrics(record: BacktestRunRecord) -> dict[str, dict[str, Any]]:
    analysis = record.run.performance_analysis
    result = record.run.backtest_result
    legacy = metric_payload(record)
    return {
        **legacy,
        "total_twr_return": analysis.total_return.to_dict(),
        "xirr": analysis.xirr.to_dict(),
        "portfolio_turnover": analysis.turnover.to_dict(),
        "exposure": _mapping_capability(analysis.exposure_summary, "exposure unavailable"),
        "trade_count": _available_value(analysis.trade_metrics.number_of_closed_trades),
        "holding_period_count": (
            _available_value(len(result.holding_segments))
            if result.holding_segments is not None
            else _unavailable_value("holding period records were not persisted")
        ),
    }


def _available_value(value: Any) -> dict[str, Any]:
    return {"value": _sanitize(value), "status": "available", "reason": None}


def _unavailable_value(reason: str) -> dict[str, Any]:
    return {"value": None, "status": "not_available", "reason": reason}


def _mapping_capability(value: Mapping[str, Any], reason: str) -> dict[str, Any]:
    return _available_value(value) if value else _unavailable_value(reason)


def _series_payload(record: BacktestRunRecord, include: ComparisonInclude) -> dict[str, Any]:
    run = record.run
    drawdown = _drawdown_series(record, included=include.drawdown)
    portfolio = _portfolio_value_series(record, included=include.portfolio_value)
    return {
        "backtest_run_id": run.backtest_run_id,
        "strategy_version_id": run.strategy_version_id,
        "start_date": run.backtest_result.start_date.isoformat(),
        "end_date": run.backtest_result.end_date.isoformat(),
        "twr": _twr_series(record, included=include.twr),
        "drawdown": drawdown,
        "portfolio_value": portfolio,
        # Deprecated PHASE 6 fields remain available during the schema transition.
        "equity_curve": (
            [
                {"date": point.date.isoformat(), "total_equity": point.total_equity}
                for point in run.backtest_result.equity_curve
            ]
            if include.portfolio_value
            else []
        ),
        "drawdown_curve": (
            [point.to_dict() for point in run.performance_analysis.drawdown_curve]
            if include.drawdown
            else []
        ),
    }


def _twr_series(record: BacktestRunRecord, *, included: bool) -> dict[str, Any]:
    if not included:
        return {"status": "excluded", "points": []}
    curve = record.run.performance_analysis.twr_wealth_curve
    if not curve:
        return {
            "status": "not_available",
            "reason": "canonical TWR wealth curve was not persisted",
            "points": [],
        }
    first = curve[0]
    return {
        "status": "available",
        "unit": "base_100_wealth_index",
        "source": "PerformanceAnalysisResult.twr_wealth_curve",
        "normalization": {
            "base_value": 100.0,
            "source": "twr_wealth_curve",
            "method": "rebase_first_valid_point",
            "first_valid_date": first.date.isoformat(),
            "first_valid_value": first.value,
        },
        "points": [
            {"date": point.date.isoformat(), "value": point.value / first.value * 100.0}
            for point in curve
        ],
    }


def _drawdown_series(record: BacktestRunRecord, *, included: bool) -> dict[str, Any]:
    if not included:
        return {"status": "excluded", "points": []}
    curve = record.run.performance_analysis.drawdown_curve
    if not curve:
        return {
            "status": "not_available",
            "reason": "canonical drawdown curve was not persisted",
            "points": [],
        }
    return {
        "status": "available",
        "unit": "decimal_return",
        "source": "PerformanceAnalysisResult.drawdown_curve",
        "points": [point.to_dict() for point in curve],
    }


def _portfolio_value_series(record: BacktestRunRecord, *, included: bool) -> dict[str, Any]:
    if not included:
        return {"status": "excluded", "points": []}
    return {
        "status": "available",
        "unit": "USD",
        "source": "BacktestResult.equity_curve.total_equity",
        "fair_comparison_requires": "compatibility.portfolio_value.status == COMPARABLE",
        "points": [
            {"date": point.date.isoformat(), "value": point.total_equity}
            for point in record.run.backtest_result.equity_curve
        ],
    }


def _comparison_provenance(record: BacktestRunRecord) -> dict[str, Any]:
    run = record.run
    result = run.backtest_result
    config = _config(record)
    benchmark = run.benchmark_evaluation
    return {
        "engine_version": result.engine_version,
        "analytics_version": record.analysis_version,
        "data_source": run.provenance.get("data_source_reference"),
        "data_snapshot_reference": _sanitize(result.data_snapshot_reference),
        "dataset_version": _dataset_version(record),
        "price_field": run.performance_analysis.price_field_used.value,
        "execution_semantics": config.get("execution_rule"),
        "contribution_config": _sanitize(config.get("contribution_schedule")),
        "effective_contributions": _effective_contributions(record),
        "cost_model": {
            "commission": _sanitize(config.get("commission")),
            "slippage": config.get("slippage"),
        },
        "benchmark": {
            "status": (
                str(benchmark.get("status", "unknown"))
                if isinstance(benchmark, Mapping)
                else "not_available"
            ),
            "symbol": (
                benchmark.get("benchmark_symbol") if isinstance(benchmark, Mapping) else None
            ),
        },
        "contribution_provenance_available": run.contribution_provenance_available,
    }


def _compatibility(records: tuple[BacktestRunRecord, ...]) -> dict[str, Any]:
    dimensions = _compatibility_dimensions(records)
    return {
        "twr": _twr_compatibility(dimensions),
        "portfolio_value": _portfolio_value_compatibility(dimensions),
        "investor_experience": _investor_compatibility(dimensions),
    }


def _compatibility_dimensions(
    records: tuple[BacktestRunRecord, ...],
) -> dict[str, dict[str, Any]]:
    accessors: tuple[tuple[str, Callable[[BacktestRunRecord], Any]], ...] = (
        ("date_range", _date_range),
        ("price_field", lambda item: item.run.performance_analysis.price_field_used.value),
        ("execution_rule", lambda item: _config(item).get("execution_rule")),
        ("commission", lambda item: _sanitize(_config(item).get("commission"))),
        ("slippage", lambda item: _config(item).get("slippage")),
        ("fractional_shares", lambda item: _config(item).get("fractional_shares")),
        ("rebalance_policy", lambda item: _sanitize(_config(item).get("rebalance_policy"))),
        ("initial_capital", lambda item: item.run.backtest_result.initial_capital),
        (
            "contribution_schedule",
            _contribution_schedule,
        ),
        ("requested_contribution_dates", _requested_contribution_dates),
        ("effective_contributions", _effective_contributions),
        ("cash_flow_timeline", _cash_flow_timeline),
        (
            "asset_universe",
            lambda item: sorted(
                str(key) for key in item.run.backtest_result.data_snapshot_reference
            ),
        ),
        ("benchmark", _benchmark_capability),
        ("engine_version", lambda item: item.run.backtest_result.engine_version),
        ("analytics_version", lambda item: item.analysis_version),
        (
            "data_provenance",
            _data_provenance,
        ),
        ("dataset_version", _dataset_version),
        ("xirr_availability", lambda item: item.run.performance_analysis.xirr.is_evaluable),
        (
            "contribution_provenance",
            lambda item: item.run.contribution_provenance_available,
        ),
    )
    return {name: _dimension(records, accessor) for name, accessor in accessors}


def _dimension(
    records: tuple[BacktestRunRecord, ...], accessor: Callable[[BacktestRunRecord], Any]
) -> dict[str, Any]:
    values = [
        {"backtest_run_id": item.run.backtest_run_id, "value": accessor(item)} for item in records
    ]
    raw = [item["value"] for item in values]
    if any(value is None for value in raw):
        status = "UNKNOWN"
    elif all(value == raw[0] for value in raw[1:]):
        status = "MATCH"
    else:
        status = "MISMATCH"
    return {"status": status, "values": values}


def _twr_compatibility(dimensions: Mapping[str, dict[str, Any]]) -> dict[str, Any]:
    reasons: list[tuple[str, str, CompatibilityStatus]] = []
    _add_mismatch_reasons(
        reasons,
        dimensions,
        {
            "price_field": ("DIFFERENT_PRICE_FIELDS", CompatibilityStatus.INCOMPATIBLE),
            "execution_rule": (
                "DIFFERENT_EXECUTION_SEMANTICS",
                CompatibilityStatus.INCOMPATIBLE,
            ),
            "commission": ("DIFFERENT_COMMISSION", CompatibilityStatus.INCOMPATIBLE),
            "slippage": ("DIFFERENT_SLIPPAGE", CompatibilityStatus.INCOMPATIBLE),
            "fractional_shares": (
                "DIFFERENT_FRACTIONAL_SHARE_POLICY",
                CompatibilityStatus.INCOMPATIBLE,
            ),
            "date_range": ("DIFFERENT_DATE_RANGES", CompatibilityStatus.WARNING),
            "rebalance_policy": ("DIFFERENT_REBALANCE_CONFIG", CompatibilityStatus.WARNING),
            "asset_universe": ("DIFFERENT_ASSET_UNIVERSE", CompatibilityStatus.WARNING),
            "engine_version": ("DIFFERENT_ENGINE_VERSIONS", CompatibilityStatus.WARNING),
            "analytics_version": (
                "DIFFERENT_ANALYTICS_VERSIONS",
                CompatibilityStatus.WARNING,
            ),
            "data_provenance": ("DIFFERENT_DATA_PROVENANCE", CompatibilityStatus.WARNING),
        },
    )
    cash_dimensions = (
        "contribution_schedule",
        "requested_contribution_dates",
        "effective_contributions",
        "cash_flow_timeline",
    )
    if any(dimensions[name]["status"] == "MISMATCH" for name in cash_dimensions):
        reasons.extend(
            (
                (
                    "DIFFERENT_EXTERNAL_CASH_FLOWS",
                    "External cash-flow context differs across runs.",
                    CompatibilityStatus.WARNING,
                ),
                (
                    "INTEGER_SHARE_PATH_DEPENDENCY",
                    "Integer-share execution can make cash flows change the strategy path.",
                    CompatibilityStatus.WARNING,
                ),
            )
        )
    if dimensions["initial_capital"]["status"] == "MISMATCH":
        reasons.extend(
            (
                (
                    "DIFFERENT_INITIAL_CAPITAL",
                    "Initial capital differs across runs.",
                    CompatibilityStatus.WARNING,
                ),
                (
                    "INTEGER_SHARE_PATH_DEPENDENCY",
                    "Integer-share sizing may make initial capital alter execution paths.",
                    CompatibilityStatus.WARNING,
                ),
            )
        )
    _add_provenance_reasons(reasons, dimensions)
    return _compatibility_payload(reasons, dimensions)


def _portfolio_value_compatibility(
    dimensions: Mapping[str, dict[str, Any]],
) -> dict[str, Any]:
    reasons: list[tuple[str, str, CompatibilityStatus]] = []
    _add_mismatch_reasons(
        reasons,
        dimensions,
        {
            "date_range": ("DIFFERENT_DATE_RANGES", CompatibilityStatus.INCOMPATIBLE),
            "initial_capital": (
                "DIFFERENT_INITIAL_CAPITAL",
                CompatibilityStatus.INCOMPATIBLE,
            ),
            "price_field": ("DIFFERENT_PRICE_FIELDS", CompatibilityStatus.INCOMPATIBLE),
            "execution_rule": (
                "DIFFERENT_EXECUTION_SEMANTICS",
                CompatibilityStatus.INCOMPATIBLE,
            ),
            "commission": ("DIFFERENT_COMMISSION", CompatibilityStatus.INCOMPATIBLE),
            "slippage": ("DIFFERENT_SLIPPAGE", CompatibilityStatus.INCOMPATIBLE),
            "fractional_shares": (
                "DIFFERENT_FRACTIONAL_SHARE_POLICY",
                CompatibilityStatus.INCOMPATIBLE,
            ),
            "contribution_schedule": (
                "DIFFERENT_CONTRIBUTION_SCHEDULES",
                CompatibilityStatus.INCOMPATIBLE,
            ),
            "effective_contributions": (
                "DIFFERENT_EFFECTIVE_CONTRIBUTION_DATES",
                CompatibilityStatus.INCOMPATIBLE,
            ),
            "cash_flow_timeline": (
                "DIFFERENT_EXTERNAL_CASH_FLOWS",
                CompatibilityStatus.INCOMPATIBLE,
            ),
            "engine_version": ("DIFFERENT_ENGINE_VERSIONS", CompatibilityStatus.WARNING),
            "data_provenance": ("DIFFERENT_DATA_PROVENANCE", CompatibilityStatus.WARNING),
        },
    )
    _add_provenance_reasons(reasons, dimensions)
    return _compatibility_payload(reasons, dimensions)


def _investor_compatibility(dimensions: Mapping[str, dict[str, Any]]) -> dict[str, Any]:
    reasons: list[tuple[str, str, CompatibilityStatus]] = []
    _add_mismatch_reasons(
        reasons,
        dimensions,
        {
            "date_range": ("DIFFERENT_DATE_RANGES", CompatibilityStatus.WARNING),
            "initial_capital": ("DIFFERENT_INITIAL_CAPITAL", CompatibilityStatus.WARNING),
            "contribution_schedule": (
                "DIFFERENT_CONTRIBUTION_SCHEDULES",
                CompatibilityStatus.WARNING,
            ),
            "effective_contributions": (
                "DIFFERENT_EFFECTIVE_CONTRIBUTION_DATES",
                CompatibilityStatus.WARNING,
            ),
            "cash_flow_timeline": (
                "DIFFERENT_EXTERNAL_CASH_FLOWS",
                CompatibilityStatus.WARNING,
            ),
            "price_field": ("DIFFERENT_PRICE_FIELDS", CompatibilityStatus.WARNING),
            "execution_rule": (
                "DIFFERENT_EXECUTION_SEMANTICS",
                CompatibilityStatus.WARNING,
            ),
            "commission": ("DIFFERENT_COMMISSION", CompatibilityStatus.WARNING),
            "slippage": ("DIFFERENT_SLIPPAGE", CompatibilityStatus.WARNING),
        },
    )
    if dimensions["xirr_availability"]["status"] != "MATCH" or not all(
        item["value"] for item in dimensions["xirr_availability"]["values"]
    ):
        reasons.append(
            (
                "XIRR_UNAVAILABLE",
                "XIRR is unavailable for one or more runs.",
                CompatibilityStatus.UNKNOWN,
            )
        )
    _add_provenance_reasons(reasons, dimensions)
    return _compatibility_payload(reasons, dimensions)


def _add_mismatch_reasons(
    reasons: list[tuple[str, str, CompatibilityStatus]],
    dimensions: Mapping[str, dict[str, Any]],
    rules: Mapping[str, tuple[str, CompatibilityStatus]],
) -> None:
    for dimension, (code, severity) in rules.items():
        if dimensions[dimension]["status"] == "MISMATCH":
            reasons.append(
                (
                    code,
                    f"Comparison dimension {dimension.replace('_', ' ')} differs across runs.",
                    severity,
                )
            )


def _add_provenance_reasons(
    reasons: list[tuple[str, str, CompatibilityStatus]],
    dimensions: Mapping[str, dict[str, Any]],
) -> None:
    if dimensions["dataset_version"]["status"] == "UNKNOWN":
        reasons.append(
            (
                "DATASET_VERSION_UNAVAILABLE",
                "An immutable dataset version or content hash is not available for every run.",
                CompatibilityStatus.WARNING,
            )
        )
    unknown_dimensions = [
        name
        for name, value in dimensions.items()
        if value["status"] == "UNKNOWN" and name != "dataset_version"
    ]
    if unknown_dimensions:
        reasons.append(
            (
                "MISSING_PROVENANCE",
                "Required comparison provenance is unavailable: " + ", ".join(unknown_dimensions),
                CompatibilityStatus.UNKNOWN,
            )
        )
    if not all(item["value"] for item in dimensions["contribution_provenance"]["values"]):
        reasons.append(
            (
                "CONTRIBUTION_PROVENANCE_UNAVAILABLE",
                "Contribution provenance is unavailable for one or more legacy runs.",
                CompatibilityStatus.UNKNOWN,
            )
        )


def _compatibility_payload(
    reasons: list[tuple[str, str, CompatibilityStatus]],
    dimensions: Mapping[str, dict[str, Any]],
) -> dict[str, Any]:
    deduplicated: list[tuple[str, str, CompatibilityStatus]] = []
    seen: set[str] = set()
    for item in reasons:
        if item[0] not in seen:
            seen.add(item[0])
            deduplicated.append(item)
    severities = {item[2] for item in deduplicated}
    if CompatibilityStatus.INCOMPATIBLE in severities:
        status = CompatibilityStatus.INCOMPATIBLE
    elif CompatibilityStatus.WARNING in severities:
        status = CompatibilityStatus.WARNING
    elif CompatibilityStatus.UNKNOWN in severities:
        status = CompatibilityStatus.UNKNOWN
    else:
        status = CompatibilityStatus.COMPARABLE
    return {
        "status": status.value,
        "reason_codes": [item[0] for item in deduplicated],
        "human_readable_reasons": [item[1] for item in deduplicated],
        "dimensions": dict(dimensions),
    }


def _requested_contribution_dates(record: BacktestRunRecord) -> list[str]:
    return [
        item.requested_date.isoformat() for item in record.run.backtest_result.contribution_events
    ]


def _contribution_schedule(record: BacktestRunRecord) -> dict[str, Any] | None:
    config = _config(record)
    if "contribution_schedule" not in config:
        return None
    schedule = config["contribution_schedule"]
    return (
        {"enabled": False}
        if schedule is None
        else {"enabled": True, "schedule": _sanitize(schedule)}
    )


def _data_provenance(record: BacktestRunRecord) -> dict[str, Any] | None:
    references = record.run.backtest_result.data_snapshot_reference
    return _sanitize(references) if references else None


def _effective_contributions(record: BacktestRunRecord) -> list[dict[str, str]]:
    return [
        {
            "requested_date": item.requested_date.isoformat(),
            "effective_date": item.effective_date.isoformat(),
            "amount": canonical_decimal(item.amount),
            "frequency": item.frequency.value,
        }
        for item in record.run.backtest_result.contribution_events
    ]


def _cash_flow_timeline(record: BacktestRunRecord) -> list[dict[str, str]]:
    return [
        {
            "date": item.date.isoformat(),
            "amount": canonical_decimal(item.amount),
            "source": item.source,
        }
        for item in record.run.backtest_result.external_cash_flows
    ]


def _benchmark_capability(record: BacktestRunRecord) -> dict[str, Any]:
    benchmark = record.run.benchmark_evaluation
    if not isinstance(benchmark, Mapping):
        return {"status": "not_available", "symbol": None}
    return {
        "status": str(benchmark.get("status", "unknown")),
        "symbol": benchmark.get("benchmark_symbol"),
    }


def _dataset_version(record: BacktestRunRecord) -> dict[str, str] | None:
    references = record.run.backtest_result.data_snapshot_reference
    versions: dict[str, str] = {}
    for symbol, payload in references.items():
        if not isinstance(payload, Mapping):
            return None
        value = next(
            (
                payload.get(key)
                for key in ("dataset_version", "snapshot_id", "content_hash")
                if payload.get(key)
            ),
            None,
        )
        if value is None:
            return None
        versions[str(symbol)] = str(value)
    return dict(sorted(versions.items())) if versions else None


def _sanitize(value: Any) -> Any:
    if isinstance(value, Mapping):
        sensitive = ("api_key", "secret", "token", "password", "credential")
        return {
            str(key): (
                "[redacted]"
                if any(term in str(key).lower() for term in sensitive)
                else _sanitize(item)
            )
            for key, item in sorted(value.items(), key=lambda pair: str(pair[0]))
        }
    if isinstance(value, tuple | list):
        return [_sanitize(item) for item in value]
    return value


def _config(record: BacktestRunRecord) -> dict[str, Any]:
    return dict(record.run.backtest_result.configuration_snapshot)


def _date_range(record: BacktestRunRecord) -> dict[str, str]:
    result = record.run.backtest_result
    return {"start_date": result.start_date.isoformat(), "end_date": result.end_date.isoformat()}


__all__ = [
    "COMPARISON_SCHEMA_VERSION",
    "ComparisonInclude",
    "ComparisonResult",
    "CompatibilityStatus",
    "SORTABLE_METRICS",
    "compare_records",
    "sorted_records",
    "summary_payload",
]
