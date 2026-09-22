"""Canonical wealth projections over an immutable backtest result."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from backtest.models import BacktestResult


@dataclass(frozen=True)
class WealthProjectionPoint:
    """One canonical valuation observation with its external-capital context."""

    date: str
    portfolio_value: float
    capital_invested: float
    investment_profit: float

    def series_point(self, field: str) -> dict[str, Any]:
        return {"date": self.date, "value": getattr(self, field)}


def project_wealth(result: BacktestResult) -> tuple[WealthProjectionPoint, ...]:
    """Project value, invested capital, and dollar profit on canonical valuation dates."""
    if not isinstance(result, BacktestResult):
        raise TypeError("result must be a BacktestResult")

    contributions = sorted(
        result.contribution_events,
        key=lambda item: (item.effective_date, item.requested_date, item.amount),
    )
    contribution_index = 0
    capital = Decimal(str(result.initial_capital))
    points: list[WealthProjectionPoint] = []
    for equity in result.equity_curve:
        while (
            contribution_index < len(contributions)
            and contributions[contribution_index].effective_date <= equity.date
        ):
            capital += contributions[contribution_index].amount
            contribution_index += 1
        portfolio_value = float(equity.total_equity)
        capital_invested = float(capital)
        points.append(
            WealthProjectionPoint(
                date=equity.date.isoformat(),
                portfolio_value=portfolio_value,
                capital_invested=capital_invested,
                investment_profit=portfolio_value - capital_invested,
            )
        )
    return tuple(points)


def wealth_series_points(result: BacktestResult, field: str) -> tuple[dict[str, Any], ...]:
    """Return one allowlisted canonical wealth series without interpolation or alignment."""
    if field not in {"portfolio_value", "capital_invested", "investment_profit"}:
        raise ValueError("unsupported wealth projection field")
    return tuple(point.series_point(field) for point in project_wealth(result))


__all__ = ["WealthProjectionPoint", "project_wealth", "wealth_series_points"]
