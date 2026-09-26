"""Stable semantic targets for regime-aware parameter grid bindings."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from research.exceptions import InvalidParameterBindingError
from research.experiments import ParameterSet
from research.materialization import (
    BindingValueType,
    ParameterBinding,
    ParameterBindingSet,
    materialize_strategy_version,
)
from strategies import StrategyVersion


class SemanticTargetKind(StrEnum):
    TRANSITION_THRESHOLD = "transition_threshold"
    TRANSITION_INDICATOR_PERIOD = "transition_indicator_period"
    NESTED_CONDITION_THRESHOLD = "nested_condition_threshold"
    NESTED_CONDITION_PERIOD = "nested_condition_period"
    STATE_ALLOCATION_WEIGHT = "state_allocation_weight"
    VALUE_ZONE_ENTRY_THRESHOLD = "value_zone_entry_threshold"
    VALUE_ZONE_EXIT_THRESHOLD = "value_zone_exit_threshold"
    VALUE_ZONE_PERIOD = "value_zone_period"


class OperandSide(StrEnum):
    LEFT = "left"
    RIGHT = "right"


@dataclass(frozen=True)
class SemanticTargetDescriptor:
    """Typed, ID-based address inside one frozen strategy configuration."""

    target_kind: SemanticTargetKind | str
    base_strategy_version_id: str
    base_strategy_version_hash: str
    transition_id: str | None = None
    state_id: str | None = None
    zone_id: str | None = None
    symbol: str | None = None
    condition_path: tuple[int, ...] = ()
    operand_side: OperandSide | str | None = None

    def __post_init__(self) -> None:
        try:
            kind = (
                self.target_kind
                if isinstance(self.target_kind, SemanticTargetKind)
                else SemanticTargetKind(self.target_kind)
            )
        except (TypeError, ValueError) as exc:
            raise InvalidParameterBindingError("semantic target kind is unsupported") from exc
        for label in ("base_strategy_version_id", "base_strategy_version_hash"):
            value = getattr(self, label)
            if not isinstance(value, str) or not value.strip():
                raise InvalidParameterBindingError(f"{label} must be non-empty")
        normalized_hash = self.base_strategy_version_hash.strip().lower()
        if len(normalized_hash) != 64 or any(
            char not in "0123456789abcdef" for char in normalized_hash
        ):
            raise InvalidParameterBindingError(
                "base_strategy_version_hash must be a SHA-256 digest"
            )
        if any(
            isinstance(item, bool) or not isinstance(item, int) or item < 0
            for item in self.condition_path
        ):
            raise InvalidParameterBindingError("condition_path must contain non-negative integers")
        side = self.operand_side
        if side is not None:
            try:
                side = side if isinstance(side, OperandSide) else OperandSide(side)
            except (TypeError, ValueError) as exc:
                raise InvalidParameterBindingError("operand_side must be left or right") from exc
        object.__setattr__(self, "target_kind", kind)
        object.__setattr__(self, "base_strategy_version_id", self.base_strategy_version_id.strip())
        object.__setattr__(
            self, "base_strategy_version_hash", self.base_strategy_version_hash.strip().lower()
        )
        object.__setattr__(self, "condition_path", tuple(self.condition_path))
        for key in ("transition_id", "state_id", "zone_id"):
            value = getattr(self, key)
            object.__setattr__(self, key, value.strip() if value else None)
        object.__setattr__(self, "symbol", self.symbol.strip().upper() if self.symbol else None)
        object.__setattr__(self, "operand_side", side)

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "target_kind": self.target_kind.value,
            "base_strategy_version_id": self.base_strategy_version_id,
            "base_strategy_version_hash": self.base_strategy_version_hash,
            "condition_path": list(self.condition_path),
        }
        for key in ("transition_id", "state_id", "zone_id", "symbol", "operand_side"):
            value = getattr(self, key)
            if value is not None:
                payload[key] = value.value if isinstance(value, StrEnum) else value
        return payload

    @classmethod
    def from_dict(cls, payload: object) -> SemanticTargetDescriptor:
        if not isinstance(payload, Mapping):
            raise InvalidParameterBindingError("semantic target descriptor must be an object")
        path = payload.get("condition_path", ())
        if isinstance(path, (str, bytes)) or not isinstance(path, Sequence):
            raise InvalidParameterBindingError("condition_path must be an array")
        return cls(
            target_kind=payload.get("target_kind", ""),
            base_strategy_version_id=payload.get("base_strategy_version_id", ""),
            base_strategy_version_hash=payload.get("base_strategy_version_hash", ""),
            transition_id=payload.get("transition_id"),
            state_id=payload.get("state_id"),
            zone_id=payload.get("zone_id"),
            symbol=payload.get("symbol"),
            condition_path=tuple(path),
            operand_side=payload.get("operand_side"),
        )


@dataclass(frozen=True)
class SemanticParameterBinding:
    parameter_name: str
    value_type: BindingValueType | str
    binding_version: str
    descriptor: SemanticTargetDescriptor

    def __post_init__(self) -> None:
        if not isinstance(self.descriptor, SemanticTargetDescriptor):
            raise InvalidParameterBindingError("descriptor must be a SemanticTargetDescriptor")
        if not isinstance(self.parameter_name, str) or not self.parameter_name.strip():
            raise InvalidParameterBindingError("parameter_name must be non-empty")
        try:
            value_type = (
                self.value_type
                if isinstance(self.value_type, BindingValueType)
                else BindingValueType(self.value_type)
            )
        except (TypeError, ValueError) as exc:
            raise InvalidParameterBindingError("value_type is unsupported") from exc
        object.__setattr__(self, "parameter_name", self.parameter_name.strip())
        object.__setattr__(self, "value_type", value_type)
        binding_version = str(self.binding_version).strip()
        if not binding_version:
            raise InvalidParameterBindingError("binding_version must be non-empty")
        object.__setattr__(self, "binding_version", binding_version)

    def to_dict(self) -> dict[str, Any]:
        return {
            "parameter_name": self.parameter_name,
            "value_type": self.value_type.value,
            "binding_version": self.binding_version,
            "descriptor": self.descriptor.to_dict(),
        }

    @classmethod
    def from_dict(cls, payload: object) -> SemanticParameterBinding:
        if not isinstance(payload, Mapping):
            raise InvalidParameterBindingError("semantic parameter binding must be an object")
        return cls(
            parameter_name=payload.get("parameter_name", ""),
            value_type=payload.get("value_type", ""),
            binding_version=payload.get("binding_version", ""),
            descriptor=SemanticTargetDescriptor.from_dict(payload.get("descriptor")),
        )


@dataclass(frozen=True)
class ResolvedSemanticBinding:
    semantic_binding: SemanticParameterBinding
    parameter_binding: ParameterBinding
    audit: Mapping[str, Any]


@dataclass(frozen=True)
class SemanticTargetCatalog:
    supported: tuple[SemanticParameterBinding, ...]
    unsupported: tuple[Mapping[str, str], ...] = ()


def discover_semantic_bindings(base_strategy_version: StrategyVersion) -> SemanticTargetCatalog:
    """Discover supported targets for a version without exposing array paths."""
    if not isinstance(base_strategy_version, StrategyVersion):
        raise InvalidParameterBindingError("base_strategy_version must be a StrategyVersion")
    version_id = base_strategy_version.version_id
    version_hash = base_strategy_version.content_hash or ""
    supported: list[SemanticParameterBinding] = []
    unsupported: list[Mapping[str, str]] = []
    payload = base_strategy_version.configuration.to_dict()
    for transition in payload.get("transitions", ()):
        transition_id = transition.get("transition_id")
        _discover_condition_targets(
            transition.get("condition"),
            transition_id,
            version_id,
            version_hash,
            supported,
            unsupported,
            (),
        )
        unsupported.append(
            {
                "target": f"transition:{transition_id}:priority",
                "reason": "TRANSITION_PRIORITY_NOT_TUNABLE",
            }
        )
        unsupported.append(
            {
                "target": f"transition:{transition_id}:structure",
                "reason": "SYMBOL_TIMEFRAME_COMPARATOR_NOT_TUNABLE",
            }
        )
    for regime in payload.get("regimes", ()):
        for allocation in regime.get("target_allocation", {}).get("allocations", ()):
            symbol = allocation.get("symbol")
            descriptor = SemanticTargetDescriptor(
                SemanticTargetKind.STATE_ALLOCATION_WEIGHT,
                version_id,
                version_hash,
                state_id=regime.get("state_id"),
                symbol=symbol,
            )
            supported.append(
                SemanticParameterBinding(
                    f"state_{regime.get('state_id')}_{symbol}_weight",
                    BindingValueType.ALLOCATION_WEIGHT,
                    "phase-13e-1-v1",
                    descriptor,
                )
            )
    for zone in payload.get("value_zones", ()):
        zone_id = zone.get("zone_id")
        for kind, field, value_type in (
            (
                SemanticTargetKind.VALUE_ZONE_ENTRY_THRESHOLD,
                "entry",
                BindingValueType.RELATIVE_THRESHOLD,
            ),
            (
                SemanticTargetKind.VALUE_ZONE_EXIT_THRESHOLD,
                "exit",
                BindingValueType.RELATIVE_THRESHOLD,
            ),
            (SemanticTargetKind.VALUE_ZONE_PERIOD, "period", BindingValueType.POSITIVE_INTEGER),
        ):
            if field != "exit" or zone.get("exit_threshold") is not None:
                descriptor = SemanticTargetDescriptor(
                    kind, version_id, version_hash, zone_id=zone_id
                )
                supported.append(
                    SemanticParameterBinding(
                        f"value_zone_{zone_id}_{field}", value_type, "phase-13e-1-v1", descriptor
                    )
                )
        unsupported.append(
            {
                "target": f"value_zone:{zone_id}:priority",
                "reason": "VALUE_ZONE_PRIORITY_NOT_TUNABLE",
            }
        )
    return SemanticTargetCatalog(tuple(supported), tuple(unsupported))


def _discover_condition_targets(
    node: object,
    transition_id: str | None,
    version_id: str,
    version_hash: str,
    supported: list[SemanticParameterBinding],
    unsupported: list[Mapping[str, str]],
    path: tuple[int, ...],
) -> None:
    if not isinstance(node, Mapping):
        return
    if node.get("type") == "group":
        unsupported.append(
            {
                "target": f"transition:{transition_id}:condition",
                "reason": "LOGICAL_GROUP_STRUCTURE_NOT_TUNABLE",
            }
        )
        for index, child in enumerate(node.get("children", ())):
            _discover_condition_targets(
                child,
                transition_id,
                version_id,
                version_hash,
                supported,
                unsupported,
                path + (index,),
            )
        return
    if node.get("type") != "condition":
        return
    kind_prefix = "transition" if not path else "nested"
    if isinstance(node.get("threshold"), Mapping) and node["threshold"].get("type") == "relative":
        kind = (
            SemanticTargetKind.TRANSITION_THRESHOLD
            if not path
            else SemanticTargetKind.NESTED_CONDITION_THRESHOLD
        )
        supported.append(
            SemanticParameterBinding(
                f"{kind_prefix}_{transition_id}_{'_'.join(map(str, path)) or 'root'}_threshold",
                BindingValueType.RELATIVE_THRESHOLD,
                "phase-13e-1-v1",
                SemanticTargetDescriptor(
                    kind, version_id, version_hash, transition_id=transition_id, condition_path=path
                ),
            )
        )
    elif node.get("threshold") is not None:
        unsupported.append(
            {
                "target": f"transition:{transition_id}:threshold",
                "reason": "ABSOLUTE_THRESHOLD_NOT_TUNABLE",
            }
        )
    for side in (OperandSide.LEFT, OperandSide.RIGHT):
        operand = node.get(side.value)
        if isinstance(operand, Mapping) and operand.get("type") in {"ma", "ema"}:
            kind = (
                SemanticTargetKind.TRANSITION_INDICATOR_PERIOD
                if not path
                else SemanticTargetKind.NESTED_CONDITION_PERIOD
            )
            supported.append(
                SemanticParameterBinding(
                    (
                        f"{kind_prefix}_{transition_id}_"
                        f"{'_'.join(map(str, path)) or 'root'}_{side.value}_period"
                    ),
                    BindingValueType.POSITIVE_INTEGER,
                    "phase-13e-1-v1",
                    SemanticTargetDescriptor(
                        kind,
                        version_id,
                        version_hash,
                        transition_id=transition_id,
                        condition_path=path,
                        operand_side=side,
                    ),
                )
            )
        elif isinstance(operand, Mapping) and operand.get("type") == "constant":
            unsupported.append(
                {
                    "target": f"transition:{transition_id}:{side.value}",
                    "reason": "CONSTANT_VALUE_NOT_TUNABLE",
                }
            )


def resolve_semantic_bindings(
    base_strategy_version: StrategyVersion,
    bindings: Sequence[SemanticParameterBinding],
) -> tuple[ResolvedSemanticBinding, ...]:
    """Resolve semantic IDs to legacy paths without weakening identity checks."""
    if not isinstance(base_strategy_version, StrategyVersion):
        raise InvalidParameterBindingError("base_strategy_version must be a StrategyVersion")
    resolved: list[ResolvedSemanticBinding] = []
    names: set[str] = set()
    for binding in bindings:
        if binding.parameter_name in names:
            raise InvalidParameterBindingError("semantic parameter names must be unique")
        names.add(binding.parameter_name)
        descriptor = binding.descriptor
        if (
            descriptor.base_strategy_version_id != base_strategy_version.version_id
            or descriptor.base_strategy_version_hash != base_strategy_version.content_hash
        ):
            raise InvalidParameterBindingError(
                "semantic binding base strategy identity does not match"
            )
        path = _resolve_path(base_strategy_version.configuration.to_dict(), descriptor)
        legacy = ParameterBinding(
            binding.parameter_name, path, binding.value_type, binding.binding_version
        )
        resolved.append(
            ResolvedSemanticBinding(
                binding,
                legacy,
                {
                    "parameter_name": binding.parameter_name,
                    "descriptor": descriptor.to_dict(),
                    "resolved_target_path": path,
                    "base_strategy_version_id": base_strategy_version.version_id,
                    "base_strategy_version_hash": base_strategy_version.content_hash,
                },
            )
        )
    return tuple(resolved)


def materialize_semantic_strategy_version(
    base_strategy_version: StrategyVersion,
    parameter_set: ParameterSet,
    bindings: Sequence[SemanticParameterBinding],
) -> StrategyVersion:
    resolved = resolve_semantic_bindings(base_strategy_version, bindings)
    audits = [
        {
            **item.audit,
            "parameter_value": parameter_set.values[item.semantic_binding.parameter_name],
        }
        for item in resolved
    ]
    return materialize_strategy_version(
        base_strategy_version,
        parameter_set,
        ParameterBindingSet(tuple(item.parameter_binding for item in resolved)),
        binding_audit=audits,
    )


def _resolve_path(payload: Mapping[str, Any], descriptor: SemanticTargetDescriptor) -> str:
    kind = descriptor.target_kind
    if kind in {
        SemanticTargetKind.TRANSITION_THRESHOLD,
        SemanticTargetKind.TRANSITION_INDICATOR_PERIOD,
        SemanticTargetKind.NESTED_CONDITION_THRESHOLD,
        SemanticTargetKind.NESTED_CONDITION_PERIOD,
    }:
        index, transition = _find_unique(
            payload.get("transitions", ()), "transition_id", descriptor.transition_id, "transition"
        )
        node: Any = transition.get("condition")
        for child_index in descriptor.condition_path:
            if not isinstance(node, Mapping) or node.get("type") != "group":
                raise InvalidParameterBindingError(
                    "condition_path does not resolve through a group"
                )
            try:
                node = node["children"][child_index]
            except (IndexError, KeyError, TypeError) as exc:
                raise InvalidParameterBindingError("condition_path target was not found") from exc
        prefix = f"transitions[{index}].condition" + "".join(
            f".children[{item}]" for item in descriptor.condition_path
        )
        if kind in {
            SemanticTargetKind.TRANSITION_THRESHOLD,
            SemanticTargetKind.NESTED_CONDITION_THRESHOLD,
        }:
            if (
                not isinstance(node, Mapping)
                or node.get("type") != "condition"
                or node.get("threshold") is None
            ):
                raise InvalidParameterBindingError(
                    "semantic threshold target has no condition threshold"
                )
            if node["threshold"].get("type") != "relative":
                raise InvalidParameterBindingError("only relative thresholds are supported")
            return f"{prefix}.threshold.value"
        side = descriptor.operand_side
        if side is None:
            raise InvalidParameterBindingError("period target requires operand_side")
        operand = node.get(side.value) if isinstance(node, Mapping) else None
        if not isinstance(operand, Mapping) or operand.get("type") not in {"ma", "ema"}:
            raise InvalidParameterBindingError("period target requires an MA or EMA operand")
        return f"{prefix}.{side.value}.period"
    if kind is SemanticTargetKind.STATE_ALLOCATION_WEIGHT:
        state_index, state = _find_unique(
            payload.get("regimes", ()), "state_id", descriptor.state_id, "state"
        )
        allocation_index = _allocation_index(
            state.get("target_allocation", {}).get("allocations", ()), descriptor.symbol
        )
        return (
            f"regimes[{state_index}].target_allocation.allocations["
            f"{allocation_index}].target_weight"
        )
    if kind in {
        SemanticTargetKind.VALUE_ZONE_ENTRY_THRESHOLD,
        SemanticTargetKind.VALUE_ZONE_EXIT_THRESHOLD,
        SemanticTargetKind.VALUE_ZONE_PERIOD,
    }:
        index, _zone = _find_unique(
            payload.get("value_zones", ()), "zone_id", descriptor.zone_id, "value zone"
        )
        field = {
            SemanticTargetKind.VALUE_ZONE_ENTRY_THRESHOLD: "entry_threshold",
            SemanticTargetKind.VALUE_ZONE_EXIT_THRESHOLD: "exit_threshold",
            SemanticTargetKind.VALUE_ZONE_PERIOD: "period",
        }[kind]
        return f"value_zones[{index}].{field}"
    raise InvalidParameterBindingError("semantic target kind is unsupported")


def _find_unique(
    items: object, key: str, value: str | None, label: str
) -> tuple[int, Mapping[str, Any]]:
    if not value or isinstance(items, (str, bytes)) or not isinstance(items, Sequence):
        raise InvalidParameterBindingError(f"{label} identity is required")
    matches = [
        (index, item)
        for index, item in enumerate(items)
        if isinstance(item, Mapping) and item.get(key) == value
    ]
    if len(matches) != 1:
        raise InvalidParameterBindingError(f"{label} identity is missing or ambiguous: {value}")
    return matches[0]


def _allocation_index(items: object, symbol: str | None) -> int:
    if not symbol or isinstance(items, (str, bytes)) or not isinstance(items, Sequence):
        raise InvalidParameterBindingError("allocation symbol is required")
    matches = [
        index
        for index, item in enumerate(items)
        if isinstance(item, Mapping) and item.get("symbol") == symbol
    ]
    if len(matches) != 1:
        raise InvalidParameterBindingError(f"allocation symbol is missing or ambiguous: {symbol}")
    return matches[0]


__all__ = [
    "OperandSide",
    "ResolvedSemanticBinding",
    "SemanticParameterBinding",
    "SemanticTargetDescriptor",
    "SemanticTargetKind",
    "SemanticTargetCatalog",
    "discover_semantic_bindings",
    "materialize_semantic_strategy_version",
    "resolve_semantic_bindings",
]
