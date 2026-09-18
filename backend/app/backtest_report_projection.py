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
SERIES_NAMES = frozenset(
    {"equity", "capital", "twr", "drawdown", "benchmark", "benchmark_twr", "benchmark_drawdown"}
)


class BacktestReportProjectionError(ValueError):
    """Raised when a report projection request is invalid."""


class BacktestReportProjectionService:
    """Project persisted truth into a deterministic report read model."""

    def project(self, run: BacktestRun) -> dict[str, Any]:
        if not isinstance(run, BacktestRun):
            raise TypeError("run must be a BacktestRun")
        result = run.backtest_result
        analysis = run.performance_analysis
        benchmark = self._benchmark(run)
        strategy_provenance = self._strategy_provenance(run)
        holdings = self._holding_summary(run)
        contribution_report = self._contribution_report(run)
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
                "contributions": contribution_report["status"],
                "strategy_provenance": strategy_provenance["status"],
                "holdings": holdings["status"],
                "benchmark": benchmark["status"],
                "xirr": _availability_for_metric(analysis.xirr),
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
                    "xirr": analysis.xirr.to_dict(),
                    "turnover": analysis.turnover.to_dict(),
                    "exposure": dict(analysis.exposure_summary),
                },
                "benchmark": benchmark["summary"],
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
                "xirr": analysis.xirr.to_dict(),
                "xirr_unit": "annualized decimal return",
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
            "contribution_report": contribution_report,
            "strategy_provenance": strategy_provenance,
            "allocations": self._allocations(run, strategy_provenance),
            "holdings": holdings,
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

    @staticmethod
    def _contribution_report(run: BacktestRun) -> dict[str, Any]:
        result = run.backtest_result
        if not run.contribution_provenance_available:
            return {
                "status": "not_available",
                "reason": "legacy contribution provenance was not persisted for this run",
                "schedule": None,
                "event_count": 0,
                "events": [],
                "integrity": {
                    "status": "not_available",
                    "event_amount_total": None,
                    "external_cash_flow_total": None,
                    "cumulative_contributions": None,
                },
            }

        raw_schedule = result.configuration_snapshot.get("contribution_schedule")
        if isinstance(raw_schedule, Mapping):
            frequency = str(raw_schedule.get("frequency", ""))
            schedule = {
                "enabled": True,
                "frequency": frequency,
                "amount": str(raw_schedule.get("amount", "")),
                "requested_date": raw_schedule.get("requested_date"),
                "currency": str(raw_schedule.get("currency", "USD")),
                "requested_date_semantics": (
                    "month_start" if frequency == "monthly" else "explicit_date"
                ),
            }
        else:
            schedule = {
                "enabled": False,
                "frequency": None,
                "amount": None,
                "requested_date": None,
                "currency": "USD",
                "requested_date_semantics": None,
            }

        ordered_events = sorted(
            enumerate(result.contribution_events),
            key=lambda pair: (
                pair[1].effective_date,
                pair[1].requested_date,
                pair[0],
            ),
        )
        events: list[dict[str, Any]] = []
        for sequence, (_, event) in enumerate(ordered_events, start=1):
            orders = tuple(
                order
                for order in result.orders
                if order.date == event.effective_date
                and order.rebalance_cause.value == "contribution"
            )
            order_ids = {order.order_id for order in orders}
            fills = tuple(fill for fill in result.fills if fill.order_id in order_ids)
            events.append(
                {
                    "sequence": sequence,
                    "requested_date": event.requested_date.isoformat(),
                    "effective_date": event.effective_date.isoformat(),
                    "amount": canonical_decimal(event.amount),
                    "currency": event.currency,
                    "frequency": event.frequency.value,
                    "source": "ContributionEvent",
                    "strategy_signal": False,
                    "deployment": {
                        "cause": "contribution",
                        "status": (
                            "rebalance_executed"
                            if orders
                            else "no_contribution_rebalance_execution"
                        ),
                        "order_count": len(orders),
                        "fill_count": len(fills),
                        "symbols": sorted({order.symbol for order in orders}),
                    },
                }
            )

        event_total = sum((item.amount for item in result.contribution_events), Decimal("0"))
        flow_total = sum((item.amount for item in result.external_cash_flows), Decimal("0"))
        cumulative = Decimal(str(result.cumulative_contributions))
        integrity_status = (
            "consistent"
            if event_total == cumulative and flow_total == cumulative
            else "inconsistent"
        )
        return {
            "status": "available",
            "schedule": schedule,
            "event_count": len(events),
            "events": events,
            "integrity": {
                "status": integrity_status,
                "event_amount_total": canonical_decimal(event_total),
                "external_cash_flow_total": canonical_decimal(flow_total),
                "cumulative_contributions": result.cumulative_contributions,
            },
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

    def holdings(
        self,
        run: BacktestRun,
        *,
        status: str = "ALL",
        symbol: str | None = None,
        limit: int = 50,
        offset: int = 0,
        sort_by: str = "entry_date",
        order: str = "asc",
    ) -> dict[str, Any]:
        """Return a bounded view of immutable FIFO holding segments."""
        segments = run.backtest_result.holding_segments
        normalized_symbol = symbol.strip().upper() if symbol else None
        base = {
            "report_schema_version": REPORT_SCHEMA_VERSION,
            "identity": {
                "backtest_run_id": run.backtest_run_id,
                "strategy_version_id": run.strategy_version_id,
            },
            "limit": limit,
            "offset": offset,
            "filters": {"status": status, "symbol": normalized_symbol},
            "sort": {"by": sort_by, "order": order},
            "duration_basis": "calendar_days_between_execution_dates",
            "metric_availability": self._holding_metric_availability(),
        }
        if segments is None:
            return {
                **base,
                "status": "not_available",
                "reason": "canonical FIFO holding provenance was not persisted for this legacy run",
                "summary": {"open_count": None, "closed_count": None},
                "total": 0,
                "items": [],
            }
        filtered = [
            item
            for item in segments
            if (status == "ALL" or item.status.value == status)
            and (normalized_symbol is None or item.symbol == normalized_symbol)
        ]
        sorted_segments = self._sort_holdings(filtered, sort_by=sort_by, order=order)
        return {
            **base,
            "status": "available",
            "source": "BacktestResult.holding_segments",
            "summary": {
                "open_count": sum(item.status.value == "OPEN" for item in segments),
                "closed_count": sum(item.status.value == "CLOSED" for item in segments),
            },
            "total": len(sorted_segments),
            "items": [
                self._holding_item(run, item) for item in sorted_segments[offset : offset + limit]
            ],
        }

    @staticmethod
    def _holding_summary(run: BacktestRun) -> dict[str, Any]:
        segments = run.backtest_result.holding_segments
        if segments is None:
            return {
                "status": "not_available",
                "reason": "canonical FIFO holding provenance was not persisted for this legacy run",
            }
        return {
            "status": "available",
            "source": "BacktestResult.holding_segments",
            "open_count": sum(item.status.value == "OPEN" for item in segments),
            "closed_count": sum(item.status.value == "CLOSED" for item in segments),
            "duration_basis": "calendar_days_between_execution_dates",
        }

    @staticmethod
    def _holding_metric_availability() -> dict[str, Any]:
        reason = "immutable lot-level market price path is not persisted"
        return {
            name: {"status": "not_available", "value": None, "reason": reason}
            for name in ("mfe", "mae", "holding_drawdown")
        }

    @staticmethod
    def _sort_holdings(segments: list[Any], *, sort_by: str, order: str) -> list[Any]:
        def value(item: Any) -> Any:
            if sort_by == "entry_date":
                return item.entry_execution_date
            if sort_by == "exit_date":
                return item.exit_execution_date
            if sort_by == "symbol":
                return item.symbol
            if sort_by == "holding_return":
                return item.holding_return
            if sort_by == "pnl":
                return item.realized_pnl if item.status.value == "CLOSED" else item.unrealized_pnl
            return item.holding_days

        available = [item for item in segments if value(item) is not None]
        unavailable = [item for item in segments if value(item) is None]
        return sorted(
            available,
            key=lambda item: (value(item), item.holding_id),
            reverse=order == "desc",
        ) + sorted(unavailable, key=lambda item: item.holding_id)

    @staticmethod
    def _holding_item(run: BacktestRun, item: Any) -> dict[str, Any]:
        entry_context = BacktestReportProjectionService._signal_context(run, item.entry_signal_date)
        exit_context = BacktestReportProjectionService._signal_context(run, item.exit_signal_date)
        pnl = item.realized_pnl if item.status.value == "CLOSED" else item.unrealized_pnl
        return {
            "holding_id": item.holding_id,
            "lot_id": item.lot_id,
            "symbol": item.symbol,
            "status": item.status.value,
            "quantity": item.quantity,
            "entry_fill_id": item.entry_order_id,
            "entry_signal_date": (
                item.entry_signal_date.isoformat() if item.entry_signal_date is not None else None
            ),
            "entry_execution_date": item.entry_execution_date.isoformat(),
            "entry_price": item.entry_price,
            "entry_execution_cause": item.entry_execution_cause.value,
            "entry_matched_rule_id": entry_context.get("matched_rule_id"),
            "entry_allocation_source": entry_context.get("allocation_source"),
            "exit_fill_id": item.exit_order_id,
            "exit_signal_date": (
                item.exit_signal_date.isoformat() if item.exit_signal_date is not None else None
            ),
            "exit_execution_date": (
                item.exit_execution_date.isoformat()
                if item.exit_execution_date is not None
                else None
            ),
            "exit_price": item.exit_price,
            "exit_execution_cause": (
                item.exit_execution_cause.value if item.exit_execution_cause is not None else None
            ),
            "exit_matched_rule_id": exit_context.get("matched_rule_id"),
            "exit_allocation_source": exit_context.get("allocation_source"),
            "report_end_date": (
                item.report_end_date.isoformat() if item.report_end_date is not None else None
            ),
            "ending_price": item.ending_price,
            "market_value": item.market_value,
            "holding_days": item.holding_days,
            "holding_days_basis": "calendar_days",
            "realized_pnl": item.realized_pnl,
            "unrealized_pnl": item.unrealized_pnl,
            "pnl": pnl,
            "pnl_type": "realized" if item.status.value == "CLOSED" else "unrealized",
            "holding_return": item.holding_return,
        }

    @staticmethod
    def _signal_context(run: BacktestRun, signal_date: date | None) -> Mapping[str, Any]:
        if signal_date is None or run.strategy_provenance is None:
            return {}
        records = run.strategy_provenance.get("records")
        if not isinstance(records, tuple):
            return {}
        expected = signal_date.isoformat()
        return next(
            (
                item
                for item in records
                if isinstance(item, Mapping) and item.get("signal_date") == expected
            ),
            {},
        )

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
        normalized_records = [
            {
                "signal_date": item.get("signal_date"),
                "matched_rule_id": item.get("matched_rule_id"),
                "allocation_source": item.get("allocation_source"),
                "target_allocation": dict(sorted(item.get("target_allocation", {}).items()))
                if isinstance(item.get("target_allocation"), Mapping)
                else {},
                "execution_date": item.get("execution_date"),
                "execution_status": item.get("execution_status"),
                "omission_reason": item.get("omission_reason"),
            }
            for item in records
            if isinstance(item, Mapping)
        ]
        meaningful = {"rule_match", "fallback"}
        markers: list[dict[str, Any]] = []
        previous_decision: tuple[Any, ...] | None = None
        for item in normalized_records:
            if item["allocation_source"] not in meaningful:
                continue
            decision = (
                item["allocation_source"],
                item["matched_rule_id"],
                tuple(sorted(item["target_allocation"].items())),
            )
            if decision == previous_decision:
                continue
            previous_decision = decision
            markers.append(
                {
                    "date": item["signal_date"],
                    "marker_type": "signal",
                    "signal_date": item["signal_date"],
                    "allocation_source": item["allocation_source"],
                    "matched_rule_id": item["matched_rule_id"],
                    "target_allocation": item["target_allocation"],
                    "execution_date": item["execution_date"],
                    "execution_status": item["execution_status"],
                    "omission_reason": item["omission_reason"],
                }
            )
        records_by_date = {item["signal_date"]: item for item in normalized_records}
        fills_by_order = Counter(item.order_id for item in run.backtest_result.fills)
        execution_groups: dict[tuple[str, str, str], list[Any]] = {}
        for order in run.backtest_result.orders:
            key = (
                order.date.isoformat(),
                order.signal_date.isoformat(),
                order.rebalance_cause.value,
            )
            execution_groups.setdefault(key, []).append(order)
        for (execution_date, signal_date, cause), orders in execution_groups.items():
            related = records_by_date.get(signal_date) if cause == "target" else None
            fill_count = sum(fills_by_order[order.order_id] for order in orders)
            statuses = sorted({order.status.value for order in orders})
            markers.append(
                {
                    "date": execution_date,
                    "marker_type": "execution",
                    "signal_date": signal_date,
                    "execution_date": execution_date,
                    "execution_status": "filled" if fill_count else ",".join(statuses),
                    "rebalance_cause": cause,
                    "order_count": len(orders),
                    "fill_count": fill_count,
                    "symbols": sorted({order.symbol for order in orders}),
                    "sides": sorted({order.side.value for order in orders}),
                    "allocation_source": related.get("allocation_source") if related else None,
                    "matched_rule_id": related.get("matched_rule_id") if related else None,
                    "target_allocation": related.get("target_allocation", {}) if related else {},
                }
            )
        markers.sort(
            key=lambda marker: (
                str(marker["date"]),
                0 if marker["marker_type"] == "execution" else 1,
            )
        )
        return {
            "status": "available",
            "source": persisted.get("source"),
            "record_count": len(records),
            "allocation_sources": dict(sorted(sources.items())),
            "submitted_count": submitted,
            "omitted_count": omitted,
            "records": normalized_records,
            "markers": markers,
        }

    @staticmethod
    def _allocations(run: BacktestRun, strategy_provenance: Mapping[str, Any]) -> dict[str, Any]:
        target: dict[str, Any]
        if strategy_provenance["status"] == "available":
            records = strategy_provenance.get("records", [])
            target_points = []
            target_symbols: set[str] = set()
            for item in records if isinstance(records, list) else []:
                allocation = item.get("target_allocation", {})
                if not isinstance(allocation, Mapping):
                    allocation = {}
                assets = {str(symbol): float(weight) for symbol, weight in allocation.items()}
                target_symbols.update(assets)
                cash_weight = 1.0 - sum(assets.values())
                target_points.append(
                    {
                        "date": item.get("signal_date"),
                        "asset_weights": dict(sorted(assets.items())),
                        "cash_weight": 0.0 if abs(cash_weight) <= 1e-12 else max(0.0, cash_weight),
                        "allocation_source": item.get("allocation_source"),
                        "matched_rule_id": item.get("matched_rule_id"),
                    }
                )
            target = {
                "status": "available",
                "source": "BacktestRun.strategy_provenance.records[].target_allocation",
                "timeline": target_points,
                "asset_symbols": sorted(target_symbols),
            }
        else:
            target = {
                "status": "not_available",
                "reason": "strategy target allocation provenance was not persisted",
            }
        actual_points: dict[str, dict[str, Any]] = {}
        actual_symbols: set[str] = set()
        for item in run.backtest_result.allocation_history:
            point = actual_points.setdefault(
                item.date.isoformat(),
                {"date": item.date.isoformat(), "asset_weights": {}, "target_asset_weights": {}},
            )
            point["asset_weights"][item.symbol] = item.actual_weight
            point["target_asset_weights"][item.symbol] = item.target_weight
            actual_symbols.add(item.symbol)
        equity_by_date = {item.date.isoformat(): item for item in run.backtest_result.equity_curve}
        for point in actual_points.values():
            equity = equity_by_date.get(point["date"])
            point["cash_weight"] = (
                equity.cash / equity.total_equity
                if equity is not None and equity.total_equity
                else 0.0
            )
            point["asset_weights"] = dict(sorted(point["asset_weights"].items()))
            point["target_asset_weights"] = dict(sorted(point["target_asset_weights"].items()))
        actual_timeline = [actual_points[key] for key in sorted(actual_points)]
        return {
            "target": target,
            "actual": {
                "status": "available",
                "source": "BacktestResult.allocation_history",
                "record_count": len(run.backtest_result.allocation_history),
                "timeline": actual_timeline,
                "asset_symbols": sorted(actual_symbols),
            },
            "cash_semantics": "cash_weight is ledger cash / total equity; SGOV remains an asset",
        }

    @staticmethod
    def _series_metadata(run: BacktestRun) -> dict[str, Any]:
        has_drawdown_curve = bool(run.performance_analysis.drawdown_curve)
        has_twr_curve = bool(run.performance_analysis.twr_wealth_curve)
        benchmark = run.benchmark_evaluation
        benchmark_available = (
            isinstance(benchmark, Mapping) and benchmark.get("status") == "available"
        )
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
                "status": "available" if has_twr_curve else "not_available",
                "reason": None
                if has_twr_curve
                else "normalized TWR wealth series was not persisted",
                "source": "PerformanceAnalysisResult.twr_wealth_curve",
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
                "status": "available" if benchmark_available else "not_available",
                "reason": None if benchmark_available else "benchmark evaluation was not persisted",
            },
            "benchmark_twr": {
                "status": "available" if benchmark_available else "not_available",
                "source": "BacktestRun.benchmark_evaluation",
            },
            "benchmark_drawdown": {
                "status": "available" if benchmark_available else "not_available",
                "source": "BacktestRun.benchmark_evaluation",
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
        if name == "twr":
            curve = run.performance_analysis.twr_wealth_curve
            if not curve:
                return {
                    "status": "not_available",
                    "reason": "normalized TWR wealth series was not persisted",
                    "source": "PerformanceAnalysisResult.twr_wealth_curve",
                }
            return {
                "status": "available",
                "series_type": "normalized_wealth",
                "unit": "normalized",
                "source": "PerformanceAnalysisResult.twr_wealth_curve",
                "points": _window_points((item.to_dict() for item in curve), start, end),
            }
        if name in {"benchmark", "benchmark_twr", "benchmark_drawdown"}:
            return self._benchmark_series(run, name, start=start, end=end)
        raise BacktestReportProjectionError(f"unsupported report series: {name}")

    @staticmethod
    def _benchmark(run: BacktestRun) -> dict[str, Any]:
        benchmark = run.benchmark_evaluation
        if not isinstance(benchmark, Mapping):
            return {
                "status": "not_available",
                "summary": {
                    "status": "not_available",
                    "reason": "benchmark evaluation was not persisted for this run",
                },
            }
        status = str(benchmark.get("status", "not_evaluable"))
        if status != "available":
            return {
                "status": "not_evaluable",
                "summary": {
                    "status": "not_evaluable",
                    "reason": benchmark.get("reason", "benchmark is not evaluable"),
                },
            }
        performance = benchmark.get("performance")
        if not isinstance(performance, Mapping):
            return {
                "status": "not_evaluable",
                "summary": {
                    "status": "not_evaluable",
                    "reason": "benchmark performance is invalid",
                },
            }
        strategy_twr = run.performance_analysis.total_return
        benchmark_twr = performance.get("twr_total_return")
        excess = None
        if (
            strategy_twr.is_evaluable
            and isinstance(benchmark_twr, Mapping)
            and benchmark_twr.get("status") == "available"
        ):
            excess = {
                "value": float(strategy_twr.value) - float(benchmark_twr["value"]),
                "status": "available",
                "reason": None,
            }
        return {
            "status": "available",
            "summary": {
                "status": "available",
                "benchmark_symbol": benchmark.get("benchmark_symbol"),
                "ending_value": benchmark.get("ending_value"),
                "twr_total_return": benchmark_twr,
                "cagr": performance.get("cagr"),
                "max_drawdown": performance.get("max_drawdown"),
                "excess_return": excess
                or {
                    "value": None,
                    "status": "not_evaluable",
                    "reason": "strategy or benchmark TWR is not evaluable",
                },
                "ending_value_difference": {
                    "value": None,
                    "status": "not_evaluable",
                    "reason": "ending strategy value is not paired in benchmark artifact",
                },
                "provenance": benchmark.get("provenance", {}),
            },
        }

    @staticmethod
    def _benchmark_series(
        run: BacktestRun,
        name: str,
        *,
        start: date | None,
        end: date | None,
    ) -> dict[str, Any]:
        benchmark = run.benchmark_evaluation
        if not isinstance(benchmark, Mapping) or benchmark.get("status") != "available":
            return {
                "status": "not_available" if benchmark is None else "not_evaluable",
                "reason": "benchmark evaluation was not persisted or is not evaluable",
            }
        performance = benchmark.get("performance")
        if not isinstance(performance, Mapping):
            return {"status": "not_evaluable", "reason": "benchmark performance is invalid"}
        key = "twr_wealth_curve" if name in {"benchmark", "benchmark_twr"} else "drawdown_curve"
        points = performance.get(key)
        if not isinstance(points, list):
            return {"status": "not_available", "reason": "benchmark series was not persisted"}
        return {
            "status": "available",
            "series_type": "normalized_wealth" if key == "twr_wealth_curve" else "drawdown",
            "unit": "normalized" if key == "twr_wealth_curve" else "percent",
            "source": "BacktestRun.benchmark_evaluation",
            "points": _window_points(points, start, end),
        }


def _availability_for_metric(metric: Any) -> str:
    return "available" if metric.is_evaluable else "not_evaluable"


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
