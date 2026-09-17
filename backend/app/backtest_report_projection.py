"""Versioned, read-only projections of immutable backtest research artifacts."""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterable, Mapping
from datetime import date
from decimal import Decimal
from typing import Any

from backend.app.backtest_models import BacktestRun
from backtest.models import canonical_decimal

REPORT_SCHEMA_VERSION = "1.0"
SERIES_NAMES = frozenset({"equity", "capital", "twr", "drawdown", "benchmark"})


class BacktestReportProjectionError(ValueError):
    """Raised when a report projection request is invalid."""


class BacktestReportProjectionService:
    """Project persisted truth into a deterministic report read model."""

    def project(self, run: BacktestRun) -> dict[str, Any]:
        if not isinstance(run, BacktestRun):
            raise TypeError("run must be a BacktestRun")
        result = run.backtest_result
        analysis = run.performance_analysis
        strategy_provenance = self._strategy_provenance(run)
        return {
            "report_schema_version": REPORT_SCHEMA_VERSION,
            "identity": {
                "backtest_run_id": run.backtest_run_id,
                "strategy_id": run.strategy_id,
                "strategy_version_id": run.strategy_version_id,
                "strategy_version_content_hash": run.strategy_version_content_hash,
                "created_at": run.created_at.isoformat(),
                "engine_version": result.engine_version,
            },
            "availability": {
                "account": "available",
                "capital": "available",
                "profit": "available",
                "performance": "available",
                "contributions": "available",
                "strategy_provenance": strategy_provenance["status"],
                "holdings": "not_available",
                "benchmark": "not_available",
                "xirr": "not_available",
            },
            "summary_period": {
                "start_date": result.start_date.isoformat(),
                "end_date": result.end_date.isoformat(),
            },
            "summary": {
                "account": {
                    "ending_value": result.final_equity,
                    "unit": "USD",
                },
                "strategy_performance": {
                    "twr_total_return": analysis.total_return.to_dict(),
                    "cagr": analysis.cagr.to_dict(),
                    "annualized_volatility": analysis.annualized_volatility.to_dict(),
                    "sharpe_ratio": analysis.sharpe_ratio.to_dict(),
                    "sortino_ratio": analysis.sortino_ratio.to_dict(),
                    "max_drawdown": analysis.max_drawdown.to_dict(),
                    "calmar_ratio": analysis.calmar_ratio.to_dict(),
                    "closed_trade_count": analysis.trade_metrics.number_of_closed_trades,
                    "win_rate": analysis.trade_metrics.win_rate.to_dict(),
                },
            },
            "capital": {
                "initial_capital": result.initial_capital,
                "cumulative_contributions": result.cumulative_contributions,
                "total_capital_invested": result.total_capital_invested,
            },
            "profit": {"investment_profit": result.investment_profit},
            "performance": {
                "twr_total_return": analysis.total_return.to_dict(),
                "cagr": analysis.cagr.to_dict(),
                "annualized_volatility": analysis.annualized_volatility.to_dict(),
                "sharpe_ratio": analysis.sharpe_ratio.to_dict(),
                "sortino_ratio": analysis.sortino_ratio.to_dict(),
                "max_drawdown": analysis.max_drawdown.to_dict(),
                "max_drawdown_duration": analysis.max_drawdown_duration.to_dict(),
                "recovery_duration": analysis.recovery_duration.to_dict(),
                "max_drawdown_recovered": analysis.max_drawdown_recovered,
                "calmar_ratio": analysis.calmar_ratio.to_dict(),
                "trade_metrics": analysis.trade_metrics.to_dict(),
            },
            "investor_experience": {
                "xirr": {
                    "value": None,
                    "status": "not_available",
                    "reason": "XIRR is deferred to PHASE 9G-B",
                    "unit": "percent",
                }
            },
            "series_metadata": self._series_metadata(run),
            "contributions": [
                {
                    "requested_date": item.requested_date.isoformat(),
                    "effective_date": item.effective_date.isoformat(),
                    "amount": canonical_decimal(item.amount),
                    "frequency": item.frequency.value,
                    "currency": item.currency,
                    "source": "ContributionEvent",
                }
                for item in result.contribution_events
            ],
            "strategy_provenance": strategy_provenance,
            "allocations": self._allocations(run, strategy_provenance),
            "holdings": {
                "status": "not_available",
                "reason": "canonical FIFO open-lot provenance is not persisted",
            },
            "trades": {
                "closed_trade_count": len(result.trades),
                "source": "BacktestResult.trades",
            },
            "configuration": {
                "price_field_used": analysis.price_field_used.value,
                "requested_start_date": result.requested_start_date.isoformat(),
                "requested_end_date": result.requested_end_date.isoformat(),
                "effective_start_date": result.effective_start_date.isoformat(),
                "effective_end_date": result.effective_end_date.isoformat(),
                "backtest_configuration": _sanitize(result.configuration_snapshot),
            },
            "provenance": _sanitize(run.provenance),
        }

    def series(
        self,
        run: BacktestRun,
        *,
        include: Iterable[str],
        start: date | None = None,
        end: date | None = None,
    ) -> dict[str, Any]:
        if start is not None and end is not None and start > end:
            raise BacktestReportProjectionError("report series start date is after end date")
        requested = _normalized_series_names(include)
        return {
            "report_schema_version": REPORT_SCHEMA_VERSION,
            "identity": {
                "backtest_run_id": run.backtest_run_id,
                "strategy_version_id": run.strategy_version_id,
            },
            "summary_period": {
                "start_date": run.backtest_result.start_date.isoformat(),
                "end_date": run.backtest_result.end_date.isoformat(),
            },
            "window": {
                "from": start.isoformat() if start is not None else None,
                "to": end.isoformat() if end is not None else None,
            },
            "series": {name: self._series(run, name, start=start, end=end) for name in requested},
        }

    @staticmethod
    def _strategy_provenance(run: BacktestRun) -> dict[str, Any]:
        persisted = run.strategy_provenance
        if persisted is None:
            return {
                "status": "not_available",
                "reason": "strategy execution provenance was not persisted for this legacy run",
            }
        records = persisted.get("records")
        if not isinstance(records, tuple):
            return {
                "status": "not_available",
                "reason": "persisted strategy execution provenance is invalid",
            }
        sources = Counter(
            str(item.get("allocation_source")) for item in records if isinstance(item, Mapping)
        )
        submitted = sum(
            1
            for item in records
            if isinstance(item, Mapping) and item.get("execution_status") == "submitted"
        )
        omitted = sum(
            1
            for item in records
            if isinstance(item, Mapping) and item.get("execution_status") == "omitted"
        )
        return {
            "status": "available",
            "source": persisted.get("source"),
            "record_count": len(records),
            "allocation_sources": dict(sorted(sources.items())),
            "submitted_count": submitted,
            "omitted_count": omitted,
        }

    @staticmethod
    def _allocations(run: BacktestRun, strategy_provenance: Mapping[str, Any]) -> dict[str, Any]:
        target: dict[str, Any]
        if strategy_provenance["status"] == "available":
            target = {
                "status": "available",
                "source": "BacktestRun.strategy_provenance.records[].target_allocation",
            }
        else:
            target = {
                "status": "not_available",
                "reason": "strategy target allocation provenance was not persisted",
            }
        return {
            "target": target,
            "actual": {
                "status": "available",
                "source": "BacktestResult.allocation_history",
                "record_count": len(run.backtest_result.allocation_history),
            },
            "cash_semantics": "implicit_unallocated_portfolio_weight",
        }

    @staticmethod
    def _series_metadata(run: BacktestRun) -> dict[str, Any]:
        has_drawdown_curve = bool(run.performance_analysis.drawdown_curve)
        return {
            "equity": {
                "status": "available",
                "series_type": "account_value",
                "unit": "USD",
                "source": "BacktestResult.equity_curve.total_equity",
            },
            "capital": {
                "status": "available",
                "series_type": "capital_invested",
                "unit": "USD",
                "source": "BacktestResult.initial_capital + effective ContributionEvent values",
            },
            "twr": {
                "status": "not_available",
                "reason": "normalized TWR wealth series is deferred to PHASE 9G-B",
                "unit": "normalized",
            },
            "drawdown": {
                "status": "available" if has_drawdown_curve else "not_available",
                "source": "PerformanceAnalysisResult.drawdown_curve",
                "unit": "percent",
                "reason": (
                    None if has_drawdown_curve else "drawdown curve was not persisted for this run"
                ),
            },
            "benchmark": {
                "status": "not_available",
                "reason": "benchmark evaluation is deferred to PHASE 9G-B",
            },
        }

    def _series(
        self,
        run: BacktestRun,
        name: str,
        *,
        start: date | None,
        end: date | None,
    ) -> dict[str, Any]:
        if name == "equity":
            return {
                "status": "available",
                "series_type": "account_value",
                "unit": "USD",
                "source": "BacktestResult.equity_curve.total_equity",
                "points": _window_points(
                    (
                        {"date": item.date.isoformat(), "value": item.total_equity}
                        for item in run.backtest_result.equity_curve
                    ),
                    start,
                    end,
                ),
            }
        if name == "capital":
            return {
                "status": "available",
                "series_type": "capital_invested",
                "unit": "USD",
                "source": "BacktestResult.initial_capital + effective ContributionEvent values",
                "points": _window_points(_capital_points(run), start, end),
            }
        if name == "drawdown":
            curve = run.performance_analysis.drawdown_curve
            if not curve:
                return {
                    "status": "not_available",
                    "reason": "drawdown curve was not persisted for this run",
                    "source": "PerformanceAnalysisResult.drawdown_curve",
                }
            return {
                "status": "available",
                "series_type": "drawdown",
                "unit": "percent",
                "source": "PerformanceAnalysisResult.drawdown_curve",
                "points": _window_points(
                    ({"date": item.date.isoformat(), "value": item.value} for item in curve),
                    start,
                    end,
                ),
            }
        reasons = {
            "twr": "normalized TWR wealth series is deferred to PHASE 9G-B",
            "benchmark": "benchmark evaluation is deferred to PHASE 9G-B",
        }
        return {"status": "not_available", "reason": reasons[name]}


def _normalized_series_names(include: Iterable[str]) -> tuple[str, ...]:
    names = tuple(str(item).strip().lower() for item in include)
    if not names or any(not item for item in names) or len(names) != len(set(names)):
        raise BacktestReportProjectionError("report series include must be unique non-empty names")
    unknown = sorted(set(names) - SERIES_NAMES)
    if unknown:
        raise BacktestReportProjectionError("unsupported report series: " + ", ".join(unknown))
    return names


def _window_points(
    points: Iterable[dict[str, Any]], start: date | None, end: date | None
) -> list[dict[str, Any]]:
    return [
        point
        for point in points
        if (start is None or point["date"] >= start.isoformat())
        and (end is None or point["date"] <= end.isoformat())
    ]


def _capital_points(run: BacktestRun) -> Iterable[dict[str, Any]]:
    contributions: dict[date, Decimal] = {}
    for item in run.backtest_result.contribution_events:
        current = contributions.get(item.effective_date, Decimal("0"))
        contributions[item.effective_date] = current + item.amount
    capital = Decimal(str(run.backtest_result.initial_capital))
    for equity in run.backtest_result.equity_curve:
        capital += contributions.get(equity.date, Decimal("0"))
        yield {"date": equity.date.isoformat(), "value": float(capital)}


def _sanitize(value: Any) -> Any:
    if isinstance(value, Mapping):
        sensitive_terms = ("api_key", "secret", "token", "password")
        return {
            str(key): (
                "[redacted]"
                if any(term in str(key).lower() for term in sensitive_terms)
                else _sanitize(item)
            )
            for key, item in sorted(value.items(), key=lambda item: str(item[0]))
        }
    if isinstance(value, tuple | list):
        return [_sanitize(item) for item in value]
    return value


__all__ = [
    "REPORT_SCHEMA_VERSION",
    "SERIES_NAMES",
    "BacktestReportProjectionError",
    "BacktestReportProjectionService",
]
