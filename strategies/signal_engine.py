"""Pure assembly of immutable, explainable strategy signals."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Any

from data.models import PriceField
from strategies.enums import AllocationSource
from strategies.evaluation import (
    ConditionResult,
    OperandValue,
    RuleGroupResult,
    TargetAllocationResult,
)
from strategies.exceptions import (
    EvaluationError,
    InvalidSignalInputError,
    SignalEvaluationPropagationError,
    SignalInputConsistencyError,
)
from strategies.models import StrategyVersion


@dataclass(frozen=True)
class StrategySignal:
    """Immutable point-in-time signal assembled from prior evaluation results."""

    date: date
    strategy_version_id: str
    matched_rule_id: str | None
    allocation_source: AllocationSource
    condition_results: RuleGroupResult | None
    target_allocation: TargetAllocationResult
    price_field_used: PriceField
    explanation: str
    source_data_reference: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.date, date):
            raise TypeError("signal date must be a date")
        if not isinstance(self.strategy_version_id, str) or not self.strategy_version_id.strip():
            raise InvalidSignalInputError("strategy_version_id must not be empty")
        if self.matched_rule_id is not None and (
            not isinstance(self.matched_rule_id, str) or not self.matched_rule_id.strip()
        ):
            raise InvalidSignalInputError("matched_rule_id must be a non-empty string or None")
        if not isinstance(self.allocation_source, AllocationSource):
            try:
                object.__setattr__(
                    self, "allocation_source", AllocationSource(self.allocation_source)
                )
            except (TypeError, ValueError) as exc:
                raise InvalidSignalInputError("allocation_source is invalid") from exc
        if self.condition_results is not None and not isinstance(
            self.condition_results, RuleGroupResult
        ):
            raise InvalidSignalInputError("condition_results must be a RuleGroupResult or None")
        if not isinstance(self.target_allocation, TargetAllocationResult):
            raise InvalidSignalInputError("target_allocation must be a TargetAllocationResult")
        if not isinstance(self.price_field_used, PriceField):
            try:
                object.__setattr__(self, "price_field_used", PriceField(self.price_field_used))
            except (TypeError, ValueError) as exc:
                raise InvalidSignalInputError("price_field_used is invalid") from exc
        if not isinstance(self.explanation, str) or not self.explanation.strip():
            raise InvalidSignalInputError("signal explanation must not be empty")
        if self.source_data_reference is not None and (
            not isinstance(self.source_data_reference, str)
            or not self.source_data_reference.strip()
        ):
            raise InvalidSignalInputError(
                "source_data_reference must be a non-empty string or None"
            )
        if self.condition_results is not None and self.condition_results.date != self.date:
            raise SignalInputConsistencyError("signal date must match condition_results.date")
        if self.target_allocation.date != self.date:
            raise SignalInputConsistencyError("signal date must match target_allocation.date")
        if self.allocation_source is AllocationSource.FALLBACK:
            if self.matched_rule_id is not None:
                raise SignalInputConsistencyError(
                    "fallback signals must not have a matched_rule_id"
                )
        elif not self.matched_rule_id:
            raise SignalInputConsistencyError("rule-match signals require a matched_rule_id")
        object.__setattr__(self, "strategy_version_id", self.strategy_version_id.strip())
        object.__setattr__(
            self,
            "matched_rule_id",
            self.matched_rule_id.strip() if self.matched_rule_id else None,
        )
        if self.source_data_reference is not None:
            object.__setattr__(self, "source_data_reference", self.source_data_reference.strip())

    @property
    def rule_group_result(self) -> RuleGroupResult | None:
        """Alias naming the complete condition evaluation tree."""
        return self.condition_results

    def to_dict(self) -> dict[str, Any]:
        """Return a deterministic, JSON-compatible provenance snapshot."""
        return {
            "date": self.date.isoformat(),
            "strategy_version_id": self.strategy_version_id,
            "matched_rule_id": self.matched_rule_id,
            "allocation_source": self.allocation_source.value,
            "condition_results": (
                _rule_group_to_dict(self.condition_results)
                if self.condition_results is not None
                else None
            ),
            "target_allocation": {
                "date": self.target_allocation.date.isoformat(),
                "matched_rule_id": self.target_allocation.matched_rule_id,
                "used_fallback": self.target_allocation.used_fallback,
                "allocations": [item.to_dict() for item in self.target_allocation.allocations],
                "remaining_weight": self.target_allocation.remaining_weight,
                "cash_buffer": self.target_allocation.cash_buffer,
                "explanation": self.target_allocation.explanation,
            },
            "price_field_used": self.price_field_used.value,
            "explanation": self.explanation,
            "source_data_reference": self.source_data_reference,
        }


def build_signal(
    strategy_version: StrategyVersion,
    rule_group_result: RuleGroupResult | EvaluationError | None,
    target_allocation: TargetAllocationResult | EvaluationError,
    *,
    source_data_reference: str | None = None,
) -> StrategySignal:
    """Assemble a signal without evaluating or resolving any upstream input."""
    if not isinstance(strategy_version, StrategyVersion):
        raise InvalidSignalInputError("strategy_version must be a StrategyVersion")
    if isinstance(rule_group_result, EvaluationError):
        raise SignalEvaluationPropagationError(
            "rule-group evaluation failed before signal assembly", cause=rule_group_result
        ) from rule_group_result
    if rule_group_result is None and any(
        rule.condition is not None for rule in strategy_version.configuration.rules
    ):
        raise InvalidSignalInputError(
            "rule_group_result is required when a strategy has conditional rules"
        )
    if rule_group_result is not None and not isinstance(rule_group_result, RuleGroupResult):
        raise InvalidSignalInputError("rule_group_result must be a RuleGroupResult or None")
    if isinstance(target_allocation, EvaluationError):
        raise SignalEvaluationPropagationError(
            "allocation resolution failed before signal assembly", cause=target_allocation
        ) from target_allocation
    if target_allocation is None or not isinstance(target_allocation, TargetAllocationResult):
        raise InvalidSignalInputError("target_allocation must be a TargetAllocationResult")
    if source_data_reference is not None and (
        not isinstance(source_data_reference, str) or not source_data_reference.strip()
    ):
        raise InvalidSignalInputError("source_data_reference must be a non-empty string or None")
    if rule_group_result is not None and rule_group_result.date != target_allocation.date:
        raise SignalInputConsistencyError(
            "rule_group_result.date must match target_allocation.date"
        )

    if rule_group_result is not None:
        _validate_price_fields(rule_group_result, strategy_version.configuration.price_field)
    matched_rule_id, allocation_source = _resolve_provenance(
        strategy_version, rule_group_result, target_allocation
    )
    explanation = _build_explanation(
        strategy_version,
        rule_group_result,
        target_allocation,
        allocation_source,
        matched_rule_id,
    )
    return StrategySignal(
        date=target_allocation.date,
        strategy_version_id=strategy_version.version_id,
        matched_rule_id=matched_rule_id,
        allocation_source=allocation_source,
        condition_results=rule_group_result,
        target_allocation=target_allocation,
        price_field_used=strategy_version.configuration.price_field,
        explanation=explanation,
        source_data_reference=source_data_reference,
    )


def _resolve_provenance(
    strategy_version: StrategyVersion,
    rule_group_result: RuleGroupResult | None,
    target_allocation: TargetAllocationResult,
) -> tuple[str | None, AllocationSource]:
    if target_allocation.used_fallback:
        expected_fallback_id = strategy_version.configuration.fallback.name
        if target_allocation.matched_rule_id != expected_fallback_id:
            raise SignalInputConsistencyError(
                "fallback target allocation must identify the configured fallback"
            )
        return None, AllocationSource.FALLBACK
    matched_rule_id = target_allocation.matched_rule_id
    if not matched_rule_id:
        raise SignalInputConsistencyError(
            "non-fallback target allocation must identify a matched rule"
        )
    rules = {rule.rule_id: rule for rule in strategy_version.configuration.rules}
    rule = rules.get(matched_rule_id)
    if rule is None:
        raise SignalInputConsistencyError(
            f"target allocation references unknown rule {matched_rule_id}"
        )
    if rule.condition is not None and (rule_group_result is None or not rule_group_result.passed):
        raise SignalInputConsistencyError(
            f"matched conditional rule {matched_rule_id} has a non-passing result"
        )
    return matched_rule_id, AllocationSource.RULE_MATCH


def _validate_price_fields(result: RuleGroupResult, expected: PriceField) -> None:
    fields: set[PriceField] = set()
    _collect_price_fields(result, fields)
    if fields and (len(fields) != 1 or expected not in fields):
        values = ", ".join(sorted(field.value for field in fields))
        raise SignalInputConsistencyError(
            f"condition results use price fields [{values}], expected {expected.value}"
        )


def _collect_price_fields(result: RuleGroupResult, fields: set[PriceField]) -> None:
    for child in result.child_results:
        if isinstance(child, ConditionResult):
            for operand in (child.left_operand_result, child.right_operand_result):
                if operand.price_field_used is not None:
                    fields.add(operand.price_field_used)
        else:
            _collect_price_fields(child, fields)


def _build_explanation(
    strategy_version: StrategyVersion,
    rule_group_result: RuleGroupResult | None,
    target_allocation: TargetAllocationResult,
    allocation_source: AllocationSource,
    matched_rule_id: str | None,
) -> str:
    selected = (
        f"rule {matched_rule_id}"
        if allocation_source is AllocationSource.RULE_MATCH
        else "fallback"
    )
    return (
        f"StrategySignal(date={target_allocation.date.isoformat()}, "
        f"strategy_version_id={strategy_version.version_id}, selected={selected}, "
        f"price_field={strategy_version.configuration.price_field.value}); "
        f"rule_group={rule_group_result.explanation if rule_group_result else 'none'}; "
        f"target_allocation={target_allocation.explanation}"
    )


def _rule_group_to_dict(result: RuleGroupResult) -> dict[str, Any]:
    return {
        "rule_group_id": result.rule_group_id,
        "date": result.date.isoformat(),
        "operator": result.operator.value,
        "passed": result.passed,
        "child_results": [
            (
                _condition_to_dict(child)
                if isinstance(child, ConditionResult)
                else _rule_group_to_dict(child)
            )
            for child in result.child_results
        ],
        "explanation": result.explanation,
    }


def _condition_to_dict(result: ConditionResult) -> dict[str, Any]:
    return {
        "condition_id": result.condition_id,
        "date": result.date.isoformat(),
        "passed": result.passed,
        "left_operand_result": _operand_to_dict(result.left_operand_result),
        "right_operand_result": _operand_to_dict(result.right_operand_result),
        "operator": result.operator.value,
        "threshold": result.threshold.to_dict() if result.threshold is not None else None,
        "effective_right_value": result.effective_right_value,
        "explanation": result.explanation,
    }


def _operand_to_dict(operand: OperandValue) -> dict[str, Any]:
    return {
        "value": operand.value,
        "asset": operand.asset,
        "operand_type": operand.operand_type.value,
        "price_field_used": (
            operand.price_field_used.value if operand.price_field_used is not None else None
        ),
        "date": operand.date.isoformat(),
    }


__all__ = ["StrategySignal", "build_signal"]
