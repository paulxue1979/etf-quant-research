"""Pure recursive AND/OR composition for PHASE 4D."""

from __future__ import annotations

from datetime import date

from strategies.condition_evaluator import evaluate_condition
from strategies.enums import LogicalOperator
from strategies.evaluation import (
    ConditionResult,
    EvaluationContext,
    RuleGroupResult,
)
from strategies.exceptions import (
    EvaluationError,
    InvalidRuleGroupError,
    RuleGroupEvaluationError,
)
from strategies.models import Condition, RuleGroup


def evaluate_rule_group(
    rule_group: RuleGroup,
    context: EvaluationContext,
    as_of_date: date,
    *,
    rule_group_id: str = "rule-group",
) -> RuleGroupResult:
    """Evaluate every child recursively and combine results with AND or OR."""
    if not isinstance(rule_group, RuleGroup):
        raise TypeError("rule_group must be a RuleGroup value")
    if not isinstance(context, EvaluationContext):
        raise TypeError("context must be an EvaluationContext value")
    if not isinstance(as_of_date, date):
        raise TypeError("as_of_date must be a date")
    _validate_group_identity(rule_group_id)
    return _evaluate_group(rule_group, context, as_of_date, rule_group_id)


def _evaluate_group(
    rule_group: RuleGroup,
    context: EvaluationContext,
    as_of_date: date,
    rule_group_id: str,
) -> RuleGroupResult:
    _validate_rule_group_shape(rule_group)
    results: list[ConditionResult | RuleGroupResult] = []
    errors: list[Exception] = []

    for index, child in enumerate(rule_group.children):
        child_id = f"{rule_group_id}.children[{index}]"
        try:
            if isinstance(child, Condition):
                results.append(
                    evaluate_condition(child, context, as_of_date, condition_id=child_id)
                )
            elif isinstance(child, RuleGroup):
                results.append(_evaluate_group(child, context, as_of_date, child_id))
            else:  # Defensive check for malformed objects bypassing the domain model.
                raise InvalidRuleGroupError(
                    "rule group children must be Condition or RuleGroup values"
                )
        except (EvaluationError, InvalidRuleGroupError) as exc:
            errors.append(exc)

    if errors:
        error = RuleGroupEvaluationError(
            f"rule group {rule_group_id} has {len(errors)} child evaluation error(s)",
            rule_group_id=rule_group_id,
            evaluation_date=as_of_date,
            child_errors=tuple(errors),
            evaluated_children=tuple(results),
        )
        error.partial_result = None  # type: ignore[attr-defined]
        raise error

    passed = _combine(rule_group.operator, results)
    child_text = "; ".join(
        f"{_child_identity(result)}={'TRUE' if result.passed else 'FALSE'}"
        for result in results
    )
    explanation = (
        f"RuleGroup({rule_group_id}) {rule_group.operator.value.upper()}: "
        f"{child_text}; result={'TRUE' if passed else 'FALSE'}"
    )
    return RuleGroupResult(
        rule_group_id=rule_group_id,
        date=as_of_date,
        operator=rule_group.operator,
        passed=passed,
        child_results=tuple(results),
        explanation=explanation,
    )


def _validate_rule_group_shape(rule_group: RuleGroup) -> None:
    operator = getattr(rule_group, "operator", None)
    children = getattr(rule_group, "children", None)
    if not isinstance(operator, LogicalOperator):
        raise InvalidRuleGroupError("rule group operator must be AND or OR")
    if not children:
        raise InvalidRuleGroupError("rule group children must not be empty")
    if not all(isinstance(child, (Condition, RuleGroup)) for child in children):
        raise InvalidRuleGroupError("rule group children must be Condition or RuleGroup values")


def _validate_group_identity(rule_group_id: str) -> None:
    if not isinstance(rule_group_id, str) or not rule_group_id.strip():
        raise ValueError("rule_group_id must not be empty")


def _combine(
    operator: LogicalOperator,
    results: list[ConditionResult | RuleGroupResult],
) -> bool:
    if operator is LogicalOperator.AND:
        return all(result.passed for result in results)
    if operator is LogicalOperator.OR:
        return any(result.passed for result in results)
    raise InvalidRuleGroupError("rule group operator must be AND or OR")


def _child_identity(result: ConditionResult | RuleGroupResult) -> str:
    if isinstance(result, ConditionResult):
        return f"Condition({result.condition_id})"
    return f"RuleGroup({result.rule_group_id})"


__all__ = ["evaluate_rule_group"]
