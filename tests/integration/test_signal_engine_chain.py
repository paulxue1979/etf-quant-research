"""Local module-chain coverage for PHASE 4F signal assembly."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

from data.models import (
    DataSource,
    HistoricalDataRequest,
    HistoricalDataSet,
    MarketDataPoint,
    PriceField,
)
from indicators import moving_average
from strategies import (
    Allocation,
    AllocationRule,
    AllocationSource,
    AssetReference,
    ComparisonOperator,
    Condition,
    EvaluationContext,
    FallbackAllocation,
    LogicalOperator,
    Operand,
    OperandType,
    RebalanceFrequency,
    RebalancePolicy,
    RuleGroup,
    StrategyDefinition,
    StrategyStatus,
    StrategyVersion,
    build_signal,
    evaluate_rule_group,
    resolve_allocations,
)

START = date(2026, 9, 3)


def _qqq_data() -> HistoricalDataSet:
    prices = (100.0, 102.0, 110.0)
    points = tuple(
        MarketDataPoint(
            date=START + timedelta(days=index),
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
        for index, price in enumerate(prices)
    )
    return HistoricalDataSet(
        HistoricalDataRequest("QQQ", START, START + timedelta(days=len(points) - 1)),
        points,
        DataSource.API_FRESH,
    )


def test_existing_evaluation_and_allocation_results_are_assembled_without_recalculation() -> None:
    dataset = _qqq_data()
    price_field = PriceField.RAW_CLOSE
    rule_group = RuleGroup(
        LogicalOperator.AND,
        (
            Condition(
                Operand("QQQ", OperandType.PRICE, price_field=price_field),
                ComparisonOperator.GREATER_THAN,
                Operand("QQQ", OperandType.MA, period=2, price_field=price_field),
            ),
        ),
    )
    strategy = StrategyDefinition(
        strategy_id="signal-chain",
        name="Signal Chain",
        description="PHASE 4F local module-chain fixture",
        assets=tuple(AssetReference(symbol) for symbol in ("QQQ", "TQQQ", "SGOV")),
        price_field=price_field,
        rules=(
            AllocationRule(
                "risk-on",
                "Risk On",
                100,
                (
                    Allocation("QQQ", 0.7),
                    Allocation("TQQQ", 0.2),
                    Allocation("SGOV", 0.1),
                ),
                rule_group,
            ),
        ),
        fallback=FallbackAllocation((Allocation("SGOV", 1.0),)),
        rebalance_policy=RebalancePolicy(RebalanceFrequency.DAILY),
    )
    version = StrategyVersion(
        strategy_id=strategy.strategy_id,
        version_id="signal-chain-v1",
        version_number=1,
        created_at=datetime(2026, 9, 7, tzinfo=UTC),
        configuration=strategy,
        status=StrategyStatus.ACTIVE,
    )
    context = EvaluationContext.from_components(
        {"QQQ": dataset},
        (("QQQ", moving_average(dataset, period=2, price_field=price_field)),),
    )
    as_of_date = START + timedelta(days=2)

    rule_result = evaluate_rule_group(
        rule_group, context, as_of_date, rule_group_id="risk-on"
    )
    allocation_result = resolve_allocations(
        strategy, {"risk-on": rule_result}, as_of_date
    )
    signal = build_signal(
        version,
        rule_result,
        allocation_result,
        source_data_reference="local-module-chain",
    )

    assert rule_result.passed
    assert signal.condition_results is rule_result
    assert signal.target_allocation is allocation_result
    assert signal.matched_rule_id == "risk-on"
    assert signal.allocation_source is AllocationSource.RULE_MATCH
    assert signal.price_field_used is PriceField.RAW_CLOSE
    assert signal.target_allocation.weights == {"QQQ": 0.7, "TQQQ": 0.2, "SGOV": 0.1}
