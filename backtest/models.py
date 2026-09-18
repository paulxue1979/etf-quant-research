"""Contracts for targets, orders, fills, trades, and backtest runs."""

from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal, InvalidOperation
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


class ContributionFrequency(StrEnum):
    ONE_TIME = "one_time"
    MONTHLY = "monthly"


class RebalanceCause(StrEnum):
    TARGET = "target"
    CONTRIBUTION = "contribution"


class HoldingStatus(StrEnum):
    OPEN = "OPEN"
    CLOSED = "CLOSED"


def _positive_decimal(value: object, label: str) -> Decimal:
    if isinstance(value, bool):
        raise ValueError(f"{label} must be a positive finite decimal")
    try:
        result = Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise ValueError(f"{label} must be a positive finite decimal") from exc
    if not result.is_finite() or result <= 0:
        raise ValueError(f"{label} must be a positive finite decimal")
    return result.normalize()


def canonical_decimal(value: Decimal) -> str:
    normalized = value.normalize()
    text = format(normalized, "f")
    return text.rstrip("0").rstrip(".") if "." in text else text


@dataclass(frozen=True)
class ContributionSchedule:
    frequency: ContributionFrequency
    amount: Decimal | str | int | float
    requested_date: date | None = None
    currency: str = "USD"

    def __post_init__(self) -> None:
        try:
            frequency = (
                self.frequency
                if isinstance(self.frequency, ContributionFrequency)
                else ContributionFrequency(self.frequency)
            )
        except (TypeError, ValueError) as exc:
            raise ValueError("contribution frequency is invalid") from exc
        amount = _positive_decimal(self.amount, "contribution amount")
        if frequency is ContributionFrequency.ONE_TIME:
            if not isinstance(self.requested_date, date):
                raise ValueError("one-time contribution requested_date is required")
        elif self.requested_date is not None:
            raise ValueError("monthly contribution requested_date must be omitted")
        currency = self.currency.strip().upper() if isinstance(self.currency, str) else ""
        if currency != "USD":
            raise ValueError("contribution currency must be USD")
        object.__setattr__(self, "frequency", frequency)
        object.__setattr__(self, "amount", amount)
        object.__setattr__(self, "currency", currency)

    def to_dict(self) -> dict[str, Any]:
        return {
            "frequency": self.frequency.value,
            "amount": canonical_decimal(self.amount),
            "requested_date": (
                self.requested_date.isoformat() if self.requested_date is not None else None
            ),
            "currency": self.currency,
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> ContributionSchedule:
        requested_date = payload.get("requested_date")
        return cls(
            frequency=ContributionFrequency(str(payload.get("frequency", ""))),
            amount=payload.get("amount"),
            requested_date=(
                date.fromisoformat(str(requested_date)) if requested_date is not None else None
            ),
            currency=str(payload.get("currency", "USD")),
        )


@dataclass(frozen=True)
class ContributionEvent:
    frequency: ContributionFrequency
    amount: Decimal
    requested_date: date
    effective_date: date
    currency: str = "USD"

    def __post_init__(self) -> None:
        object.__setattr__(self, "frequency", ContributionFrequency(self.frequency))
        object.__setattr__(self, "amount", _positive_decimal(self.amount, "contribution amount"))
        if not isinstance(self.requested_date, date) or not isinstance(self.effective_date, date):
            raise TypeError("contribution dates must be date values")
        if self.effective_date < self.requested_date:
            raise ValueError("effective contribution date cannot precede requested date")
        if self.currency != "USD":
            raise ValueError("contribution currency must be USD")


@dataclass(frozen=True)
class ExternalCashFlow:
    date: date
    amount: Decimal
    currency: str = "USD"
    source: str = "contribution"

    def __post_init__(self) -> None:
        if not isinstance(self.date, date):
            raise TypeError("external cash flow date must be a date")
        object.__setattr__(self, "amount", _positive_decimal(self.amount, "cash flow amount"))
        if self.currency != "USD" or self.source != "contribution":
            raise ValueError("only USD contribution cash flows are supported")


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
        if self.threshold is not None and (not math.isfinite(self.threshold) or self.threshold < 0):
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
    rebalance_cause: RebalanceCause = RebalanceCause.TARGET


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
class HoldingSegment:
    """One immutable FIFO lot fragment from real entry execution to exit or report end."""

    holding_id: str
    lot_id: str
    symbol: str
    quantity: int
    entry_order_id: str
    entry_signal_date: date | None
    entry_execution_date: date
    entry_price: float
    entry_execution_cause: RebalanceCause
    status: HoldingStatus
    holding_days: int
    exit_order_id: str | None = None
    exit_signal_date: date | None = None
    exit_execution_date: date | None = None
    exit_price: float | None = None
    exit_execution_cause: RebalanceCause | None = None
    report_end_date: date | None = None
    ending_price: float | None = None
    market_value: float | None = None
    realized_pnl: float | None = None
    unrealized_pnl: float | None = None
    holding_return: float | None = None

    def __post_init__(self) -> None:
        for label in ("holding_id", "lot_id", "entry_order_id"):
            value = getattr(self, label)
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"{label} must be non-empty")
            object.__setattr__(self, label, value.strip())
        symbol = self.symbol.strip().upper() if isinstance(self.symbol, str) else ""
        if not symbol or symbol == "CASH":
            raise ValueError("holding symbol must identify a non-cash security")
        if (
            not isinstance(self.quantity, int)
            or isinstance(self.quantity, bool)
            or self.quantity <= 0
        ):
            raise ValueError("holding quantity must be a positive integer")
        if not isinstance(self.entry_execution_date, date):
            raise TypeError("entry_execution_date must be a date")
        if self.entry_signal_date is not None and not isinstance(self.entry_signal_date, date):
            raise TypeError("entry_signal_date must be a date or None")
        cause = RebalanceCause(self.entry_execution_cause)
        status = HoldingStatus(self.status)
        if cause is RebalanceCause.TARGET and self.entry_signal_date is None:
            raise ValueError("target-caused holding entry requires a signal date")
        if cause is RebalanceCause.CONTRIBUTION and self.entry_signal_date is not None:
            raise ValueError("contribution-caused holding entry must not claim a strategy signal")
        if not math.isfinite(self.entry_price) or self.entry_price <= 0:
            raise ValueError("entry_price must be finite and positive")
        if not isinstance(self.holding_days, int) or self.holding_days < 0:
            raise ValueError("holding_days must be a non-negative integer")
        if self.holding_return is None:
            raise ValueError("holding_return is required")
        object.__setattr__(self, "symbol", symbol)
        object.__setattr__(self, "entry_execution_cause", cause)
        object.__setattr__(self, "status", status)
        self._validate_status_fields()
        for label in (
            "exit_price",
            "ending_price",
            "market_value",
            "realized_pnl",
            "unrealized_pnl",
            "holding_return",
        ):
            value = getattr(self, label)
            if value is not None and not math.isfinite(value):
                raise ValueError(f"{label} must be finite when available")

    def _validate_status_fields(self) -> None:
        if self.status is HoldingStatus.CLOSED:
            if (
                not isinstance(self.exit_order_id, str)
                or not self.exit_order_id.strip()
                or not isinstance(self.exit_execution_date, date)
            ):
                raise ValueError("closed holding requires exit order and execution date")
            object.__setattr__(self, "exit_order_id", self.exit_order_id.strip())
            if self.exit_signal_date is not None and not isinstance(self.exit_signal_date, date):
                raise TypeError("exit_signal_date must be a date or None")
            if self.exit_execution_date < self.entry_execution_date:
                raise ValueError("holding exit cannot precede entry")
            if self.exit_price is None or self.exit_price <= 0:
                raise ValueError("closed holding requires a positive exit_price")
            if self.exit_execution_cause is None or self.realized_pnl is None:
                raise ValueError("closed holding requires exit cause and realized_pnl")
            if any(
                value is not None
                for value in (
                    self.report_end_date,
                    self.ending_price,
                    self.market_value,
                    self.unrealized_pnl,
                )
            ):
                raise ValueError("closed holding cannot contain open valuation fields")
            expected_days = (self.exit_execution_date - self.entry_execution_date).days
            object.__setattr__(
                self,
                "exit_execution_cause",
                RebalanceCause(self.exit_execution_cause),
            )
            if self.exit_execution_cause is RebalanceCause.TARGET and self.exit_signal_date is None:
                raise ValueError("target-caused holding exit requires a signal date")
            if (
                self.exit_execution_cause is RebalanceCause.CONTRIBUTION
                and self.exit_signal_date is not None
            ):
                raise ValueError(
                    "contribution-caused holding exit must not claim a strategy signal"
                )
        else:
            if any(
                value is not None
                for value in (
                    self.exit_order_id,
                    self.exit_signal_date,
                    self.exit_execution_date,
                    self.exit_price,
                    self.exit_execution_cause,
                    self.realized_pnl,
                )
            ):
                raise ValueError("open holding cannot contain exit or realized fields")
            if not isinstance(self.report_end_date, date):
                raise ValueError("open holding requires report_end_date")
            if self.report_end_date < self.entry_execution_date:
                raise ValueError("report end cannot precede holding entry")
            if self.ending_price is None or self.ending_price <= 0:
                raise ValueError("open holding requires a positive ending_price")
            if self.market_value is None or self.market_value < 0:
                raise ValueError("open holding requires non-negative market_value")
            if self.unrealized_pnl is None:
                raise ValueError("open holding requires unrealized_pnl")
            expected_days = (self.report_end_date - self.entry_execution_date).days
        if self.holding_days != expected_days:
            raise ValueError("holding_days must use calendar days from execution dates")


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
    contribution_schedule: ContributionSchedule | None = None

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
        if self.contribution_schedule is not None and not isinstance(
            self.contribution_schedule, ContributionSchedule
        ):
            raise TypeError("contribution_schedule must be a ContributionSchedule or None")

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
            "contribution_schedule": (
                self.contribution_schedule.to_dict()
                if self.contribution_schedule is not None
                else None
            ),
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
    contribution_events: tuple[ContributionEvent, ...] = ()
    external_cash_flows: tuple[ExternalCashFlow, ...] = ()
    cumulative_contributions: float = 0.0
    total_capital_invested: float | None = None
    investment_profit: float | None = None
    holding_segments: tuple[HoldingSegment, ...] | None = None

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
        contributions = tuple(self.contribution_events)
        flows = tuple(self.external_cash_flows)
        if not all(isinstance(item, ContributionEvent) for item in contributions):
            raise TypeError("contribution_events must contain ContributionEvent values")
        if not all(isinstance(item, ExternalCashFlow) for item in flows):
            raise TypeError("external_cash_flows must contain ExternalCashFlow values")
        holdings = None if self.holding_segments is None else tuple(self.holding_segments)
        if holdings is not None and not all(isinstance(item, HoldingSegment) for item in holdings):
            raise TypeError("holding_segments must contain HoldingSegment values")
        cumulative = float(sum((item.amount for item in contributions), Decimal("0")))
        total_invested = (
            self.initial_capital + cumulative
            if self.total_capital_invested is None
            else float(self.total_capital_invested)
        )
        profit = (
            self.final_equity - total_invested
            if self.investment_profit is None
            else float(self.investment_profit)
        )
        if not math.isclose(float(self.cumulative_contributions), cumulative, abs_tol=1e-9):
            raise ValueError("cumulative_contributions does not match contribution_events")
        object.__setattr__(self, "contribution_events", contributions)
        object.__setattr__(self, "external_cash_flows", flows)
        object.__setattr__(self, "holding_segments", holdings)
        object.__setattr__(self, "cumulative_contributions", cumulative)
        object.__setattr__(self, "total_capital_invested", total_invested)
        object.__setattr__(self, "investment_profit", profit)
