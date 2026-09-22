"""Immutable, JSON-compatible Strategy Engine V1.0 domain models.

This module defines structure and field-level validation only. It deliberately
does not evaluate conditions, resolve allocations, persist versions, or
generate signals.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from types import MappingProxyType
from typing import Any

from data.models import PriceField, Timeframe
from strategies.enums import (
    AssetRole,
    ComparisonOperator,
    LogicalOperator,
    NoMatchBehavior,
    OperandType,
    RebalanceFrequency,
    StrategyEvaluationMode,
    StrategyStatus,
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
    InvalidStrategyVersionError,
    InvalidThresholdError,
)

_SYMBOL_PATTERN = re.compile(r"^[A-Z][A-Z0-9.-]{0,15}$")
_HASH_PATTERN = re.compile(r"^[0-9a-f]{64}$")

LEGACY_STRATEGY_SCHEMA_VERSION = "1.0"
CURRENT_STRATEGY_SCHEMA_VERSION = "2.0"
SUPPORTED_STRATEGY_SCHEMA_VERSIONS = frozenset(
    {LEGACY_STRATEGY_SCHEMA_VERSION, CURRENT_STRATEGY_SCHEMA_VERSION}
)


def _require_mapping(payload: object, label: str) -> Mapping[str, Any]:
    if not isinstance(payload, Mapping):
        raise ValueError(f"{label} must be an object")
    return payload


def _normalize_sequence(value: object, label: str) -> tuple[Any, ...]:
    if isinstance(value, (str, bytes)) or not isinstance(value, Sequence):
        raise ValueError(f"{label} must be a sequence")
    return tuple(value)


def _is_finite_number(value: object) -> bool:
    return (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and math.isfinite(float(value))
    )


def _validate_enum(
    value: object, enum_type: type[Any], error_type: type[ValueError], label: str
) -> Any:
    if isinstance(value, enum_type):
        return value
    try:
        return enum_type(value)
    except (TypeError, ValueError) as exc:
        raise error_type(f"{label} must be a valid {enum_type.__name__}") from exc


@dataclass(frozen=True)
class AssetReference:
    """A normalized, extensible asset identifier."""

    symbol: str

    def __post_init__(self) -> None:
        if not isinstance(self.symbol, str):
            raise InvalidAssetError("asset symbol must be a string")
        normalized = self.symbol.strip().upper()
        if not _SYMBOL_PATTERN.fullmatch(normalized):
            raise InvalidAssetError("asset symbol must be a valid market ticker")
        object.__setattr__(self, "symbol", normalized)

    def to_dict(self) -> dict[str, str]:
        return {"symbol": self.symbol}

    @classmethod
    def from_dict(cls, payload: object) -> AssetReference:
        data = _require_mapping(payload, "asset")
        return cls(symbol=data.get("symbol", ""))


@dataclass(frozen=True)
class StrategyAssetReference(AssetReference):
    """One declared asset and its generic strategy capabilities."""

    role: AssetRole = AssetRole.BOTH

    def __post_init__(self) -> None:
        super().__post_init__()
        role = _validate_enum(self.role, AssetRole, InvalidAssetError, "asset role")
        object.__setattr__(self, "role", role)

    def to_dict(self) -> dict[str, str]:
        return {"symbol": self.symbol, "role": self.role.value}

    @classmethod
    def from_dict(cls, payload: object) -> StrategyAssetReference:
        data = _require_mapping(payload, "strategy asset")
        return cls(symbol=data.get("symbol", ""), role=data.get("role", ""))


@dataclass(frozen=True)
class Operand:
    """A price, indicator, or asset-qualified constant reference."""

    asset: AssetReference | str
    operand_type: OperandType
    period: int | None = None
    price_field: PriceField | None = None
    value: float | None = None
    timeframe: Timeframe = Timeframe.DAILY

    def __post_init__(self) -> None:
        asset = self.asset if isinstance(self.asset, AssetReference) else AssetReference(self.asset)
        operand_type = _validate_enum(
            self.operand_type, OperandType, InvalidOperandError, "operand_type"
        )
        timeframe = _validate_enum(self.timeframe, Timeframe, InvalidOperandError, "timeframe")
        if operand_type is OperandType.CONSTANT and timeframe is not Timeframe.DAILY:
            raise InvalidOperandError("CONSTANT operand timeframe must be DAILY")
        if operand_type is OperandType.PRICE and self.period is not None:
            raise InvalidOperandError("PRICE operand must not have a period")
        if operand_type in (OperandType.MA, OperandType.EMA):
            if (
                isinstance(self.period, bool)
                or not isinstance(self.period, int)
                or self.period <= 0
            ):
                raise InvalidOperandError("MA and EMA operands require a positive integer period")
        if operand_type is OperandType.CONSTANT:
            if self.period is not None:
                raise InvalidOperandError("CONSTANT operand must not have a period")
            if self.price_field is not None:
                raise InvalidOperandError("CONSTANT operand must not have a price_field")
            if not _is_finite_number(self.value):
                raise InvalidOperandError("CONSTANT operand requires a finite numeric value")
        elif self.value is not None:
            raise InvalidOperandError("only CONSTANT operands may have a value")
        if self.price_field is not None:
            price_field = _validate_enum(
                self.price_field, PriceField, InvalidOperandError, "price_field"
            )
            object.__setattr__(self, "price_field", price_field)
        object.__setattr__(self, "asset", asset)
        object.__setattr__(self, "operand_type", operand_type)
        object.__setattr__(self, "timeframe", timeframe)
        if operand_type is OperandType.CONSTANT:
            object.__setattr__(self, "value", float(self.value))

    @property
    def symbol(self) -> str:
        """Expose the asset ticker for convenient registry lookups."""
        return self.asset.symbol

    @property
    def type(self) -> OperandType:
        """Expose the wire-level operand type without changing the field name."""
        return self.operand_type

    def to_dict(self, *, include_timeframe: bool = False) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "type": self.operand_type.value,
            "asset": self.asset.symbol,
        }
        if self.period is not None:
            payload["period"] = self.period
        if self.price_field is not None:
            payload["price_field"] = self.price_field.value
        if self.value is not None:
            payload["value"] = self.value
        if include_timeframe:
            payload["timeframe"] = self.timeframe.value
        return payload

    @classmethod
    def from_dict(cls, payload: object) -> Operand:
        data = _require_mapping(payload, "operand")
        return cls(
            asset=data.get("asset", ""),
            operand_type=data.get("type", ""),
            period=data.get("period"),
            price_field=data.get("price_field"),
            value=data.get("value"),
            timeframe=data.get("timeframe", Timeframe.DAILY.value),
        )


@dataclass(frozen=True)
class Threshold:
    """A signed threshold value; evaluation is deferred to a later phase."""

    threshold_type: ThresholdType
    value: float

    def __post_init__(self) -> None:
        threshold_type = _validate_enum(
            self.threshold_type, ThresholdType, InvalidThresholdError, "threshold_type"
        )
        if not _is_finite_number(self.value):
            raise InvalidThresholdError("threshold value must be a finite number")
        object.__setattr__(self, "threshold_type", threshold_type)
        object.__setattr__(self, "value", float(self.value))

    @property
    def type(self) -> ThresholdType:
        return self.threshold_type

    def to_dict(self) -> dict[str, str | float]:
        return {"type": self.threshold_type.value, "value": self.value}

    @classmethod
    def from_dict(cls, payload: object) -> Threshold:
        data = _require_mapping(payload, "threshold")
        return cls(threshold_type=data.get("type", ""), value=data.get("value"))


@dataclass(frozen=True)
class Condition:
    """A comparison between two operands with an optional threshold."""

    left: Operand
    operator: ComparisonOperator
    right: Operand
    threshold: Threshold | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.left, Operand) or not isinstance(self.right, Operand):
            raise InvalidConditionError("condition left and right must be Operand values")
        operator = _validate_enum(
            self.operator, ComparisonOperator, InvalidConditionError, "operator"
        )
        if self.threshold is not None and not isinstance(self.threshold, Threshold):
            raise InvalidConditionError("condition threshold must be a Threshold value")
        object.__setattr__(self, "operator", operator)

    def to_dict(self, *, include_timeframe: bool = False) -> dict[str, Any]:
        return {
            "type": "condition",
            "left": self.left.to_dict(include_timeframe=include_timeframe),
            "operator": self.operator.value,
            "right": self.right.to_dict(include_timeframe=include_timeframe),
            "threshold": self.threshold.to_dict() if self.threshold else None,
        }

    @classmethod
    def from_dict(cls, payload: object) -> Condition:
        data = _require_mapping(payload, "condition")
        threshold = data.get("threshold")
        return cls(
            left=Operand.from_dict(data.get("left")),
            operator=data.get("operator", ""),
            right=Operand.from_dict(data.get("right")),
            threshold=Threshold.from_dict(threshold) if threshold is not None else None,
        )


@dataclass(frozen=True)
class RuleGroup:
    """A non-empty recursive AND/OR group of conditions and rule groups."""

    operator: LogicalOperator
    children: tuple[RuleNode, ...]

    def __post_init__(self) -> None:
        operator = _validate_enum(self.operator, LogicalOperator, InvalidRuleGroupError, "operator")
        children = _normalize_sequence(self.children, "children")
        if not children:
            raise InvalidRuleGroupError("rule group children must not be empty")
        if not all(isinstance(child, (Condition, RuleGroup)) for child in children):
            raise InvalidRuleGroupError("rule group children must be Condition or RuleGroup values")
        object.__setattr__(self, "operator", operator)
        object.__setattr__(self, "children", children)

    def to_dict(self, *, include_timeframe: bool = False) -> dict[str, Any]:
        return {
            "type": "group",
            "operator": self.operator.value,
            "children": [
                child.to_dict(include_timeframe=include_timeframe) for child in self.children
            ],
        }

    @classmethod
    def from_dict(cls, payload: object) -> RuleGroup:
        data = _require_mapping(payload, "rule group")
        children = []
        for child in _normalize_sequence(data.get("children", []), "children"):
            child_data = _require_mapping(child, "rule node")
            node_type = child_data.get("type")
            if node_type == "condition":
                children.append(Condition.from_dict(child_data))
            elif node_type == "group":
                children.append(cls.from_dict(child_data))
            else:
                raise InvalidRuleGroupError("rule node type must be condition or group")
        return cls(operator=data.get("operator", ""), children=tuple(children))


type RuleNode = Condition | RuleGroup


@dataclass(frozen=True)
class Allocation:
    """One asset target weight, represented as a decimal fraction."""

    symbol: AssetReference | str
    target_weight: float
    minimum_weight: float | None = None
    maximum_weight: float | None = None

    def __post_init__(self) -> None:
        symbol = (
            self.symbol if isinstance(self.symbol, AssetReference) else AssetReference(self.symbol)
        )
        if not _is_finite_number(self.target_weight) or not 0 <= self.target_weight <= 1:
            raise InvalidAllocationError("target_weight must be finite and in [0, 1]")
        for label, value in (
            ("minimum_weight", self.minimum_weight),
            ("maximum_weight", self.maximum_weight),
        ):
            if value is not None and (not _is_finite_number(value) or not 0 <= value <= 1):
                raise InvalidAllocationError(f"{label} must be finite and in [0, 1]")
        object.__setattr__(self, "symbol", symbol)
        object.__setattr__(self, "target_weight", float(self.target_weight))
        if self.minimum_weight is not None:
            object.__setattr__(self, "minimum_weight", float(self.minimum_weight))
        if self.maximum_weight is not None:
            object.__setattr__(self, "maximum_weight", float(self.maximum_weight))

    def to_dict(self) -> dict[str, str | float]:
        payload: dict[str, str | float] = {
            "symbol": self.symbol.symbol,
            "target_weight": self.target_weight,
        }
        if self.minimum_weight is not None:
            payload["minimum_weight"] = self.minimum_weight
        if self.maximum_weight is not None:
            payload["maximum_weight"] = self.maximum_weight
        return payload

    @classmethod
    def from_dict(cls, payload: object) -> Allocation:
        data = _require_mapping(payload, "allocation")
        return cls(
            symbol=data.get("symbol", ""),
            target_weight=data.get("target_weight"),
            minimum_weight=data.get("minimum_weight"),
            maximum_weight=data.get("maximum_weight"),
        )


@dataclass(frozen=True)
class RemainingAllocation:
    """Explicit recipient for the unallocated weight of a rule."""

    symbol: AssetReference | str

    def __post_init__(self) -> None:
        symbol = (
            self.symbol if isinstance(self.symbol, AssetReference) else AssetReference(self.symbol)
        )
        object.__setattr__(self, "symbol", symbol)

    def to_dict(self) -> dict[str, str]:
        return {"symbol": self.symbol.symbol}

    @classmethod
    def from_dict(cls, payload: object) -> RemainingAllocation:
        data = _require_mapping(payload, "remaining allocation")
        return cls(symbol=data.get("symbol", ""))


@dataclass(frozen=True)
class AllocationRule:
    """A prioritized conditional or unconditional target allocation."""

    rule_id: str
    name: str
    priority: int
    allocations: tuple[Allocation, ...]
    condition: RuleNode | None = None
    remaining: RemainingAllocation | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.rule_id, str) or not self.rule_id.strip():
            raise InvalidRuleError("rule_id must not be empty")
        if not isinstance(self.name, str) or not self.name.strip():
            raise InvalidRuleError("rule name must not be empty")
        if isinstance(self.priority, bool) or not isinstance(self.priority, int):
            raise InvalidRuleError("priority must be an integer")
        allocations = _normalize_sequence(self.allocations, "allocations")
        if not allocations or not all(isinstance(item, Allocation) for item in allocations):
            raise InvalidRuleError("allocations must contain at least one Allocation")
        if self.condition is not None and not isinstance(self.condition, (Condition, RuleGroup)):
            raise InvalidRuleError("condition must be Condition, RuleGroup, or None")
        if self.remaining is not None and not isinstance(self.remaining, RemainingAllocation):
            raise InvalidRuleError("remaining must be a RemainingAllocation or None")
        object.__setattr__(self, "rule_id", self.rule_id.strip())
        object.__setattr__(self, "name", self.name.strip())
        object.__setattr__(self, "allocations", allocations)

    def to_dict(self, *, include_timeframe: bool = False) -> dict[str, Any]:
        return {
            "rule_id": self.rule_id,
            "name": self.name,
            "priority": self.priority,
            "condition": (
                self.condition.to_dict(include_timeframe=include_timeframe)
                if self.condition
                else None
            ),
            "allocations": [allocation.to_dict() for allocation in self.allocations],
            "remaining": self.remaining.to_dict() if self.remaining else None,
        }

    @classmethod
    def from_dict(cls, payload: object) -> AllocationRule:
        data = _require_mapping(payload, "allocation rule")
        condition_payload = data.get("condition")
        condition = None
        if condition_payload is not None:
            condition_data = _require_mapping(condition_payload, "condition")
            if condition_data.get("type") == "condition":
                condition = Condition.from_dict(condition_data)
            elif condition_data.get("type") == "group":
                condition = RuleGroup.from_dict(condition_data)
            else:
                raise InvalidRuleError("condition type must be condition or group")
        return cls(
            rule_id=data.get("rule_id", ""),
            name=data.get("name", ""),
            priority=data.get("priority"),
            condition=condition,
            allocations=tuple(
                Allocation.from_dict(item)
                for item in _normalize_sequence(data.get("allocations", []), "allocations")
            ),
            remaining=(
                RemainingAllocation.from_dict(data["remaining"])
                if data.get("remaining") is not None
                else None
            ),
        )


@dataclass(frozen=True)
class FallbackAllocation:
    """Explicit fallback allocation used when no conditional rule matches."""

    allocations: tuple[Allocation, ...]
    name: str = "fallback"

    def __post_init__(self) -> None:
        if not isinstance(self.name, str) or not self.name.strip():
            raise InvalidAllocationError("fallback name must not be empty")
        allocations = _normalize_sequence(self.allocations, "allocations")
        if not allocations or not all(isinstance(item, Allocation) for item in allocations):
            raise InvalidAllocationError("fallback allocations must not be empty")
        object.__setattr__(self, "name", self.name.strip())
        object.__setattr__(self, "allocations", allocations)

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "allocations": [allocation.to_dict() for allocation in self.allocations],
        }

    @classmethod
    def from_dict(cls, payload: object) -> FallbackAllocation:
        data = _require_mapping(payload, "fallback")
        return cls(
            name=data.get("name", "fallback"),
            allocations=tuple(
                Allocation.from_dict(item)
                for item in _normalize_sequence(data.get("allocations", []), "allocations")
            ),
        )


@dataclass(frozen=True)
class AllocationSpecification:
    """Static target allocation used to initialize a stateful strategy.

    Unallocated weight is an implicit cash position.  Cash therefore remains
    distinct from declared ETF assets and no synthetic ticker is introduced.
    """

    allocations: tuple[Allocation, ...]

    def __post_init__(self) -> None:
        allocations = _normalize_sequence(self.allocations, "allocations")
        if not all(isinstance(item, Allocation) for item in allocations):
            raise InvalidAllocationError("allocation specification must contain Allocation values")
        symbols = [item.symbol.symbol for item in allocations]
        if len(set(symbols)) != len(symbols):
            raise InvalidAllocationError("allocation specification symbols must be unique")
        if sum(item.target_weight for item in allocations) > 1.0 + 1e-12:
            raise InvalidAllocationError("allocation specification weights must not exceed 1.0")
        object.__setattr__(self, "allocations", allocations)

    @property
    def cash_weight(self) -> float:
        """Return the implicit cash remainder of the specification."""
        return 1.0 - sum(item.target_weight for item in self.allocations)

    def to_dict(self) -> dict[str, Any]:
        return {"allocations": [allocation.to_dict() for allocation in self.allocations]}

    @classmethod
    def from_dict(cls, payload: object) -> AllocationSpecification:
        data = _require_mapping(payload, "allocation specification")
        return cls(
            allocations=tuple(
                Allocation.from_dict(item)
                for item in _normalize_sequence(data.get("allocations", []), "allocations")
            )
        )


@dataclass(frozen=True)
class RegimeDefinition:
    """One strategy-defined state and its complete target allocation."""

    state_id: str
    display_name: str
    target_allocation: AllocationSpecification
    metadata: Mapping[str, str] = MappingProxyType({})

    def __post_init__(self) -> None:
        if not isinstance(self.state_id, str) or not self.state_id.strip():
            raise InvalidStrategyError("regime state_id must not be empty")
        if not isinstance(self.display_name, str) or not self.display_name.strip():
            raise InvalidStrategyError("regime display_name must not be empty")
        if not isinstance(self.target_allocation, AllocationSpecification):
            raise InvalidStrategyError(
                "regime target_allocation must be an AllocationSpecification"
            )
        if not isinstance(self.metadata, Mapping):
            raise InvalidStrategyError("regime metadata must be a mapping")
        metadata = dict(self.metadata)
        if any(
            not isinstance(key, str) or not key.strip() or not isinstance(value, str)
            for key, value in metadata.items()
        ):
            raise InvalidStrategyError("regime metadata must contain string key/value pairs")
        object.__setattr__(self, "state_id", self.state_id.strip())
        object.__setattr__(self, "display_name", self.display_name.strip())
        object.__setattr__(
            self,
            "metadata",
            MappingProxyType({key.strip(): value for key, value in metadata.items()}),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "state_id": self.state_id,
            "display_name": self.display_name,
            "target_allocation": {
                "allocations": [
                    item.to_dict()
                    for item in sorted(
                        self.target_allocation.allocations,
                        key=lambda allocation: allocation.symbol.symbol,
                    )
                ]
            },
            "metadata": {key: self.metadata[key] for key in sorted(self.metadata)},
        }

    @classmethod
    def from_dict(cls, payload: object) -> RegimeDefinition:
        data = _require_mapping(payload, "regime definition")
        return cls(
            state_id=data.get("state_id", ""),
            display_name=data.get("display_name", ""),
            target_allocation=AllocationSpecification.from_dict(data.get("target_allocation")),
            metadata=data.get("metadata", {}),
        )


@dataclass(frozen=True)
class RegimeTransitionDefinition:
    """One prioritized, condition-driven edge in a strategy-defined graph."""

    transition_id: str
    from_state: str
    to_state: str
    condition: RuleNode
    priority: int
    description: str = ""

    def __post_init__(self) -> None:
        for label in ("transition_id", "from_state", "to_state"):
            value = getattr(self, label)
            if not isinstance(value, str) or not value.strip():
                raise InvalidStrategyError(f"regime {label} must not be empty")
        if not isinstance(self.condition, (Condition, RuleGroup)):
            raise InvalidStrategyError("regime transition condition must be a rule node")
        if isinstance(self.priority, bool) or not isinstance(self.priority, int):
            raise InvalidStrategyError("regime transition priority must be an integer")
        if not isinstance(self.description, str):
            raise InvalidStrategyError("regime transition description must be a string")
        object.__setattr__(self, "transition_id", self.transition_id.strip())
        object.__setattr__(self, "from_state", self.from_state.strip())
        object.__setattr__(self, "to_state", self.to_state.strip())
        object.__setattr__(self, "description", self.description.strip())

    def to_dict(self) -> dict[str, Any]:
        return {
            "transition_id": self.transition_id,
            "from_state": self.from_state,
            "to_state": self.to_state,
            "condition": self.condition.to_dict(include_timeframe=True),
            "priority": self.priority,
            "description": self.description,
        }

    @classmethod
    def from_dict(cls, payload: object) -> RegimeTransitionDefinition:
        data = _require_mapping(payload, "regime transition")
        condition_payload = _require_mapping(data.get("condition"), "transition condition")
        node_type = condition_payload.get("type")
        if node_type == "condition":
            condition: RuleNode = Condition.from_dict(condition_payload)
        elif node_type == "group":
            condition = RuleGroup.from_dict(condition_payload)
        else:
            raise InvalidStrategyError("transition condition type must be condition or group")
        return cls(
            transition_id=data.get("transition_id", ""),
            from_state=data.get("from_state", ""),
            to_state=data.get("to_state", ""),
            condition=condition,
            priority=data.get("priority"),
            description=data.get("description", ""),
        )


@dataclass(frozen=True, init=False)
class RebalancePolicy:
    """Rebalance configuration; execution is owned by PHASE 3."""

    frequency: RebalanceFrequency
    threshold: float | None

    def __init__(
        self,
        frequency: RebalanceFrequency,
        threshold: float | None = None,
        *,
        rebalance_threshold: float | None = None,
    ) -> None:
        if (
            threshold is not None
            and rebalance_threshold is not None
            and threshold != rebalance_threshold
        ):
            raise InvalidRebalancePolicyError("threshold and rebalance_threshold must agree")
        canonical_threshold = threshold if threshold is not None else rebalance_threshold
        frequency_value = _validate_enum(
            frequency, RebalanceFrequency, InvalidRebalancePolicyError, "frequency"
        )
        if canonical_threshold is not None and (
            not _is_finite_number(canonical_threshold) or not 0 <= canonical_threshold <= 1
        ):
            raise InvalidRebalancePolicyError("rebalance threshold must be finite and in [0, 1]")
        object.__setattr__(self, "frequency", frequency_value)
        object.__setattr__(
            self, "threshold", None if canonical_threshold is None else float(canonical_threshold)
        )

    @property
    def rebalance_threshold(self) -> float | None:
        return self.threshold

    def to_dict(self) -> dict[str, Any]:
        return {"frequency": self.frequency.value, "threshold": self.threshold}

    @classmethod
    def from_dict(cls, payload: object) -> RebalancePolicy:
        data = _require_mapping(payload, "rebalance policy")
        return cls(frequency=data.get("frequency", ""), threshold=data.get("threshold"))


@dataclass(frozen=True)
class StrategyDefinition:
    """Complete structural definition of one data-driven strategy."""

    strategy_id: str
    name: str
    description: str
    assets: tuple[AssetReference | StrategyAssetReference, ...]
    price_field: PriceField
    rules: tuple[AllocationRule, ...]
    fallback: FallbackAllocation
    rebalance_policy: RebalancePolicy
    no_match_behavior: NoMatchBehavior = NoMatchBehavior.USE_FALLBACK
    initial_allocation: AllocationSpecification | None = None
    strategy_schema_version: str = LEGACY_STRATEGY_SCHEMA_VERSION
    strategy_mode: StrategyEvaluationMode = StrategyEvaluationMode.RULE_BASED
    initial_regime: str | None = None
    regimes: tuple[RegimeDefinition, ...] = ()
    transitions: tuple[RegimeTransitionDefinition, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.strategy_id, str) or not self.strategy_id.strip():
            raise InvalidStrategyError("strategy_id must not be empty")
        if not isinstance(self.name, str) or not self.name.strip():
            raise InvalidStrategyError("strategy name must not be empty")
        if not isinstance(self.description, str):
            raise InvalidStrategyError("strategy description must be a string")
        if (
            not isinstance(self.strategy_schema_version, str)
            or self.strategy_schema_version not in SUPPORTED_STRATEGY_SCHEMA_VERSIONS
        ):
            raise InvalidStrategyError(
                f"unsupported strategy schema version: {self.strategy_schema_version}"
            )
        assets = _normalize_sequence(self.assets, "assets")
        if not assets or not all(
            isinstance(item, (AssetReference, StrategyAssetReference)) for item in assets
        ):
            raise InvalidStrategyError("assets must contain at least one AssetReference")
        if len({item.symbol for item in assets}) != len(assets):
            raise InvalidStrategyError("strategy assets must not contain duplicate symbols")
        if self.strategy_schema_version == CURRENT_STRATEGY_SCHEMA_VERSION:
            assets = tuple(
                item
                if isinstance(item, StrategyAssetReference)
                else StrategyAssetReference(item.symbol, AssetRole.BOTH)
                for item in assets
            )
        price_field = _validate_enum(
            self.price_field, PriceField, InvalidStrategyError, "price_field"
        )
        rules = _normalize_sequence(self.rules, "rules")
        if not all(isinstance(item, AllocationRule) for item in rules):
            raise InvalidStrategyError("rules must contain AllocationRule values")
        if not isinstance(self.fallback, FallbackAllocation):
            raise InvalidStrategyError("fallback must be a FallbackAllocation value")
        if not isinstance(self.rebalance_policy, RebalancePolicy):
            raise InvalidStrategyError("rebalance_policy must be a RebalancePolicy value")
        no_match_behavior = _validate_enum(
            self.no_match_behavior,
            NoMatchBehavior,
            InvalidStrategyError,
            "no_match_behavior",
        )
        strategy_mode = _validate_enum(
            self.strategy_mode,
            StrategyEvaluationMode,
            InvalidStrategyError,
            "strategy_mode",
        )
        if self.initial_allocation is not None and not isinstance(
            self.initial_allocation, AllocationSpecification
        ):
            raise InvalidStrategyError(
                "initial_allocation must be an AllocationSpecification value or None"
            )
        object.__setattr__(self, "strategy_id", self.strategy_id.strip())
        object.__setattr__(self, "name", self.name.strip())
        object.__setattr__(self, "assets", assets)
        object.__setattr__(self, "price_field", price_field)
        object.__setattr__(self, "rules", rules)
        object.__setattr__(self, "no_match_behavior", no_match_behavior)
        object.__setattr__(self, "strategy_schema_version", self.strategy_schema_version)
        object.__setattr__(self, "strategy_mode", strategy_mode)
        if self.initial_regime is not None:
            if not isinstance(self.initial_regime, str) or not self.initial_regime.strip():
                raise InvalidStrategyError("initial_regime must be a non-empty string or None")
            object.__setattr__(self, "initial_regime", self.initial_regime.strip())
        regimes = _normalize_sequence(self.regimes, "regimes")
        transitions = _normalize_sequence(self.transitions, "transitions")
        if not all(isinstance(item, RegimeDefinition) for item in regimes):
            raise InvalidStrategyError("regimes must contain RegimeDefinition values")
        if not all(isinstance(item, RegimeTransitionDefinition) for item in transitions):
            raise InvalidStrategyError("transitions must contain RegimeTransitionDefinition values")
        object.__setattr__(self, "regimes", regimes)
        object.__setattr__(self, "transitions", transitions)

    def to_dict(self) -> dict[str, Any]:
        current_schema = self.strategy_schema_version == CURRENT_STRATEGY_SCHEMA_VERSION
        payload = {
            "strategy_id": self.strategy_id,
            "name": self.name,
            "description": self.description,
            "assets": [
                asset.to_dict() if current_schema else {"symbol": asset.symbol}
                for asset in self.assets
            ],
            "price_field": self.price_field.value,
            "rules": [rule.to_dict(include_timeframe=current_schema) for rule in self.rules],
            "fallback": self.fallback.to_dict(),
            "rebalance_policy": self.rebalance_policy.to_dict(),
        }
        if current_schema:
            payload["strategy_schema_version"] = CURRENT_STRATEGY_SCHEMA_VERSION
        if self.no_match_behavior is not NoMatchBehavior.USE_FALLBACK:
            payload["no_match_behavior"] = self.no_match_behavior.value
        if self.initial_allocation is not None:
            payload["initial_allocation"] = self.initial_allocation.to_dict()
        if current_schema and self.strategy_mode is not StrategyEvaluationMode.RULE_BASED:
            payload["strategy_mode"] = self.strategy_mode.value
        if current_schema and self.strategy_mode is StrategyEvaluationMode.REGIME_STATE_MACHINE:
            payload["initial_regime"] = self.initial_regime
            payload["regimes"] = [
                regime.to_dict() for regime in sorted(self.regimes, key=lambda item: item.state_id)
            ]
            payload["transitions"] = [
                transition.to_dict()
                for transition in sorted(
                    self.transitions,
                    key=lambda item: (item.from_state, item.priority, item.transition_id),
                )
            ]
        return payload

    def to_json(self) -> str:
        return _canonical_json(self.to_dict())

    @property
    def signal_asset_symbols(self) -> frozenset[str]:
        """Return assets permitted as indicator/price signal sources."""
        return frozenset(
            asset.symbol
            for asset in self.assets
            if not isinstance(asset, StrategyAssetReference) or asset.role.signal_capable
        )

    @property
    def execution_asset_symbols(self) -> frozenset[str]:
        """Return assets permitted in target allocations."""
        return frozenset(
            asset.symbol
            for asset in self.assets
            if not isinstance(asset, StrategyAssetReference) or asset.role.execution_capable
        )

    @classmethod
    def from_dict(cls, payload: object) -> StrategyDefinition:
        data = _require_mapping(payload, "strategy definition")
        schema_version = data.get("strategy_schema_version", LEGACY_STRATEGY_SCHEMA_VERSION)
        asset_type = (
            StrategyAssetReference
            if schema_version == CURRENT_STRATEGY_SCHEMA_VERSION
            else AssetReference
        )
        return cls(
            strategy_id=data.get("strategy_id", ""),
            name=data.get("name", ""),
            description=data.get("description", ""),
            assets=tuple(
                asset_type.from_dict(item)
                for item in _normalize_sequence(data.get("assets", []), "assets")
            ),
            price_field=data.get("price_field", ""),
            rules=tuple(
                AllocationRule.from_dict(item)
                for item in _normalize_sequence(data.get("rules", []), "rules")
            ),
            fallback=FallbackAllocation.from_dict(data.get("fallback")),
            rebalance_policy=RebalancePolicy.from_dict(data.get("rebalance_policy")),
            no_match_behavior=data.get("no_match_behavior", NoMatchBehavior.USE_FALLBACK.value),
            initial_allocation=(
                AllocationSpecification.from_dict(data["initial_allocation"])
                if data.get("initial_allocation") is not None
                else None
            ),
            strategy_schema_version=schema_version,
            strategy_mode=data.get("strategy_mode", StrategyEvaluationMode.RULE_BASED.value),
            initial_regime=data.get("initial_regime"),
            regimes=tuple(
                RegimeDefinition.from_dict(item)
                for item in _normalize_sequence(data.get("regimes", []), "regimes")
            ),
            transitions=tuple(
                RegimeTransitionDefinition.from_dict(item)
                for item in _normalize_sequence(data.get("transitions", []), "transitions")
            ),
        )

    @classmethod
    def from_json(cls, payload: str) -> StrategyDefinition:
        return cls.from_dict(json.loads(payload))


@dataclass(frozen=True)
class StrategyMaterializationProvenance:
    """Immutable provenance for a parameter-materialized strategy version."""

    base_strategy_version_id: str
    base_strategy_version_hash: str
    parameter_set_hash: str
    binding_hash: str
    materialization_spec_hash: str
    derived_strategy_version_hash: str

    def __post_init__(self) -> None:
        value = self.base_strategy_version_id
        if not isinstance(value, str) or not value.strip():
            raise InvalidStrategyVersionError("base_strategy_version_id must be a non-empty string")
        object.__setattr__(self, "base_strategy_version_id", value.strip())
        for label in (
            "base_strategy_version_hash",
            "parameter_set_hash",
            "binding_hash",
            "materialization_spec_hash",
            "derived_strategy_version_hash",
        ):
            value = getattr(self, label)
            if not isinstance(value, str) or not _HASH_PATTERN.fullmatch(value):
                raise InvalidStrategyVersionError(f"{label} must be a SHA-256 hex digest")
            object.__setattr__(self, label, value)

    def to_dict(self) -> dict[str, str]:
        return {
            "base_strategy_version_id": self.base_strategy_version_id,
            "base_strategy_version_hash": self.base_strategy_version_hash,
            "parameter_set_hash": self.parameter_set_hash,
            "binding_hash": self.binding_hash,
            "materialization_spec_hash": self.materialization_spec_hash,
            "derived_strategy_version_hash": self.derived_strategy_version_hash,
        }

    @classmethod
    def from_dict(cls, payload: object) -> StrategyMaterializationProvenance:
        data = _require_mapping(payload, "materialization provenance")
        return cls(
            base_strategy_version_id=data.get("base_strategy_version_id", ""),
            base_strategy_version_hash=data.get("base_strategy_version_hash", ""),
            parameter_set_hash=data.get("parameter_set_hash", ""),
            binding_hash=data.get("binding_hash", ""),
            materialization_spec_hash=data.get("materialization_spec_hash", ""),
            derived_strategy_version_hash=data.get("derived_strategy_version_hash", ""),
        )


@dataclass(frozen=True)
class StrategyVersion:
    """Immutable strategy configuration snapshot with a deterministic hash."""

    strategy_id: str
    version_id: str
    version_number: int
    created_at: datetime
    configuration: StrategyDefinition
    content_hash: str | None = None
    status: StrategyStatus = StrategyStatus.DRAFT
    materialization_provenance: StrategyMaterializationProvenance | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.strategy_id, str) or not self.strategy_id.strip():
            raise InvalidStrategyVersionError("strategy_id must not be empty")
        if not isinstance(self.version_id, str) or not self.version_id.strip():
            raise InvalidStrategyVersionError("version_id must not be empty")
        if (
            isinstance(self.version_number, bool)
            or not isinstance(self.version_number, int)
            or self.version_number <= 0
        ):
            raise InvalidStrategyVersionError("version_number must be a positive integer")
        if not isinstance(self.created_at, datetime):
            raise InvalidStrategyVersionError("created_at must be a datetime")
        if not isinstance(self.configuration, StrategyDefinition):
            raise InvalidStrategyVersionError("configuration must be a StrategyDefinition value")
        if self.configuration.strategy_id != self.strategy_id:
            raise InvalidStrategyVersionError(
                "configuration strategy_id must match version strategy_id"
            )
        status = _validate_enum(self.status, StrategyStatus, InvalidStrategyVersionError, "status")
        if self.materialization_provenance is not None and not isinstance(
            self.materialization_provenance, StrategyMaterializationProvenance
        ):
            raise InvalidStrategyVersionError(
                "materialization_provenance must be StrategyMaterializationProvenance"
            )
        expected_hash = _content_hash(self.configuration)
        if self.content_hash is not None and self.content_hash != expected_hash:
            raise InvalidStrategyVersionError("content_hash does not match configuration")
        object.__setattr__(self, "strategy_id", self.strategy_id.strip())
        object.__setattr__(self, "version_id", self.version_id.strip())
        object.__setattr__(self, "content_hash", expected_hash)
        object.__setattr__(self, "status", status)

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "strategy_id": self.strategy_id,
            "version_id": self.version_id,
            "version_number": self.version_number,
            "created_at": self.created_at.isoformat(),
            "configuration": self.configuration.to_dict(),
            "content_hash": self.content_hash,
            "status": self.status.value,
        }
        if self.materialization_provenance is not None:
            payload["materialization_provenance"] = self.materialization_provenance.to_dict()
        return payload

    def to_json(self) -> str:
        return _canonical_json(self.to_dict())

    @classmethod
    def from_dict(cls, payload: object) -> StrategyVersion:
        data = _require_mapping(payload, "strategy version")
        return cls(
            strategy_id=data.get("strategy_id", ""),
            version_id=data.get("version_id", ""),
            version_number=data.get("version_number"),
            created_at=datetime.fromisoformat(str(data.get("created_at", ""))),
            configuration=StrategyDefinition.from_dict(data.get("configuration")),
            content_hash=data.get("content_hash"),
            status=data.get("status", StrategyStatus.DRAFT.value),
            materialization_provenance=(
                StrategyMaterializationProvenance.from_dict(data["materialization_provenance"])
                if data.get("materialization_provenance") is not None
                else None
            ),
        )

    @classmethod
    def from_json(cls, payload: str) -> StrategyVersion:
        return cls.from_dict(json.loads(payload))


def _canonical_json(payload: Mapping[str, Any]) -> str:
    return json.dumps(payload, ensure_ascii=True, sort_keys=True, separators=(",", ":"))


def _content_hash(configuration: StrategyDefinition) -> str:
    return hashlib.sha256(configuration.to_json().encode("utf-8")).hexdigest()
