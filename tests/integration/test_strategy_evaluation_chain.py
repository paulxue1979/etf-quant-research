"""Local PHASE 4C-to-4G chain coverage; no network or Tiingo dependency."""

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
    StrategyEvaluationStatus,
    StrategyVersion,
    evaluate_strategy,
)


def test_local_condition_to_signal_to_timeline_chain() -> None:
    start = date(2026, 2, 2)
    points = tuple(
        MarketDataPoint(
            date=start + timedelta(days=index),
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
        for index, price in enumerate((100.0, 110.0))
    )
    data = {
        symbol: HistoricalDataSet(
            HistoricalDataRequest(
                symbol, start, start + timedelta(days=1), price_field_used=PriceField.RAW_CLOSE
            ),
            points,
            DataSource.API_FRESH,
        )
        for symbol in ("QQQ", "TQQQ", "SGOV")
    }
    condition = Condition(
        Operand("QQQ", OperandType.PRICE, price_field=PriceField.RAW_CLOSE),
        ComparisonOperator.GREATER_THAN,
        Operand("QQQ", OperandType.MA, period=2, price_field=PriceField.RAW_CLOSE),
    )
    definition = StrategyDefinition(
        strategy_id="chain",
        name="Chain",
        description="Local integration fixture",
        assets=tuple(AssetReference(symbol) for symbol in data),
        price_field=PriceField.RAW_CLOSE,
        rules=(
            AllocationRule(
                "risk-on",
                "Risk On",
                100,
                (Allocation("TQQQ", 1.0),),
                RuleGroup(LogicalOperator.AND, (condition,)),
            ),
        ),
        fallback=FallbackAllocation((Allocation("SGOV", 1.0),)),
        rebalance_policy=RebalancePolicy(RebalanceFrequency.DAILY),
    )
    version = StrategyVersion(
        strategy_id=definition.strategy_id,
        version_id="chain-v1",
        version_number=1,
        created_at=datetime(2026, 2, 1, tzinfo=UTC),
        configuration=definition,
    )
    context = EvaluationContext.from_components(
        data,
        (("QQQ", moving_average(data["QQQ"], period=2, price_field=PriceField.RAW_CLOSE)),),
    )

    result = evaluate_strategy(
        version, context, start + timedelta(days=1), start + timedelta(days=1)
    ).evaluations[0]

    assert result.status is StrategyEvaluationStatus.EVALUATED
    assert result.rule_group_results[0].passed
    assert result.target_allocation is not None
    assert result.target_allocation.weights == {"TQQQ": 1.0}
    assert result.signal is not None
    assert result.signal.matched_rule_id == "risk-on"
    assert result.signal.condition_results is not None
