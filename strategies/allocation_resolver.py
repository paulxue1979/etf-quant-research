"""Pure first-match resolution of allocation rules into target weights."""

from __future__ import annotations

import math
from collections.abc import Mapping
from datetime import date

from strategies.enums import NoMatchBehavior
from strategies.evaluation import RuleGroupResult, TargetAllocationResult
from strategies.exceptions import (
    EvaluationError,
    InvalidAllocationConfigurationError,
    MissingRuleEvaluationError,
    RuleEvaluationPropagationError,
)
from strategies.models import (
    Allocation,
    AllocationRule,
    AllocationSpecification,
    FallbackAllocation,
    RemainingAllocation,
    StrategyDefinition,
)

_WEIGHT_TOLERANCE = 1e-12


def resolve_allocations(
    strategy: StrategyDefinition,
    rule_results: Mapping[str, RuleGroupResult | EvaluationError],
    as_of_date: date,
    *,
    previous_target_allocation: AllocationSpecification | None = None,
) -> TargetAllocationResult:
    """Resolve all supplied rule results without evaluating conditions again."""
    if not isinstance(strategy, StrategyDefinition):
        raise TypeError("strategy must be a StrategyDefinition value")
    if not isinstance(rule_results, Mapping):
        raise TypeError("rule_results must be a mapping")
    if not isinstance(as_of_date, date):
        raise TypeError("as_of_date must be a date")

    declared_assets = _declared_asset_symbols(strategy)
    rules = _validated_rules(strategy.rules)
    _validate_rule_priorities(rules)
    _validate_result_keys(rule_results, rules)

    matched: list[AllocationRule] = []
    for rule in sorted(rules, key=lambda item: item.priority, reverse=True):
        if rule.condition is None:
            matched.append(rule)
            continue
        result = rule_results.get(rule.rule_id)
        if result is None:
            raise MissingRuleEvaluationError(
                f"conditional rule {rule.rule_id} has no supplied RuleGroupResult"
            )
        if isinstance(result, EvaluationError):
            raise RuleEvaluationPropagationError(
                f"condition evaluation failed for rule {rule.rule_id}",
                rule_id=rule.rule_id,
                cause=result,
            ) from result
        if not isinstance(result, RuleGroupResult):
            raise InvalidAllocationConfigurationError(
                f"rule result for {rule.rule_id} must be a RuleGroupResult"
            )
        if result.date != as_of_date:
            raise InvalidAllocationConfigurationError(
                f"rule result for {rule.rule_id} has date {result.date.isoformat()}, "
                f"expected {as_of_date.isoformat()}"
            )
        if result.passed:
            matched.append(rule)

    if matched:
        selected = matched[0]
        return _build_result(
            selected.allocations,
            selected.remaining,
            declared_assets,
            as_of_date,
            matched_rule_id=selected.rule_id,
            used_fallback=False,
            selected_priority=selected.priority,
        )

    if strategy.no_match_behavior is NoMatchBehavior.HOLD_PREVIOUS_ALLOCATION:
        if not isinstance(previous_target_allocation, AllocationSpecification):
            raise InvalidAllocationConfigurationError(
                "HOLD_PREVIOUS_ALLOCATION requires a previous target allocation"
            )
        return _build_result(
            previous_target_allocation.allocations,
            None,
            declared_assets,
            as_of_date,
            matched_rule_id=None,
            used_fallback=False,
            selected_priority=None,
            allow_empty=True,
            source_description="previous target allocation",
        )

    fallback = getattr(strategy, "fallback", None)
    if not isinstance(fallback, FallbackAllocation):
        raise InvalidAllocationConfigurationError(
            "no allocation rule matched and no valid fallback is configured"
        )
    return _build_result(
        fallback.allocations,
        None,
        declared_assets,
        as_of_date,
        matched_rule_id=fallback.name,
        used_fallback=True,
        selected_priority=None,
    )


def resolve_regime_allocation(
    strategy: StrategyDefinition,
    state_id: str,
    allocation: AllocationSpecification,
    as_of_date: date,
) -> TargetAllocationResult:
    """Resolve one complete regime target without evaluating legacy rules."""
    if not isinstance(allocation, AllocationSpecification):
        raise InvalidAllocationConfigurationError("regime allocation is invalid")
    return _build_result(
        allocation.allocations,
        None,
        _declared_asset_symbols(strategy),
        as_of_date,
        matched_rule_id=state_id,
        used_fallback=False,
        selected_priority=None,
        source_description=f"regime {state_id}",
    )


def _declared_asset_symbols(strategy: StrategyDefinition) -> tuple[str, ...]:
    assets = getattr(strategy, "assets", None)
    if (
        isinstance(assets, (str, bytes))
        or not assets
        or not all(isinstance(asset.symbol, str) for asset in assets if hasattr(asset, "symbol"))
        or not all(hasattr(asset, "symbol") for asset in assets)
    ):
        raise InvalidAllocationConfigurationError("strategy assets are malformed")
    symbols = tuple(asset.symbol for asset in assets)
    if len(symbols) != len(set(symbols)):
        raise InvalidAllocationConfigurationError("strategy assets must be unique")
    return symbols


def _validated_rules(rules: object) -> tuple[AllocationRule, ...]:
    if isinstance(rules, (str, bytes)) or not isinstance(rules, tuple):
        raise InvalidAllocationConfigurationError("strategy rules must be an immutable tuple")
    if not all(isinstance(rule, AllocationRule) for rule in rules):
        raise InvalidAllocationConfigurationError(
            "strategy rules must contain AllocationRule values"
        )
    return rules


def _validate_rule_priorities(rules: tuple[AllocationRule, ...]) -> None:
    priorities = [rule.priority for rule in rules]
    if len(priorities) != len(set(priorities)):
        raise InvalidAllocationConfigurationError(
            "allocation rules must not contain duplicate priorities"
        )
    rule_ids = [rule.rule_id for rule in rules]
    if len(rule_ids) != len(set(rule_ids)):
        raise InvalidAllocationConfigurationError(
            "allocation rules must not contain duplicate rule IDs"
        )


def _validate_result_keys(
    rule_results: Mapping[str, RuleGroupResult | EvaluationError],
    rules: tuple[AllocationRule, ...],
) -> None:
    conditional_ids = {rule.rule_id for rule in rules if rule.condition is not None}
    unknown = set(rule_results) - conditional_ids
    if unknown:
        names = ", ".join(sorted(str(item) for item in unknown))
        raise InvalidAllocationConfigurationError(
            f"rule_results contains unknown rule IDs: {names}"
        )


def _build_result(
    allocations: object,
    remaining: RemainingAllocation | None,
    declared_assets: tuple[str, ...],
    as_of_date: date,
    *,
    matched_rule_id: str | None,
    used_fallback: bool,
    selected_priority: int | None,
    allow_empty: bool = False,
    source_description: str | None = None,
) -> TargetAllocationResult:
    if isinstance(allocations, (str, bytes)) or not isinstance(allocations, tuple):
        raise InvalidAllocationConfigurationError("allocations must be an immutable tuple")
    if (not allocations and not allow_empty) or not all(
        isinstance(item, Allocation) for item in allocations
    ):
        raise InvalidAllocationConfigurationError("allocations must contain Allocation values")
    explicit = list(allocations)
    try:
        symbols = [item.symbol.symbol for item in explicit]
    except AttributeError as exc:
        raise InvalidAllocationConfigurationError("allocation symbols are malformed") from exc
    if len(symbols) != len(set(symbols)):
        raise InvalidAllocationConfigurationError("allocation symbols must be unique")
    unknown = set(symbols) - set(declared_assets)
    if unknown:
        names = ", ".join(sorted(unknown))
        raise InvalidAllocationConfigurationError(
            f"allocation references undeclared assets: {names}"
        )
    if remaining is not None:
        if not isinstance(remaining, RemainingAllocation):
            raise InvalidAllocationConfigurationError("remaining allocation is malformed")
        recipient = remaining.symbol.symbol
        if recipient not in declared_assets:
            raise InvalidAllocationConfigurationError(
                f"remaining recipient {recipient} is not a declared asset"
            )
        if recipient in symbols:
            raise InvalidAllocationConfigurationError(
                "remaining recipient must not have an explicit allocation"
            )

    explicit_sum = math.fsum(item.target_weight for item in explicit)
    for allocation in explicit:
        if (
            isinstance(allocation.target_weight, bool)
            or not isinstance(allocation.target_weight, (int, float))
            or not math.isfinite(float(allocation.target_weight))
            or not 0 <= float(allocation.target_weight) <= 1
        ):
            raise InvalidAllocationConfigurationError(
                f"target weight is invalid for {allocation.symbol.symbol}"
            )
        if allocation.minimum_weight is not None and allocation.maximum_weight is not None:
            if not _is_valid_bound(allocation.minimum_weight) or not _is_valid_bound(
                allocation.maximum_weight
            ):
                raise InvalidAllocationConfigurationError(
                    f"allocation bounds are invalid for {allocation.symbol.symbol}"
                )
            if allocation.minimum_weight > allocation.maximum_weight:
                raise InvalidAllocationConfigurationError(
                    f"minimum_weight exceeds maximum_weight for {allocation.symbol.symbol}"
                )
        if (
            allocation.minimum_weight is not None
            and allocation.target_weight < allocation.minimum_weight
        ):
            raise InvalidAllocationConfigurationError(
                f"target weight is below minimum_weight for {allocation.symbol.symbol}"
            )
        if (
            allocation.maximum_weight is not None
            and allocation.target_weight > allocation.maximum_weight
        ):
            raise InvalidAllocationConfigurationError(
                f"target weight exceeds maximum_weight for {allocation.symbol.symbol}"
            )
    if explicit_sum > 1.0 + _WEIGHT_TOLERANCE:
        raise InvalidAllocationConfigurationError(
            f"explicit allocation weight sum exceeds 1.0: {explicit_sum:.15g}"
        )
    remaining_weight = 1.0 - explicit_sum
    if abs(remaining_weight) <= _WEIGHT_TOLERANCE:
        remaining_weight = 0.0

    final_allocations = explicit[:]
    if remaining is not None:
        final_allocations.append(Allocation(remaining.symbol, remaining_weight))

    ordered = tuple(
        sorted(final_allocations, key=lambda item: declared_assets.index(item.symbol.symbol))
    )
    cash_buffer = 0.0 if remaining is not None else remaining_weight
    _validate_final_weights(ordered, remaining_weight, cash_buffer)
    allocation_text = ", ".join(
        f"{item.symbol.symbol}={item.target_weight:.12g}" for item in ordered
    )
    source = source_description or ("fallback" if used_fallback else f"rule {matched_rule_id}")
    priority_text = "" if selected_priority is None else f", priority={selected_priority}"
    explanation = (
        f"Selected {source}{priority_text}; allocations=[{allocation_text}]; "
        f"remaining_weight={remaining_weight:.12g}; cash_buffer={cash_buffer:.12g}; "
        f"fallback_used={'TRUE' if used_fallback else 'FALSE'}"
    )
    return TargetAllocationResult(
        date=as_of_date,
        matched_rule_id=matched_rule_id,
        used_fallback=used_fallback,
        allocations=ordered,
        remaining_weight=remaining_weight,
        cash_buffer=cash_buffer,
        explanation=explanation,
    )


def _validate_final_weights(
    allocations: tuple[Allocation, ...], remaining_weight: float, cash_buffer: float
) -> None:
    total = math.fsum(item.target_weight for item in allocations) + cash_buffer
    if not math.isfinite(total) or total > 1.0 + _WEIGHT_TOLERANCE:
        raise InvalidAllocationConfigurationError("final target weights exceed 1.0")
    if remaining_weight < -_WEIGHT_TOLERANCE or cash_buffer < -_WEIGHT_TOLERANCE:
        raise InvalidAllocationConfigurationError("remaining or cash buffer cannot be negative")


def _is_valid_bound(value: object) -> bool:
    return (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and math.isfinite(float(value))
        and 0 <= float(value) <= 1
    )


__all__ = ["resolve_allocations", "resolve_regime_allocation"]
