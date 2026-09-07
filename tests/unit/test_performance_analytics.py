from __future__ import annotations

import math
from dataclasses import replace
from datetime import date, timedelta
from types import MappingProxyType

import pytest

from analytics import (
    AnalyticsInputError,
    MetricStatus,
    PerformanceAnalyticsConfig,
    analyze_backtest,
)
from backtest import BacktestResult, EquityPoint, Trade
from data.models import PriceField


def _backtest(
    equities: tuple[float, ...],
    *,
    start: date = date(2025, 1, 1),
    trades: tuple[Trade, ...] = (),
    price_field: PriceField = PriceField.RAW_CLOSE,
    asset_values: tuple[dict[str, float], ...] | None = None,
) -> BacktestResult:
    values = asset_values or tuple({} for _ in equities)
    points = tuple(
        EquityPoint(
            date=start + timedelta(days=index),
            cash=equity - sum(values[index].values()),
            asset_values=MappingProxyType(dict(values[index])),
            total_equity=equity,
        )
        for index, equity in enumerate(equities)
    )
    return BacktestResult(
        start_date=points[0].date,
        end_date=points[-1].date,
        initial_capital=equities[0],
        final_equity=equities[-1],
        equity_curve=points,
        orders=(),
        fills=(),
        trades=trades,
        positions=(),
        allocation_history=(),
        cash_history=tuple((point.date, point.cash) for point in points),
        strategy_version_id="analytics-test-v1",
        configuration_snapshot=MappingProxyType(
            {
                "price_field_used": price_field.value,
                "rebalance_policy": {"frequency": "daily"},
            }
        ),
        data_snapshot_reference=MappingProxyType({"QQQ": {"source": "fixture"}}),
    )


def _trade(
    pnl: float,
    pnl_pct: float,
    holding_period: int,
    *,
    symbol: str = "QQQ",
) -> Trade:
    entry = date(2025, 1, 1)
    return Trade(
        symbol=symbol,
        entry_date=entry,
        exit_date=entry + timedelta(days=holding_period),
        entry_price=100.0,
        exit_price=100.0 + pnl,
        quantity=1,
        pnl=pnl,
        pnl_pct=pnl_pct,
        holding_period=holding_period,
    )


def test_total_return_and_known_cagr_are_hand_computable() -> None:
    result = _backtest(
        (100.0, 121.0),
        start=date(2025, 1, 1),
    )
    result = replace(result, end_date=date(2027, 1, 1))
    result = replace(
        result,
        equity_curve=(
            result.equity_curve[0],
            replace(result.equity_curve[1], date=date(2027, 1, 1)),
        ),
    )

    analysis = analyze_backtest(result)

    assert analysis.total_return.value == pytest.approx(0.21)
    assert analysis.cagr.value == pytest.approx(0.10)


def test_flat_equity_uses_explicit_not_evaluable_for_zero_denominators() -> None:
    analysis = analyze_backtest(_backtest((100.0, 100.0, 100.0, 100.0)))

    assert analysis.total_return.value == 0.0
    assert analysis.cagr.value == 0.0
    assert analysis.annualized_volatility.value == 0.0
    assert analysis.max_drawdown.value == 0.0
    assert analysis.sharpe_ratio.status is MetricStatus.NOT_EVALUABLE
    assert analysis.sortino_ratio.status is MetricStatus.NOT_EVALUABLE
    assert analysis.calmar_ratio.status is MetricStatus.NOT_EVALUABLE


def test_monotonic_equity_has_no_drawdown_and_no_infinite_calmar() -> None:
    analysis = analyze_backtest(_backtest((100.0, 110.0, 121.0, 133.1)))

    assert analysis.total_return.value == pytest.approx(0.331)
    assert analysis.max_drawdown.value == 0.0
    assert analysis.max_drawdown_recovered is True
    assert analysis.calmar_ratio.status is MetricStatus.NOT_EVALUABLE
    assert analysis.calmar_ratio.value is None


def test_one_drawdown_reports_peak_to_recovery_and_trough_to_recovery_durations() -> None:
    analysis = analyze_backtest(_backtest((100.0, 120.0, 90.0, 110.0, 130.0)))

    assert analysis.max_drawdown.value == pytest.approx(-0.25)
    assert analysis.max_drawdown_duration.value == 3.0
    assert analysis.recovery_duration.value == 2.0
    assert analysis.max_drawdown_recovered is True


def test_multiple_drawdowns_selects_the_largest_not_the_last() -> None:
    analysis = analyze_backtest(_backtest((100.0, 120.0, 90.0, 130.0, 100.0, 150.0)))

    assert analysis.max_drawdown.value == pytest.approx(-0.25)
    assert analysis.max_drawdown_duration.value == 2.0
    assert analysis.recovery_duration.value == 1.0


def test_unrecovered_drawdown_is_explicit() -> None:
    analysis = analyze_backtest(_backtest((100.0, 120.0, 80.0, 90.0)))

    assert analysis.max_drawdown.value == pytest.approx(-1.0 / 3.0)
    assert analysis.max_drawdown_duration.value == 2.0
    assert analysis.max_drawdown_recovered is False
    assert analysis.recovery_duration.status is MetricStatus.NOT_EVALUABLE


def test_negative_portfolio_return_and_cagr_are_supported() -> None:
    analysis = analyze_backtest(_backtest((100.0, 90.0, 80.0, 70.0)))

    assert analysis.total_return.value == pytest.approx(-0.30)
    assert analysis.cagr.value < 0
    assert analysis.max_drawdown.value == pytest.approx(-0.30)


def test_risk_free_rate_is_converted_from_annual_to_periodic_before_sharpe() -> None:
    backtest = _backtest((100.0, 120.0, 108.0, 129.6))
    no_hurdle = analyze_backtest(backtest, config=PerformanceAnalyticsConfig(periods_per_year=1))
    with_hurdle = analyze_backtest(
        backtest,
        config=PerformanceAnalyticsConfig(risk_free_rate=0.10, periods_per_year=1),
    )

    assert no_hurdle.sharpe_ratio.value == pytest.approx(0.5773502692)
    assert with_hurdle.sharpe_ratio.value == pytest.approx(0.0)


def test_single_equity_point_keeps_total_return_but_not_return_series_metrics() -> None:
    analysis = analyze_backtest(_backtest((100.0,)))

    assert analysis.total_return.value == 0.0
    assert analysis.cagr.status is MetricStatus.NOT_EVALUABLE
    assert analysis.annualized_volatility.status is MetricStatus.NOT_EVALUABLE
    assert analysis.sharpe_ratio.status is MetricStatus.NOT_EVALUABLE
    assert analysis.sortino_ratio.status is MetricStatus.NOT_EVALUABLE


def test_closed_trade_metrics_use_existing_trade_records_only() -> None:
    trades = (
        _trade(10.0, 0.10, 2),
        _trade(-5.0, -0.05, 4),
        _trade(20.0, 0.20, 6),
        _trade(-10.0, -0.10, 8),
    )
    metrics = analyze_backtest(_backtest((100.0, 105.0), trades=trades)).trade_metrics

    assert metrics.number_of_closed_trades == 4
    assert metrics.winning_trades == 2
    assert metrics.losing_trades == 2
    assert metrics.win_rate.value == pytest.approx(0.5)
    assert metrics.profit_factor.value == pytest.approx(2.0)
    assert metrics.average_trade_return.value == pytest.approx(0.0375)
    assert metrics.best_trade.value == pytest.approx(0.20)
    assert metrics.worst_trade.value == pytest.approx(-0.10)
    assert metrics.average_holding_period.value == pytest.approx(5.0)
    assert metrics.turnover.status is MetricStatus.NOT_EVALUABLE


def test_no_trades_is_explicitly_not_evaluable() -> None:
    metrics = analyze_backtest(_backtest((100.0, 100.0))).trade_metrics

    assert metrics.number_of_closed_trades == 0
    assert metrics.win_rate.status is MetricStatus.NOT_EVALUABLE
    assert metrics.profit_factor.status is MetricStatus.NOT_EVALUABLE


def test_multi_asset_equity_is_the_only_portfolio_return_source() -> None:
    analysis = analyze_backtest(
        _backtest(
            (100.0, 115.0, 120.0),
            asset_values=(
                {"QQQ": 40.0, "TQQQ": 40.0},
                {"QQQ": 70.0, "TQQQ": 35.0},
                {"QQQ": 72.0, "TQQQ": 38.0},
            ),
        )
    )

    assert analysis.total_return.value == pytest.approx(0.20)
    assert analysis.provenance["equity_source"] == "equity_curve.total_equity"


def test_price_and_identity_provenance_are_retained() -> None:
    analysis = analyze_backtest(
        _backtest((100.0, 110.0), price_field=PriceField.ADJUSTED_CLOSE),
        backtest_run_id="run-123",
        strategy_id="trend-following",
    )

    assert analysis.backtest_run_id == "run-123"
    assert analysis.strategy_id == "trend-following"
    assert analysis.strategy_version_id == "analytics-test-v1"
    assert analysis.price_field_used is PriceField.ADJUSTED_CLOSE
    assert analysis.rebalance_frequency == "daily"


def test_analysis_is_deterministic_and_does_not_mutate_the_backtest_input() -> None:
    backtest = _backtest((100.0, 120.0, 90.0, 130.0))
    before = backtest

    first = analyze_backtest(backtest)
    second = analyze_backtest(backtest)

    assert first == second
    assert backtest == before
    with pytest.raises(TypeError):
        first.provenance["source"] = "other"  # type: ignore[index]


@pytest.mark.parametrize("bad_value", [math.nan, math.inf, -math.inf])
def test_non_finite_equity_is_rejected(bad_value: float) -> None:
    backtest = _backtest((100.0, 110.0))
    bad_point = replace(backtest.equity_curve[1], cash=bad_value, total_equity=bad_value)
    malformed = replace(backtest, equity_curve=(backtest.equity_curve[0], bad_point))

    with pytest.raises(AnalyticsInputError, match="equity point cash"):
        analyze_backtest(malformed)


@pytest.mark.parametrize(
    ("mutator", "message"),
    [
        (
            lambda result: replace(
                result,
                equity_curve=(result.equity_curve[1], result.equity_curve[0]),
            ),
            "sorted and unique",
        ),
        (
            lambda result: replace(
                result,
                equity_curve=(
                    result.equity_curve[0],
                    replace(result.equity_curve[1], date=result.equity_curve[0].date),
                ),
            ),
            "sorted and unique",
        ),
        (lambda result: replace(result, initial_capital=99.0), "initial_capital must match"),
        (
            lambda result: replace(
                result,
                configuration_snapshot=MappingProxyType({}),
            ),
            "price_field_used",
        ),
    ],
)
def test_malformed_backtest_results_are_rejected(mutator, message: str) -> None:
    with pytest.raises(AnalyticsInputError, match=message):
        analyze_backtest(mutator(_backtest((100.0, 110.0))))


def test_invalid_trade_holding_period_is_rejected() -> None:
    trade = replace(_trade(10.0, 0.10, 2), holding_period=1)

    with pytest.raises(AnalyticsInputError, match="holding_period"):
        analyze_backtest(_backtest((100.0, 110.0), trades=(trade,)))


def test_invalid_config_type_is_rejected() -> None:
    with pytest.raises(TypeError, match="PerformanceAnalyticsConfig"):
        analyze_backtest(_backtest((100.0, 110.0)), config="daily")  # type: ignore[arg-type]
