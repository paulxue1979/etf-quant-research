"""Pure performance analytics over completed PHASE 3 backtest results."""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from typing import Any

from analytics.exceptions import AnalyticsInputError
from analytics.models import (
    DrawdownPoint,
    ExposurePoint,
    MetricValue,
    PerformanceAnalysisResult,
    TradeMetrics,
    WealthPoint,
)
from backtest.models import BacktestResult, EquityPoint, RebalanceCause, Trade
from data.models import PriceField

_DAYS_PER_YEAR = 365.0
_EQUITY_TOLERANCE = 1e-9


@dataclass(frozen=True)
class PerformanceAnalyticsConfig:
    """Explicit annualization and hurdle-rate conventions for analytics."""

    risk_free_rate: float = 0.0
    minimum_acceptable_return: float = 0.0
    periods_per_year: float = 252.0
    observation_frequency: str = "daily"

    def __post_init__(self) -> None:
        for label, value in (
            ("risk_free_rate", self.risk_free_rate),
            ("minimum_acceptable_return", self.minimum_acceptable_return),
        ):
            if (
                isinstance(value, bool)
                or not isinstance(value, (int, float))
                or not math.isfinite(float(value))
                or value <= -1
            ):
                raise ValueError(f"{label} must be finite and greater than -1")
        if (
            isinstance(self.periods_per_year, bool)
            or not isinstance(self.periods_per_year, (int, float))
            or not math.isfinite(float(self.periods_per_year))
            or self.periods_per_year <= 0
        ):
            raise ValueError("periods_per_year must be finite and positive")
        if (
            not isinstance(self.observation_frequency, str)
            or not self.observation_frequency.strip()
        ):
            raise ValueError("observation_frequency must not be empty")
        object.__setattr__(self, "risk_free_rate", float(self.risk_free_rate))
        object.__setattr__(self, "minimum_acceptable_return", float(self.minimum_acceptable_return))
        object.__setattr__(self, "periods_per_year", float(self.periods_per_year))
        object.__setattr__(self, "observation_frequency", self.observation_frequency.strip())


def analyze_backtest(
    backtest: BacktestResult,
    *,
    backtest_run_id: str | None = None,
    strategy_id: str | None = None,
    config: PerformanceAnalyticsConfig | None = None,
) -> PerformanceAnalysisResult:
    """Analyze a completed BacktestResult without executing or mutating anything."""
    _validate_backtest(backtest)
    if config is not None and not isinstance(config, PerformanceAnalyticsConfig):
        raise TypeError("config must be a PerformanceAnalyticsConfig or None")
    analytics_config = config or PerformanceAnalyticsConfig()
    snapshot = backtest.configuration_snapshot
    price_field = _snapshot_price_field(snapshot)
    rebalance_frequency = _snapshot_rebalance_frequency(snapshot)
    dates = tuple(point.date for point in backtest.equity_curve)
    wealth_index = _flow_adjusted_wealth(backtest)
    returns = _periodic_returns(wealth_index)
    risk_free_period = _annual_to_periodic(analytics_config.risk_free_rate, analytics_config)
    mar_period = _annual_to_periodic(analytics_config.minimum_acceptable_return, analytics_config)
    total_return = MetricValue.available(wealth_index[-1] - 1.0)
    cagr = _cagr(1.0, wealth_index[-1], dates[0], dates[-1])
    volatility = _annualized_volatility(returns, analytics_config.periods_per_year)
    sharpe = _sharpe(returns, risk_free_period, analytics_config.periods_per_year)
    sortino = _sortino(returns, mar_period, analytics_config.periods_per_year)
    drawdown = _drawdown_metrics(wealth_index)
    calmar = _calmar(cagr, drawdown["max_drawdown"])
    trades = _trade_metrics(backtest.trades)
    twr_curve = tuple(
        WealthPoint(point_date, value)
        for point_date, value in zip(dates, wealth_index, strict=True)
    )
    exposure_curve, exposure_summary = _exposure_analytics(backtest)
    turnover, turnover_provenance = _turnover_analytics(backtest)
    xirr = _xirr(backtest)
    provenance = {
        "source": "BacktestResult",
        "requested_start_date": backtest.requested_start_date.isoformat(),
        "requested_end_date": backtest.requested_end_date.isoformat(),
        "effective_start_date": backtest.effective_start_date.isoformat(),
        "effective_end_date": backtest.effective_end_date.isoformat(),
        "equity_source": "equity_curve.total_equity",
        "return_source": "time-weighted portfolio returns adjusted for external cash flows",
        "external_cash_flow_source": "BacktestResult.external_cash_flows",
        "trade_source": "BacktestResult.trades",
        "risk_free_rate_annual": analytics_config.risk_free_rate,
        "minimum_acceptable_return_annual": analytics_config.minimum_acceptable_return,
        "sample_standard_deviation": True,
        "cagr_year_basis_days": _DAYS_PER_YEAR,
        "drawdown_duration_unit": "trading_periods",
        "recovery_duration_unit": "trading_periods",
        "average_holding_period_source": "Trade.holding_period",
        "average_holding_period_unit": "Trade.holding_period units (PHASE 3 calendar days)",
        "turnover_source": "BacktestResult.fills joined to BacktestResult.orders",
        "twr_wealth_source": "flow-adjusted geometric wealth factors",
        "xirr_source": "initial capital and BacktestResult.external_cash_flows",
        "exposure_source": "BacktestResult.equity_curve and allocation_history",
    }
    return PerformanceAnalysisResult(
        backtest_run_id=backtest_run_id,
        strategy_id=strategy_id,
        strategy_version_id=backtest.strategy_version_id,
        start_date=backtest.start_date,
        end_date=backtest.end_date,
        price_field_used=price_field,
        rebalance_frequency=rebalance_frequency,
        observation_frequency=analytics_config.observation_frequency,
        periods_per_year=analytics_config.periods_per_year,
        initial_capital=backtest.initial_capital,
        final_equity=backtest.final_equity,
        total_return=total_return,
        cagr=cagr,
        annualized_volatility=volatility,
        sharpe_ratio=sharpe,
        sortino_ratio=sortino,
        max_drawdown=drawdown["max_drawdown"],
        max_drawdown_duration=drawdown["max_drawdown_duration"],
        recovery_duration=drawdown["recovery_duration"],
        max_drawdown_recovered=drawdown["max_drawdown_recovered"],
        calmar_ratio=calmar,
        trade_metrics=trades,
        provenance=provenance,
        drawdown_curve=tuple(
            DrawdownPoint(point_date, value)
            for point_date, value in zip(dates, _drawdown_curve(wealth_index), strict=True)
        ),
        xirr=xirr,
        twr_wealth_curve=twr_curve,
        exposure_curve=exposure_curve,
        exposure_summary=exposure_summary,
        turnover=turnover,
        turnover_provenance=turnover_provenance,
    )


def _validate_backtest(backtest: BacktestResult) -> None:
    if not isinstance(backtest, BacktestResult):
        raise AnalyticsInputError("backtest must be a BacktestResult")
    if (
        not isinstance(backtest.strategy_version_id, str)
        or not backtest.strategy_version_id.strip()
    ):
        raise AnalyticsInputError("strategy_version_id must not be empty")
    if not isinstance(backtest.start_date, date) or not isinstance(backtest.end_date, date):
        raise AnalyticsInputError("backtest dates must be date values")
    if backtest.start_date > backtest.end_date:
        raise AnalyticsInputError("backtest start_date must be on or before end_date")
    _finite_non_negative(backtest.initial_capital, "initial_capital", positive=True)
    _finite_non_negative(backtest.final_equity, "final_equity")
    if not isinstance(backtest.equity_curve, tuple) or not backtest.equity_curve:
        raise AnalyticsInputError("equity_curve must be a non-empty tuple")
    if not isinstance(backtest.trades, tuple):
        raise AnalyticsInputError("trades must be an immutable tuple")
    if not all(isinstance(point, EquityPoint) for point in backtest.equity_curve):
        raise AnalyticsInputError("equity_curve must contain EquityPoint values")
    dates = tuple(point.date for point in backtest.equity_curve)
    if dates != tuple(sorted(dates)) or len(dates) != len(set(dates)):
        raise AnalyticsInputError("equity_curve dates must be sorted and unique")
    if dates[0] != backtest.start_date or dates[-1] != backtest.end_date:
        raise AnalyticsInputError("equity_curve must cover the BacktestResult date bounds")
    for point in backtest.equity_curve:
        if not isinstance(point.asset_values, Mapping):
            raise AnalyticsInputError("equity point asset_values must be a mapping")
        _finite_non_negative(point.cash, "equity point cash")
        _finite_non_negative(point.total_equity, "equity point total_equity")
        asset_total = 0.0
        for symbol, value in point.asset_values.items():
            if not isinstance(symbol, str) or not symbol.strip():
                raise AnalyticsInputError("equity point asset symbols must be non-empty")
            _finite_non_negative(value, f"equity point asset value for {symbol}")
            asset_total += float(value)
        if not math.isclose(
            float(point.total_equity), float(point.cash) + asset_total, abs_tol=_EQUITY_TOLERANCE
        ):
            raise AnalyticsInputError("equity point accounting identity is inconsistent")
    first_day_flow = math.fsum(
        float(item.amount)
        for item in backtest.external_cash_flows
        if item.date == backtest.equity_curve[0].date
    )
    if not math.isclose(
        float(backtest.initial_capital) + first_day_flow,
        float(backtest.equity_curve[0].total_equity),
        abs_tol=_EQUITY_TOLERANCE,
    ):
        raise AnalyticsInputError(
            "initial_capital must match the first equity point after first-day external flow"
        )
    if not math.isclose(
        float(backtest.final_equity),
        float(backtest.equity_curve[-1].total_equity),
        abs_tol=_EQUITY_TOLERANCE,
    ):
        raise AnalyticsInputError("final_equity must match the last equity point")
    for trade in backtest.trades:
        _validate_trade(trade)


def _validate_trade(trade: Trade) -> None:
    if not isinstance(trade, Trade):
        raise AnalyticsInputError("trades must contain Trade values")
    if not isinstance(trade.entry_date, date) or not isinstance(trade.exit_date, date):
        raise AnalyticsInputError("trade dates must be date values")
    if trade.exit_date < trade.entry_date:
        raise AnalyticsInputError("trade exit_date must not precede entry_date")
    if isinstance(trade.holding_period, bool) or not isinstance(trade.holding_period, int):
        raise AnalyticsInputError("trade holding_period must be an integer")
    if trade.holding_period < 0:
        raise AnalyticsInputError("trade holding_period must be non-negative")
    if trade.holding_period != (trade.exit_date - trade.entry_date).days:
        raise AnalyticsInputError("trade holding_period must match its entry and exit dates")
    for label, value in (("pnl", trade.pnl), ("pnl_pct", trade.pnl_pct)):
        if (
            isinstance(value, bool)
            or not isinstance(value, (int, float))
            or not math.isfinite(float(value))
        ):
            raise AnalyticsInputError(f"trade {label} must be finite")


def _snapshot_price_field(snapshot: Mapping[str, Any]) -> PriceField:
    try:
        return PriceField(snapshot["price_field_used"])
    except (KeyError, TypeError, ValueError) as exc:
        raise AnalyticsInputError("configuration_snapshot lacks a valid price_field_used") from exc


def _snapshot_rebalance_frequency(snapshot: Mapping[str, Any]) -> str:
    try:
        frequency = snapshot["rebalance_policy"]["frequency"]
    except (KeyError, TypeError) as exc:
        raise AnalyticsInputError(
            "configuration_snapshot lacks a valid rebalance frequency"
        ) from exc
    if not isinstance(frequency, str) or not frequency.strip():
        raise AnalyticsInputError("rebalance frequency must be a non-empty string")
    return frequency.strip()


def _finite_non_negative(value: object, label: str, *, positive: bool = False) -> None:
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not math.isfinite(float(value))
        or value < 0
        or (positive and value <= 0)
    ):
        suffix = " positive" if positive else " non-negative"
        raise AnalyticsInputError(f"{label} must be finite and{suffix}")


def _periodic_returns(equities: Sequence[float]) -> tuple[float, ...]:
    values: list[float] = []
    for previous, current in zip(equities, equities[1:]):
        if previous <= 0:
            raise AnalyticsInputError("equity curve requires positive prior equity for returns")
        value = current / previous - 1.0
        if not math.isfinite(value):
            raise AnalyticsInputError("periodic return is not finite")
        values.append(value)
    return tuple(values)


def _flow_adjusted_wealth(backtest: BacktestResult) -> tuple[float, ...]:
    flows: dict[date, float] = {}
    for item in backtest.external_cash_flows:
        flows[item.date] = flows.get(item.date, 0.0) + float(item.amount)
    wealth = 1.0
    values: list[float] = []
    previous_equity: float | None = None
    for point in backtest.equity_curve:
        flow = flows.get(point.date, 0.0)
        denominator = (
            backtest.initial_capital + flow if previous_equity is None else previous_equity + flow
        )
        if denominator <= 0:
            raise AnalyticsInputError("cash-flow adjusted return requires positive capital")
        factor = point.total_equity / denominator
        if not math.isfinite(factor) or factor < 0:
            raise AnalyticsInputError("cash-flow adjusted return is invalid")
        wealth *= factor
        values.append(wealth)
        previous_equity = point.total_equity
    return tuple(values)


def flow_adjusted_wealth_curve(backtest: BacktestResult) -> tuple[WealthPoint, ...]:
    """Return the canonical normalized wealth curve used by all TWR metrics."""
    _validate_backtest(backtest)
    return tuple(
        WealthPoint(point.date, value)
        for point, value in zip(backtest.equity_curve, _flow_adjusted_wealth(backtest), strict=True)
    )


def _xirr(backtest: BacktestResult) -> MetricValue:
    cash_flows: list[tuple[date, float]] = [
        (backtest.effective_start_date, -backtest.initial_capital)
    ]
    cash_flows.extend(
        (item.date, -float(item.amount))
        for item in backtest.external_cash_flows
        if float(item.amount) != 0
    )
    cash_flows.append((backtest.effective_end_date, float(backtest.final_equity)))
    if (
        len(cash_flows) < 2
        or not any(amount < 0 for _, amount in cash_flows)
        or not any(amount > 0 for _, amount in cash_flows)
    ):
        return MetricValue.not_evaluable(
            "XIRR requires both negative and positive dated cash flows"
        )
    origin = cash_flows[0][0]

    def npv(rate: float) -> float:
        if rate <= -1:
            return math.inf
        return math.fsum(
            amount / (1.0 + rate) ** ((flow_date - origin).days / _DAYS_PER_YEAR)
            for flow_date, amount in cash_flows
        )

    lower = -0.999999999
    lower_value = npv(lower)
    upper = 1.0
    upper_value = npv(upper)
    for _ in range(64):
        if math.isfinite(upper_value) and lower_value * upper_value <= 0:
            break
        upper = upper * 2.0 + 1.0
        upper_value = npv(upper)
    else:
        return MetricValue.not_evaluable("XIRR has no bounded sign change")
    if not math.isfinite(lower_value) or not math.isfinite(upper_value):
        return MetricValue.not_evaluable("XIRR cash flows are not finite")
    for _ in range(200):
        midpoint = (lower + upper) / 2.0
        midpoint_value = npv(midpoint)
        if not math.isfinite(midpoint_value):
            return MetricValue.not_evaluable("XIRR calculation is not finite")
        if abs(midpoint_value) < 1e-10 or abs(upper - lower) < 1e-12:
            return MetricValue.available(midpoint)
        if lower_value * midpoint_value <= 0:
            upper, upper_value = midpoint, midpoint_value
        else:
            lower, lower_value = midpoint, midpoint_value
    return MetricValue.not_evaluable("XIRR solver did not converge")


def _exposure_analytics(
    backtest: BacktestResult,
) -> tuple[tuple[ExposurePoint, ...], dict[str, float | str]]:
    targets: dict[date, dict[str, float]] = {}
    for item in backtest.allocation_history:
        targets.setdefault(item.date, {})[item.symbol] = float(item.target_weight)
    points: list[ExposurePoint] = []
    for equity in backtest.equity_curve:
        if equity.total_equity <= 0:
            raise AnalyticsInputError("exposure analytics requires positive equity")
        asset_weights = {
            symbol: float(value) / equity.total_equity
            for symbol, value in equity.asset_values.items()
        }
        target_asset_weights = dict(targets.get(equity.date, {}))
        target_cash = 1.0 - math.fsum(target_asset_weights.values())
        points.append(
            ExposurePoint(
                date=equity.date,
                cash_weight=float(equity.cash) / equity.total_equity,
                gross_exposure=math.fsum(abs(value) for value in asset_weights.values()),
                net_exposure=math.fsum(asset_weights.values()),
                asset_weights=asset_weights,
                target_cash_weight=target_cash,
                target_asset_weights=target_asset_weights,
            )
        )
    cash_weights = tuple(item.cash_weight for item in points)
    gross = tuple(item.gross_exposure for item in points)
    return tuple(points), {
        "average_cash_weight": math.fsum(cash_weights) / len(cash_weights),
        "max_cash_weight": max(cash_weights),
        "average_gross_exposure": math.fsum(gross) / len(gross),
        "max_gross_exposure": max(gross),
        "time_in_cash_fraction": sum(value >= 1.0 - 1e-12 for value in cash_weights)
        / len(cash_weights),
        "time_invested_fraction": sum(value < 1.0 - 1e-12 for value in cash_weights)
        / len(cash_weights),
        "cash_is_not_a_security": True,
    }


def _turnover_analytics(backtest: BacktestResult) -> tuple[MetricValue, dict[str, Any]]:
    orders = {item.order_id: item for item in backtest.orders}
    traded_notional = math.fsum(
        abs(float(fill.quantity) * float(fill.price))
        for fill in backtest.fills
        if orders.get(fill.order_id) is not None
        and orders[fill.order_id].rebalance_cause is RebalanceCause.TARGET
    )
    average_equity = math.fsum(float(item.total_equity) for item in backtest.equity_curve) / len(
        backtest.equity_curve
    )
    if average_equity <= 0:
        return MetricValue.not_evaluable("average portfolio equity is not positive"), {
            "contribution_cash_is_not_traded": True,
            "status": "not_evaluable",
        }
    return MetricValue.available(traded_notional / average_equity), {
        "contribution_cash_is_not_traded": True,
        "traded_notional_source": "target-cause fills only",
        "denominator": "average raw BacktestResult.equity_curve.total_equity",
        "traded_notional": traded_notional,
    }


def _annual_to_periodic(annual_rate: float, config: PerformanceAnalyticsConfig) -> float:
    return (1.0 + annual_rate) ** (1.0 / config.periods_per_year) - 1.0


def _cagr(initial: float, final: float, start: date, end: date) -> MetricValue:
    years = (end - start).days / _DAYS_PER_YEAR
    if years <= 0:
        return MetricValue.not_evaluable("CAGR requires a positive elapsed calendar period")
    if final == 0:
        return MetricValue.available(-1.0)
    return MetricValue.available((final / initial) ** (1.0 / years) - 1.0)


def _annualized_volatility(returns: Sequence[float], periods_per_year: float) -> MetricValue:
    if len(returns) < 2:
        return MetricValue.not_evaluable("at least two periodic returns are required")
    deviation = _sample_std(returns)
    return MetricValue.available(deviation * math.sqrt(periods_per_year))


def _sharpe(returns: Sequence[float], risk_free: float, periods_per_year: float) -> MetricValue:
    if len(returns) < 2:
        return MetricValue.not_evaluable("at least two periodic returns are required")
    excess = tuple(value - risk_free for value in returns)
    deviation = _sample_std(excess)
    if deviation == 0:
        return MetricValue.not_evaluable("excess return volatility is zero")
    return MetricValue.available(
        math.fsum(excess) / len(excess) / deviation * math.sqrt(periods_per_year)
    )


def _sortino(returns: Sequence[float], mar: float, periods_per_year: float) -> MetricValue:
    if not returns:
        return MetricValue.not_evaluable("at least one periodic return is required")
    excess = tuple(value - mar for value in returns)
    downside = math.sqrt(math.fsum(min(value, 0.0) ** 2 for value in excess) / len(excess))
    if downside == 0:
        return MetricValue.not_evaluable("downside deviation is zero")
    return MetricValue.available(
        math.fsum(excess) / len(excess) / downside * math.sqrt(periods_per_year)
    )


def _sample_std(values: Sequence[float]) -> float:
    mean = math.fsum(values) / len(values)
    return math.sqrt(math.fsum((value - mean) ** 2 for value in values) / (len(values) - 1))


def _drawdown_metrics(equities: Sequence[float]) -> dict[str, Any]:
    peak_value = equities[0]
    peak_index = 0
    best_drawdown = 0.0
    best_peak_index = 0
    best_trough_index = 0
    for index, equity in enumerate(equities):
        if equity > peak_value:
            peak_value = equity
            peak_index = index
        drawdown = equity / peak_value - 1.0
        if drawdown < best_drawdown:
            best_drawdown = drawdown
            best_peak_index = peak_index
            best_trough_index = index
    if best_drawdown == 0:
        return {
            "max_drawdown": MetricValue.available(0.0),
            "max_drawdown_duration": MetricValue.available(0.0),
            "recovery_duration": MetricValue.available(0.0),
            "max_drawdown_recovered": True,
        }
    recovery_index = next(
        (
            index
            for index in range(best_trough_index + 1, len(equities))
            if equities[index] >= equities[best_peak_index]
        ),
        None,
    )
    end_index = recovery_index if recovery_index is not None else len(equities) - 1
    return {
        "max_drawdown": MetricValue.available(best_drawdown),
        "max_drawdown_duration": MetricValue.available(float(end_index - best_peak_index)),
        "recovery_duration": (
            MetricValue.available(float(recovery_index - best_trough_index))
            if recovery_index is not None
            else MetricValue.not_evaluable("maximum drawdown was not recovered")
        ),
        "max_drawdown_recovered": recovery_index is not None,
    }


def _drawdown_curve(equities: Sequence[float]) -> tuple[float, ...]:
    peak = equities[0]
    values: list[float] = []
    for equity in equities:
        peak = max(peak, equity)
        values.append(equity / peak - 1.0)
    return tuple(values)


def _calmar(cagr: MetricValue, max_drawdown: MetricValue) -> MetricValue:
    if not cagr.is_evaluable or not max_drawdown.is_evaluable:
        return MetricValue.not_evaluable("CAGR or maximum drawdown is not evaluable")
    if max_drawdown.value == 0:
        return MetricValue.not_evaluable("maximum drawdown is zero")
    return MetricValue.available(cagr.value / abs(max_drawdown.value))


def _trade_metrics(trades: Sequence[Trade]) -> TradeMetrics:
    count = len(trades)
    winning = sum(trade.pnl_pct > 0 for trade in trades)
    losing = sum(trade.pnl_pct < 0 for trade in trades)
    if count == 0:
        unavailable = MetricValue.not_evaluable("no closed trades")
        return TradeMetrics(
            0,
            0,
            0,
            unavailable,
            unavailable,
            unavailable,
            unavailable,
            unavailable,
            unavailable,
            unavailable,
        )
    returns = tuple(float(trade.pnl_pct) for trade in trades)
    gross_profit = math.fsum(trade.pnl for trade in trades if trade.pnl > 0)
    gross_loss = math.fsum(trade.pnl for trade in trades if trade.pnl < 0)
    return TradeMetrics(
        count,
        winning,
        losing,
        MetricValue.available(winning / count),
        (
            MetricValue.available(gross_profit / abs(gross_loss))
            if gross_loss != 0
            else MetricValue.not_evaluable("gross loss is zero")
        ),
        MetricValue.available(math.fsum(returns) / count),
        MetricValue.available(max(returns)),
        MetricValue.available(min(returns)),
        MetricValue.available(math.fsum(trade.holding_period for trade in trades) / count),
        MetricValue.not_evaluable("BacktestResult has no canonical turnover contract"),
    )


__all__ = ["PerformanceAnalyticsConfig", "analyze_backtest", "flow_adjusted_wealth_curve"]
