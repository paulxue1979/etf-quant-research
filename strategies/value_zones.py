"""Deterministic value-zone evidence and hysteresis resolution."""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import date
from typing import Any

from strategies.enums import ComparisonOperator, OperandType
from strategies.evaluation import EvaluationContext
from strategies.exceptions import (
    InvalidConditionConfigurationError,
    InvalidEvaluationValueError,
    ZeroReferenceValueError,
)
from strategies.models import Operand, StrategyDefinition
from strategies.operand_evaluator import evaluate_operand


@dataclass(frozen=True)
class ValueZoneEvidence:
    """Auditable evidence for one zone evaluated at one date."""

    zone_id: str
    evaluation_date: date
    asset: str
    timeframe: str
    indicator_kind: str
    period: int
    priority: int
    price_field: str
    reference_price: float
    indicator_value: float
    distance: float
    entry_threshold: float
    exit_threshold: float | None
    entry_operator: ComparisonOperator
    exit_operator: ComparisonOperator
    entry_matches: bool
    exit_matches: bool
    price_source_date: date
    indicator_source_date: date
    was_current: bool = False

    @property
    def source_date(self) -> date:
        """Return the latest source date contributing to this evidence."""
        return max(self.price_source_date, self.indicator_source_date)

    def to_dict(self) -> dict[str, Any]:
        return {
            "zone_id": self.zone_id,
            "evaluation_date": self.evaluation_date.isoformat(),
            "asset": self.asset,
            "timeframe": self.timeframe,
            "indicator_kind": self.indicator_kind,
            "period": self.period,
            "priority": self.priority,
            "price_field": self.price_field,
            "reference_price": self.reference_price,
            "indicator_value": self.indicator_value,
            "distance": self.distance,
            "entry_threshold": self.entry_threshold,
            "exit_threshold": self.exit_threshold,
            "entry_operator": self.entry_operator.value,
            "exit_operator": self.exit_operator.value,
            "entry_matches": self.entry_matches,
            "exit_matches": self.exit_matches,
            "price_source_date": self.price_source_date.isoformat(),
            "indicator_source_date": self.indicator_source_date.isoformat(),
            "source_date": self.source_date.isoformat(),
            "was_current": self.was_current,
        }


@dataclass(frozen=True)
class ValueZoneResolution:
    """One deterministic resolution of all configured zones."""

    evaluation_date: date
    matched_zone_id: str | None
    exited_zone_id: str | None
    transition_type: str
    evidence: tuple[ValueZoneEvidence, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "evaluation_date": self.evaluation_date.isoformat(),
            "matched_zone_id": self.matched_zone_id,
            "exited_zone_id": self.exited_zone_id,
            "transition_type": self.transition_type,
            "evidence": [item.to_dict() for item in self.evidence],
        }


def resolve_value_zones(
    strategy: StrategyDefinition,
    context: EvaluationContext,
    as_of_date: date,
    current_zone_id: str | None = None,
) -> ValueZoneResolution:
    """Resolve zone membership using existing operand evaluation semantics.

    Zones are ordered by explicit priority and then by ``zone_id``.  The current
    zone is retained until its exit band matches; a higher-priority matching
    zone may still take over, which gives nested bands deterministic resolution.
    """
    if not isinstance(strategy, StrategyDefinition):
        raise TypeError("strategy must be a StrategyDefinition")
    if not isinstance(context, EvaluationContext):
        raise TypeError("context must be an EvaluationContext")
    if not isinstance(as_of_date, date):
        raise TypeError("as_of_date must be a date")
    zones = tuple(sorted(strategy.value_zones, key=lambda item: (item.priority, item.zone_id)))
    if not zones:
        return ValueZoneResolution(as_of_date, None, None, "none", ())
    if current_zone_id is not None and current_zone_id not in {zone.zone_id for zone in zones}:
        raise InvalidConditionConfigurationError("current value zone is not declared by strategy")

    evidence: list[ValueZoneEvidence] = []
    for zone in zones:
        price = evaluate_operand(
            Operand(
                zone.asset,
                OperandType.PRICE,
                price_field=zone.price_field,
                timeframe=zone.timeframe,
            ),
            context,
            as_of_date,
        )
        indicator_type = OperandType.MA if zone.indicator_kind.value == "ma" else OperandType.EMA
        indicator = evaluate_operand(
            Operand(
                zone.asset,
                indicator_type,
                period=zone.period,
                price_field=zone.price_field,
                timeframe=zone.timeframe,
            ),
            context,
            as_of_date,
        )
        if indicator.value == 0:
            raise ZeroReferenceValueError("value zone indicator reference must not be zero")
        distance = price.value / indicator.value - 1.0
        if not math.isfinite(distance):
            raise InvalidEvaluationValueError("value zone distance must be finite")
        evidence.append(
            ValueZoneEvidence(
                zone_id=zone.zone_id,
                evaluation_date=as_of_date,
                asset=zone.asset.symbol,
                timeframe=zone.timeframe.value,
                indicator_kind=zone.indicator_kind.value,
                period=zone.period,
                priority=zone.priority,
                price_field=zone.price_field.value,
                reference_price=price.value,
                indicator_value=indicator.value,
                distance=distance,
                entry_threshold=zone.entry_threshold,
                exit_threshold=zone.exit_threshold,
                entry_operator=zone.entry_operator,
                exit_operator=zone.exit_operator,
                entry_matches=_compare(distance, zone.entry_threshold, zone.entry_operator),
                exit_matches=(
                    zone.exit_threshold is not None
                    and _compare(distance, zone.exit_threshold, zone.exit_operator)
                ),
                price_source_date=price.source_date or price.date,
                indicator_source_date=indicator.source_date or indicator.date,
                was_current=zone.zone_id == current_zone_id,
            )
        )

    by_id = {item.zone_id: item for item in evidence}
    candidates = [item for item in evidence if item.entry_matches]
    current = by_id.get(current_zone_id) if current_zone_id is not None else None
    exited_zone_id: str | None = None
    transition_type = "none"
    matched_zone_id: str | None = None
    if current is None:
        if candidates:
            matched_zone_id = candidates[0].zone_id
            transition_type = "enter"
    elif not current.exit_matches:
        matched_zone_id = current.zone_id
        current_priority = by_id[current.zone_id].priority
        stronger = [
            item
            for item in candidates
            if item.zone_id != current.zone_id and item.priority < current_priority
        ]
        if stronger and (stronger[0].zone_id != current.zone_id):
            matched_zone_id = stronger[0].zone_id
            transition_type = "upgrade"
        else:
            transition_type = "stay"
    else:
        exited_zone_id = current.zone_id
        alternatives = [item for item in candidates if item.zone_id != current.zone_id]
        if alternatives:
            matched_zone_id = alternatives[0].zone_id
            transition_type = "enter"
        else:
            transition_type = "exit"
    return ValueZoneResolution(
        evaluation_date=as_of_date,
        matched_zone_id=matched_zone_id,
        exited_zone_id=exited_zone_id,
        transition_type=transition_type,
        evidence=tuple(evidence),
    )


def _compare(left: float, right: float, operator: ComparisonOperator) -> bool:
    if not math.isfinite(left) or not math.isfinite(right):
        raise InvalidEvaluationValueError("value zone comparison values must be finite")
    if operator is ComparisonOperator.GREATER_THAN:
        return left > right
    if operator is ComparisonOperator.GREATER_OR_EQUAL:
        return left >= right
    if operator is ComparisonOperator.LESS_THAN:
        return left < right
    if operator is ComparisonOperator.LESS_OR_EQUAL:
        return left <= right
    raise InvalidConditionConfigurationError("value zones do not support the equal comparator")


__all__ = ["ValueZoneEvidence", "ValueZoneResolution", "resolve_value_zones"]
