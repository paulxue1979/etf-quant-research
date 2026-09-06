"""Immutable portfolio accounting models."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date


@dataclass(frozen=True)
class Position:
    """One marked-to-market asset position."""

    symbol: str
    quantity: int
    average_cost: float
    market_price: float
    market_value: float
    unrealized_pnl: float
    as_of_date: date


@dataclass(frozen=True)
class PortfolioSnapshot:
    """End-of-day portfolio state."""

    as_of_date: date
    cash: float
    positions: tuple[Position, ...]
    total_equity: float
