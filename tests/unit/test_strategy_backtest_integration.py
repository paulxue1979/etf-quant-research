from __future__ import annotations

from dataclasses import replace
from datetime import UTC, date, datetime, timedelta

import pytest

from backtest import (
    BacktestConfig,
    CommissionPolicy,
    StrategyBacktestResult,
    run_strategy_backtest,
    target_allocations_from_timeline,
)
from backtest import RebalanceFrequency as BacktestRebalanceFrequency
from backtest import RebalancePolicy as BacktestRebalancePolicy
from backtest.exceptions import StrategyBacktestIntegrationError
from data.models import (
    DataSource,
    HistoricalDataRequest,
    HistoricalDataSet,
    MarketDataPoint,
    PriceField,
)
from strategies import (
    Allocation,
    AllocationRule,
    AssetReference,
    EvaluationContext,
    FallbackAllocation,
    RebalanceFrequency,
    RebalancePolicy,
    StrategyDefinition,
    StrategyEvaluationResult,
    StrategyEvaluationStatus,
    StrategyVersion,
    evaluate_strategy,
)
from strategies.strategy_evaluation import EvaluationFailure, StrategyEvaluationTimeline

START = date(2026, 1, 5)


def _dataset(
    symbol: str,
    opens: tuple[float, ...],
    *,
    price_field: PriceField = PriceField.RAW_CLOSE,
) -> HistoricalDataSet:
    points = tuple(
        MarketDataPoint(
            date=START + timedelta(days=index),
            open=open_price,
            high=open_price,
            low=open_price,
            close=open_price,
            volume=100.0,
            adj_open=open_price * 2,
            adj_high=open_price * 2,
            adj_low=open_price * 2,
            adj_close=open_price * 2,
            adj_volume=100.0,
            div_cash=0.0,
            split_factor=1.0,
        )
        for index, open_price in enumerate(opens)
    )
    return HistoricalDataSet(
        HistoricalDataRequest(
            symbol,
            points[0].date,
            points[-1].date,
            price_field_used=price_field,
        ),
        points,
        DataSource.API_FRESH,
    )


def _version(
    symbols: tuple[str, ...] = ("QQQ",),
    *,
    weights: tuple[tuple[str, float], ...] = (("QQQ", 1.0),),
    frequency: RebalanceFrequency = RebalanceFrequency.DAILY,
    threshold: float | None = None,
    price_field: PriceField = PriceField.RAW_CLOSE,
) -> StrategyVersion:
    definition = StrategyDefinition(
        strategy_id="integration-test",
        name="Integration Test",
        description="PHASE 4H fixture",
        assets=tuple(AssetReference(symbol) for symbol in symbols),
        price_field=price_field,
        rules=(
            AllocationRule(
                "always",
                "Always",
                100,
                tuple(Allocation(symbol, weight) for symbol, weight in weights),
            ),
        ),
        fallback=FallbackAllocation((Allocation(symbols[0], 1.0),)),
        rebalance_policy=RebalancePolicy(frequency, threshold),
    )
    return StrategyVersion(
        strategy_id=definition.strategy_id,
        version_id="integration-test-v1",
        version_number=1,
        created_at=datetime(2026, 1, 1, tzinfo=UTC),
        configuration=definition,
    )


def _config(
    version: StrategyVersion,
    *,
    end: date,
    capital: float = 1_000.0,
    price_field: PriceField | None = None,
    frequency: BacktestRebalanceFrequency | None = None,
    threshold: float | None = None,
) -> BacktestConfig:
    strategy = version.configuration
    return BacktestConfig(
        strategy_version_id=version.version_id,
        start_date=START,
        end_date=end,
        initial_capital=capital,
        price_field_used=price_field or strategy.price_field,
        commission=CommissionPolicy(),
        rebalance_policy=BacktestRebalancePolicy(
            frequency=frequency
            or BacktestRebalanceFrequency(strategy.rebalance_policy.frequency.value),
            threshold=(
                strategy.rebalance_policy.threshold if threshold is None else threshold
            ),
        ),
    )


def _timeline(
    version: StrategyVersion, data: dict[str, HistoricalDataSet]
) -> StrategyEvaluationTimeline:
    context = EvaluationContext.from_components(data)
    return evaluate_strategy(version, context, START, START + timedelta(days=3))


def test_single_asset_signal_is_converted_and_executed_next_open() -> None:
    version = _version()
    data = {"QQQ": _dataset("QQQ", (10.0, 20.0, 30.0, 40.0))}
    timeline = _timeline(version, data)

    result = run_strategy_backtest(
        version, timeline, data, _config(version, end=START + timedelta(days=3))
    )

    assert isinstance(result, StrategyBacktestResult)
    assert [record.signal.date for record in result.signal_records] == [
        START + timedelta(days=index) for index in range(4)
    ]
    assert result.backtest_result.orders[0].signal_date == START
    assert result.backtest_result.orders[0].date == START + timedelta(days=1)
    assert result.backtest_result.orders[0].requested_price == 20.0
    assert result.signal_records[-1].submitted_to_backtest is False
    assert result.signal_records[-1].execution_date is None
    assert len(result.submitted_allocations) == 3


def test_multi_asset_remaining_and_cash_buffer_reach_phase3() -> None:
    version = _version(
        ("QQQ", "TQQQ", "SGOV"),
        weights=(("QQQ", 0.6), ("TQQQ", 0.3)),
    )
    data = {
        symbol: _dataset(symbol, (10.0, 10.0, 10.0, 10.0))
        for symbol in ("QQQ", "TQQQ", "SGOV")
    }
    timeline = _timeline(version, data)
    result = run_strategy_backtest(
        version, timeline, data, _config(version, end=START + timedelta(days=3))
    )

    target = result.signal_records[0].target_allocation
    assert target.as_mapping() == {"QQQ": 0.6, "TQQQ": 0.3}
    assert target.weight_for("SGOV") == 0.0
    assert result.backtest_result.fills[0].symbol == "QQQ"
    assert result.backtest_result.fills[1].symbol == "TQQQ"
    assert sum(fill.quantity for fill in result.backtest_result.fills) == 90
    assert result.backtest_result.equity_curve[1].cash == pytest.approx(100.0)


def test_commission_slippage_integer_shares_sell_first_and_determinism_are_preserved() -> None:
    version = _version(("QQQ", "TQQQ"), weights=(("QQQ", 0.5), ("TQQQ", 0.5)))
    data = {
        "QQQ": _dataset("QQQ", (10.0, 10.0, 20.0, 20.0)),
        "TQQQ": _dataset("TQQQ", (10.0, 10.0, 5.0, 5.0)),
    }
    timeline = _timeline(version, data)
    config = replace(
        _config(version, end=START + timedelta(days=3)),
        commission=CommissionPolicy(rate=0.01, per_order=2.0),
        slippage=0.1,
    )
    first = run_strategy_backtest(version, timeline, data, config)
    second = run_strategy_backtest(version, timeline, data, config)

    assert first == second
    assert all(isinstance(order.quantity, int) for order in first.backtest_result.orders)
    assert first.backtest_result.orders[0].date == START + timedelta(days=1)
    assert all(
        point.total_equity == pytest.approx(point.cash + sum(point.asset_values.values()))
        for point in first.backtest_result.equity_curve
    )


def test_leading_not_evaluable_warmup_is_skipped_without_fallback() -> None:
    version = _version()
    not_evaluable = StrategyEvaluationResult(
        START,
        version.version_id,
        StrategyEvaluationStatus.NOT_EVALUABLE,
        (),
        None,
        None,
        "warm-up",
        EvaluationFailure("INDICATOR_WARMUP", "indicator is not ready"),
    )
    evaluated_timeline = evaluate_strategy(
        version,
        EvaluationContext.from_components({"QQQ": _dataset("QQQ", (10.0, 20.0, 30.0, 40.0))}),
        START + timedelta(days=1),
        START + timedelta(days=3),
    )
    timeline = StrategyEvaluationTimeline(
        version.version_id,
        START,
        START + timedelta(days=3),
        (not_evaluable, *evaluated_timeline.evaluations),
    )
    data = {"QQQ": _dataset("QQQ", (10.0, 20.0, 30.0, 40.0))}

    result = run_strategy_backtest(
        version, timeline, data, _config(version, end=START + timedelta(days=3))
    )

    assert result.signal_records[0].signal.date == START + timedelta(days=1)
    assert result.backtest_result.orders[0].signal_date == START + timedelta(days=1)


def test_error_and_late_not_evaluable_are_explicit_failures() -> None:
    version = _version()
    error = StrategyEvaluationResult(
        START,
        version.version_id,
        StrategyEvaluationStatus.ERROR,
        (),
        None,
        None,
        "failed",
        EvaluationFailure("RULE_GROUP_EVALUATION_ERROR", "condition failed"),
    )
    data = {"QQQ": _dataset("QQQ", (10.0, 20.0, 30.0, 40.0))}
    error_timeline = StrategyEvaluationTimeline(
        version.version_id, START, START + timedelta(days=3), (error,)
    )
    with pytest.raises(StrategyBacktestIntegrationError, match="ERROR"):
        target_allocations_from_timeline(
            version,
            error_timeline,
            data,
            _config(version, end=START + timedelta(days=3)),
        )

    evaluated = evaluate_strategy(
        version,
        EvaluationContext.from_components(data),
        START,
        START,
    ).evaluations[0]
    late_not_evaluable = replace(
        error,
        date=START + timedelta(days=1),
        status=StrategyEvaluationStatus.NOT_EVALUABLE,
        explanation="late warm-up",
        failure=EvaluationFailure("INDICATOR_WARMUP", "late warm-up"),
    )
    late_timeline = StrategyEvaluationTimeline(
        version.version_id,
        START,
        START + timedelta(days=3),
        (evaluated, late_not_evaluable),
    )
    with pytest.raises(StrategyBacktestIntegrationError, match="after an evaluated"):
        target_allocations_from_timeline(
            version,
            late_timeline,
            data,
            _config(version, end=START + timedelta(days=3)),
        )


@pytest.mark.parametrize(
    ("field", "message"),
    [
        (PriceField.ADJUSTED_CLOSE, "strategy uses"),
        (PriceField.RAW_CLOSE, "market data for QQQ uses"),
    ],
)
def test_price_field_mismatch_is_rejected(field: PriceField, message: str) -> None:
    version = _version(price_field=PriceField.RAW_CLOSE)
    data = {
        "QQQ": _dataset(
            "QQQ",
            (10.0, 20.0, 30.0, 40.0),
            price_field=PriceField.ADJUSTED_CLOSE if field is PriceField.RAW_CLOSE else field,
        )
    }
    timeline = _timeline(version, {"QQQ": _dataset("QQQ", (10.0, 20.0, 30.0, 40.0))})
    config = _config(version, end=START + timedelta(days=3), price_field=field)

    with pytest.raises(StrategyBacktestIntegrationError, match=message):
        target_allocations_from_timeline(version, timeline, data, config)


def test_strategy_version_and_rebalance_policy_mismatches_are_rejected() -> None:
    version = _version(frequency=RebalanceFrequency.ON_SIGNAL_CHANGE, threshold=0.03)
    data = {"QQQ": _dataset("QQQ", (10.0, 20.0, 30.0, 40.0))}
    timeline = _timeline(version, data)
    wrong_version_config = replace(
        _config(version, end=START + timedelta(days=3)), strategy_version_id="other-v1"
    )
    with pytest.raises(StrategyBacktestIntegrationError, match="strategy version"):
        target_allocations_from_timeline(version, timeline, data, wrong_version_config)

    wrong_policy = replace(
        _config(version, end=START + timedelta(days=3)),
        rebalance_policy=BacktestRebalancePolicy(BacktestRebalanceFrequency.DAILY, 0.03),
    )
    with pytest.raises(StrategyBacktestIntegrationError, match="frequencies"):
        target_allocations_from_timeline(version, timeline, data, wrong_policy)


def test_empty_evaluation_timeline_runs_cash_only_without_inventing_allocations() -> None:
    version = _version()
    data = {"QQQ": _dataset("QQQ", (10.0, 20.0, 30.0, 40.0))}
    timeline = StrategyEvaluationTimeline(
        version.version_id, START, START + timedelta(days=3), ()
    )

    result = run_strategy_backtest(
        version, timeline, data, _config(version, end=START + timedelta(days=3))
    )

    assert result.signal_records == ()
    assert result.backtest_result.fills == ()
    assert result.backtest_result.final_equity == pytest.approx(1_000.0)


def test_adapter_calls_the_real_phase3_engine() -> None:
    version = _version()
    data = {"QQQ": _dataset("QQQ", (10.0, 20.0, 30.0, 40.0))}
    timeline = _timeline(version, data)

    result = run_strategy_backtest(
        version, timeline, data, _config(version, end=START + timedelta(days=3))
    )

    assert result.backtest_result.engine_version == "phase-3.0"
    assert (
        result.backtest_result.configuration_snapshot["execution_rule"]
        == "next_trading_day_open"
    )
    assert result.backtest_result.strategy_version_id == version.version_id
