from __future__ import annotations

from datetime import date, timedelta

from backend.app.backtest_models import deserialize_backtest_result, serialize_backtest_result
from backtest import (
    AllocationConstraint,
    BacktestConfig,
    BacktestEngine,
    ContributionFrequency,
    ContributionSchedule,
    PositionRebalancePolicy,
    RebalanceDecisionType,
    RebalanceFrequency,
    RebalancePolicy,
    RebalanceSuppressionReason,
    TargetAllocation,
)
from data.models import (
    DataSource,
    HistoricalDataRequest,
    HistoricalDataSet,
    MarketDataPoint,
    PriceField,
)
from research.oos import OosEvaluationConfig


def _dataset(symbol: str, prices: list[float]) -> HistoricalDataSet:
    start = date(2024, 1, 2)
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
        for index, price in enumerate(prices)
    )
    return HistoricalDataSet(
        request=HistoricalDataRequest(
            symbol=symbol,
            start_date=start,
            end_date=start + timedelta(days=len(points) - 1),
        ),
        points=points,
        source=DataSource.API_FRESH,
    )


def _config(
    policy: PositionRebalancePolicy,
    *,
    frequency: RebalanceFrequency = RebalanceFrequency.DAILY,
    contribution_schedule: ContributionSchedule | None = None,
) -> BacktestConfig:
    return BacktestConfig(
        strategy_version_id="policy-v1",
        start_date=date(2024, 1, 2),
        end_date=date(2024, 1, 6),
        initial_capital=1_000.0,
        price_field_used=PriceField.RAW_CLOSE,
        rebalance_policy=RebalancePolicy(frequency),
        contribution_schedule=contribution_schedule,
        position_rebalance_policy=policy,
    )


def test_minimum_change_suppresses_execution_without_mutating_intent() -> None:
    result = BacktestEngine().run(
        {"QQQ": _dataset("QQQ", [10, 10, 10, 10, 10])},
        [
            TargetAllocation.from_weights(date(2024, 1, 2), {"QQQ": 1.0}),
            TargetAllocation.from_weights(date(2024, 1, 3), {"QQQ": 0.99}),
        ],
        _config(PositionRebalancePolicy(minimum_allocation_change=0.02)),
    )

    assert len(result.fills) == 1
    assert result.rebalance_decisions[0].decision is RebalanceDecisionType.EXECUTE
    suppressed = result.rebalance_decisions[1]
    assert suppressed.decision is RebalanceDecisionType.SUPPRESS
    assert (
        suppressed.suppression_reason is RebalanceSuppressionReason.BELOW_MINIMUM_ALLOCATION_CHANGE
    )
    assert result.allocation_history[-1].target_weight == 0.99


def test_turnover_limit_hard_suppresses_without_partial_orders() -> None:
    result = BacktestEngine().run(
        {"QQQ": _dataset("QQQ", [10, 10, 10, 10, 10])},
        [TargetAllocation.from_weights(date(2024, 1, 2), {"QQQ": 1.0})],
        _config(PositionRebalancePolicy(maximum_turnover=0.5)),
    )

    assert result.fills == ()
    assert (
        result.rebalance_decisions[0].suppression_reason
        is RebalanceSuppressionReason.TURNOVER_LIMIT
    )
    assert result.rebalance_decisions[0].turnover_estimate == 1.0


def test_cash_reserve_and_constraints_are_validated_as_intent_constraints() -> None:
    policy = PositionRebalancePolicy(
        minimum_cash_reserve=0.2,
        allocation_constraints=(AllocationConstraint("QQQ", maximum_weight=0.8),),
    )
    result = BacktestEngine().run(
        {"QQQ": _dataset("QQQ", [10, 10, 10, 10, 10])},
        [TargetAllocation.from_weights(date(2024, 1, 2), {"QQQ": 1.0})],
        _config(policy),
    )

    assert result.fills == ()
    assert (
        result.rebalance_decisions[0].suppression_reason
        is RebalanceSuppressionReason.MINIMUM_CASH_RESERVE
    )
    assert result.rebalance_decisions[0].target_allocation["QQQ"] == 1.0
    assert result.rebalance_decisions[0].target_allocation["CASH"] == 0.0


def test_custom_policy_is_part_of_backtest_identity_and_round_trips() -> None:
    policy = PositionRebalancePolicy(drift_threshold=0.05)
    config = _config(policy)
    snapshot = config.snapshot({})
    assert snapshot["position_rebalance_policy"]["drift_threshold"] == 0.05
    assert PositionRebalancePolicy.from_dict(snapshot["position_rebalance_policy"]) == policy


def test_rebalance_decisions_round_trip_through_persisted_result_payload() -> None:
    result = BacktestEngine().run(
        {"QQQ": _dataset("QQQ", [10, 10, 10, 10, 10])},
        [TargetAllocation.from_weights(date(2024, 1, 2), {"QQQ": 1.0})],
        _config(PositionRebalancePolicy(maximum_turnover=0.5)),
    )

    restored = deserialize_backtest_result(serialize_backtest_result(result))
    assert restored.rebalance_decisions == result.rebalance_decisions


def test_custom_policy_is_carried_into_oos_configuration_identity() -> None:
    policy = PositionRebalancePolicy(maximum_turnover=0.25, minimum_cash_reserve=0.1)
    config = _config(policy)
    oos_config = OosEvaluationConfig.from_backtest_config(
        config, analytics_version="test-analytics"
    )

    restored = OosEvaluationConfig.from_dict(oos_config.to_dict())
    assert restored == oos_config
    assert restored.configuration_hash == oos_config.configuration_hash


def test_same_day_contribution_and_schedule_create_one_decision_and_one_plan() -> None:
    contribution = ContributionSchedule(
        frequency=ContributionFrequency.ONE_TIME,
        amount=100.0,
        requested_date=date(2024, 1, 3),
    )
    result = BacktestEngine().run(
        {"QQQ": _dataset("QQQ", [10, 10, 10, 10, 10])},
        [
            TargetAllocation.from_weights(date(2024, 1, 2), {"QQQ": 1.0}),
            TargetAllocation.from_weights(date(2024, 1, 3), {"QQQ": 1.0}),
        ],
        _config(
            PositionRebalancePolicy(drift_threshold=0.05),
            contribution_schedule=contribution,
        ),
    )

    same_day = [
        item for item in result.rebalance_decisions if item.evaluation_date == date(2024, 1, 3)
    ]
    assert len(same_day) == 1
    assert same_day[0].reasons == ("contribution", "drift_threshold", "schedule")
    assert len(result.orders) == 2
    assert len(result.fills) == 2


def test_contribution_only_uses_cash_adjusted_actual_once() -> None:
    contribution = ContributionSchedule(
        frequency=ContributionFrequency.ONE_TIME,
        amount=100.0,
        requested_date=date(2024, 1, 3),
    )
    result = BacktestEngine().run(
        {"QQQ": _dataset("QQQ", [10, 10, 10, 10, 10])},
        [TargetAllocation.from_weights(date(2024, 1, 2), {"QQQ": 1.0})],
        _config(
            PositionRebalancePolicy(drift_threshold=0.05),
            frequency=RebalanceFrequency.WEEKLY,
            contribution_schedule=contribution,
        ),
    )

    same_day = [
        item for item in result.rebalance_decisions if item.evaluation_date == date(2024, 1, 3)
    ]
    assert len(same_day) == 1
    assert same_day[0].reasons == ("contribution", "drift_threshold")
    assert same_day[0].actual_allocation["CASH"] > 0.0
    assert result.orders[0].date == date(2024, 1, 3)
    assert result.orders[1].date == date(2024, 1, 4)


def test_cash_target_contribution_is_suppressed_without_security_orders() -> None:
    contribution = ContributionSchedule(
        frequency=ContributionFrequency.ONE_TIME,
        amount=100.0,
        requested_date=date(2024, 1, 3),
    )
    result = BacktestEngine().run(
        {"QQQ": _dataset("QQQ", [10, 10, 10, 10, 10])},
        [TargetAllocation.from_weights(date(2024, 1, 2), {})],
        _config(
            PositionRebalancePolicy(drift_threshold=0.05),
            frequency=RebalanceFrequency.WEEKLY,
            contribution_schedule=contribution,
        ),
    )

    same_day = [
        item for item in result.rebalance_decisions if item.evaluation_date == date(2024, 1, 3)
    ]
    assert len(same_day) == 1
    assert same_day[0].decision is RebalanceDecisionType.SUPPRESS
    assert result.fills == ()


def test_target_change_contribution_drift_and_schedule_are_aggregated() -> None:
    contribution = ContributionSchedule(
        frequency=ContributionFrequency.ONE_TIME,
        amount=100.0,
        requested_date=date(2024, 1, 3),
    )
    result = BacktestEngine().run(
        {
            "QQQ": _dataset("QQQ", [10, 10, 10, 10, 10]),
            "TQQQ": _dataset("TQQQ", [10, 10, 10, 10, 10]),
        },
        [
            TargetAllocation.from_weights(date(2024, 1, 2), {"QQQ": 1.0}),
            TargetAllocation.from_weights(date(2024, 1, 3), {"TQQQ": 1.0}),
        ],
        _config(
            PositionRebalancePolicy(drift_threshold=0.05),
            contribution_schedule=contribution,
        ),
    )

    same_day = [
        item for item in result.rebalance_decisions if item.evaluation_date == date(2024, 1, 3)
    ]
    assert len(same_day) == 1
    assert same_day[0].reasons == (
        "contribution",
        "target_change",
        "drift_threshold",
        "schedule",
    )
    assert len(result.fills) == 3
