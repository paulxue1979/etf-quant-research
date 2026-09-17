"""Immutable application models and wire serialization for backtest runs."""

from __future__ import annotations

import json
import math
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, date, datetime
from types import MappingProxyType
from typing import Any
from uuid import uuid4

from analytics.models import (
    DrawdownPoint,
    ExposurePoint,
    MetricValue,
    PerformanceAnalysisResult,
    TradeMetrics,
    WealthPoint,
)
from backtest.models import (
    AllocationPoint,
    BacktestResult,
    ContributionEvent,
    ContributionFrequency,
    EquityPoint,
    ExternalCashFlow,
    Fill,
    Order,
    OrderSide,
    OrderStatus,
    RebalanceCause,
    Trade,
    canonical_decimal,
)
from data.models import PriceField
from portfolio.models import PortfolioSnapshot, Position

ANALYSIS_VERSION = "phase-4i.0"


@dataclass(frozen=True)
class BacktestRun:
    """Persisted immutable research artifact produced by one backtest request."""

    backtest_run_id: str
    strategy_id: str
    strategy_version_id: str
    created_at: datetime
    strategy_version_content_hash: str
    backtest_result: BacktestResult
    performance_analysis: PerformanceAnalysisResult
    provenance: Mapping[str, Any]
    strategy_provenance: Mapping[str, Any] | None = None
    benchmark_evaluation: Mapping[str, Any] | None = None

    def __post_init__(self) -> None:
        for label in ("backtest_run_id", "strategy_id", "strategy_version_id"):
            value = getattr(self, label)
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"{label} must be a non-empty string")
            object.__setattr__(self, label, value.strip())
        if not isinstance(self.created_at, datetime):
            raise TypeError("created_at must be a datetime")
        if not isinstance(self.strategy_version_content_hash, str) or not (
            self.strategy_version_content_hash.strip()
        ):
            raise ValueError("strategy_version_content_hash must be non-empty")
        if not isinstance(self.backtest_result, BacktestResult):
            raise TypeError("backtest_result must be a BacktestResult")
        if not isinstance(self.performance_analysis, PerformanceAnalysisResult):
            raise TypeError("performance_analysis must be a PerformanceAnalysisResult")
        if self.backtest_result.strategy_version_id != self.strategy_version_id:
            raise ValueError("backtest result strategy version does not match run")
        if self.performance_analysis.strategy_version_id != self.strategy_version_id:
            raise ValueError("analysis strategy version does not match run")
        if self.performance_analysis.backtest_run_id != self.backtest_run_id:
            raise ValueError("analysis backtest run id does not match run")
        if not isinstance(self.provenance, Mapping):
            raise TypeError("provenance must be a mapping")
        if self.strategy_provenance is not None and not isinstance(
            self.strategy_provenance, Mapping
        ):
            raise TypeError("strategy_provenance must be a mapping or None")
        if self.benchmark_evaluation is not None and not isinstance(
            self.benchmark_evaluation, Mapping
        ):
            raise TypeError("benchmark_evaluation must be a mapping or None")
        object.__setattr__(
            self,
            "strategy_version_content_hash",
            self.strategy_version_content_hash.strip(),
        )
        object.__setattr__(self, "provenance", MappingProxyType(dict(self.provenance)))
        if self.strategy_provenance is not None:
            object.__setattr__(
                self,
                "strategy_provenance",
                _freeze_json_value(self.strategy_provenance),
            )
        if self.benchmark_evaluation is not None:
            object.__setattr__(
                self,
                "benchmark_evaluation",
                _freeze_json_value(self.benchmark_evaluation),
            )

    @classmethod
    def create(
        cls,
        *,
        strategy_id: str,
        strategy_version_id: str,
        strategy_version_content_hash: str,
        backtest_result: BacktestResult,
        performance_analysis: PerformanceAnalysisResult,
        provenance: Mapping[str, Any],
        strategy_provenance: Mapping[str, Any] | None = None,
        benchmark_evaluation: Mapping[str, Any] | None = None,
    ) -> BacktestRun:
        """Create a new run with a unique id; never reuses an existing run."""
        run_id = f"backtest-{uuid4().hex}"
        analysis = PerformanceAnalysisResult(
            **{
                **performance_analysis.__dict__,
                "backtest_run_id": run_id,
                "strategy_id": strategy_id,
            }
        )
        return cls(
            backtest_run_id=run_id,
            strategy_id=strategy_id,
            strategy_version_id=strategy_version_id,
            created_at=datetime.now(UTC),
            strategy_version_content_hash=strategy_version_content_hash,
            backtest_result=backtest_result,
            performance_analysis=analysis,
            provenance=provenance,
            strategy_provenance=strategy_provenance,
            benchmark_evaluation=benchmark_evaluation,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "backtest_run_id": self.backtest_run_id,
            "strategy_id": self.strategy_id,
            "strategy_version_id": self.strategy_version_id,
            "created_at": self.created_at.isoformat(),
            "strategy_version_content_hash": self.strategy_version_content_hash,
            "backtest_result": serialize_backtest_result(self.backtest_result),
            "performance_analysis": self.performance_analysis.to_dict(),
            "provenance": dict(self.provenance),
            "strategy_provenance": _thaw_json_value(self.strategy_provenance),
            "benchmark_evaluation": _thaw_json_value(self.benchmark_evaluation),
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> BacktestRun:
        if not isinstance(payload, Mapping):
            raise ValueError("backtest run payload must be an object")
        result = deserialize_backtest_result(payload["backtest_result"])
        analysis = deserialize_performance_analysis(payload["performance_analysis"])
        return cls(
            backtest_run_id=str(payload["backtest_run_id"]),
            strategy_id=str(payload["strategy_id"]),
            strategy_version_id=str(payload["strategy_version_id"]),
            created_at=datetime.fromisoformat(str(payload["created_at"])),
            strategy_version_content_hash=str(payload["strategy_version_content_hash"]),
            backtest_result=result,
            performance_analysis=analysis,
            provenance=payload["provenance"],
            strategy_provenance=payload.get("strategy_provenance"),
            benchmark_evaluation=payload.get("benchmark_evaluation"),
        )


def _freeze_json_value(value: Any) -> Any:
    """Freeze a strict JSON value so persisted report provenance is immutable."""
    if value is None or isinstance(value, (str, bool)):
        return value
    if isinstance(value, int) and not isinstance(value, bool):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError("strategy_provenance must not contain non-finite values")
        return value
    if isinstance(value, Mapping):
        if not all(isinstance(key, str) for key in value):
            raise ValueError("strategy_provenance object keys must be strings")
        return MappingProxyType(
            {key: _freeze_json_value(item) for key, item in sorted(value.items())}
        )
    if isinstance(value, (list, tuple)):
        return tuple(_freeze_json_value(item) for item in value)
    raise TypeError("strategy_provenance must contain JSON-compatible values")


def _thaw_json_value(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {key: _thaw_json_value(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_thaw_json_value(item) for item in value]
    return value


def _date(value: object) -> date:
    return date.fromisoformat(str(value))


def serialize_backtest_result(result: BacktestResult) -> dict[str, Any]:
    return {
        "start_date": result.start_date.isoformat(),
        "end_date": result.end_date.isoformat(),
        "requested_start_date": result.requested_start_date.isoformat(),
        "requested_end_date": result.requested_end_date.isoformat(),
        "effective_start_date": result.effective_start_date.isoformat(),
        "effective_end_date": result.effective_end_date.isoformat(),
        "initial_capital": result.initial_capital,
        "final_equity": result.final_equity,
        "equity_curve": [
            {
                "date": item.date.isoformat(),
                "cash": item.cash,
                "asset_values": dict(item.asset_values),
                "total_equity": item.total_equity,
            }
            for item in result.equity_curve
        ],
        "orders": [
            {
                "order_id": item.order_id,
                "signal_date": item.signal_date.isoformat(),
                "date": item.date.isoformat(),
                "symbol": item.symbol,
                "side": item.side.value,
                "quantity": item.quantity,
                "requested_price": item.requested_price,
                "execution_price": item.execution_price,
                "status": item.status.value,
                "commission": item.commission,
                "slippage": item.slippage,
                "target_weight": item.target_weight,
                "rebalance_cause": item.rebalance_cause.value,
            }
            for item in result.orders
        ],
        "fills": [
            {
                "order_id": item.order_id,
                "date": item.date.isoformat(),
                "symbol": item.symbol,
                "side": item.side.value,
                "quantity": item.quantity,
                "price": item.price,
                "commission": item.commission,
                "slippage": item.slippage,
                "cash_effect": item.cash_effect,
            }
            for item in result.fills
        ],
        "trades": [
            {
                "symbol": item.symbol,
                "entry_date": item.entry_date.isoformat(),
                "exit_date": item.exit_date.isoformat(),
                "entry_price": item.entry_price,
                "exit_price": item.exit_price,
                "quantity": item.quantity,
                "pnl": item.pnl,
                "pnl_pct": item.pnl_pct,
                "holding_period": item.holding_period,
            }
            for item in result.trades
        ],
        "positions": [
            {
                "as_of_date": item.as_of_date.isoformat(),
                "cash": item.cash,
                "total_equity": item.total_equity,
                "positions": [
                    {
                        "symbol": position.symbol,
                        "quantity": position.quantity,
                        "average_cost": position.average_cost,
                        "market_price": position.market_price,
                        "market_value": position.market_value,
                        "unrealized_pnl": position.unrealized_pnl,
                        "as_of_date": position.as_of_date.isoformat(),
                    }
                    for position in item.positions
                ],
            }
            for item in result.positions
        ],
        "allocation_history": [
            {
                "date": item.date.isoformat(),
                "symbol": item.symbol,
                "target_weight": item.target_weight,
                "actual_weight": item.actual_weight,
            }
            for item in result.allocation_history
        ],
        "cash_history": [[item_date.isoformat(), cash] for item_date, cash in result.cash_history],
        "contribution_events": [
            {
                "frequency": item.frequency.value,
                "amount": canonical_decimal(item.amount),
                "requested_date": item.requested_date.isoformat(),
                "effective_date": item.effective_date.isoformat(),
                "currency": item.currency,
            }
            for item in result.contribution_events
        ],
        "external_cash_flows": [
            {
                "date": item.date.isoformat(),
                "amount": canonical_decimal(item.amount),
                "currency": item.currency,
                "source": item.source,
            }
            for item in result.external_cash_flows
        ],
        "cumulative_contributions": result.cumulative_contributions,
        "total_capital_invested": result.total_capital_invested,
        "investment_profit": result.investment_profit,
        "strategy_version_id": result.strategy_version_id,
        "configuration_snapshot": dict(result.configuration_snapshot),
        "data_snapshot_reference": dict(result.data_snapshot_reference),
        "engine_version": result.engine_version,
    }


def deserialize_backtest_result(payload: Mapping[str, Any]) -> BacktestResult:
    if not isinstance(payload, Mapping):
        raise ValueError("backtest result payload must be an object")
    return BacktestResult(
        start_date=_date(payload["start_date"]),
        end_date=_date(payload["end_date"]),
        requested_start_date=(
            _date(payload["requested_start_date"])
            if payload.get("requested_start_date") is not None
            else None
        ),
        requested_end_date=(
            _date(payload["requested_end_date"])
            if payload.get("requested_end_date") is not None
            else None
        ),
        effective_start_date=(
            _date(payload["effective_start_date"])
            if payload.get("effective_start_date") is not None
            else None
        ),
        effective_end_date=(
            _date(payload["effective_end_date"])
            if payload.get("effective_end_date") is not None
            else None
        ),
        initial_capital=float(payload["initial_capital"]),
        final_equity=float(payload["final_equity"]),
        equity_curve=tuple(
            EquityPoint(
                date=_date(item["date"]),
                cash=float(item["cash"]),
                asset_values=MappingProxyType(
                    {str(k): float(v) for k, v in item["asset_values"].items()}
                ),
                total_equity=float(item["total_equity"]),
            )
            for item in payload["equity_curve"]
        ),
        orders=tuple(
            Order(
                order_id=str(item["order_id"]),
                signal_date=_date(item["signal_date"]),
                date=_date(item["date"]),
                symbol=str(item["symbol"]),
                side=OrderSide(item["side"]),
                quantity=int(item["quantity"]),
                requested_price=float(item["requested_price"]),
                execution_price=float(item["execution_price"]),
                status=OrderStatus(item["status"]),
                commission=float(item["commission"]),
                slippage=float(item["slippage"]),
                target_weight=float(item["target_weight"]),
                rebalance_cause=RebalanceCause(
                    item.get("rebalance_cause", RebalanceCause.TARGET.value)
                ),
            )
            for item in payload["orders"]
        ),
        fills=tuple(
            Fill(
                order_id=str(item["order_id"]),
                date=_date(item["date"]),
                symbol=str(item["symbol"]),
                side=OrderSide(item["side"]),
                quantity=int(item["quantity"]),
                price=float(item["price"]),
                commission=float(item["commission"]),
                slippage=float(item["slippage"]),
                cash_effect=float(item["cash_effect"]),
            )
            for item in payload["fills"]
        ),
        trades=tuple(
            Trade(
                **{
                    "symbol": str(item["symbol"]),
                    "entry_date": _date(item["entry_date"]),
                    "exit_date": _date(item["exit_date"]),
                    "entry_price": float(item["entry_price"]),
                    "exit_price": float(item["exit_price"]),
                    "quantity": int(item["quantity"]),
                    "pnl": float(item["pnl"]),
                    "pnl_pct": float(item["pnl_pct"]),
                    "holding_period": int(item["holding_period"]),
                }
            )
            for item in payload["trades"]
        ),
        positions=tuple(
            PortfolioSnapshot(
                as_of_date=_date(item["as_of_date"]),
                cash=float(item["cash"]),
                positions=tuple(
                    Position(
                        symbol=str(position["symbol"]),
                        quantity=int(position["quantity"]),
                        average_cost=float(position["average_cost"]),
                        market_price=float(position["market_price"]),
                        market_value=float(position["market_value"]),
                        unrealized_pnl=float(position["unrealized_pnl"]),
                        as_of_date=_date(position["as_of_date"]),
                    )
                    for position in item["positions"]
                ),
                total_equity=float(item["total_equity"]),
            )
            for item in payload["positions"]
        ),
        allocation_history=tuple(
            AllocationPoint(
                date=_date(item["date"]),
                symbol=str(item["symbol"]),
                target_weight=float(item["target_weight"]),
                actual_weight=float(item["actual_weight"]),
            )
            for item in payload["allocation_history"]
        ),
        cash_history=tuple((_date(item[0]), float(item[1])) for item in payload["cash_history"]),
        contribution_events=tuple(
            ContributionEvent(
                frequency=ContributionFrequency(item["frequency"]),
                amount=item["amount"],
                requested_date=_date(item["requested_date"]),
                effective_date=_date(item["effective_date"]),
                currency=str(item.get("currency", "USD")),
            )
            for item in payload.get("contribution_events", [])
        ),
        external_cash_flows=tuple(
            ExternalCashFlow(
                date=_date(item["date"]),
                amount=item["amount"],
                currency=str(item.get("currency", "USD")),
                source=str(item.get("source", "contribution")),
            )
            for item in payload.get("external_cash_flows", [])
        ),
        cumulative_contributions=float(payload.get("cumulative_contributions", 0.0)),
        total_capital_invested=(
            float(payload["total_capital_invested"])
            if payload.get("total_capital_invested") is not None
            else None
        ),
        investment_profit=(
            float(payload["investment_profit"])
            if payload.get("investment_profit") is not None
            else None
        ),
        strategy_version_id=str(payload["strategy_version_id"]),
        configuration_snapshot=MappingProxyType(dict(payload["configuration_snapshot"])),
        data_snapshot_reference=MappingProxyType(dict(payload["data_snapshot_reference"])),
        engine_version=str(payload["engine_version"]),
    )


def _metric(payload: Mapping[str, Any]) -> MetricValue:
    return MetricValue(
        value=payload.get("value"),
        status=payload["status"],
        reason=payload.get("reason"),
    )


def deserialize_performance_analysis(payload: Mapping[str, Any]) -> PerformanceAnalysisResult:
    if not isinstance(payload, Mapping):
        raise ValueError("performance analysis payload must be an object")
    trade = payload["trade_metrics"]
    return PerformanceAnalysisResult(
        backtest_run_id=payload.get("backtest_run_id"),
        strategy_id=payload.get("strategy_id"),
        strategy_version_id=str(payload["strategy_version_id"]),
        start_date=_date(payload["start_date"]),
        end_date=_date(payload["end_date"]),
        price_field_used=PriceField(payload["price_field_used"]),
        rebalance_frequency=str(payload["rebalance_frequency"]),
        observation_frequency=str(payload["observation_frequency"]),
        periods_per_year=float(payload["periods_per_year"]),
        initial_capital=float(payload["initial_capital"]),
        final_equity=float(payload["final_equity"]),
        total_return=_metric(payload["total_return"]),
        cagr=_metric(payload["cagr"]),
        annualized_volatility=_metric(payload["annualized_volatility"]),
        sharpe_ratio=_metric(payload["sharpe_ratio"]),
        sortino_ratio=_metric(payload["sortino_ratio"]),
        max_drawdown=_metric(payload["max_drawdown"]),
        max_drawdown_duration=_metric(payload["max_drawdown_duration"]),
        recovery_duration=_metric(payload["recovery_duration"]),
        max_drawdown_recovered=bool(payload["max_drawdown_recovered"]),
        calmar_ratio=_metric(payload["calmar_ratio"]),
        trade_metrics=TradeMetrics(
            number_of_closed_trades=int(trade["number_of_closed_trades"]),
            winning_trades=int(trade["winning_trades"]),
            losing_trades=int(trade["losing_trades"]),
            win_rate=_metric(trade["win_rate"]),
            profit_factor=_metric(trade["profit_factor"]),
            average_trade_return=_metric(trade["average_trade_return"]),
            best_trade=_metric(trade["best_trade"]),
            worst_trade=_metric(trade["worst_trade"]),
            average_holding_period=_metric(trade["average_holding_period"]),
            turnover=_metric(trade["turnover"]),
        ),
        provenance=payload["provenance"],
        drawdown_curve=tuple(
            DrawdownPoint(date=_date(item["date"]), value=float(item["value"]))
            for item in payload.get("drawdown_curve", [])
        ),
        xirr=_metric(
            payload.get("xirr", {"value": None, "status": "not_evaluable", "reason": "legacy run"})
        ),
        twr_wealth_curve=tuple(
            WealthPoint(date=_date(item["date"]), value=float(item["value"]))
            for item in payload.get("twr_wealth_curve", [])
        ),
        exposure_curve=tuple(
            ExposurePoint(
                date=_date(item["date"]),
                cash_weight=float(item["cash_weight"]),
                gross_exposure=float(item["gross_exposure"]),
                net_exposure=float(item["net_exposure"]),
                asset_weights=item.get("asset_weights", {}),
                target_cash_weight=float(item.get("target_cash_weight", 0.0)),
                target_asset_weights=item.get("target_asset_weights", {}),
            )
            for item in payload.get("exposure_curve", [])
        ),
        exposure_summary=payload.get("exposure_summary", {}),
        turnover=_metric(
            payload.get(
                "turnover", {"value": None, "status": "not_evaluable", "reason": "legacy run"}
            )
        ),
        turnover_provenance=payload.get("turnover_provenance", {}),
    )


def dumps(payload: Mapping[str, Any]) -> str:
    """Serialize with strict JSON settings so corrupt/non-finite values fail early."""
    return json.dumps(
        payload,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


__all__ = [
    "ANALYSIS_VERSION",
    "BacktestRun",
    "deserialize_backtest_result",
    "deserialize_performance_analysis",
    "dumps",
    "serialize_backtest_result",
]
