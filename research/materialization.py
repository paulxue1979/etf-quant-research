"""PHASE 8D-1 safe parameter binding and immutable strategy materialization."""

from __future__ import annotations

import copy
import math
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from research.canonical import sha256_hash
from research.exceptions import (
    InvalidParameterBindingError,
    ParameterBindingTypeMismatchError,
    StrategyMaterializationError,
    UnsupportedParameterBindingTargetError,
)
from research.experiments import ParameterSet, ParameterType
from strategies import (
    StrategyDefinition,
    StrategyMaterializationProvenance,
    StrategyVersion,
    validate_strategy_definition,
)

_NAME = re.compile(r"^[A-Za-z][A-Za-z0-9_.-]*$")
_ROOT_INDEX = r"(?:0|[1-9][0-9]*)"
_CONDITION_TARGET = re.compile(
    rf"^rules\[({_ROOT_INDEX})\]\.condition"
    rf"(?P<children>(?:\.children\[{_ROOT_INDEX}\])*)\."
    rf"(?P<side>left|right)\.(?P<field>period)$"
)
_THRESHOLD_TARGET = re.compile(
    rf"^rules\[({_ROOT_INDEX})\]\.condition"
    rf"(?P<children>(?:\.children\[{_ROOT_INDEX}\])*)\.threshold\.value$"
)
_ALLOCATION_TARGET = re.compile(
    rf"^rules\[({_ROOT_INDEX})\]\.allocations\[({_ROOT_INDEX})\]\.target_weight$"
)
_FALLBACK_ALLOCATION_TARGET = re.compile(
    rf"^fallback\.allocations\[({_ROOT_INDEX})\]\.target_weight$"
)

MATERIALIZATION_SPEC_VERSION = "phase-8d-1-v1"


def materialization_spec_hash(parameter_bindings: ParameterBindingSet) -> str:
    """Return the canonical hash of the binding specification."""
    return sha256_hash(
        {
            "materialization_spec_version": MATERIALIZATION_SPEC_VERSION,
            "bindings": [binding.to_dict() for binding in parameter_bindings.bindings],
        }
    )


def derived_strategy_version_hash(
    configuration: StrategyDefinition,
    *,
    base_strategy_version_id: str,
    base_strategy_version_hash: str,
    parameter_set_hash: str,
    binding_hash: str,
    materialization_spec_hash: str,
) -> str:
    """Return the deterministic semantic identity for a materialized version."""
    return sha256_hash(
        {
            "configuration": configuration.to_dict(),
            "base_strategy_version_id": base_strategy_version_id,
            "base_strategy_version_hash": base_strategy_version_hash,
            "parameter_set_hash": parameter_set_hash,
            "binding_hash": binding_hash,
            "materialization_spec_hash": materialization_spec_hash,
        }
    )


def derived_strategy_version_id(base_version_id: str, derived_hash: str) -> str:
    """Return the stable version id used by PHASE 8D-1 materialization."""
    return f"{base_version_id}--derived-{derived_hash[:16]}"


class BindingValueType(StrEnum):
    """Explicit value semantics supported by the V1 binding contract."""

    POSITIVE_INTEGER = "positive_integer"
    INTEGER = "integer"
    FINITE_FLOAT = "finite_float"
    FLOAT = "float"
    RELATIVE_THRESHOLD = "relative_threshold"
    ALLOCATION_WEIGHT = "allocation_weight"
    ENUM = "enum"
    DISCRETE = "discrete"


@dataclass(frozen=True)
class ParameterBinding:
    """One explicit parameter-to-strategy target mapping."""

    parameter_name: str
    target_path: str
    value_type: BindingValueType | str
    binding_version: str

    def __post_init__(self) -> None:
        if not isinstance(self.parameter_name, str) or not _NAME.fullmatch(self.parameter_name):
            raise InvalidParameterBindingError("parameter_name must be a safe identifier")
        if not isinstance(self.target_path, str) or not self.target_path.strip():
            raise InvalidParameterBindingError("target_path must be a non-empty string")
        if not _is_supported_path_syntax(self.target_path):
            raise UnsupportedParameterBindingTargetError(
                f"target path is outside the supported whitelist: {self.target_path}"
            )
        try:
            value_type = (
                self.value_type
                if isinstance(self.value_type, BindingValueType)
                else BindingValueType(self.value_type)
            )
        except (TypeError, ValueError) as exc:
            raise InvalidParameterBindingError("value_type is unsupported") from exc
        if not isinstance(self.binding_version, str) or not self.binding_version.strip():
            raise InvalidParameterBindingError("binding_version must be a non-empty string")
        object.__setattr__(self, "parameter_name", self.parameter_name.strip())
        object.__setattr__(self, "target_path", self.target_path.strip())
        object.__setattr__(self, "value_type", value_type)
        object.__setattr__(self, "binding_version", self.binding_version.strip())

    def to_dict(self) -> dict[str, str]:
        return {
            "parameter_name": self.parameter_name,
            "target_path": self.target_path,
            "value_type": self.value_type.value,
            "binding_version": self.binding_version,
        }

    @classmethod
    def from_dict(cls, payload: object) -> ParameterBinding:
        if not isinstance(payload, Mapping):
            raise InvalidParameterBindingError("parameter binding must be an object")
        return cls(
            parameter_name=payload.get("parameter_name", ""),
            target_path=payload.get("target_path", ""),
            value_type=payload.get("value_type", ""),
            binding_version=payload.get("binding_version", ""),
        )


@dataclass(frozen=True)
class ParameterBindingSet:
    """Immutable, uniquely keyed collection of parameter bindings."""

    bindings: tuple[ParameterBinding, ...]

    def __init__(self, bindings: Sequence[ParameterBinding]) -> None:
        if isinstance(bindings, (str, bytes)) or not isinstance(bindings, Sequence):
            raise InvalidParameterBindingError("bindings must be a sequence")
        normalized = tuple(bindings)
        if not all(isinstance(binding, ParameterBinding) for binding in normalized):
            raise InvalidParameterBindingError("bindings must contain ParameterBinding values")
        parameter_names = [binding.parameter_name for binding in normalized]
        target_paths = [binding.target_path for binding in normalized]
        if len(set(parameter_names)) != len(parameter_names):
            raise InvalidParameterBindingError("parameter_name must be unique")
        if len(set(target_paths)) != len(target_paths):
            raise InvalidParameterBindingError("target_path must be unique")
        object.__setattr__(
            self,
            "bindings",
            tuple(sorted(normalized, key=lambda item: item.parameter_name)),
        )

    def to_dict(self) -> dict[str, list[dict[str, str]]]:
        return {"bindings": [binding.to_dict() for binding in self.bindings]}

    @classmethod
    def from_dict(cls, payload: object) -> ParameterBindingSet:
        if not isinstance(payload, Mapping):
            raise InvalidParameterBindingError("binding set must be an object")
        raw_bindings = payload.get("bindings", ())
        if isinstance(raw_bindings, (str, bytes)) or not isinstance(raw_bindings, Sequence):
            raise InvalidParameterBindingError("bindings must be a sequence")
        return cls(tuple(ParameterBinding.from_dict(item) for item in raw_bindings))

    @property
    def binding_hash(self) -> str:
        return sha256_hash(self.to_dict())


def materialize_strategy_version(
    base_strategy_version: StrategyVersion,
    parameter_set: ParameterSet,
    parameter_bindings: ParameterBindingSet | Sequence[ParameterBinding],
) -> StrategyVersion:
    """Apply one candidate to a strategy using only whitelisted immutable paths."""
    if not isinstance(base_strategy_version, StrategyVersion):
        raise StrategyMaterializationError("base_strategy_version must be a StrategyVersion")
    if not isinstance(parameter_set, ParameterSet):
        raise StrategyMaterializationError("parameter_set must be a ParameterSet")
    binding_set = (
        parameter_bindings
        if isinstance(parameter_bindings, ParameterBindingSet)
        else ParameterBindingSet(parameter_bindings)
    )
    values = dict(parameter_set.values)
    expected_names = {binding.parameter_name for binding in binding_set.bindings}
    actual_names = set(values)
    if expected_names != actual_names:
        missing = sorted(expected_names - actual_names)
        unbound = sorted(actual_names - expected_names)
        raise InvalidParameterBindingError(
            f"parameter bindings must exactly cover ParameterSet values; "
            f"missing={missing}, unbound={unbound}"
        )

    payload = copy.deepcopy(base_strategy_version.configuration.to_dict())
    for binding in binding_set.bindings:
        _validate_parameter_type(parameter_set, binding, values[binding.parameter_name])
        _apply_binding(payload, binding, values[binding.parameter_name])

    try:
        definition = StrategyDefinition.from_dict(payload)
    except (TypeError, ValueError) as exc:
        raise StrategyMaterializationError(
            "materialized strategy failed domain-model construction"
        ) from exc
    validation = validate_strategy_definition(definition)
    if not validation.is_valid:
        raise StrategyMaterializationError(
            "materialized strategy failed validation", validation_result=validation
        )

    materialization_spec_hash_value = materialization_spec_hash(binding_set)
    derived_hash = derived_strategy_version_hash(
        definition,
        base_strategy_version_id=base_strategy_version.version_id,
        base_strategy_version_hash=base_strategy_version.content_hash or "",
        parameter_set_hash=parameter_set.content_hash,
        binding_hash=binding_set.binding_hash,
        materialization_spec_hash=materialization_spec_hash_value,
    )
    provenance = StrategyMaterializationProvenance(
        base_strategy_version_id=base_strategy_version.version_id,
        base_strategy_version_hash=base_strategy_version.content_hash or "",
        parameter_set_hash=parameter_set.content_hash,
        binding_hash=binding_set.binding_hash,
        materialization_spec_hash=materialization_spec_hash_value,
        derived_strategy_version_hash=derived_hash,
    )
    return StrategyVersion(
        strategy_id=base_strategy_version.strategy_id,
        version_id=derived_strategy_version_id(base_strategy_version.version_id, derived_hash),
        version_number=base_strategy_version.version_number,
        created_at=base_strategy_version.created_at,
        configuration=definition,
        status=base_strategy_version.status,
        materialization_provenance=provenance,
    )


def _validate_parameter_type(
    parameter_set: ParameterSet, binding: ParameterBinding, value: object
) -> None:
    value_type = binding.value_type
    if value_type in (BindingValueType.POSITIVE_INTEGER, BindingValueType.INTEGER):
        if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
            raise ParameterBindingTypeMismatchError(
                f"{binding.parameter_name} must be a positive integer"
            )
    elif value_type in (
        BindingValueType.FINITE_FLOAT,
        BindingValueType.FLOAT,
        BindingValueType.RELATIVE_THRESHOLD,
        BindingValueType.ALLOCATION_WEIGHT,
    ):
        if (
            isinstance(value, bool)
            or not isinstance(value, (int, float))
            or not math.isfinite(float(value))
        ):
            raise ParameterBindingTypeMismatchError(
                f"{binding.parameter_name} must be a finite numeric value"
            )
        if value_type is BindingValueType.ALLOCATION_WEIGHT and not 0 <= float(value) <= 1:
            raise ParameterBindingTypeMismatchError(
                f"{binding.parameter_name} allocation weight must be in [0, 1]"
            )
    else:
        raise UnsupportedParameterBindingTargetError(
            f"value type {value_type.value} is not supported by PHASE 8D-1 targets"
        )

    if parameter_set.parameter_space is None:
        return
    definitions = {
        definition.name: definition for definition in parameter_set.parameter_space.parameters
    }
    definition = definitions.get(binding.parameter_name)
    if definition is None:
        raise InvalidParameterBindingError(
            f"parameter {binding.parameter_name} is not declared in the ParameterSpace"
        )
    if definition.parameter_type is ParameterType.INTEGER and value_type not in (
        BindingValueType.POSITIVE_INTEGER,
        BindingValueType.INTEGER,
    ):
        raise ParameterBindingTypeMismatchError(
            f"integer parameter {binding.parameter_name} requires positive_integer binding"
        )
    if definition.parameter_type is ParameterType.FLOAT and value_type not in (
        BindingValueType.FINITE_FLOAT,
        BindingValueType.FLOAT,
        BindingValueType.RELATIVE_THRESHOLD,
        BindingValueType.ALLOCATION_WEIGHT,
    ):
        raise ParameterBindingTypeMismatchError(
            f"float parameter {binding.parameter_name} requires a numeric binding"
        )
    if definition.parameter_type in (ParameterType.ENUM, ParameterType.DISCRETE):
        if value not in definition.allowed_values:
            raise ParameterBindingTypeMismatchError(
                f"value for {binding.parameter_name} is not allowed by ParameterSpace"
            )


def _apply_binding(payload: dict[str, Any], binding: ParameterBinding, value: object) -> None:
    target_kind = _target_kind(binding.target_path)
    expected = {
        "period": (BindingValueType.POSITIVE_INTEGER, BindingValueType.INTEGER),
        "threshold": (BindingValueType.RELATIVE_THRESHOLD,),
        "allocation": (BindingValueType.ALLOCATION_WEIGHT,),
    }[target_kind]
    if binding.value_type not in expected:
        raise ParameterBindingTypeMismatchError(
            f"{binding.target_path} has an incompatible value_type"
        )
    target = _resolve_target(payload, binding.target_path)
    if not isinstance(target, dict):
        raise UnsupportedParameterBindingTargetError(
            f"target path does not resolve to an object: {binding.target_path}"
        )
    if target_kind == "period" and target.get("type") not in {"ma", "ema"}:
        raise UnsupportedParameterBindingTargetError(
            "period bindings are supported only for MA and EMA operands"
        )
    if target_kind == "threshold" and target.get("type") != "relative":
        raise UnsupportedParameterBindingTargetError(
            "only relative threshold values are supported in PHASE 8D-1"
        )
    field = (
        "period"
        if target_kind == "period"
        else "value"
        if target_kind == "threshold"
        else "target_weight"
    )
    target[field] = value


def _target_kind(path: str) -> str:
    if _CONDITION_TARGET.fullmatch(path):
        return "period"
    if _THRESHOLD_TARGET.fullmatch(path):
        return "threshold"
    if _ALLOCATION_TARGET.fullmatch(path) or _FALLBACK_ALLOCATION_TARGET.fullmatch(path):
        return "allocation"
    raise UnsupportedParameterBindingTargetError(
        f"target path is outside the supported whitelist: {path}"
    )


def _is_supported_path_syntax(path: str) -> bool:
    return any(pattern.fullmatch(path) for pattern in (
        _CONDITION_TARGET,
        _THRESHOLD_TARGET,
        _ALLOCATION_TARGET,
        _FALLBACK_ALLOCATION_TARGET,
    ))


def _resolve_target(payload: dict[str, Any], path: str) -> dict[str, Any]:
    condition_match = _CONDITION_TARGET.fullmatch(path) or _THRESHOLD_TARGET.fullmatch(path)
    if condition_match is not None:
        rule_index = int(condition_match.group(1))
        try:
            node: Any = payload["rules"][rule_index]["condition"]
            for child_index in re.findall(
                r"\.children\[(\d+)\]", condition_match.group("children")
            ):
                node = node["children"][int(child_index)]
            groups = condition_match.groupdict()
            if groups.get("side"):
                return node[groups["side"]]
            return node["threshold"]
        except (IndexError, KeyError, TypeError) as exc:
            raise UnsupportedParameterBindingTargetError(
                f"target path does not exist in the base strategy: {path}"
            ) from exc

    allocation_match = _ALLOCATION_TARGET.fullmatch(path)
    if allocation_match is not None:
        try:
            return payload["rules"][int(allocation_match.group(1))]["allocations"][
                int(allocation_match.group(2))
            ]
        except (IndexError, KeyError, TypeError) as exc:
            raise UnsupportedParameterBindingTargetError(
                f"target path does not exist in the base strategy: {path}"
            ) from exc

    fallback_match = _FALLBACK_ALLOCATION_TARGET.fullmatch(path)
    if fallback_match is not None:
        try:
            return payload["fallback"]["allocations"][int(fallback_match.group(1))]
        except (IndexError, KeyError, TypeError) as exc:
            raise UnsupportedParameterBindingTargetError(
                f"target path does not exist in the base strategy: {path}"
            ) from exc
    raise UnsupportedParameterBindingTargetError(f"target path is unsupported: {path}")
