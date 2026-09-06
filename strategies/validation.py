"""Cross-field validation for Strategy Engine V1.0 schemas.

This module validates strategy configuration structure and consistency. It does
not evaluate conditions, resolve allocations, calculate indicators, or emit
signals.
"""

from __future__ import annotations

import json
import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from data.models import PriceField
from strategies.enums import (
    ComparisonOperator,
    LogicalOperator,
    OperandType,
    RebalanceFrequency,
    ThresholdType,
)
from strategies.exceptions import (
    InvalidAllocationError,
    InvalidAssetError,
    InvalidConditionError,
    InvalidOperandError,
    InvalidRebalancePolicyError,
    InvalidRuleError,
    InvalidRuleGroupError,
    InvalidStrategyError,
    InvalidThresholdError,
)
from strategies.models import (
    Allocation,
    AllocationRule,
    AssetReference,
    Condition,
    FallbackAllocation,
    Operand,
    RebalancePolicy,
    RemainingAllocation,
    RuleGroup,
    StrategyDefinition,
)


class ValidationCode(StrEnum):
    """Stable machine-readable validation issue categories."""

    SCHEMA_ERROR = "SchemaError"
    INVALID_STRATEGY = "InvalidStrategy"
    EMPTY_ASSETS = "EmptyAssets"
    DUPLICATE_ASSET = "DuplicateAsset"
    UNKNOWN_ASSET_REFERENCE = "UnknownAssetReference"
    DUPLICATE_ALLOCATION = "DuplicateAllocation"
    INVALID_ALLOCATION = "InvalidAllocation"
    ALLOCATION_EXCEEDS_100_PERCENT = "AllocationExceeds100Percent"
    INVALID_MIN_MAX = "InvalidMinMax"
    INVALID_REMAINING_ALLOCATION = "InvalidRemainingAllocation"
    DUPLICATE_RULE_ID = "DuplicateRuleId"
    PRIORITY_CONFLICT = "PriorityConflict"
    INVALID_RULE = "InvalidRule"
    INVALID_FALLBACK = "InvalidFallback"
    MISSING_FALLBACK = "MissingFallback"
    INVALID_CONDITION = "InvalidCondition"
    INVALID_RULE_GROUP = "InvalidRuleGroup"
    INVALID_THRESHOLD = "InvalidThreshold"
    INVALID_PRICE_FIELD = "InvalidPriceField"
    PRICE_FIELD_MISMATCH = "PriceFieldMismatch"
    INVALID_REBALANCE_POLICY = "InvalidRebalancePolicy"


@dataclass(frozen=True)
class ValidationIssue:
    """One deterministic, structured validation error."""

    code: str
    path: str
    message: str

    def to_dict(self) -> dict[str, str]:
        return {"code": self.code, "path": self.path, "message": self.message}


@dataclass(frozen=True)
class ValidationResult:
    """Validation outcome containing all collected errors and warnings."""

    errors: tuple[ValidationIssue, ...] = ()
    warnings: tuple[ValidationIssue, ...] = ()

    @property
    def is_valid(self) -> bool:
        return not self.errors

    def to_dict(self) -> dict[str, Any]:
        return {
            "is_valid": self.is_valid,
            "errors": [issue.to_dict() for issue in self.errors],
            "warnings": [issue.to_dict() for issue in self.warnings],
        }


class StrategyValidator:
    """Validate a StrategyDefinition or its JSON-compatible payload."""

    def validate(self, strategy: StrategyDefinition | Mapping[str, Any] | str) -> ValidationResult:
        if isinstance(strategy, StrategyDefinition):
            return self._validate_definition(strategy)
        if isinstance(strategy, str):
            try:
                strategy = json.loads(strategy)
            except (json.JSONDecodeError, TypeError) as exc:
                detail = exc.msg if isinstance(exc, json.JSONDecodeError) else str(exc)
                return ValidationResult(
                    errors=(
                        self._issue(
                            ValidationCode.SCHEMA_ERROR,
                            "$",
                            f"strategy JSON is invalid: {detail}",
                        ),
                    )
                )
        if not isinstance(strategy, Mapping):
            return ValidationResult(
                errors=(
                    self._issue(
                        ValidationCode.SCHEMA_ERROR,
                        "$",
                        "strategy payload must be an object or StrategyDefinition",
                    ),
                )
            )
        if strategy.get("fallback") is None:
            return ValidationResult(
                errors=(
                    self._issue(
                        ValidationCode.MISSING_FALLBACK,
                        "fallback",
                        "strategy must define an explicit fallback allocation",
                    ),
                )
            )
        try:
            definition = StrategyDefinition.from_dict(strategy)
        except (TypeError, ValueError) as exc:
            # Model construction is deliberately kept separate from cross-field
            # validation, but malformed wire payloads still need stable result
            # codes instead of leaking constructor exceptions to callers.
            return ValidationResult(errors=(self._issue_from_exception(exc),))
        return self._validate_definition(definition)

    def validate_or_raise(
        self, strategy: StrategyDefinition | Mapping[str, Any] | str
    ) -> StrategyDefinition:
        result = self.validate(strategy)
        if not result.is_valid:
            raise ValueError(
                "; ".join(
                    f"{issue.code} at {issue.path}: {issue.message}" for issue in result.errors
                )
            )
        if isinstance(strategy, StrategyDefinition):
            return strategy
        if isinstance(strategy, str):
            strategy = json.loads(strategy)
        return StrategyDefinition.from_dict(strategy)

    def _validate_definition(self, strategy: StrategyDefinition) -> ValidationResult:
        errors: list[ValidationIssue] = []
        assets = self._validate_assets(strategy, errors)
        self._validate_price_field(strategy, errors)
        self._validate_rules(strategy, assets, errors)
        self._validate_fallback(strategy, assets, errors)
        self._validate_rebalance_policy(strategy, errors)
        return ValidationResult(errors=tuple(errors))

    def _validate_assets(
        self, strategy: StrategyDefinition, errors: list[ValidationIssue]
    ) -> set[str]:
        assets = getattr(strategy, "assets", ())
        if not isinstance(assets, Sequence) or isinstance(assets, (str, bytes)):
            errors.append(
                self._issue(ValidationCode.INVALID_STRATEGY, "assets", "assets must be a sequence")
            )
            return set()
        if not assets:
            errors.append(
                self._issue(ValidationCode.EMPTY_ASSETS, "assets", "assets must not be empty")
            )
        symbols: set[str] = set()
        for index, asset in enumerate(assets):
            if not isinstance(asset, AssetReference):
                errors.append(
                    self._issue(
                        ValidationCode.INVALID_STRATEGY,
                        f"assets[{index}]",
                        "asset must be an AssetReference",
                    )
                )
                continue
            symbol = asset.symbol
            if symbol in symbols:
                errors.append(
                    self._issue(
                        ValidationCode.DUPLICATE_ASSET,
                        f"assets[{index}].symbol",
                        f"asset {symbol} is declared more than once",
                    )
                )
            symbols.add(symbol)
        return symbols

    def _validate_price_field(
        self, strategy: StrategyDefinition, errors: list[ValidationIssue]
    ) -> None:
        if not isinstance(getattr(strategy, "price_field", None), PriceField):
            errors.append(
                self._issue(
                    ValidationCode.INVALID_PRICE_FIELD,
                    "price_field",
                    "price_field must be RAW_CLOSE or ADJUSTED_CLOSE",
                )
            )

    def _validate_rules(
        self,
        strategy: StrategyDefinition,
        assets: set[str],
        errors: list[ValidationIssue],
    ) -> None:
        rules = getattr(strategy, "rules", ())
        if not isinstance(rules, Sequence) or isinstance(rules, (str, bytes)):
            errors.append(
                self._issue(ValidationCode.INVALID_STRATEGY, "rules", "rules must be a sequence")
            )
            return
        rule_ids: set[str] = set()
        priorities: set[int] = set()
        for index, rule in enumerate(rules):
            path = f"rules[{index}]"
            if not isinstance(rule, AllocationRule):
                errors.append(
                    self._issue(ValidationCode.INVALID_RULE, path, "rule must be an AllocationRule")
                )
                continue
            if rule.rule_id in rule_ids:
                errors.append(
                    self._issue(
                        ValidationCode.DUPLICATE_RULE_ID,
                        f"{path}.rule_id",
                        f"rule_id {rule.rule_id} is duplicated",
                    )
                )
            rule_ids.add(rule.rule_id)
            if rule.priority in priorities:
                errors.append(
                    self._issue(
                        ValidationCode.PRIORITY_CONFLICT,
                        f"{path}.priority",
                        f"priority {rule.priority} is used by more than one rule",
                    )
                )
            priorities.add(rule.priority)
            if rule.condition is not None:
                self._validate_node(rule.condition, f"{path}.condition", strategy, assets, errors)
            self._validate_allocations(rule.allocations, f"{path}.allocations", assets, errors)
            self._validate_remaining(rule, path, assets, errors)

    def _validate_fallback(
        self,
        strategy: StrategyDefinition,
        assets: set[str],
        errors: list[ValidationIssue],
    ) -> None:
        fallback = getattr(strategy, "fallback", None)
        if fallback is None:
            errors.append(
                self._issue(
                    ValidationCode.MISSING_FALLBACK,
                    "fallback",
                    "strategy must define an explicit fallback allocation",
                )
            )
            return
        if not isinstance(fallback, FallbackAllocation):
            errors.append(
                self._issue(
                    ValidationCode.INVALID_FALLBACK,
                    "fallback",
                    "fallback must be a FallbackAllocation",
                )
            )
            return
        self._validate_allocations(fallback.allocations, "fallback.allocations", assets, errors)

    def _validate_allocations(
        self,
        allocations: object,
        path: str,
        assets: set[str],
        errors: list[ValidationIssue],
    ) -> set[str]:
        if not isinstance(allocations, Sequence) or isinstance(allocations, (str, bytes)):
            errors.append(
                self._issue(
                    ValidationCode.INVALID_ALLOCATION, path, "allocations must be a sequence"
                )
            )
            return set()
        if not allocations:
            errors.append(
                self._issue(
                    ValidationCode.INVALID_ALLOCATION, path, "allocations must not be empty"
                )
            )
            return set()
        symbols: set[str] = set()
        total = 0.0
        for index, allocation in enumerate(allocations):
            item_path = f"{path}[{index}]"
            if not isinstance(allocation, Allocation):
                errors.append(
                    self._issue(
                        ValidationCode.INVALID_ALLOCATION, item_path, "allocation is invalid"
                    )
                )
                continue
            symbol = allocation.symbol.symbol
            if symbol in symbols:
                errors.append(
                    self._issue(
                        ValidationCode.DUPLICATE_ALLOCATION,
                        f"{item_path}.symbol",
                        f"asset {symbol} is allocated more than once",
                    )
                )
            symbols.add(symbol)
            if symbol not in assets:
                errors.append(
                    self._issue(
                        ValidationCode.UNKNOWN_ASSET_REFERENCE,
                        f"{item_path}.symbol",
                        f"asset {symbol} is not declared in strategy assets",
                    )
                )
            if not self._valid_weight(allocation.target_weight):
                errors.append(
                    self._issue(
                        ValidationCode.INVALID_ALLOCATION,
                        f"{item_path}.target_weight",
                        "target_weight must be finite and in [0, 1]",
                    )
                )
            total += allocation.target_weight
            self._validate_bounds(allocation, item_path, errors)
        if total > 1.0 + 1e-12:
            errors.append(
                self._issue(
                    ValidationCode.ALLOCATION_EXCEEDS_100_PERCENT,
                    path,
                    f"allocation weights sum to {total:.12g}, which exceeds 1.0",
                )
            )
        return symbols

    def _validate_bounds(
        self, allocation: Allocation, path: str, errors: list[ValidationIssue]
    ) -> None:
        minimum = allocation.minimum_weight
        maximum = allocation.maximum_weight
        if minimum is not None and not self._valid_weight(minimum):
            errors.append(
                self._issue(
                    ValidationCode.INVALID_MIN_MAX,
                    f"{path}.minimum_weight",
                    "minimum_weight is invalid",
                )
            )
        if maximum is not None and not self._valid_weight(maximum):
            errors.append(
                self._issue(
                    ValidationCode.INVALID_MIN_MAX,
                    f"{path}.maximum_weight",
                    "maximum_weight is invalid",
                )
            )
        if minimum is not None and maximum is not None and minimum > maximum:
            errors.append(
                self._issue(
                    ValidationCode.INVALID_MIN_MAX,
                    path,
                    "minimum_weight must not exceed maximum_weight",
                )
            )
        if minimum is not None and allocation.target_weight < minimum:
            errors.append(
                self._issue(
                    ValidationCode.INVALID_MIN_MAX,
                    path,
                    "target_weight is below minimum_weight",
                )
            )
        if maximum is not None and allocation.target_weight > maximum:
            errors.append(
                self._issue(
                    ValidationCode.INVALID_MIN_MAX,
                    path,
                    "target_weight exceeds maximum_weight",
                )
            )

    def _validate_remaining(
        self,
        rule: AllocationRule,
        path: str,
        assets: set[str],
        errors: list[ValidationIssue],
    ) -> None:
        remaining = rule.remaining
        if remaining is None:
            return
        if not isinstance(remaining, RemainingAllocation):
            errors.append(
                self._issue(
                    ValidationCode.INVALID_REMAINING_ALLOCATION,
                    f"{path}.remaining",
                    "remaining is invalid",
                )
            )
            return
        symbol = remaining.symbol.symbol
        if symbol not in assets:
            errors.append(
                self._issue(
                    ValidationCode.UNKNOWN_ASSET_REFERENCE,
                    f"{path}.remaining.symbol",
                    f"remaining asset {symbol} is not declared in strategy assets",
                )
            )
        if symbol in {allocation.symbol.symbol for allocation in rule.allocations}:
            errors.append(
                self._issue(
                    ValidationCode.INVALID_REMAINING_ALLOCATION,
                    f"{path}.remaining.symbol",
                    "remaining recipient must not also have an explicit allocation",
                )
            )

    def _validate_node(
        self,
        node: object,
        path: str,
        strategy: StrategyDefinition,
        assets: set[str],
        errors: list[ValidationIssue],
    ) -> None:
        if isinstance(node, Condition):
            self._validate_condition(node, path, strategy, assets, errors)
            return
        if isinstance(node, RuleGroup):
            if not isinstance(node.operator, LogicalOperator):
                errors.append(
                    self._issue(
                        ValidationCode.INVALID_RULE_GROUP, path, "group operator is invalid"
                    )
                )
            if not node.children:
                errors.append(
                    self._issue(
                        ValidationCode.INVALID_RULE_GROUP, path, "group children must not be empty"
                    )
                )
            for index, child in enumerate(node.children):
                self._validate_node(child, f"{path}.children[{index}]", strategy, assets, errors)
            return
        errors.append(
            self._issue(
                ValidationCode.INVALID_RULE_GROUP, path, "node must be a Condition or RuleGroup"
            )
        )

    def _validate_condition(
        self,
        condition: Condition,
        path: str,
        strategy: StrategyDefinition,
        assets: set[str],
        errors: list[ValidationIssue],
    ) -> None:
        if not isinstance(condition.operator, ComparisonOperator):
            errors.append(
                self._issue(
                    ValidationCode.INVALID_CONDITION, f"{path}.operator", "operator is invalid"
                )
            )
        self._validate_operand(condition.left, f"{path}.left", strategy, assets, errors)
        self._validate_operand(condition.right, f"{path}.right", strategy, assets, errors)
        threshold = condition.threshold
        if threshold is not None:
            if not isinstance(threshold.threshold_type, ThresholdType):
                errors.append(
                    self._issue(
                        ValidationCode.INVALID_THRESHOLD,
                        f"{path}.threshold.type",
                        "threshold type is invalid",
                    )
                )
            if not self._finite_number(threshold.value):
                errors.append(
                    self._issue(
                        ValidationCode.INVALID_THRESHOLD,
                        f"{path}.threshold.value",
                        "threshold value must be finite",
                    )
                )

    def _validate_operand(
        self,
        operand: object,
        path: str,
        strategy: StrategyDefinition,
        assets: set[str],
        errors: list[ValidationIssue],
    ) -> None:
        if not isinstance(operand, Operand):
            errors.append(self._issue(ValidationCode.INVALID_CONDITION, path, "operand is invalid"))
            return
        if operand.symbol not in assets:
            errors.append(
                self._issue(
                    ValidationCode.UNKNOWN_ASSET_REFERENCE,
                    f"{path}.asset",
                    f"asset {operand.symbol} is not declared in strategy assets",
                )
            )
        if not isinstance(operand.operand_type, OperandType):
            errors.append(
                self._issue(
                    ValidationCode.INVALID_CONDITION, f"{path}.type", "operand type is invalid"
                )
            )
        if operand.operand_type is OperandType.PRICE and operand.period is not None:
            errors.append(
                self._issue(
                    ValidationCode.INVALID_CONDITION,
                    f"{path}.period",
                    "PRICE must not have a period",
                )
            )
        if operand.operand_type in (OperandType.MA, OperandType.EMA) and (
            isinstance(operand.period, bool)
            or not isinstance(operand.period, int)
            or operand.period <= 0
        ):
            errors.append(
                self._issue(
                    ValidationCode.INVALID_CONDITION,
                    f"{path}.period",
                    "indicator period must be a positive integer",
                )
            )
        if operand.operand_type is OperandType.CONSTANT:
            if operand.period is not None:
                errors.append(
                    self._issue(
                        ValidationCode.INVALID_CONDITION,
                        f"{path}.period",
                        "CONSTANT must not have a period",
                    )
                )
            if operand.price_field is not None:
                errors.append(
                    self._issue(
                        ValidationCode.INVALID_CONDITION,
                        f"{path}.price_field",
                        "CONSTANT must not have a price_field",
                    )
                )
            if not self._finite_number(operand.value):
                errors.append(
                    self._issue(
                        ValidationCode.INVALID_CONDITION,
                        f"{path}.value",
                        "CONSTANT value must be finite",
                    )
                )
        elif operand.value is not None:
            errors.append(
                self._issue(
                    ValidationCode.INVALID_CONDITION,
                    f"{path}.value",
                    "only CONSTANT operands may have a value",
                )
            )
        if operand.price_field is not None:
            if not isinstance(operand.price_field, PriceField):
                errors.append(
                    self._issue(
                        ValidationCode.INVALID_PRICE_FIELD,
                        f"{path}.price_field",
                        "price_field is invalid",
                    )
                )
            elif operand.price_field is not strategy.price_field:
                errors.append(
                    self._issue(
                        ValidationCode.PRICE_FIELD_MISMATCH,
                        f"{path}.price_field",
                        "operand price_field must match strategy price_field",
                    )
                )

    def _validate_rebalance_policy(
        self, strategy: StrategyDefinition, errors: list[ValidationIssue]
    ) -> None:
        policy = getattr(strategy, "rebalance_policy", None)
        if not isinstance(policy, RebalancePolicy):
            errors.append(
                self._issue(
                    ValidationCode.INVALID_REBALANCE_POLICY,
                    "rebalance_policy",
                    "rebalance_policy is invalid",
                )
            )
            return
        if not isinstance(policy.frequency, RebalanceFrequency):
            errors.append(
                self._issue(
                    ValidationCode.INVALID_REBALANCE_POLICY,
                    "rebalance_policy.frequency",
                    "frequency is invalid",
                )
            )
        if policy.threshold is not None and not self._valid_weight(policy.threshold):
            errors.append(
                self._issue(
                    ValidationCode.INVALID_REBALANCE_POLICY,
                    "rebalance_policy.threshold",
                    "threshold must be finite and in [0, 1]",
                )
            )

    @staticmethod
    def _valid_weight(value: object) -> bool:
        return StrategyValidator._finite_number(value) and 0 <= float(value) <= 1

    @staticmethod
    def _finite_number(value: object) -> bool:
        return (
            isinstance(value, (int, float))
            and not isinstance(value, bool)
            and math.isfinite(float(value))
        )

    @staticmethod
    def _issue(code: ValidationCode | str, path: str, message: str) -> ValidationIssue:
        return ValidationIssue(code=str(code), path=path, message=message)

    def _issue_from_exception(self, exc: Exception) -> ValidationIssue:
        message = str(exc)
        normalized_message = message.lower()
        if isinstance(exc, InvalidStrategyError):
            if "assets must contain at least one" in normalized_message:
                code = ValidationCode.EMPTY_ASSETS
            elif "duplicate symbols" in normalized_message:
                code = ValidationCode.DUPLICATE_ASSET
            elif "price_field" in normalized_message:
                code = ValidationCode.INVALID_PRICE_FIELD
            else:
                code = ValidationCode.INVALID_STRATEGY
            return self._issue(code, "$", message or "strategy schema is invalid")
        if "minimum_weight" in normalized_message or "maximum_weight" in normalized_message:
            return self._issue(
                ValidationCode.INVALID_MIN_MAX,
                "$",
                message or "allocation bounds are invalid",
            )
        if "fallback" in normalized_message:
            return self._issue(
                ValidationCode.INVALID_FALLBACK,
                "$",
                message or "fallback is invalid",
            )
        if "rebalance policy" in normalized_message:
            return self._issue(
                ValidationCode.INVALID_REBALANCE_POLICY,
                "$",
                message or "rebalance policy is invalid",
            )
        if "allocation rule" in normalized_message:
            return self._issue(
                ValidationCode.INVALID_RULE,
                "$",
                message or "allocation rule is invalid",
            )
        if "rule group" in normalized_message or "rule node" in normalized_message:
            return self._issue(
                ValidationCode.INVALID_RULE_GROUP,
                "$",
                message or "rule group is invalid",
            )
        if "allocation" in normalized_message:
            return self._issue(
                ValidationCode.INVALID_ALLOCATION,
                "$",
                message or "allocation is invalid",
            )
        if "operand must be an object" in normalized_message:
            return self._issue(
                ValidationCode.INVALID_CONDITION,
                "$",
                message or "condition operand is invalid",
            )
        exception_codes = {
            InvalidAllocationError: ValidationCode.INVALID_ALLOCATION,
            InvalidAssetError: ValidationCode.INVALID_STRATEGY,
            InvalidConditionError: ValidationCode.INVALID_CONDITION,
            InvalidOperandError: ValidationCode.INVALID_CONDITION,
            InvalidRebalancePolicyError: ValidationCode.INVALID_REBALANCE_POLICY,
            InvalidRuleError: ValidationCode.INVALID_RULE,
            InvalidRuleGroupError: ValidationCode.INVALID_RULE_GROUP,
            InvalidStrategyError: ValidationCode.INVALID_STRATEGY,
            InvalidThresholdError: ValidationCode.INVALID_THRESHOLD,
        }
        for exception_type, code in exception_codes.items():
            if isinstance(exc, exception_type):
                return self._issue(code, "$", message or "strategy schema is invalid")
        if "assets must contain at least one" in normalized_message:
            code = ValidationCode.EMPTY_ASSETS
        elif "duplicate symbols" in normalized_message:
            code = ValidationCode.DUPLICATE_ASSET
        elif "asset symbol" in normalized_message:
            code = ValidationCode.INVALID_STRATEGY
        elif "threshold" in normalized_message:
            code = ValidationCode.INVALID_THRESHOLD
        elif "condition" in normalized_message or "operand" in normalized_message:
            code = ValidationCode.INVALID_CONDITION
        else:
            code = ValidationCode.SCHEMA_ERROR
        return self._issue(code, "$", message or "strategy schema is invalid")


def validate_strategy(
    strategy: StrategyDefinition | Mapping[str, Any] | str,
) -> ValidationResult:
    """Validate a StrategyDefinition or JSON-compatible strategy payload."""

    return StrategyValidator().validate(strategy)


def validate_strategy_definition(strategy: StrategyDefinition) -> ValidationResult:
    """Explicit alias for callers validating an already-built definition."""

    return StrategyValidator().validate(strategy)
