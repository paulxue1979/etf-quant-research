from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

import pytest

from data.models import (
    DataSource,
    HistoricalDataRequest,
    HistoricalDataSet,
    MarketDataPoint,
    PriceField,
)
from indicators.models import IndicatorKind, IndicatorPoint, IndicatorSeries
from strategies import (
    Allocation,
    AllocationRule,
    AllocationSpecification,
    AssetReference,
    ComparisonOperator,
    Condition,
    EvaluationContext,
    FallbackAllocation,
    NoMatchBehavior,
    Operand,
    OperandType,
    RebalanceFrequency,
    RebalancePolicy,
    StrategyDefinition,
    StrategyVersion,
    Threshold,
    ThresholdType,
    evaluate_strategy,
)

START = date(2026, 9, 7)
DATES = tuple(START + timedelta(days=offset) for offset in (0, 1, 2, 3, 4, 7))


def _dataset(prices: tuple[float, ...]) -> HistoricalDataSet:
    points = tuple(
        MarketDataPoint(
            date=as_of_date,
            open=price,
            high=price,
            low=price,
            close=price,
            volume=1_000.0,
            adj_open=price,
            adj_high=price,
            adj_low=price,
            adj_close=price,
            adj_volume=1_000.0,
            div_cash=0.0,
            split_factor=1.0,
        )
        for as_of_date, price in zip(DATES, prices, strict=True)
    )
    return HistoricalDataSet(
        HistoricalDataRequest(
            "QQQ",
            DATES[0],
            DATES[-1],
            price_field_used=PriceField.RAW_CLOSE,
        ),
        points,
        DataSource.API_FRESH,
    )


def _context(prices: tuple[float, ...]) -> EvaluationContext:
    dataset = _dataset(prices)
    sma = IndicatorSeries(
        kind=IndicatorKind.MOVING_AVERAGE,
        period=200,
        price_field_used=PriceField.RAW_CLOSE,
        points=tuple(IndicatorPoint(as_of_date, 100.0) for as_of_date in DATES),
    )
    return EvaluationContext.from_components({"QQQ": dataset}, (("QQQ", sma),))


def _condition(operator: ComparisonOperator, threshold: float) -> Condition:
    return Condition(
        Operand("QQQ", OperandType.PRICE, price_field=PriceField.RAW_CLOSE),
        operator,
        Operand("QQQ", OperandType.MA, period=200, price_field=PriceField.RAW_CLOSE),
        Threshold(ThresholdType.RELATIVE, threshold),
    )


def _version(
    *,
    behavior: NoMatchBehavior = NoMatchBehavior.HOLD_PREVIOUS_ALLOCATION,
) -> StrategyVersion:
    definition = StrategyDefinition(
        strategy_id="stateful-runtime",
        name="Stateful Runtime",
        description="SMA200 hysteresis runtime fixture",
        assets=(AssetReference("QQQ"),),
        price_field=PriceField.RAW_CLOSE,
        rules=(
            AllocationRule(
                "buy",
                "Buy",
                100,
                (Allocation("QQQ", 1.0),),
                _condition(ComparisonOperator.GREATER_THAN, 0.04),
            ),
            AllocationRule(
                "sell",
                "Sell",
                90,
                (Allocation("QQQ", 0.0),),
                _condition(ComparisonOperator.LESS_THAN, -0.03),
            ),
        ),
        # Deliberately risk-on so HOLD tests prove fallback is ignored.
        fallback=FallbackAllocation((Allocation("QQQ", 1.0),), name="legacy-risk-on"),
        rebalance_policy=RebalancePolicy(RebalanceFrequency.ON_SIGNAL_CHANGE),
        no_match_behavior=behavior,
        initial_allocation=(
            AllocationSpecification(())
            if behavior is NoMatchBehavior.HOLD_PREVIOUS_ALLOCATION
            else None
        ),
    )
    return StrategyVersion(
        strategy_id=definition.strategy_id,
        version_id=f"stateful-runtime-{behavior.value}",
        version_number=1,
        created_at=datetime(2026, 9, 7, tzinfo=UTC),
        configuration=definition,
    )


def _timeline(prices: tuple[float, ...], *, behavior: NoMatchBehavior):
    return evaluate_strategy(
        _version(behavior=behavior),
        _context(prices),
        DATES[0],
        DATES[-1],
    )


def test_golden_six_day_hysteresis_holds_previous_target_with_distinct_provenance() -> None:
    timeline = _timeline(
        (105.0, 102.0, 99.0, 96.0, 100.0, 105.0),
        behavior=NoMatchBehavior.HOLD_PREVIOUS_ALLOCATION,
    )

    assert [dict(item.target_allocation.weights) for item in timeline.evaluations] == [
        {"QQQ": 1.0},
        {"QQQ": 1.0},
        {"QQQ": 1.0},
        {"QQQ": 0.0},
        {"QQQ": 0.0},
        {"QQQ": 1.0},
    ]
    assert [item.matched_rule_id for item in timeline.evaluations] == [
        "buy",
        None,
        None,
        "sell",
        None,
        "buy",
    ]
    assert [item.signal.allocation_source.value for item in timeline.evaluations] == [
        "rule_match",
        "hold_previous",
        "hold_previous",
        "rule_match",
        "hold_previous",
        "rule_match",
    ]
    assert all(
        not timeline.evaluations[index].target_allocation.used_fallback for index in (1, 2, 4)
    )


def test_initial_buffer_days_use_implicit_cash_and_ignore_configured_fallback() -> None:
    timeline = _timeline(
        (100.0, 100.0, 100.0, 100.0, 100.0, 100.0),
        behavior=NoMatchBehavior.HOLD_PREVIOUS_ALLOCATION,
    )

    for evaluation in timeline.evaluations:
        assert evaluation.target_allocation.weights == {}
        assert evaluation.target_allocation.cash_buffer == pytest.approx(1.0)
        assert evaluation.matched_rule_id is None
        assert evaluation.signal.allocation_source.value == "hold_previous"
        assert not evaluation.target_allocation.used_fallback


def test_legacy_use_fallback_runtime_remains_unchanged() -> None:
    timeline = _timeline(
        (100.0, 100.0, 100.0, 100.0, 100.0, 100.0),
        behavior=NoMatchBehavior.USE_FALLBACK,
    )

    assert all(
        evaluation.target_allocation.weights == {"QQQ": 1.0} for evaluation in timeline.evaluations
    )
    assert all(evaluation.target_allocation.used_fallback for evaluation in timeline.evaluations)
    assert all(
        evaluation.signal.allocation_source.value == "fallback"
        for evaluation in timeline.evaluations
    )


def test_stateful_evaluation_is_prefix_consistent_without_future_inputs() -> None:
    prices = (105.0, 102.0, 99.0, 96.0, 100.0, 105.0)
    version = _version()
    context = _context(prices)

    prefix = evaluate_strategy(version, context, DATES[0], DATES[3])
    full = evaluate_strategy(version, context, DATES[0], DATES[-1])

    assert full.evaluations[:4] == prefix.evaluations


def test_hold_previous_preserves_generic_multi_asset_target() -> None:
    definition = StrategyDefinition(
        strategy_id="multi-asset-hold",
        name="Multi Asset Hold",
        description="Generic stateful allocation fixture",
        assets=(AssetReference("QQQ"), AssetReference("SGOV")),
        price_field=PriceField.RAW_CLOSE,
        rules=(),
        fallback=FallbackAllocation((Allocation("SGOV", 1.0),)),
        rebalance_policy=RebalancePolicy(RebalanceFrequency.ON_SIGNAL_CHANGE),
        no_match_behavior=NoMatchBehavior.HOLD_PREVIOUS_ALLOCATION,
        initial_allocation=AllocationSpecification(
            (Allocation("QQQ", 0.4), Allocation("SGOV", 0.5))
        ),
    )
    version = StrategyVersion(
        strategy_id=definition.strategy_id,
        version_id="multi-asset-hold-v1",
        version_number=1,
        created_at=datetime(2026, 9, 7, tzinfo=UTC),
        configuration=definition,
    )
    qqq = _dataset((100.0,) * 6)
    sgov_points = tuple(
        MarketDataPoint(
            date=as_of_date,
            open=100.0,
            high=100.0,
            low=100.0,
            close=100.0,
            volume=1_000.0,
            adj_open=100.0,
            adj_high=100.0,
            adj_low=100.0,
            adj_close=100.0,
            adj_volume=1_000.0,
            div_cash=0.0,
            split_factor=1.0,
        )
        for as_of_date in DATES
    )
    sgov = HistoricalDataSet(
        HistoricalDataRequest("SGOV", DATES[0], DATES[-1], price_field_used=PriceField.RAW_CLOSE),
        sgov_points,
        DataSource.API_FRESH,
    )

    timeline = evaluate_strategy(
        version,
        EvaluationContext.from_components({"QQQ": qqq, "SGOV": sgov}),
        DATES[0],
        DATES[-1],
    )

    assert all(
        evaluation.target_allocation.weights == {"QQQ": 0.4, "SGOV": 0.5}
        for evaluation in timeline.evaluations
    )
    assert all(
        evaluation.target_allocation.cash_buffer == pytest.approx(0.1)
        for evaluation in timeline.evaluations
    )
