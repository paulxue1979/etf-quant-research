"""Immutable, serializable performance analytics result models."""

from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import date
from enum import StrEnum
from types import MappingProxyType
from typing import Any

from data.models import PriceField


class MetricStatus(StrEnum):
    """Whether a metric has a mathematically valid value."""

    AVAILABLE = "available"
    NOT_EVALUABLE = "not_evaluable"


@dataclass(frozen=True)
class MetricValue:
    """One finite metric value or an explicit not-evaluable state."""

    value: float | None
    status: MetricStatus = MetricStatus.AVAILABLE
    reason: str | None = None

    def __post_init__(self) -> None:
        status = self.status if isinstance(self.status, MetricStatus) else MetricStatus(self.status)
        if status is MetricStatus.AVAILABLE:
            if (
                self.value is None
                or isinstance(self.value, bool)
                or not isinstance(self.value, (int, float))
                or not math.isfinite(float(self.value))
            ):
                raise ValueError("available metric values must be finite numbers")
            if self.reason is not None:
                raise ValueError("available metrics must not have a reason")
            object.__setattr__(self, "value", float(self.value))
        else:
            if self.value is not None:
                raise ValueError("not-evaluable metrics must not have a value")
            if not isinstance(self.reason, str) or not self.reason.strip():
                raise ValueError("not-evaluable metrics require a reason")
            object.__setattr__(self, "reason", self.reason.strip())
        object.__setattr__(self, "status", status)

    @classmethod
    def available(cls, value: float) -> MetricValue:
        """Create a finite, available metric."""
        return cls(value=value)

    @classmethod
    def not_evaluable(cls, reason: str) -> MetricValue:
        """Create an explicit metric with no mathematically valid value."""
        return cls(value=None, status=MetricStatus.NOT_EVALUABLE, reason=reason)

    @property
    def is_evaluable(self) -> bool:
        """Return whether ``value`` is available."""
        return self.status is MetricStatus.AVAILABLE

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-compatible metric representation."""
        return {"value": self.value, "status": self.status.value, "reason": self.reason}


@dataclass(frozen=True)
class TradeMetrics:
    """Statistics derived only from PHASE 3 closed Trade records."""

    number_of_closed_trades: int
    winning_trades: int
    losing_trades: int
    win_rate: MetricValue
    profit_factor: MetricValue
    average_trade_return: MetricValue
    best_trade: MetricValue
    worst_trade: MetricValue
    average_holding_period: MetricValue
    turnover: MetricValue

    def __post_init__(self) -> None:
        for label, value in (
            ("number_of_closed_trades", self.number_of_closed_trades),
            ("winning_trades", self.winning_trades),
            ("losing_trades", self.losing_trades),
        ):
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise ValueError(f"{label} must be a non-negative integer")
        if self.winning_trades + self.losing_trades > self.number_of_closed_trades:
            raise ValueError("winning and losing trades cannot exceed closed trades")
        for label in (
            "win_rate",
            "profit_factor",
            "average_trade_return",
            "best_trade",
            "worst_trade",
            "average_holding_period",
            "turnover",
        ):
            if not isinstance(getattr(self, label), MetricValue):
                raise TypeError(f"{label} must be a MetricValue")

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-compatible trade metrics representation."""
        return {
            "number_of_closed_trades": self.number_of_closed_trades,
            "winning_trades": self.winning_trades,
            "losing_trades": self.losing_trades,
            "win_rate": self.win_rate.to_dict(),
            "profit_factor": self.profit_factor.to_dict(),
            "average_trade_return": self.average_trade_return.to_dict(),
            "best_trade": self.best_trade.to_dict(),
            "worst_trade": self.worst_trade.to_dict(),
            "average_holding_period": self.average_holding_period.to_dict(),
            "turnover": self.turnover.to_dict(),
        }


@dataclass(frozen=True)
class DrawdownPoint:
    """One backend-computed portfolio drawdown observation."""

    date: date
    value: float

    def __post_init__(self) -> None:
        if not isinstance(self.date, date):
            raise TypeError("drawdown date must be a date")
        if (
            isinstance(self.value, bool)
            or not isinstance(self.value, (int, float))
            or not math.isfinite(float(self.value))
        ):
            raise ValueError("drawdown value must be finite")
        object.__setattr__(self, "value", float(self.value))

    def to_dict(self) -> dict[str, Any]:
        return {"date": self.date.isoformat(), "value": self.value}


@dataclass(frozen=True)
class WealthPoint:
    """One normalized, flow-adjusted portfolio wealth observation."""

    date: date
    value: float

    def __post_init__(self) -> None:
        if not isinstance(self.date, date):
            raise TypeError("wealth date must be a date")
        if (
            isinstance(self.value, bool)
            or not isinstance(self.value, (int, float))
            or not math.isfinite(float(self.value))
            or self.value <= 0
        ):
            raise ValueError("wealth value must be finite and positive")
        object.__setattr__(self, "value", float(self.value))

    def to_dict(self) -> dict[str, Any]:
        return {"date": self.date.isoformat(), "value": self.value}


@dataclass(frozen=True)
class ExposurePoint:
    """Actual and target portfolio exposure for one observation date."""

    date: date
    cash_weight: float
    gross_exposure: float
    net_exposure: float
    asset_weights: Mapping[str, float]
    target_cash_weight: float
    target_asset_weights: Mapping[str, float]

    def __post_init__(self) -> None:
        if not isinstance(self.date, date):
            raise TypeError("exposure date must be a date")
        for label in (
            "cash_weight",
            "gross_exposure",
            "net_exposure",
            "target_cash_weight",
        ):
            value = getattr(self, label)
            if (
                isinstance(value, bool)
                or not isinstance(value, (int, float))
                or not math.isfinite(float(value))
            ):
                raise ValueError(f"{label} must be finite")
            object.__setattr__(self, label, float(value))
        for label in ("asset_weights", "target_asset_weights"):
            values = getattr(self, label)
            if not isinstance(values, Mapping):
                raise TypeError(f"{label} must be a mapping")
            normalized: dict[str, float] = {}
            for symbol, value in values.items():
                if not isinstance(symbol, str) or not symbol.strip():
                    raise ValueError(f"{label} symbols must be non-empty")
                if (
                    isinstance(value, bool)
                    or not isinstance(value, (int, float))
                    or not math.isfinite(float(value))
                ):
                    raise ValueError(f"{label} values must be finite")
                normalized[symbol.strip()] = float(value)
            object.__setattr__(self, label, MappingProxyType(dict(sorted(normalized.items()))))

    def to_dict(self) -> dict[str, Any]:
        return {
            "date": self.date.isoformat(),
            "cash_weight": self.cash_weight,
            "gross_exposure": self.gross_exposure,
            "net_exposure": self.net_exposure,
            "asset_weights": dict(self.asset_weights),
            "target_cash_weight": self.target_cash_weight,
            "target_asset_weights": dict(self.target_asset_weights),
        }


@dataclass(frozen=True)
class PerformanceAnalysisResult:
    """Immutable analytics over one completed PHASE 3 backtest result."""

    backtest_run_id: str | None
    strategy_id: str | None
    strategy_version_id: str
    start_date: date
    end_date: date
    price_field_used: PriceField
    rebalance_frequency: str
    observation_frequency: str
    periods_per_year: float
    initial_capital: float
    final_equity: float
    total_return: MetricValue
    cagr: MetricValue
    annualized_volatility: MetricValue
    sharpe_ratio: MetricValue
    sortino_ratio: MetricValue
    max_drawdown: MetricValue
    max_drawdown_duration: MetricValue
    recovery_duration: MetricValue
    max_drawdown_recovered: bool
    calmar_ratio: MetricValue
    trade_metrics: TradeMetrics
    provenance: Mapping[str, Any]
    drawdown_curve: tuple[DrawdownPoint, ...] = ()
    xirr: MetricValue = field(
        default_factory=lambda: MetricValue.not_evaluable("XIRR not calculated")
    )
    twr_wealth_curve: tuple[WealthPoint, ...] = ()
    exposure_curve: tuple[ExposurePoint, ...] = ()
    exposure_summary: Mapping[str, Any] = field(default_factory=dict)
    turnover: MetricValue = field(
        default_factory=lambda: MetricValue.not_evaluable("turnover not calculated")
    )
    turnover_provenance: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        for label in ("backtest_run_id", "strategy_id"):
            value = getattr(self, label)
            if value is not None:
                if not isinstance(value, str) or not value.strip():
                    raise ValueError(f"{label} must be a non-empty string or None")
                object.__setattr__(self, label, value.strip())
        if not isinstance(self.strategy_version_id, str) or not self.strategy_version_id.strip():
            raise ValueError("strategy_version_id must not be empty")
        if not isinstance(self.start_date, date) or not isinstance(self.end_date, date):
            raise TypeError("analysis dates must be date values")
        if self.start_date > self.end_date:
            raise ValueError("start_date must be on or before end_date")
        price_field = (
            self.price_field_used
            if isinstance(self.price_field_used, PriceField)
            else PriceField(self.price_field_used)
        )
        if not isinstance(self.rebalance_frequency, str) or not self.rebalance_frequency.strip():
            raise ValueError("rebalance_frequency must not be empty")
        if (
            not isinstance(self.observation_frequency, str)
            or not self.observation_frequency.strip()
        ):
            raise ValueError("observation_frequency must not be empty")
        if (
            isinstance(self.periods_per_year, bool)
            or not isinstance(self.periods_per_year, (int, float))
            or not math.isfinite(float(self.periods_per_year))
            or self.periods_per_year <= 0
        ):
            raise ValueError("periods_per_year must be finite and positive")
        for label, value in (
            ("initial_capital", self.initial_capital),
            ("final_equity", self.final_equity),
        ):
            if (
                isinstance(value, bool)
                or not isinstance(value, (int, float))
                or not math.isfinite(float(value))
                or value < 0
            ):
                raise ValueError(f"{label} must be finite and non-negative")
        for label in (
            "total_return",
            "cagr",
            "annualized_volatility",
            "sharpe_ratio",
            "sortino_ratio",
            "max_drawdown",
            "max_drawdown_duration",
            "recovery_duration",
            "calmar_ratio",
        ):
            if not isinstance(getattr(self, label), MetricValue):
                raise TypeError(f"{label} must be a MetricValue")
        if not isinstance(self.max_drawdown_recovered, bool):
            raise TypeError("max_drawdown_recovered must be boolean")
        if not isinstance(self.trade_metrics, TradeMetrics):
            raise TypeError("trade_metrics must be TradeMetrics")
        if not isinstance(self.provenance, Mapping):
            raise TypeError("provenance must be a mapping")
        drawdown_curve = tuple(self.drawdown_curve)
        if not all(isinstance(item, DrawdownPoint) for item in drawdown_curve):
            raise TypeError("drawdown_curve must contain DrawdownPoint values")
        drawdown_dates = tuple(item.date for item in drawdown_curve)
        if drawdown_dates != tuple(sorted(drawdown_dates)) or len(drawdown_dates) != len(
            set(drawdown_dates)
        ):
            raise ValueError("drawdown_curve dates must be sorted and unique")
        if not isinstance(self.xirr, MetricValue) or not isinstance(self.turnover, MetricValue):
            raise TypeError("xirr and turnover must be MetricValue values")
        wealth_curve = tuple(self.twr_wealth_curve)
        if not all(isinstance(item, WealthPoint) for item in wealth_curve):
            raise TypeError("twr_wealth_curve must contain WealthPoint values")
        wealth_dates = tuple(item.date for item in wealth_curve)
        if wealth_dates != tuple(sorted(wealth_dates)) or len(wealth_dates) != len(
            set(wealth_dates)
        ):
            raise ValueError("twr_wealth_curve dates must be sorted and unique")
        exposure_curve = tuple(self.exposure_curve)
        if not all(isinstance(item, ExposurePoint) for item in exposure_curve):
            raise TypeError("exposure_curve must contain ExposurePoint values")
        exposure_dates = tuple(item.date for item in exposure_curve)
        if exposure_dates != tuple(sorted(exposure_dates)) or len(exposure_dates) != len(
            set(exposure_dates)
        ):
            raise ValueError("exposure_curve dates must be sorted and unique")
        for label, value in (
            ("exposure_summary", self.exposure_summary),
            ("turnover_provenance", self.turnover_provenance),
        ):
            if not isinstance(value, Mapping):
                raise TypeError(f"{label} must be a mapping")
        object.__setattr__(self, "strategy_version_id", self.strategy_version_id.strip())
        object.__setattr__(self, "price_field_used", price_field)
        object.__setattr__(self, "rebalance_frequency", self.rebalance_frequency.strip())
        object.__setattr__(self, "observation_frequency", self.observation_frequency.strip())
        object.__setattr__(self, "periods_per_year", float(self.periods_per_year))
        object.__setattr__(self, "initial_capital", float(self.initial_capital))
        object.__setattr__(self, "final_equity", float(self.final_equity))
        object.__setattr__(self, "provenance", MappingProxyType(dict(self.provenance)))
        object.__setattr__(self, "drawdown_curve", drawdown_curve)
        object.__setattr__(self, "twr_wealth_curve", wealth_curve)
        object.__setattr__(self, "exposure_curve", exposure_curve)
        object.__setattr__(self, "exposure_summary", MappingProxyType(dict(self.exposure_summary)))
        object.__setattr__(
            self, "turnover_provenance", MappingProxyType(dict(self.turnover_provenance))
        )

    def to_dict(self) -> dict[str, Any]:
        """Return a deterministic JSON-compatible result."""
        return {
            "backtest_run_id": self.backtest_run_id,
            "strategy_id": self.strategy_id,
            "strategy_version_id": self.strategy_version_id,
            "start_date": self.start_date.isoformat(),
            "end_date": self.end_date.isoformat(),
            "price_field_used": self.price_field_used.value,
            "rebalance_frequency": self.rebalance_frequency,
            "observation_frequency": self.observation_frequency,
            "periods_per_year": self.periods_per_year,
            "initial_capital": self.initial_capital,
            "final_equity": self.final_equity,
            "total_return": self.total_return.to_dict(),
            "cagr": self.cagr.to_dict(),
            "annualized_volatility": self.annualized_volatility.to_dict(),
            "sharpe_ratio": self.sharpe_ratio.to_dict(),
            "sortino_ratio": self.sortino_ratio.to_dict(),
            "max_drawdown": self.max_drawdown.to_dict(),
            "max_drawdown_duration": self.max_drawdown_duration.to_dict(),
            "recovery_duration": self.recovery_duration.to_dict(),
            "max_drawdown_recovered": self.max_drawdown_recovered,
            "calmar_ratio": self.calmar_ratio.to_dict(),
            "trade_metrics": self.trade_metrics.to_dict(),
            "provenance": dict(self.provenance),
            "drawdown_curve": [item.to_dict() for item in self.drawdown_curve],
            "xirr": self.xirr.to_dict(),
            "twr_wealth_curve": [item.to_dict() for item in self.twr_wealth_curve],
            "exposure_curve": [item.to_dict() for item in self.exposure_curve],
            "exposure_summary": dict(self.exposure_summary),
            "turnover": self.turnover.to_dict(),
            "turnover_provenance": dict(self.turnover_provenance),
        }


__all__ = [
    "DrawdownPoint",
    "ExposurePoint",
    "MetricStatus",
    "MetricValue",
    "PerformanceAnalysisResult",
    "TradeMetrics",
    "WealthPoint",
]
