"""Pure position-policy calculations between target intent and orders."""

from __future__ import annotations

import math
from collections.abc import Mapping
from datetime import date

from backtest.models import (
    PositionRebalancePolicy,
    RebalanceDecision,
    RebalanceDecisionType,
    RebalanceSuppressionReason,
    TargetAllocation,
)

_EPSILON = 1e-12


def actual_allocation(
    *, cash: float, quantities: Mapping[str, int], prices: Mapping[str, float]
) -> dict[str, float]:
    """Return mark-to-market weights, keeping CASH distinct from securities."""

    values = {symbol: quantities.get(symbol, 0) * prices[symbol] for symbol in prices}
    equity = cash + sum(values.values())
    if not math.isfinite(equity) or equity <= 0:
        return {**{symbol: 0.0 for symbol in sorted(prices)}, "CASH": 1.0}
    return {
        **{symbol: values[symbol] / equity for symbol in sorted(values)},
        "CASH": cash / equity,
    }


def _target_weights(target: TargetAllocation) -> dict[str, float]:
    weights = dict(target.as_mapping())
    weights["CASH"] = max(0.0, 1.0 - sum(weights.values()))
    return weights


def _metric(left: Mapping[str, float], right: Mapping[str, float]) -> float:
    symbols = set(left) | set(right)
    return max(
        (abs(left.get(symbol, 0.0) - right.get(symbol, 0.0)) for symbol in symbols), default=0.0
    )


def _turnover(left: Mapping[str, float], right: Mapping[str, float]) -> float:
    # Half the L1 distance is a one-way notional estimate. It deliberately
    # differs from analytics turnover, which is calculated from executed fills.
    symbols = set(left) | set(right)
    return 0.5 * sum(abs(left.get(symbol, 0.0) - right.get(symbol, 0.0)) for symbol in symbols)


def evaluate_rebalance_decision(
    *,
    evaluation_date: date,
    target: TargetAllocation,
    actual: Mapping[str, float],
    previous_target: Mapping[str, float],
    policy: PositionRebalancePolicy,
    contribution_amount: float = 0.0,
) -> RebalanceDecision:
    """Evaluate policy without mutating canonical target or portfolio state."""

    target_weights = _target_weights(target)
    actual_weights = dict(actual)
    previous_weights = dict(previous_target) if previous_target else {"CASH": 1.0}
    target_change = _metric(target_weights, previous_weights)
    drift = _metric(actual_weights, target_weights)
    turnover = _turnover(actual_weights, target_weights)
    reasons: list[str] = []
    suppressed: RebalanceSuppressionReason | None = None

    for constraint in policy.allocation_constraints:
        weight = target_weights.get(constraint.symbol, 0.0)
        if not constraint.minimum_weight <= weight <= constraint.maximum_weight:
            suppressed = RebalanceSuppressionReason.ALLOCATION_CONSTRAINT
            break
    if target_weights.get("CASH", 0.0) + _EPSILON < policy.minimum_cash_reserve:
        suppressed = RebalanceSuppressionReason.MINIMUM_CASH_RESERVE

    target_trigger = target_change > _EPSILON
    if target_trigger:
        reasons.append("target_change")
    if policy.drift_threshold is not None and drift >= policy.drift_threshold:
        reasons.append("drift_threshold")
    if contribution_amount > _EPSILON:
        reasons.append("contribution")

    if suppressed is None and target_trigger:
        if (
            policy.minimum_allocation_change is not None
            and target_change < policy.minimum_allocation_change
        ):
            suppressed = RebalanceSuppressionReason.BELOW_MINIMUM_ALLOCATION_CHANGE
    if suppressed is None and not target_trigger:
        if policy.drift_threshold is not None:
            if drift < policy.drift_threshold:
                suppressed = RebalanceSuppressionReason.BELOW_DRIFT_THRESHOLD
        elif contribution_amount <= _EPSILON or drift <= _EPSILON:
            suppressed = RebalanceSuppressionReason.NO_MATERIAL_CHANGE
    if suppressed is None and policy.maximum_turnover is not None:
        if turnover > policy.maximum_turnover + _EPSILON:
            suppressed = RebalanceSuppressionReason.TURNOVER_LIMIT
    if suppressed is None and not reasons:
        suppressed = RebalanceSuppressionReason.NO_MATERIAL_CHANGE
    if suppressed is None and not target_trigger and drift <= _EPSILON:
        suppressed = RebalanceSuppressionReason.NO_MATERIAL_CHANGE

    decision = RebalanceDecisionType.SUPPRESS if suppressed else RebalanceDecisionType.EXECUTE
    return RebalanceDecision(
        evaluation_date=evaluation_date,
        execution_date=None,
        target_allocation=target_weights,
        actual_allocation=actual_weights,
        previous_target_allocation=previous_weights,
        decision=decision,
        reasons=tuple(reasons),
        target_change_metric=target_change,
        drift_metric=drift,
        turnover_estimate=turnover,
        minimum_cash_reserve=policy.minimum_cash_reserve,
        suppression_reason=suppressed,
        contribution_amount=contribution_amount,
    )
