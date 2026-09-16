"""Contracts for targets, orders, fills, trades, and backtest runs."""

from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import date
from enum import StrEnum
from types import MappingProxyType
from typing import Any

from data.models import PriceField
from portfolio.models import PortfolioSnapshot

ENGINE_VERSION = "phase-3.0"


class OrderSide(StrEnum):
    BUY = "BUY"
    SELL = "SELL"


class OrderStatus(StrEnum):
    FILLED = "filled"


class ExecutionRule(StrEnum):
    NEXT_TRADING_DAY_OPEN = "next_trading_day_open"


class RebalanceFrequency(StrEnum):
    DAILY = "daily"
    WEEKLY = "weekly"
    MONTHLY = "monthly"
    ON_SIGNAL_CHANGE = "on_signal_change"


@dataclass(frozen=True)
class CommissionPolicy:
    """Commission as a proportional rate plus optional per-order fee."""

    rate: float = 0.0
    per_order: float = 0.0

    def __post_init__(self) -> None:
        if not math.isfinite(self.rate) or self.rate < 0:
            raise ValueError("commission rate must be finite and non-negative")
        if not math.isfinite(self.per_order) or self.per_order < 0:
            raise ValueError("commission per_order must be finite and non-negative")


@dataclass(frozen=True)
class RebalancePolicy:
    """Frequency and optional absolute weight threshold."""

    frequency: RebalanceFrequency = RebalanceFrequency.DAILY
    threshold: float | None = None

    def __post_init__(self) -> None:
        if self.threshold is not None and (
            not math.isfinite(self.threshold) or self.threshold < 0
        ):
            raise ValueError("rebalance threshold must be finite and non-negative")


@dataclass(frozen=True)
class TargetWeight:
    """One asset target within a dated target allocation."""

    symbol: str
    target_weight: float

    def __post_init__(self) -> None:
        normalized = self.symbol.strip().upper()
        if not normalized:
            raise ValueError("target symbol must not be empty")
        if not math.isfinite(self.target_weight) or self.target_weight < 0:
            raise ValueError("target_weight must be finite and non-negative")
        object.__setattr__(self, "symbol", normalized)


@dataclass(frozen=True)
class TargetAllocation:
    """Target weights produced by a strategy for one signal date."""

    date: date
    weights: tuple[TargetWeight, ...]

    def __post_init__(self) -> None:
        symbols = [item.symbol for item in self.weights]
        if len(symbols) != len(set(symbols)):
            raise ValueError("target allocation cannot contain duplicate symbols")
        total = sum(item.target_weight for item in self.weights)
        if total > 1.0 + 1e-12:
            raise ValueError("target allocation weights must sum to at most 1")

    @classmethod
    def from_weights(cls, as_of_date: date, weights: Mapping[str, float]) -> TargetAllocation:
        return cls(
            date=as_of_date,
            weights=tuple(
                TargetWeight(symbol=symbol, target_weight=value)
                for symbol, value in sorted(weights.items())
            ),
        )

    def weight_for(self, symbol: str) -> float:
        normalized = symbol.strip().upper()
        return next(
            (item.target_weight for item in self.weights if item.symbol == normalized),
            0.0,
        )

    def as_mapping(self) -> Mapping[str, float]:
        return MappingProxyType({item.symbol: item.target_weight for item in self.weights})


@dataclass(frozen=True)
class Order:
    """Auditable order and its deterministic fill details."""

    order_id: str
    signal_date: date
    date: date
    symbol: str
    side: OrderSide
    quantity: int
    requested_price: float
    execution_price: float
    status: OrderStatus
    commission: float
    slippage: float
    target_weight: float


@dataclass(frozen=True)
class Fill:
    """A filled order separated from the order intent."""

    order_id: str
    date: date
    symbol: str
    side: OrderSide
    quantity: int
    price: float
    commission: float
    slippage: float
    cash_effect: float


@dataclass(frozen=True)
class Trade:
    """A completed round trip, aggregated per exit fill."""

    symbol: str
    entry_date: date
    exit_date: date
    entry_price: float
    exit_price: float
    quantity: int
    pnl: float
    pnl_pct: float
    holding_period: int


@dataclass(frozen=True)
class EquityPoint:
    date: date
    cash: float
    asset_values: Mapping[str, float]
    total_equity: float


@dataclass(frozen=True)
class AllocationPoint:
    date: date
    symbol: str
    target_weight: float
    actual_weight: float


@dataclass(frozen=True)
class BacktestConfig:
    """All result-affecting settings for one reproducible run."""

    strategy_version_id: str
    start_date: date
    end_date: date
    initial_capital: float
    price_field_used: PriceField
    commission: CommissionPolicy = field(default_factory=CommissionPolicy)
    slippage: float = 0.0
    execution_rule: ExecutionRule = ExecutionRule.NEXT_TRADING_DAY_OPEN
    rebalance_policy: RebalancePolicy = field(default_factory=RebalancePolicy)
    fractional_shares: bool = False

    def __post_init__(self) -> None:
        if not self.strategy_version_id.strip():
            raise ValueError("strategy_version_id must not be empty")
        if self.start_date > self.end_date:
            raise ValueError("start_date must be on or before end_date")
        if not math.isfinite(self.initial_capital) or self.initial_capital <= 0:
            raise ValueError("initial_capital must be finite and positive")
        if not math.isfinite(self.slippage) or not 0 <= self.slippage < 1:
            raise ValueError("slippage must be finite and in [0, 1)")
        if self.fractional_shares:
            raise ValueError("PHASE 3 supports integer shares only")

    def snapshot(
        self,
        data_snapshot_reference: Mapping[str, Any],
        *,
        effective_start_date: date | None = None,
        effective_end_date: date | None = None,
    ) -> dict[str, Any]:
        snapshot = {
            "strategy_version_id": self.strategy_version_id,
            "start_date": self.start_date.isoformat(),
            "end_date": self.end_date.isoformat(),
            "initial_capital": self.initial_capital,
            "price_field_used": self.price_field_used.value,
            "commission": {
                "rate": self.commission.rate,
                "per_order": self.commission.per_order,
            },
            "slippage": self.slippage,
            "execution_rule": self.execution_rule.value,
            "rebalance_policy": {
                "frequency": self.rebalance_policy.frequency.value,
                "threshold": self.rebalance_policy.threshold,
            },
            "fractional_shares": self.fractional_shares,
            "data_snapshot_reference": dict(data_snapshot_reference),
            "engine_version": ENGINE_VERSION,
        }
        if effective_start_date is not None:
            snapshot["effective_start_date"] = effective_start_date.isoformat()
        if effective_end_date is not None:
            snapshot["effective_end_date"] = effective_end_date.isoformat()
        return snapshot


@dataclass(frozen=True)
class BacktestResult:
    """Complete, reproducible PHASE 3 result without performance analytics."""

    start_date: date
    end_date: date
    initial_capital: float
    final_equity: float
    equity_curve: tuple[EquityPoint, ...]
    orders: tuple[Order, ...]
    fills: tuple[Fill, ...]
    trades: tuple[Trade, ...]
    positions: tuple[PortfolioSnapshot, ...]
    allocation_history: tuple[AllocationPoint, ...]
    cash_history: tuple[tuple[date, float], ...]
    strategy_version_id: str
    configuration_snapshot: Mapping[str, Any]
    data_snapshot_reference: Mapping[str, Any]
    engine_version: str = ENGINE_VERSION
    requested_start_date: date | None = None
    requested_end_date: date | None = None
    effective_start_date: date | None = None
    effective_end_date: date | None = None

    def __post_init__(self) -> None:
        requested_start = self.requested_start_date or self.start_date
        requested_end = self.requested_end_date or self.end_date
        requested_bounds = (
            ("requested_start_date", requested_start),
            ("requested_end_date", requested_end),
        )
        for label, value in requested_bounds:
            if not isinstance(value, date):
                raise TypeError(f"{label} must be a date")
        if not isinstance(self.start_date, date) or not isinstance(self.end_date, date):
            raise TypeError("BacktestResult bounds must be date values")
        object.__setattr__(self, "requested_start_date", requested_start)
        object.__setattr__(self, "requested_end_date", requested_end)
        object.__setattr__(self, "effective_start_date", self.start_date)
        object.__setattr__(self, "effective_end_date", self.end_date)
