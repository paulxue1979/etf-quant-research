"""Deterministic, allowlisted BacktestConfig parameter materialization."""

from __future__ import annotations

import math
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from enum import StrEnum
from typing import Any

from backtest.models import BacktestConfig, PositionRebalancePolicy
from research.canonical import sha256_hash
from research.exceptions import (
    InvalidParameterBindingError,
    ParameterBindingTypeMismatchError,
    StrategyMaterializationError,
    UnsupportedParameterBindingTargetError,
)
from research.experiments import ParameterSet

_NAME = re.compile(r"^[A-Za-z][A-Za-z0-9_.-]*$")
_POLICY_FIELDS = {
    "minimum_allocation_change",
    "drift_threshold",
    "maximum_turnover",
    "minimum_cash_reserve",
}
_POLICY_PREFIX = "position_rebalance_policy."


class BacktestBindingValueType(StrEnum):
    """Value semantics available to the explicit BacktestConfig allowlist."""

    NON_NEGATIVE_FLOAT = "non_negative_float"
    UNIT_INTERVAL = "unit_interval"
    OPTIONAL_NON_NEGATIVE_FLOAT = "optional_non_negative_float"


@dataclass(frozen=True)
class BacktestParameterBinding:
    """One parameter-to-BacktestConfig mapping from a closed allowlist."""

    parameter_name: str
    target_path: str
    value_type: BacktestBindingValueType | str
    binding_version: str = "phase-11h-v1"

    def __post_init__(self) -> None:
        if not isinstance(self.parameter_name, str) or not _NAME.fullmatch(self.parameter_name):
            raise InvalidParameterBindingError("parameter_name must be a safe identifier")
        if not isinstance(self.target_path, str) or not self.target_path.startswith(_POLICY_PREFIX):
            raise UnsupportedParameterBindingTargetError(
                "backtest target path is outside the supported whitelist"
            )
        field = self.target_path.removeprefix(_POLICY_PREFIX)
        if field not in _POLICY_FIELDS:
            raise UnsupportedParameterBindingTargetError(
                f"backtest target path is outside the supported whitelist: {self.target_path}"
            )
        try:
            value_type = (
                self.value_type
                if isinstance(self.value_type, BacktestBindingValueType)
                else BacktestBindingValueType(self.value_type)
            )
        except (TypeError, ValueError) as exc:
            raise InvalidParameterBindingError(
                "backtest binding value_type is unsupported"
            ) from exc
        expected = (
            BacktestBindingValueType.UNIT_INTERVAL
            if field == "minimum_cash_reserve"
            else BacktestBindingValueType.OPTIONAL_NON_NEGATIVE_FLOAT
        )
        if value_type is not expected and not (
            field != "minimum_cash_reserve"
            and value_type is BacktestBindingValueType.NON_NEGATIVE_FLOAT
        ):
            raise ParameterBindingTypeMismatchError(
                f"{self.target_path} has an incompatible value_type"
            )
        if not isinstance(self.binding_version, str) or not self.binding_version.strip():
            raise InvalidParameterBindingError("binding_version must be non-empty")
        object.__setattr__(self, "value_type", value_type)
        object.__setattr__(self, "binding_version", self.binding_version.strip())

    @property
    def policy_field(self) -> str:
        return self.target_path.removeprefix(_POLICY_PREFIX)

    def to_dict(self) -> dict[str, str]:
        return {
            "parameter_name": self.parameter_name,
            "target_path": self.target_path,
            "value_type": self.value_type.value,
            "binding_version": self.binding_version,
        }

    @classmethod
    def from_dict(cls, payload: object) -> BacktestParameterBinding:
        if not isinstance(payload, Mapping):
            raise InvalidParameterBindingError("backtest parameter binding must be an object")
        if set(payload) & {"expression", "code", "callable", "import"}:
            raise InvalidParameterBindingError("executable binding definitions are not allowed")
        return cls(
            parameter_name=str(payload.get("parameter_name", "")),
            target_path=str(payload.get("target_path", "")),
            value_type=payload.get("value_type", ""),
            binding_version=str(payload.get("binding_version", "phase-11h-v1")),
        )


@dataclass(frozen=True, init=False)
class BacktestParameterBindingSet:
    bindings: tuple[BacktestParameterBinding, ...]

    def __init__(self, bindings: Sequence[BacktestParameterBinding] = ()) -> None:
        if isinstance(bindings, (str, bytes)) or not isinstance(bindings, Sequence):
            raise InvalidParameterBindingError("backtest bindings must be a sequence")
        normalized = tuple(bindings)
        if not all(isinstance(item, BacktestParameterBinding) for item in normalized):
            raise InvalidParameterBindingError(
                "backtest bindings must contain BacktestParameterBinding values"
            )
        names = [item.parameter_name for item in normalized]
        paths = [item.target_path for item in normalized]
        if len(names) != len(set(names)) or len(paths) != len(set(paths)):
            raise InvalidParameterBindingError("backtest binding names and paths must be unique")
        object.__setattr__(
            self, "bindings", tuple(sorted(normalized, key=lambda item: item.parameter_name))
        )

    def to_dict(self) -> dict[str, list[dict[str, str]]]:
        return {"bindings": [item.to_dict() for item in self.bindings]}

    @property
    def binding_hash(self) -> str:
        return sha256_hash(self.to_dict())

    @classmethod
    def from_dict(cls, payload: object) -> BacktestParameterBindingSet:
        if not isinstance(payload, Mapping):
            raise InvalidParameterBindingError("backtest binding set must be an object")
        values = payload.get("bindings", ())
        if isinstance(values, (str, bytes)) or not isinstance(values, Sequence):
            raise InvalidParameterBindingError("backtest bindings must be a sequence")
        return cls(tuple(BacktestParameterBinding.from_dict(item) for item in values))


def materialize_backtest_config(
    base_config: BacktestConfig,
    parameter_set: ParameterSet,
    bindings: BacktestParameterBindingSet | Sequence[BacktestParameterBinding],
    *,
    strategy_version_id: str | None = None,
) -> BacktestConfig:
    """Return a new config while leaving the frozen base configuration untouched."""
    if not isinstance(base_config, BacktestConfig):
        raise StrategyMaterializationError("base_config must be a BacktestConfig")
    if not isinstance(parameter_set, ParameterSet):
        raise StrategyMaterializationError("parameter_set must be a ParameterSet")
    binding_set = (
        bindings
        if isinstance(bindings, BacktestParameterBindingSet)
        else BacktestParameterBindingSet(bindings)
    )
    values = dict(parameter_set.values)
    expected = {item.parameter_name for item in binding_set.bindings}
    if not expected.issubset(values):
        raise InvalidParameterBindingError("backtest bindings reference missing parameter values")
    updates: dict[str, float | None] = {}
    for binding in binding_set.bindings:
        value = values[binding.parameter_name]
        if (
            value is None
            and binding.value_type is BacktestBindingValueType.OPTIONAL_NON_NEGATIVE_FLOAT
        ):
            updates[binding.policy_field] = None
            continue
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ParameterBindingTypeMismatchError(
                f"value for {binding.parameter_name} must be numeric"
            )
        numeric = float(value)
        if not math.isfinite(numeric) or numeric < 0:
            raise ParameterBindingTypeMismatchError(
                f"value for {binding.parameter_name} must be finite and non-negative"
            )
        if binding.value_type is BacktestBindingValueType.UNIT_INTERVAL and numeric > 1:
            raise ParameterBindingTypeMismatchError(
                f"value for {binding.parameter_name} must be in [0, 1]"
            )
        updates[binding.policy_field] = numeric
    policy_payload: dict[str, Any] = base_config.position_rebalance_policy.to_dict()
    policy_payload.update(updates)
    policy = PositionRebalancePolicy.from_dict(policy_payload)
    return replace(
        base_config,
        strategy_version_id=strategy_version_id or base_config.strategy_version_id,
        position_rebalance_policy=policy,
    )


def backtest_config_hash(config: BacktestConfig, *, strategy_version_id: str | None = None) -> str:
    payload = config.snapshot({})
    if strategy_version_id is not None:
        payload["strategy_version_id"] = strategy_version_id
    return sha256_hash(payload)


__all__ = [
    "BacktestBindingValueType",
    "BacktestParameterBinding",
    "BacktestParameterBindingSet",
    "backtest_config_hash",
    "materialize_backtest_config",
]
