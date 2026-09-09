"""Immutable PHASE 8A experiment domain models.

This module defines contracts only. It does not enumerate parameter spaces,
generate candidates, execute backtests, rank results, or persist experiments.
"""

from __future__ import annotations

import math
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime
from types import MappingProxyType
from typing import Any

from backtest.models import BacktestConfig, ExecutionRule, RebalanceFrequency
from data.models import PriceField
from research.canonical import canonical_json, sha256_hash
from research.enums import (
    ConstraintOperator,
    ExperimentMethod,
    ExperimentStatus,
    MetricDirection,
    ParameterType,
)
from research.exceptions import (
    ExperimentDomainError,
    InvalidExperimentError,
    InvalidExperimentProvenanceError,
    InvalidObjectiveSpecificationError,
    InvalidParameterConstraintError,
    InvalidParameterDefinitionError,
    InvalidParameterSetError,
    InvalidParameterSpaceError,
)

_NAME = re.compile(r"^[A-Za-z][A-Za-z0-9_.-]*$")
_METRIC = re.compile(r"^[a-z][a-z0-9_]*$")
_HASH = re.compile(r"^[0-9a-f]{64}$")


def _text(value: object, label: str, error: type[ExperimentDomainError]) -> str:
    if not isinstance(value, str) or not value.strip():
        raise error(f"{label} must be a non-empty string")
    return value.strip()


def _name(value: object, label: str, error: type[ExperimentDomainError]) -> str:
    result = _text(value, label, error)
    if not _NAME.fullmatch(result):
        raise error(f"{label} must be a safe identifier")
    return result


def _metric(value: object, label: str) -> str:
    result = _text(value, label, InvalidObjectiveSpecificationError)
    if not _METRIC.fullmatch(result):
        raise InvalidObjectiveSpecificationError(f"{label} must be a valid metric identifier")
    return result


def _enum(
    value: object, enum_type: type[Any], label: str, error: type[ExperimentDomainError]
) -> Any:
    try:
        return value if isinstance(value, enum_type) else enum_type(value)
    except (TypeError, ValueError) as exc:
        raise error(f"{label} is invalid") from exc


def _finite(value: object, label: str, error: type[ExperimentDomainError]) -> int | float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise error(f"{label} must be finite and numeric")
    numeric = float(value)
    if not math.isfinite(numeric):
        raise error(f"{label} must be finite and numeric")
    return value if isinstance(value, int) else numeric


def _finite_scalar(value: object, label: str, error: type[ExperimentDomainError]) -> object:
    if isinstance(value, (str, int)) and not isinstance(value, bool):
        return value
    if isinstance(value, float) and math.isfinite(value):
        return value
    raise error(f"{label} must be a JSON scalar with finite numeric values")


def _sequence(value: object, label: str, error: type[ExperimentDomainError]) -> tuple[Any, ...]:
    if isinstance(value, (str, bytes)) or not isinstance(value, Sequence):
        raise error(f"{label} must be a sequence")
    return tuple(value)


def _mapping(value: object, label: str, error: type[ExperimentDomainError]) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise error(f"{label} must be an object")
    return value


@dataclass(frozen=True, init=False)
class ParameterDefinition:
    """A typed parameter domain; it never generates values."""

    name: str
    parameter_type: ParameterType
    min_value: int | float | None
    max_value: int | float | None
    step: int | float | None
    precision: int | None
    allowed_values: tuple[Any, ...]

    def __init__(
        self,
        name: str,
        parameter_type: ParameterType | str | None = None,
        min_value: int | float | None = None,
        max_value: int | float | None = None,
        step: int | float | None = None,
        precision: int | None = None,
        allowed_values: Sequence[Any] = (),
        *,
        type: ParameterType | str | None = None,
        min: int | float | None = None,
        max: int | float | None = None,
    ) -> None:
        if parameter_type is not None and type is not None and parameter_type != type:
            raise InvalidParameterDefinitionError("parameter_type and type must agree")
        if min_value is not None and min is not None and min_value != min:
            raise InvalidParameterDefinitionError("min_value and min must agree")
        if max_value is not None and max is not None and max_value != max:
            raise InvalidParameterDefinitionError("max_value and max must agree")
        object.__setattr__(self, "name", _name(name, "name", InvalidParameterDefinitionError))
        object.__setattr__(
            self,
            "parameter_type",
            _enum(
                parameter_type if parameter_type is not None else type,
                ParameterType,
                "parameter type",
                InvalidParameterDefinitionError,
            ),
        )
        actual_min = min_value if min_value is not None else min
        actual_max = max_value if max_value is not None else max
        ptype = self.parameter_type
        if ptype is ParameterType.INTEGER:
            for label, value in (("min", actual_min), ("max", actual_max), ("step", step)):
                if isinstance(value, bool) or not isinstance(value, int):
                    raise InvalidParameterDefinitionError(f"integer {label} must be an integer")
            if actual_min > actual_max or step <= 0:
                raise InvalidParameterDefinitionError(
                    "integer bounds must be ordered and step positive"
                )
            if precision is not None:
                raise InvalidParameterDefinitionError("integer parameters must not have precision")
        elif ptype is ParameterType.FLOAT:
            for label, value in (("min", actual_min), ("max", actual_max), ("step", step)):
                _finite(value, f"float {label}", InvalidParameterDefinitionError)
            if actual_min > actual_max or step <= 0:
                raise InvalidParameterDefinitionError(
                    "float bounds must be ordered and step positive"
                )
            if precision is not None and (
                isinstance(precision, bool) or not isinstance(precision, int) or precision < 0
            ):
                raise InvalidParameterDefinitionError("precision must be a non-negative integer")
        else:
            if (
                actual_min is not None
                or actual_max is not None
                or step is not None
                or precision is not None
            ):
                raise InvalidParameterDefinitionError(
                    "enum and discrete parameters only use allowed_values"
                )
        values = _sequence(allowed_values, "allowed_values", InvalidParameterDefinitionError)
        if ptype in (ParameterType.ENUM, ParameterType.DISCRETE):
            if not values:
                raise InvalidParameterDefinitionError("allowed_values must not be empty")
        elif values:
            raise InvalidParameterDefinitionError("numeric parameters must not have allowed_values")
        normalized_values = tuple(
            _finite_scalar(value, "allowed_values item", InvalidParameterDefinitionError)
            for value in values
        )
        if len({canonical_json(value) for value in normalized_values}) != len(normalized_values):
            raise InvalidParameterDefinitionError("allowed_values must not contain duplicates")
        object.__setattr__(self, "min_value", actual_min)
        object.__setattr__(self, "max_value", actual_max)
        object.__setattr__(self, "step", step)
        object.__setattr__(self, "precision", precision)
        object.__setattr__(self, "allowed_values", normalized_values)

    @property
    def type(self) -> ParameterType:
        return self.parameter_type

    @property
    def min(self) -> int | float | None:
        return self.min_value

    @property
    def max(self) -> int | float | None:
        return self.max_value

    def validate_value(self, value: object) -> object:
        if self.parameter_type is ParameterType.INTEGER:
            if isinstance(value, bool) or not isinstance(value, int):
                raise InvalidParameterSetError(f"value for {self.name} must be an integer")
            if not self.min_value <= value <= self.max_value:
                raise InvalidParameterSetError(f"value for {self.name} is outside bounds")
            if (value - self.min_value) % self.step != 0:
                raise InvalidParameterSetError(f"value for {self.name} does not match step")
            return value
        if self.parameter_type is ParameterType.FLOAT:
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise InvalidParameterSetError(f"value for {self.name} must be numeric")
            numeric = float(value)
            if not math.isfinite(numeric) or not self.min_value <= numeric <= self.max_value:
                raise InvalidParameterSetError(f"value for {self.name} is outside finite bounds")
            if self.precision is not None and round(numeric, self.precision) != numeric:
                raise InvalidParameterSetError(f"value for {self.name} exceeds precision")
            return numeric
        value = _finite_scalar(value, f"value for {self.name}", InvalidParameterSetError)
        if value not in self.allowed_values:
            raise InvalidParameterSetError(f"value for {self.name} is not allowed")
        return value

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "type": self.parameter_type.value,
            "min": self.min_value,
            "max": self.max_value,
            "step": self.step,
            "precision": self.precision,
            "allowed_values": list(self.allowed_values),
        }

    @classmethod
    def from_dict(cls, payload: object) -> ParameterDefinition:
        data = _mapping(payload, "parameter definition", InvalidParameterDefinitionError)
        return cls(
            name=data.get("name", ""),
            type=data.get("type", data.get("parameter_type")),
            min=data.get("min", data.get("min_value")),
            max=data.get("max", data.get("max_value")),
            step=data.get("step"),
            precision=data.get("precision"),
            allowed_values=data.get("allowed_values", ()),
        )


@dataclass(frozen=True)
class ParameterSet:
    """One concrete, immutable and non-executable parameter combination."""

    values: Mapping[str, Any]
    parameter_space: ParameterSpace | None = None

    def __post_init__(self) -> None:
        values = _mapping(self.values, "values", InvalidParameterSetError)
        normalized = {
            _name(key, "parameter name", InvalidParameterSetError): _finite_scalar(
                value, "parameter value", InvalidParameterSetError
            )
            for key, value in values.items()
        }
        if len(normalized) != len(values):
            raise InvalidParameterSetError("parameter names must be unique")
        if self.parameter_space is not None:
            if not isinstance(self.parameter_space, ParameterSpace):
                raise InvalidParameterSetError("parameter_space must be a ParameterSpace")
            definitions = {item.name: item for item in self.parameter_space.parameters}
            if set(normalized) != set(definitions):
                raise InvalidParameterSetError("parameter set must exactly match parameter space")
            normalized = {
                name: definitions[name].validate_value(value) for name, value in normalized.items()
            }
        object.__setattr__(self, "values", MappingProxyType(dict(sorted(normalized.items()))))

    def to_dict(self) -> dict[str, Any]:
        return {"values": dict(self.values)}

    def canonical_json(self) -> str:
        return canonical_json(self.to_dict())

    @property
    def content_hash(self) -> str:
        return sha256_hash(self.to_dict())

    @classmethod
    def from_dict(
        cls, payload: object, parameter_space: ParameterSpace | None = None
    ) -> ParameterSet:
        data = _mapping(payload, "parameter set", InvalidParameterSetError)
        return cls(values=data.get("values", data), parameter_space=parameter_space)


@dataclass(frozen=True)
class ParameterConstraint:
    """A safe binary relation between two named parameters."""

    left: str
    operator: ConstraintOperator
    right: str

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "left", _name(self.left, "left parameter", InvalidParameterConstraintError)
        )
        object.__setattr__(
            self, "right", _name(self.right, "right parameter", InvalidParameterConstraintError)
        )
        object.__setattr__(
            self,
            "operator",
            _enum(
                self.operator,
                ConstraintOperator,
                "constraint operator",
                InvalidParameterConstraintError,
            ),
        )

    @property
    def left_parameter(self) -> str:
        return self.left

    @property
    def right_parameter(self) -> str:
        return self.right

    def to_dict(self) -> dict[str, str]:
        return {"left": self.left, "operator": self.operator.value, "right": self.right}

    @classmethod
    def from_dict(cls, payload: object) -> ParameterConstraint:
        data = _mapping(payload, "parameter constraint", InvalidParameterConstraintError)
        if set(data) & {"expression", "code", "formula"}:
            raise InvalidParameterConstraintError(
                "executable constraint expressions are not allowed"
            )
        return cls(
            left=data.get("left", data.get("left_parameter", "")),
            operator=data.get("operator", ""),
            right=data.get("right", data.get("right_parameter", "")),
        )


@dataclass(frozen=True)
class ParameterSpace:
    """Immutable parameter definitions and safe references, without enumeration."""

    parameters: tuple[ParameterDefinition, ...]
    constraints: tuple[ParameterConstraint, ...] = ()
    max_candidates: int = 1

    def __post_init__(self) -> None:
        parameters = _sequence(self.parameters, "parameters", InvalidParameterSpaceError)
        if not all(isinstance(item, ParameterDefinition) for item in parameters):
            raise InvalidParameterSpaceError("parameters must contain ParameterDefinition values")
        if len({item.name for item in parameters}) != len(parameters):
            raise InvalidParameterSpaceError("parameter names must be unique")
        constraints = _sequence(self.constraints, "constraints", InvalidParameterSpaceError)
        if not all(isinstance(item, ParameterConstraint) for item in constraints):
            raise InvalidParameterSpaceError("constraints must contain ParameterConstraint values")
        names = {item.name for item in parameters}
        if any(item.left not in names or item.right not in names for item in constraints):
            raise InvalidParameterSpaceError("constraints must reference defined parameters")
        if (
            isinstance(self.max_candidates, bool)
            or not isinstance(self.max_candidates, int)
            or self.max_candidates <= 0
        ):
            raise InvalidParameterSpaceError("max_candidates must be a positive integer")
        object.__setattr__(
            self, "parameters", tuple(sorted(parameters, key=lambda item: item.name))
        )
        object.__setattr__(self, "constraints", tuple(sorted(constraints, key=canonical_json)))

    def to_dict(self) -> dict[str, Any]:
        return {
            "parameters": [item.to_dict() for item in self.parameters],
            "constraints": [item.to_dict() for item in self.constraints],
            "max_candidates": self.max_candidates,
        }

    def canonical_json(self) -> str:
        return canonical_json(self.to_dict())

    @property
    def content_hash(self) -> str:
        return sha256_hash(self.to_dict())

    @classmethod
    def from_dict(cls, payload: object) -> ParameterSpace:
        data = _mapping(payload, "parameter space", InvalidParameterSpaceError)
        return cls(
            parameters=tuple(
                ParameterDefinition.from_dict(item)
                for item in _sequence(
                    data.get("parameters", ()), "parameters", InvalidParameterSpaceError
                )
            ),
            constraints=tuple(
                ParameterConstraint.from_dict(item)
                for item in _sequence(
                    data.get("constraints", ()), "constraints", InvalidParameterSpaceError
                )
            ),
            max_candidates=data.get("max_candidates"),
        )


@dataclass(frozen=True, init=False)
class ObjectiveSpecification:
    """Immutable objective contract; it does not calculate or rank metrics."""

    primary_metric: str
    secondary_metrics: tuple[str, ...]
    metric_directions: Mapping[str, MetricDirection]
    hard_constraints: Mapping[str, float]
    tie_break_rules: tuple[str, ...]

    def __init__(
        self,
        primary_metric: str,
        secondary_metrics: Sequence[str] = (),
        metric_directions: Mapping[str, MetricDirection | str] | None = None,
        hard_constraints: Mapping[str, int | float] | Sequence[str] = (),
        tie_break_rules: Sequence[str] = (),
    ) -> None:
        primary = _metric(primary_metric, "primary_metric")
        secondary = tuple(
            _metric(item, "secondary metric")
            for item in _sequence(
                secondary_metrics, "secondary_metrics", InvalidObjectiveSpecificationError
            )
        )
        if primary in secondary or len(set(secondary)) != len(secondary):
            raise InvalidObjectiveSpecificationError("objective metrics must not be duplicated")
        directions_input = metric_directions or {}
        if not isinstance(directions_input, Mapping):
            raise InvalidObjectiveSpecificationError("metric_directions must be an object")
        directions: dict[str, MetricDirection] = {}
        for metric, direction in directions_input.items():
            normalized_metric = _metric(metric, "metric direction name")
            directions[normalized_metric] = _enum(
                direction, MetricDirection, "metric direction", InvalidObjectiveSpecificationError
            )
        required = {primary, *secondary}
        if set(directions) != required:
            raise InvalidObjectiveSpecificationError(
                "metric_directions must cover primary and secondary metrics exactly"
            )
        if isinstance(hard_constraints, Mapping):
            constraints = {
                _metric(metric, "hard constraint metric"): float(
                    _finite(value, "hard constraint", InvalidObjectiveSpecificationError)
                )
                for metric, value in hard_constraints.items()
            }
        else:
            raw_constraints = _sequence(
                hard_constraints, "hard_constraints", InvalidObjectiveSpecificationError
            )
            if raw_constraints:
                raise InvalidObjectiveSpecificationError(
                    "hard_constraints must be metric-to-number mappings"
                )
            constraints = {}
        if not set(constraints).issubset(required):
            raise InvalidObjectiveSpecificationError(
                "hard constraints must reference objective metrics"
            )
        ties = tuple(
            _metric(item, "tie-break metric")
            for item in _sequence(
                tie_break_rules, "tie_break_rules", InvalidObjectiveSpecificationError
            )
        )
        if len(set(ties)) != len(ties) or not set(ties).issubset(required):
            raise InvalidObjectiveSpecificationError(
                "tie-break rules must be unique objective metrics"
            )
        object.__setattr__(self, "primary_metric", primary)
        object.__setattr__(self, "secondary_metrics", secondary)
        object.__setattr__(
            self, "metric_directions", MappingProxyType(dict(sorted(directions.items())))
        )
        object.__setattr__(
            self, "hard_constraints", MappingProxyType(dict(sorted(constraints.items())))
        )
        object.__setattr__(self, "tie_break_rules", ties)

    def to_dict(self) -> dict[str, Any]:
        return {
            "primary_metric": self.primary_metric,
            "secondary_metrics": list(self.secondary_metrics),
            "metric_directions": {
                key: value.value for key, value in self.metric_directions.items()
            },
            "hard_constraints": dict(self.hard_constraints),
            "tie_break_rules": list(self.tie_break_rules),
        }

    def canonical_json(self) -> str:
        return canonical_json(self.to_dict())

    @property
    def content_hash(self) -> str:
        return sha256_hash(self.to_dict())

    @classmethod
    def from_dict(cls, payload: object) -> ObjectiveSpecification:
        data = _mapping(payload, "objective specification", InvalidObjectiveSpecificationError)
        return cls(
            primary_metric=data.get("primary_metric", ""),
            secondary_metrics=data.get("secondary_metrics", ()),
            metric_directions=data.get("metric_directions", {}),
            hard_constraints=data.get("hard_constraints", {}),
            tie_break_rules=data.get("tie_break_rules", ()),
        )


@dataclass(frozen=True)
class ExperimentProvenance:
    """Reproducibility metadata and the result-affecting input contract."""

    protocol_id: str
    strategy_definition_id: str
    base_strategy_version_id: str
    base_strategy_version_hash: str
    parameter_space_hash: str
    objective_spec_hash: str
    is_start_date: date
    is_end_date: date
    price_field_used: PriceField
    initial_capital: float
    commission: Mapping[str, float]
    slippage: float
    execution_rule: str
    fractional_shares: bool
    rebalance_policy: Mapping[str, Any]
    engine_version: str
    analysis_version: str
    data_snapshot_reference: Mapping[str, Any]
    parameter_bindings: tuple[Mapping[str, Any], ...] | None = None

    def __post_init__(self) -> None:
        for label in (
            "protocol_id",
            "strategy_definition_id",
            "base_strategy_version_id",
            "engine_version",
            "analysis_version",
        ):
            object.__setattr__(
                self, label, _text(getattr(self, label), label, InvalidExperimentProvenanceError)
            )
        for label in ("base_strategy_version_hash", "parameter_space_hash", "objective_spec_hash"):
            value = _text(getattr(self, label), label, InvalidExperimentProvenanceError)
            if not _HASH.fullmatch(value):
                raise InvalidExperimentProvenanceError(f"{label} must be a SHA-256 hex digest")
            object.__setattr__(self, label, value)
        if (
            not isinstance(self.is_start_date, date)
            or not isinstance(self.is_end_date, date)
            or self.is_start_date > self.is_end_date
        ):
            raise InvalidExperimentProvenanceError("IS dates must be valid and ordered")
        object.__setattr__(
            self,
            "price_field_used",
            _enum(
                self.price_field_used,
                PriceField,
                "price_field_used",
                InvalidExperimentProvenanceError,
            ),
        )
        object.__setattr__(
            self,
            "initial_capital",
            float(
                _finite(self.initial_capital, "initial_capital", InvalidExperimentProvenanceError)
            ),
        )
        if self.initial_capital <= 0:
            raise InvalidExperimentProvenanceError("initial_capital must be positive")
        commission = _mapping(self.commission, "commission", InvalidExperimentProvenanceError)
        if set(commission) != {"rate", "per_order"}:
            raise InvalidExperimentProvenanceError("commission must contain rate and per_order")
        normalized_commission = {
            key: float(_finite(value, f"commission {key}", InvalidExperimentProvenanceError))
            for key, value in commission.items()
        }
        if any(value < 0 for value in normalized_commission.values()):
            raise InvalidExperimentProvenanceError("commission values must be non-negative")
        object.__setattr__(self, "commission", MappingProxyType(normalized_commission))
        slippage = float(_finite(self.slippage, "slippage", InvalidExperimentProvenanceError))
        if not 0 <= slippage < 1:
            raise InvalidExperimentProvenanceError("slippage must be in [0, 1)")
        object.__setattr__(self, "slippage", slippage)
        object.__setattr__(
            self,
            "execution_rule",
            _enum(
                self.execution_rule,
                ExecutionRule,
                "execution_rule",
                InvalidExperimentProvenanceError,
            ).value,
        )
        if not isinstance(self.fractional_shares, bool):
            raise InvalidExperimentProvenanceError("fractional_shares must be a boolean")
        rebalance_policy = _mapping(
            self.rebalance_policy, "rebalance_policy", InvalidExperimentProvenanceError
        )
        if set(rebalance_policy) != {"frequency", "threshold"}:
            raise InvalidExperimentProvenanceError(
                "rebalance_policy must contain frequency and threshold"
            )
        frequency = _enum(
            rebalance_policy["frequency"],
            RebalanceFrequency,
            "rebalance frequency",
            InvalidExperimentProvenanceError,
        ).value
        threshold = rebalance_policy["threshold"]
        if threshold is not None:
            threshold = float(
                _finite(threshold, "rebalance threshold", InvalidExperimentProvenanceError)
            )
            if threshold < 0:
                raise InvalidExperimentProvenanceError(
                    "rebalance threshold must be non-negative"
                )
        object.__setattr__(
            self,
            "rebalance_policy",
            MappingProxyType({"frequency": frequency, "threshold": threshold}),
        )
        object.__setattr__(
            self,
            "data_snapshot_reference",
            MappingProxyType(
                dict(
                    _mapping(
                        self.data_snapshot_reference,
                        "data_snapshot_reference",
                        InvalidExperimentProvenanceError,
                    )
                )
            ),
        )
        if self.parameter_bindings is not None:
            if isinstance(self.parameter_bindings, (str, bytes)) or not isinstance(
                self.parameter_bindings, Sequence
            ):
                raise InvalidExperimentProvenanceError(
                    "parameter_bindings must be a sequence or None"
                )
            try:
                from research.materialization import ParameterBindingSet

                binding_set = ParameterBindingSet.from_dict(
                    {"bindings": list(self.parameter_bindings)}
                )
            except Exception as exc:
                raise InvalidExperimentProvenanceError(
                    "parameter_bindings contains an invalid frozen binding set"
                ) from exc
            object.__setattr__(
                self,
                "parameter_bindings",
                tuple(dict(binding.to_dict()) for binding in binding_set.bindings),
            )

    def to_dict(self) -> dict[str, Any]:
        payload = {
            "protocol_id": self.protocol_id,
            "strategy_definition_id": self.strategy_definition_id,
            "base_strategy_version_id": self.base_strategy_version_id,
            "base_strategy_version_hash": self.base_strategy_version_hash,
            "parameter_space_hash": self.parameter_space_hash,
            "objective_spec_hash": self.objective_spec_hash,
            "is_start_date": self.is_start_date.isoformat(),
            "is_end_date": self.is_end_date.isoformat(),
            "price_field_used": self.price_field_used.value,
            "initial_capital": self.initial_capital,
            "commission": dict(self.commission),
            "slippage": self.slippage,
            "execution_rule": self.execution_rule,
            "fractional_shares": self.fractional_shares,
            "rebalance_policy": dict(self.rebalance_policy),
            "engine_version": self.engine_version,
            "analysis_version": self.analysis_version,
            "data_snapshot_reference": dict(self.data_snapshot_reference),
        }
        if self.parameter_bindings is not None:
            payload["parameter_bindings"] = [dict(item) for item in self.parameter_bindings]
        return payload

    @classmethod
    def from_dict(cls, payload: object) -> ExperimentProvenance:
        data = _mapping(payload, "experiment provenance", InvalidExperimentProvenanceError)
        return cls(
            protocol_id=data.get("protocol_id", ""),
            strategy_definition_id=data.get("strategy_definition_id", ""),
            base_strategy_version_id=data.get("base_strategy_version_id", ""),
            base_strategy_version_hash=data.get("base_strategy_version_hash", ""),
            parameter_space_hash=data.get("parameter_space_hash", ""),
            objective_spec_hash=data.get("objective_spec_hash", ""),
            is_start_date=date.fromisoformat(str(data.get("is_start_date", ""))),
            is_end_date=date.fromisoformat(str(data.get("is_end_date", ""))),
            price_field_used=data.get("price_field_used", ""),
            initial_capital=data.get("initial_capital"),
            commission=data.get("commission", {}),
            slippage=data.get("slippage"),
            execution_rule=data.get("execution_rule", ""),
            fractional_shares=data.get("fractional_shares"),
            rebalance_policy=data.get("rebalance_policy", {}),
            engine_version=data.get("engine_version", ""),
            analysis_version=data.get("analysis_version", ""),
            data_snapshot_reference=data.get("data_snapshot_reference", {}),
            parameter_bindings=data.get("parameter_bindings"),
        )


@dataclass(frozen=True)
class Experiment:
    """Immutable experiment definition bound to one protocol and base version."""

    experiment_id: str
    protocol_id: str
    strategy_definition_id: str
    base_strategy_version_id: str
    base_strategy_version_hash: str
    parameter_space: ParameterSpace
    objective_specification: ObjectiveSpecification
    is_start_date: date
    is_end_date: date
    backtest_configuration: BacktestConfig
    engine_version: str
    analysis_version: str
    status: ExperimentStatus
    created_at: datetime
    method: ExperimentMethod = ExperimentMethod.GRID
    parameter_set: ParameterSet | None = None
    provenance: ExperimentProvenance | None = None

    def __post_init__(self) -> None:
        for label in (
            "experiment_id",
            "protocol_id",
            "strategy_definition_id",
            "base_strategy_version_id",
            "engine_version",
            "analysis_version",
        ):
            object.__setattr__(
                self, label, _text(getattr(self, label), label, InvalidExperimentError)
            )
        version_hash = _text(
            self.base_strategy_version_hash, "base_strategy_version_hash", InvalidExperimentError
        )
        if not _HASH.fullmatch(version_hash):
            raise InvalidExperimentError("base_strategy_version_hash must be a SHA-256 hex digest")
        object.__setattr__(self, "base_strategy_version_hash", version_hash)
        if not isinstance(self.parameter_space, ParameterSpace):
            raise InvalidExperimentError("parameter_space must be a ParameterSpace")
        if not isinstance(self.objective_specification, ObjectiveSpecification):
            raise InvalidExperimentError(
                "objective_specification must be an ObjectiveSpecification"
            )
        if (
            not isinstance(self.is_start_date, date)
            or not isinstance(self.is_end_date, date)
            or self.is_start_date > self.is_end_date
        ):
            raise InvalidExperimentError("IS dates must be valid and ordered")
        if not isinstance(self.backtest_configuration, BacktestConfig):
            raise InvalidExperimentError("backtest_configuration must be a BacktestConfig")
        if self.backtest_configuration.strategy_version_id != self.base_strategy_version_id:
            raise InvalidExperimentError(
                "backtest configuration must bind the base strategy version"
            )
        if (
            self.backtest_configuration.start_date != self.is_start_date
            or self.backtest_configuration.end_date != self.is_end_date
        ):
            raise InvalidExperimentError("backtest configuration dates must match the IS boundary")
        object.__setattr__(
            self, "method", _enum(self.method, ExperimentMethod, "method", InvalidExperimentError)
        )
        object.__setattr__(
            self, "status", _enum(self.status, ExperimentStatus, "status", InvalidExperimentError)
        )
        if not isinstance(self.created_at, datetime):
            raise InvalidExperimentError("created_at must be a datetime")
        if self.parameter_set is not None and not isinstance(self.parameter_set, ParameterSet):
            raise InvalidExperimentError("parameter_set must be a ParameterSet")
        if (
            self.parameter_set is not None
            and self.parameter_set.parameter_space is not None
            and self.parameter_set.parameter_space != self.parameter_space
        ):
            raise InvalidExperimentError("parameter_set must belong to this parameter space")
        if self.provenance is not None:
            if not isinstance(self.provenance, ExperimentProvenance):
                raise InvalidExperimentError("provenance must be ExperimentProvenance")
            expected = {
                "protocol_id": self.protocol_id,
                "strategy_definition_id": self.strategy_definition_id,
                "base_strategy_version_id": self.base_strategy_version_id,
                "base_strategy_version_hash": self.base_strategy_version_hash,
                "parameter_space_hash": self.parameter_space.content_hash,
                "objective_spec_hash": self.objective_specification.content_hash,
                "is_start_date": self.is_start_date,
                "is_end_date": self.is_end_date,
            }
            for label, value in expected.items():
                if getattr(self.provenance, label) != value:
                    raise InvalidExperimentError(f"provenance {label} does not match experiment")
            expected_configuration = self.backtest_configuration.snapshot({})
            provenance_configuration = {
                "price_field_used": self.provenance.price_field_used.value,
                "initial_capital": self.provenance.initial_capital,
                "commission": dict(self.provenance.commission),
                "slippage": self.provenance.slippage,
                "execution_rule": self.provenance.execution_rule,
                "fractional_shares": self.provenance.fractional_shares,
                "rebalance_policy": dict(self.provenance.rebalance_policy),
            }
            for label, value in provenance_configuration.items():
                if expected_configuration[label] != value:
                    raise InvalidExperimentError(
                        f"provenance {label} does not match backtest configuration"
                    )

    @property
    def parameter_space_hash(self) -> str:
        return self.parameter_space.content_hash

    @property
    def objective_spec_hash(self) -> str:
        return self.objective_specification.content_hash

    def to_dict(self) -> dict[str, Any]:
        return {
            "experiment_id": self.experiment_id,
            "protocol_id": self.protocol_id,
            "strategy_definition_id": self.strategy_definition_id,
            "base_strategy_version_id": self.base_strategy_version_id,
            "base_strategy_version_hash": self.base_strategy_version_hash,
            "parameter_space": self.parameter_space.to_dict(),
            "parameter_space_hash": self.parameter_space_hash,
            "parameter_set": self.parameter_set.to_dict() if self.parameter_set else None,
            "objective_specification": self.objective_specification.to_dict(),
            "objective_spec_hash": self.objective_spec_hash,
            "is_start_date": self.is_start_date.isoformat(),
            "is_end_date": self.is_end_date.isoformat(),
            "backtest_configuration": self.backtest_configuration.snapshot({}),
            "engine_version": self.engine_version,
            "analysis_version": self.analysis_version,
            "method": self.method.value,
            "status": self.status.value,
            "created_at": self.created_at.isoformat(),
            "provenance": self.provenance.to_dict() if self.provenance else None,
        }

    def canonical_json(self) -> str:
        return canonical_json(self.to_dict())

    @property
    def content_hash(self) -> str:
        return sha256_hash(self.to_dict())

    def with_status(self, status: ExperimentStatus | str) -> Experiment:
        target = _enum(status, ExperimentStatus, "status", InvalidExperimentError)
        allowed = {
            ExperimentStatus.DRAFT: {ExperimentStatus.DEFINED, ExperimentStatus.INVALID},
            ExperimentStatus.DEFINED: {ExperimentStatus.SPACE_FROZEN, ExperimentStatus.INVALID},
            ExperimentStatus.SPACE_FROZEN: {
                ExperimentStatus.CANDIDATES_GENERATED,
                ExperimentStatus.INVALID,
            },
            ExperimentStatus.CANDIDATES_GENERATED: {
                ExperimentStatus.RUNNING,
                ExperimentStatus.INVALID,
            },
            ExperimentStatus.RUNNING: {ExperimentStatus.COMPLETED, ExperimentStatus.INVALID},
            ExperimentStatus.COMPLETED: {
                ExperimentStatus.SELECTION_RECORDED,
                ExperimentStatus.CLOSED,
                ExperimentStatus.INVALID,
            },
            ExperimentStatus.SELECTION_RECORDED: {
                ExperimentStatus.OOS_CONTAMINATED,
                ExperimentStatus.CLOSED,
            },
            ExperimentStatus.OOS_CONTAMINATED: {ExperimentStatus.CLOSED},
            ExperimentStatus.INVALID: {ExperimentStatus.CLOSED},
            ExperimentStatus.CLOSED: set(),
        }
        if target not in allowed[self.status]:
            raise InvalidExperimentError(
                f"invalid experiment transition: {self.status} -> {target}"
            )
        return Experiment(
            experiment_id=self.experiment_id,
            protocol_id=self.protocol_id,
            strategy_definition_id=self.strategy_definition_id,
            base_strategy_version_id=self.base_strategy_version_id,
            base_strategy_version_hash=self.base_strategy_version_hash,
            parameter_space=self.parameter_space,
            objective_specification=self.objective_specification,
            is_start_date=self.is_start_date,
            is_end_date=self.is_end_date,
            backtest_configuration=self.backtest_configuration,
            engine_version=self.engine_version,
            analysis_version=self.analysis_version,
            status=target,
            created_at=self.created_at,
            method=self.method,
            parameter_set=self.parameter_set,
            provenance=self.provenance,
        )

    @classmethod
    def from_dict(cls, payload: object) -> Experiment:
        data = _mapping(payload, "experiment", InvalidExperimentError)
        config_data = _mapping(
            data.get("backtest_configuration", {}), "backtest_configuration", InvalidExperimentError
        )
        from backtest.models import (
            CommissionPolicy,
            ExecutionRule,
            RebalanceFrequency,
            RebalancePolicy,
        )

        config = BacktestConfig(
            strategy_version_id=config_data["strategy_version_id"],
            start_date=date.fromisoformat(str(config_data["start_date"])),
            end_date=date.fromisoformat(str(config_data["end_date"])),
            initial_capital=config_data["initial_capital"],
            price_field_used=PriceField(config_data["price_field_used"]),
            commission=CommissionPolicy(**config_data["commission"]),
            slippage=config_data["slippage"],
            execution_rule=ExecutionRule(config_data["execution_rule"]),
            rebalance_policy=RebalancePolicy(
                frequency=RebalanceFrequency(config_data["rebalance_policy"]["frequency"]),
                threshold=config_data["rebalance_policy"]["threshold"],
            ),
            fractional_shares=config_data["fractional_shares"],
        )
        return cls(
            experiment_id=data.get("experiment_id", ""),
            protocol_id=data.get("protocol_id", ""),
            strategy_definition_id=data.get("strategy_definition_id", ""),
            base_strategy_version_id=data.get("base_strategy_version_id", ""),
            base_strategy_version_hash=data.get("base_strategy_version_hash", ""),
            parameter_space=ParameterSpace.from_dict(data.get("parameter_space")),
            objective_specification=ObjectiveSpecification.from_dict(
                data.get("objective_specification")
            ),
            is_start_date=date.fromisoformat(str(data.get("is_start_date", ""))),
            is_end_date=date.fromisoformat(str(data.get("is_end_date", ""))),
            backtest_configuration=config,
            engine_version=data.get("engine_version", ""),
            analysis_version=data.get("analysis_version", ""),
            status=data.get("status", ""),
            created_at=datetime.fromisoformat(str(data.get("created_at", ""))),
            method=data.get("method", ExperimentMethod.GRID.value),
            parameter_set=(
                ParameterSet.from_dict(
                    data["parameter_set"],
                    parameter_space=ParameterSpace.from_dict(data.get("parameter_space")),
                )
                if data.get("parameter_set")
                else None
            ),
            provenance=(
                ExperimentProvenance.from_dict(data["provenance"])
                if data.get("provenance")
                else None
            ),
        )


__all__ = [
    "Experiment",
    "ExperimentMethod",
    "ExperimentProvenance",
    "ExperimentStatus",
    "ObjectiveSpecification",
    "ParameterConstraint",
    "ParameterDefinition",
    "ParameterSet",
    "ParameterSpace",
]
