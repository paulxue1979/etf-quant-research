"""Pure comparison and relative-threshold evaluation for PHASE 4C."""

from __future__ import annotations

import math
from datetime import date

from strategies.enums import ComparisonOperator, ThresholdType
from strategies.evaluation import ConditionResult, EvaluationContext, OperandValue
from strategies.exceptions import (
    InvalidConditionConfigurationError,
    InvalidEvaluationValueError,
    ZeroReferenceValueError,
)
from strategies.models import Condition
from strategies.operand_evaluator import evaluate_operand


def evaluate_condition(
    condition: Condition,
    context: EvaluationContext,
    as_of_date: date,
    *,
    condition_id: str = "condition",
) -> ConditionResult:
    """Evaluate one condition at one date using only caller-provided inputs."""
    if not isinstance(condition, Condition):
        raise TypeError("condition must be a Condition value")
    if not isinstance(context, EvaluationContext):
        raise TypeError("context must be an EvaluationContext value")
    if not isinstance(as_of_date, date):
        raise TypeError("as_of_date must be a date")
    if not isinstance(condition_id, str) or not condition_id.strip():
        raise ValueError("condition_id must not be empty")

    left = evaluate_operand(condition.left, context, as_of_date)
    right = evaluate_operand(condition.right, context, as_of_date)
    _validate_price_field_consistency(left, right)

    effective_right = right.value
    threshold = condition.threshold
    if threshold is not None:
        if threshold.threshold_type is ThresholdType.ABSOLUTE:
            raise InvalidConditionConfigurationError(
                "absolute thresholds are reserved and not supported in PHASE 4C"
            )
        if condition.operator is ComparisonOperator.EQUAL:
            raise InvalidConditionConfigurationError(
                "relative threshold is not supported with the equal operator"
            )
        if right.value == 0:
            raise ZeroReferenceValueError(
                "relative threshold reference value must not be zero"
            )
        effective_right = right.value * (1.0 + threshold.value)

    if not math.isfinite(effective_right):
        raise InvalidEvaluationValueError("effective threshold value must be finite")

    passed = _compare(left.value, effective_right, condition.operator)
    operator_text = {
        ComparisonOperator.GREATER_THAN: ">",
        ComparisonOperator.GREATER_OR_EQUAL: ">=",
        ComparisonOperator.LESS_THAN: "<",
        ComparisonOperator.LESS_OR_EQUAL: "<=",
        ComparisonOperator.EQUAL: "==",
    }[condition.operator]
    threshold_text = ""
    if threshold is not None:
        threshold_text = f" with relative threshold {threshold.value:.12g}"
    explanation = (
        f"{_describe_operand(left)}={left.value:.12g} {operator_text} "
        f"{_describe_operand(right)}={right.value:.12g}; "
        f"effective_right={effective_right:.12g}{threshold_text}; "
        f"result={'TRUE' if passed else 'FALSE'}"
    )
    return ConditionResult(
        condition_id=condition_id,
        date=as_of_date,
        passed=passed,
        left_operand_result=left,
        right_operand_result=right,
        operator=condition.operator,
        threshold=threshold,
        effective_right_value=effective_right,
        explanation=explanation,
    )


def _validate_price_field_consistency(left: OperandValue, right: OperandValue) -> None:
    fields = {
        item.price_field_used
        for item in (left, right)
        if item.price_field_used is not None
    }
    if len(fields) > 1:
        raise InvalidConditionConfigurationError(
            "condition operands must use the same price_field"
        )


def _compare(left: float, right: float, operator: ComparisonOperator) -> bool:
    if not math.isfinite(left) or not math.isfinite(right):
        raise InvalidEvaluationValueError("condition values must be finite")
    if operator is ComparisonOperator.GREATER_THAN:
        return left > right
    if operator is ComparisonOperator.GREATER_OR_EQUAL:
        return left >= right
    if operator is ComparisonOperator.LESS_THAN:
        return left < right
    if operator is ComparisonOperator.LESS_OR_EQUAL:
        return left <= right
    if operator is ComparisonOperator.EQUAL:
        return left == right
    raise InvalidConditionConfigurationError("unsupported comparison operator")


def _describe_operand(value: OperandValue) -> str:
    if value.operand_type.value == "constant":
        return "CONSTANT"
    return f"{value.operand_type.value.upper()}({value.asset})"


__all__ = ["evaluate_condition"]
