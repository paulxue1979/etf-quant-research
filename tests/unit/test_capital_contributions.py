from __future__ import annotations

from dataclasses import replace
from datetime import date
from decimal import Decimal

import pytest

from analytics import analyze_backtest
from backend.app.backtest_lab import BacktestRequest
from backend.app.backtest_models import deserialize_backtest_result, serialize_backtest_result
from backend.app.research_protocol import ResearchEvaluationConfig
from backtest import (
    BacktestConfig,
    BacktestEngine,
    ContributionFrequency,
    ContributionSchedule,
    RebalanceCause,
    RebalanceFrequency,
    RebalancePolicy,
    TargetAllocation,
    normalize_contribution_schedule,
)
from data.models import (
    DataSource,
    HistoricalDataRequest,
    HistoricalDataSet,
    MarketDataPoint,
    PriceField,
)
from research.exceptions import OosConfigurationMismatchError
from research.oos import OosEvaluationConfig, validate_oos_configuration


def _dataset(symbol: str, dates: tuple[date, ...], price: float) -> HistoricalDataSet:
    points = tuple(
        MarketDataPoint(
            date=day,
            open=price,
            high=price,
            low=price,
            close=price,
            volume=100.0,
            adj_open=price,
            adj_high=price,
            adj_low=price,
            adj_close=price,
            adj_volume=100.0,
            div_cash=0.0,
            split_factor=1.0,
        )
        for day in dates
    )
    return HistoricalDataSet(
        HistoricalDataRequest(symbol, dates[0], dates[-1]),
        points,
        DataSource.CACHE,
    )


def _config(
    dates: tuple[date, ...],
    *,
    capital: float = 1_000.0,
    schedule: ContributionSchedule | None = None,
    threshold: float | None = None,
) -> BacktestConfig:
    return BacktestConfig(
        strategy_version_id="contribution-v1",
        start_date=dates[0],
        end_date=dates[-1],
        initial_capital=capital,
        price_field_used=PriceField.RAW_CLOSE,
        rebalance_policy=RebalancePolicy(
            frequency=RebalanceFrequency.ON_SIGNAL_CHANGE,
            threshold=threshold,
        ),
        contribution_schedule=schedule,
    )


@pytest.mark.parametrize("amount", [0, -1, "NaN", "Infinity", "-Infinity"])
def test_contribution_schedule_rejects_non_positive_or_non_finite_amounts(amount: object) -> None:
    with pytest.raises(ValueError, match="amount"):
        ContributionSchedule(ContributionFrequency.MONTHLY, amount)


def test_contribution_schedule_has_canonical_decimal_identity() -> None:
    first = ContributionSchedule(ContributionFrequency.MONTHLY, "1000")
    second = ContributionSchedule(ContributionFrequency.MONTHLY, "1000.000000")

    assert first.amount == second.amount == Decimal("1000")
    assert (
        first.to_dict()
        == second.to_dict()
        == {
            "frequency": "monthly",
            "amount": "1000",
            "requested_date": None,
            "currency": "USD",
        }
    )


def test_one_time_requires_a_date_and_monthly_forbids_one() -> None:
    with pytest.raises(ValueError, match="requested_date"):
        ContributionSchedule(ContributionFrequency.ONE_TIME, "100")
    with pytest.raises(ValueError, match="requested_date"):
        ContributionSchedule(
            ContributionFrequency.MONTHLY,
            "100",
            requested_date=date(2026, 1, 1),
        )


def test_monthly_and_one_time_normalization_use_next_common_trading_date() -> None:
    trading_dates = (
        date(2026, 1, 2),
        date(2026, 1, 5),
        date(2026, 2, 2),
        date(2026, 3, 2),
    )
    monthly = normalize_contribution_schedule(
        ContributionSchedule(ContributionFrequency.MONTHLY, "250"),
        trading_dates,
        investment_start=date(2026, 1, 1),
        investment_end=date(2026, 2, 28),
    )
    weekend = normalize_contribution_schedule(
        ContributionSchedule(
            ContributionFrequency.ONE_TIME,
            "100",
            requested_date=date(2026, 1, 3),
        ),
        trading_dates,
        investment_start=date(2026, 1, 1),
        investment_end=date(2026, 2, 28),
    )

    assert [(item.requested_date, item.effective_date) for item in monthly] == [
        (date(2026, 1, 1), date(2026, 1, 2)),
        (date(2026, 2, 1), date(2026, 2, 2)),
    ]
    assert weekend[0].effective_date == date(2026, 1, 5)


def test_monthly_month_start_normalizes_into_effective_range() -> None:
    events = normalize_contribution_schedule(
        ContributionSchedule(ContributionFrequency.MONTHLY, "250"),
        (date(2026, 1, 2), date(2026, 1, 5)),
        investment_start=date(2026, 1, 2),
        investment_end=date(2026, 1, 31),
    )

    assert [(item.requested_date, item.effective_date) for item in events] == [
        (date(2026, 1, 1), date(2026, 1, 2))
    ]


def test_normalization_excludes_warmup_and_events_beyond_the_end() -> None:
    dates = (date(2026, 1, 2), date(2026, 1, 5), date(2026, 2, 2))
    before_range = normalize_contribution_schedule(
        ContributionSchedule(
            ContributionFrequency.ONE_TIME,
            "100",
            requested_date=date(2025, 12, 15),
        ),
        dates,
        investment_start=date(2026, 1, 2),
        investment_end=date(2026, 1, 31),
    )
    beyond_end = normalize_contribution_schedule(
        ContributionSchedule(
            ContributionFrequency.ONE_TIME,
            "100",
            requested_date=date(2026, 1, 31),
        ),
        dates,
        investment_start=date(2026, 1, 2),
        investment_end=date(2026, 1, 31),
    )

    assert before_range == ()
    assert beyond_end == ()


def test_risk_off_contribution_stays_cash_without_orders_or_trades() -> None:
    dates = tuple(date(2026, 1, day) for day in (2, 3, 4, 5))
    result = BacktestEngine().run(
        {"QQQ": _dataset("QQQ", dates, 100.0)},
        [],
        _config(
            dates,
            schedule=ContributionSchedule(
                ContributionFrequency.ONE_TIME,
                "500",
                requested_date=date(2026, 1, 4),
            ),
        ),
    )

    assert result.final_equity == pytest.approx(1_500.0)
    assert result.orders == result.fills == result.trades == ()
    assert result.cumulative_contributions == pytest.approx(500.0)
    assert result.external_cash_flows[0].amount == Decimal("500")


def test_contribution_rebalances_current_target_and_records_cause() -> None:
    dates = tuple(date(2026, 1, day) for day in (2, 3, 4, 5))
    result = BacktestEngine().run(
        {"QQQ": _dataset("QQQ", dates, 100.0)},
        [TargetAllocation.from_weights(dates[0], {"QQQ": 1.0})],
        _config(
            dates,
            schedule=ContributionSchedule(
                ContributionFrequency.ONE_TIME,
                "500",
                requested_date=date(2026, 1, 4),
            ),
        ),
    )

    assert [(order.quantity, order.rebalance_cause) for order in result.orders] == [
        (10, RebalanceCause.TARGET),
        (5, RebalanceCause.CONTRIBUTION),
    ]
    assert result.positions[-1].positions[0].quantity == 15


def test_pending_target_sizes_with_same_day_contribution_cash() -> None:
    dates = tuple(date(2026, 1, day) for day in (2, 3, 4))
    result = BacktestEngine().run(
        {"QQQ": _dataset("QQQ", dates, 100.0)},
        [TargetAllocation.from_weights(dates[0], {"QQQ": 1.0})],
        _config(
            dates,
            schedule=ContributionSchedule(
                ContributionFrequency.ONE_TIME,
                "500",
                requested_date=date(2026, 1, 3),
            ),
        ),
    )

    assert len(result.orders) == 1
    assert result.orders[0].quantity == 15
    assert result.orders[0].rebalance_cause is RebalanceCause.TARGET


@pytest.mark.parametrize(("amount", "expected_orders"), [("10", 1), ("100", 2)])
def test_contribution_triggered_rebalance_respects_threshold(
    amount: str, expected_orders: int
) -> None:
    dates = tuple(date(2026, 1, day) for day in (2, 3, 4, 5))
    result = BacktestEngine().run(
        {"QQQ": _dataset("QQQ", dates, 100.0)},
        [TargetAllocation.from_weights(dates[0], {"QQQ": 1.0})],
        _config(
            dates,
            schedule=ContributionSchedule(
                ContributionFrequency.ONE_TIME,
                amount,
                requested_date=date(2026, 1, 4),
            ),
            threshold=0.02,
        ),
    )

    assert len(result.orders) == expected_orders


def test_contribution_too_small_for_integer_share_retains_cash_without_error() -> None:
    dates = tuple(date(2026, 1, day) for day in (2, 3, 4, 5))
    result = BacktestEngine().run(
        {"QQQ": _dataset("QQQ", dates, 500.0)},
        [TargetAllocation.from_weights(dates[0], {"QQQ": 1.0})],
        _config(
            dates,
            capital=500.0,
            schedule=ContributionSchedule(
                ContributionFrequency.ONE_TIME,
                "100",
                requested_date=date(2026, 1, 4),
            ),
        ),
    )

    assert len(result.orders) == 1
    assert result.cash_history[-1][1] == pytest.approx(100.0)
    assert all(point.cash >= 0 for point in result.equity_curve)


def test_flow_adjusted_analytics_do_not_count_contribution_as_return() -> None:
    dates = tuple(date(2026, 1, day) for day in (2, 3, 4))
    result = BacktestEngine().run(
        {"QQQ": _dataset("QQQ", dates, 100.0)},
        [],
        _config(
            dates,
            capital=100_000.0,
            schedule=ContributionSchedule(
                ContributionFrequency.ONE_TIME,
                "10000",
                requested_date=date(2026, 1, 3),
            ),
        ),
    )
    analysis = analyze_backtest(result)

    assert result.final_equity == pytest.approx(110_000.0)
    assert result.total_capital_invested == pytest.approx(110_000.0)
    assert result.investment_profit == pytest.approx(0.0)
    assert analysis.total_return.value == pytest.approx(0.0)
    assert analysis.max_drawdown.value == pytest.approx(0.0)


def test_no_contribution_analytics_remain_compatible() -> None:
    dates = tuple(date(2026, 1, day) for day in (2, 3, 4, 5))
    result = BacktestEngine().run(
        {"QQQ": _dataset("QQQ", dates, 100.0)},
        [],
        _config(dates),
    )
    analysis = analyze_backtest(result)

    assert analysis.total_return.value == pytest.approx(result.final_equity / 1_000.0 - 1)
    assert result.cumulative_contributions == 0.0
    assert result.total_capital_invested == 1_000.0


def test_config_identity_and_backtest_result_serialization_include_contributions() -> None:
    dates = tuple(date(2026, 1, day) for day in (2, 3, 4))
    no_dca = _config(dates)
    dca = _config(
        dates,
        schedule=ContributionSchedule(ContributionFrequency.MONTHLY, "1000.00"),
    )
    assert no_dca.strategy_version_id == dca.strategy_version_id
    assert no_dca.snapshot({}) != dca.snapshot({})

    result = BacktestEngine().run({"QQQ": _dataset("QQQ", dates, 100.0)}, [], dca)
    restored = deserialize_backtest_result(serialize_backtest_result(result))
    assert restored.contribution_events == result.contribution_events
    assert restored.external_cash_flows == result.external_cash_flows
    assert restored.total_capital_invested == result.total_capital_invested


def test_api_and_frozen_research_configuration_round_trip_contributions() -> None:
    request = BacktestRequest.model_validate(
        {
            "strategy_id": "strategy",
            "strategy_version_id": "strategy-v1",
            "start_date": "2026-01-01",
            "end_date": "2026-03-31",
            "initial_capital": 10000,
            "price_field_used": "adjusted_close",
            "contribution_schedule": {"frequency": "monthly", "amount": "250.00"},
        }
    )
    config = request.to_config("strategy-v1", RebalancePolicy(frequency=RebalanceFrequency.DAILY))
    assert config.contribution_schedule.to_dict()["amount"] == "250"

    frozen = ResearchEvaluationConfig.from_dict(config.snapshot({}))
    oos = OosEvaluationConfig.from_research_evaluation_config(
        frozen, analytics_version="phase-4i.0"
    )
    assert frozen.contribution_schedule == config.contribution_schedule.to_dict()
    assert oos.contribution_schedule == config.contribution_schedule
    assert OosEvaluationConfig.from_dict(oos.to_dict()) == oos
    assert "contribution_schedule" in replace(frozen, contribution_schedule=None).mismatch_fields(
        frozen
    )


def test_oos_configuration_rejects_no_dca_and_dca_mismatch() -> None:
    base = ResearchEvaluationConfig(
        price_field_used="adjusted_close",
        initial_capital=10_000,
        commission={"rate": 0.0, "per_order": 0.0},
        slippage=0.0,
        execution_rule="next_trading_day_open",
        fractional_shares=False,
        rebalance_policy={"frequency": "daily", "threshold": None},
        engine_version="phase-3.0",
    )
    no_dca = OosEvaluationConfig.from_research_evaluation_config(
        base, analytics_version="phase-4i.0"
    )
    dca = OosEvaluationConfig.from_research_evaluation_config(
        replace(
            base,
            contribution_schedule={"frequency": "monthly", "amount": "250"},
        ),
        analytics_version="phase-4i.0",
    )

    with pytest.raises(OosConfigurationMismatchError):
        validate_oos_configuration(no_dca, dca)
    with pytest.raises(OosConfigurationMismatchError):
        validate_oos_configuration(dca, no_dca)
