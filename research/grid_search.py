"""Controlled deterministic grid-search domain and preflight planning."""

from __future__ import annotations

import math
import re
from collections import Counter
from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from itertools import product
from types import MappingProxyType
from typing import Any, Protocol

from research.backtest_materialization import (
    BacktestParameterBinding,
    BacktestParameterBindingSet,
    backtest_config_hash,
    materialize_backtest_config,
)
from research.candidates import (
    ParameterCandidateSet,
    parameter_constraints_hold,
    parameter_values,
    theoretical_candidate_count,
)
from research.canonical import canonical_json, sha256_hash
from research.enums import ConstraintOperator, ExperimentStatus
from research.exceptions import InvalidParameterConstraintError, InvalidParameterSpaceError
from research.experiments import Experiment, ParameterSet
from research.materialization import (
    ParameterBinding,
    ParameterBindingSet,
    materialize_strategy_version,
)
from strategies import StrategyVersion

GRID_SEARCH_SCHEMA_VERSION = "phase-11h.1"
DEFAULT_MAX_CANDIDATES = 100
HARD_MAX_CANDIDATES = 1000
DEFAULT_THEORETICAL_GUARD = 1_000_000
_HASH = re.compile(r"^[0-9a-f]{64}$")
_NAME = re.compile(r"^[A-Za-z][A-Za-z0-9_.:-]{0,127}$")
_SENSITIVE = re.compile(
    r"(?:api[_-]?key|access[_-]?token|authorization|credential|password|secret)", re.I
)


class GridPreflightStatus(StrEnum):
    READY = "ready"
    FAILED = "failed"


class MonotonicDirection(StrEnum):
    NON_DECREASING = "non_decreasing"
    NON_INCREASING = "non_increasing"


class StructuredConstraint(Protocol):
    reason_code: str

    def evaluate(self, values: Mapping[str, object]) -> bool: ...

    def referenced_parameters(self) -> tuple[str, ...]: ...

    def to_dict(self) -> dict[str, Any]: ...


def _safe_name(value: object, label: str) -> str:
    if not isinstance(value, str) or not _NAME.fullmatch(value):
        raise InvalidParameterConstraintError(f"{label} must be a safe identifier")
    return value


def _finite(value: object, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise InvalidParameterConstraintError(f"{label} must be numeric")
    result = float(value)
    if not math.isfinite(result):
        raise InvalidParameterConstraintError(f"{label} must be finite")
    return result


def _compare(left: object, operator: ConstraintOperator, right: object) -> bool:
    try:
        if operator is ConstraintOperator.LESS_THAN:
            return left < right
        if operator is ConstraintOperator.LESS_OR_EQUAL:
            return left <= right
        if operator is ConstraintOperator.GREATER_THAN:
            return left > right
        if operator is ConstraintOperator.GREATER_OR_EQUAL:
            return left >= right
        if operator is ConstraintOperator.EQUAL:
            return left == right
        return left != right
    except TypeError as exc:
        raise InvalidParameterConstraintError(
            "constraint values do not support the declared comparison"
        ) from exc


@dataclass(frozen=True)
class BoundConstraint:
    parameter: str
    minimum: float | None = None
    maximum: float | None = None
    minimum_inclusive: bool = True
    maximum_inclusive: bool = True
    reason_code: str = "BOUND_CONSTRAINT_FAILED"

    def __post_init__(self) -> None:
        object.__setattr__(self, "parameter", _safe_name(self.parameter, "parameter"))
        if self.minimum is None and self.maximum is None:
            raise InvalidParameterConstraintError("bound constraint requires a bound")
        minimum = None if self.minimum is None else _finite(self.minimum, "minimum")
        maximum = None if self.maximum is None else _finite(self.maximum, "maximum")
        if minimum is not None and maximum is not None and minimum > maximum:
            raise InvalidParameterConstraintError("bound constraint bounds are reversed")
        if not isinstance(self.minimum_inclusive, bool) or not isinstance(
            self.maximum_inclusive, bool
        ):
            raise InvalidParameterConstraintError("bound inclusion flags must be booleans")
        object.__setattr__(self, "minimum", minimum)
        object.__setattr__(self, "maximum", maximum)

    def evaluate(self, values: Mapping[str, object]) -> bool:
        value = _finite(values[self.parameter], self.parameter)
        lower = self.minimum is None or (
            value >= self.minimum if self.minimum_inclusive else value > self.minimum
        )
        upper = self.maximum is None or (
            value <= self.maximum if self.maximum_inclusive else value < self.maximum
        )
        return lower and upper

    def referenced_parameters(self) -> tuple[str, ...]:
        return (self.parameter,)

    def to_dict(self) -> dict[str, Any]:
        return {
            "constraint_type": "bound",
            "parameter": self.parameter,
            "minimum": self.minimum,
            "maximum": self.maximum,
            "minimum_inclusive": self.minimum_inclusive,
            "maximum_inclusive": self.maximum_inclusive,
            "reason_code": self.reason_code,
        }


@dataclass(frozen=True)
class SumConstraint:
    parameters: tuple[str, ...]
    operator: ConstraintOperator | str
    limit: float
    reason_code: str = "SUM_CONSTRAINT_FAILED"

    def __post_init__(self) -> None:
        parameters = tuple(_safe_name(item, "sum parameter") for item in self.parameters)
        if not parameters or len(set(parameters)) != len(parameters):
            raise InvalidParameterConstraintError("sum parameters must be non-empty and unique")
        try:
            operator = (
                self.operator
                if isinstance(self.operator, ConstraintOperator)
                else ConstraintOperator(self.operator)
            )
        except (TypeError, ValueError) as exc:
            raise InvalidParameterConstraintError("sum operator is invalid") from exc
        if operator not in {
            ConstraintOperator.LESS_THAN,
            ConstraintOperator.LESS_OR_EQUAL,
            ConstraintOperator.GREATER_THAN,
            ConstraintOperator.GREATER_OR_EQUAL,
            ConstraintOperator.EQUAL,
        }:
            raise InvalidParameterConstraintError("sum operator is unsupported")
        object.__setattr__(self, "parameters", parameters)
        object.__setattr__(self, "operator", operator)
        object.__setattr__(self, "limit", _finite(self.limit, "sum limit"))

    def evaluate(self, values: Mapping[str, object]) -> bool:
        total = sum(_finite(values[item], item) for item in self.parameters)
        return _compare(total, self.operator, self.limit)

    def referenced_parameters(self) -> tuple[str, ...]:
        return self.parameters

    def to_dict(self) -> dict[str, Any]:
        return {
            "constraint_type": "sum",
            "parameters": list(self.parameters),
            "operator": self.operator.value,
            "limit": self.limit,
            "reason_code": self.reason_code,
        }


@dataclass(frozen=True)
class OrderingConstraint:
    left: str
    operator: ConstraintOperator | str
    right: str
    reason_code: str = "ORDERING_CONSTRAINT_FAILED"

    def __post_init__(self) -> None:
        object.__setattr__(self, "left", _safe_name(self.left, "left parameter"))
        object.__setattr__(self, "right", _safe_name(self.right, "right parameter"))
        try:
            operator = (
                self.operator
                if isinstance(self.operator, ConstraintOperator)
                else ConstraintOperator(self.operator)
            )
        except (TypeError, ValueError) as exc:
            raise InvalidParameterConstraintError("ordering operator is invalid") from exc
        object.__setattr__(self, "operator", operator)

    def evaluate(self, values: Mapping[str, object]) -> bool:
        return _compare(values[self.left], self.operator, values[self.right])

    def referenced_parameters(self) -> tuple[str, ...]:
        return (self.left, self.right)

    def to_dict(self) -> dict[str, Any]:
        return {
            "constraint_type": "ordering",
            "left": self.left,
            "operator": self.operator.value,
            "right": self.right,
            "reason_code": self.reason_code,
        }


@dataclass(frozen=True)
class BudgetConstraint:
    parameters: tuple[str, ...]
    reserve_parameter: str | None = None
    reserve_value: float = 0.0
    reason_code: str = "BUDGET_CONSTRAINT_FAILED"

    def __post_init__(self) -> None:
        parameters = tuple(_safe_name(item, "budget parameter") for item in self.parameters)
        if not parameters or len(set(parameters)) != len(parameters):
            raise InvalidParameterConstraintError("budget parameters must be non-empty and unique")
        reserve_parameter = (
            None
            if self.reserve_parameter is None
            else _safe_name(self.reserve_parameter, "reserve parameter")
        )
        reserve = _finite(self.reserve_value, "reserve value")
        if not 0 <= reserve <= 1:
            raise InvalidParameterConstraintError("reserve value must be in [0, 1]")
        object.__setattr__(self, "parameters", parameters)
        object.__setattr__(self, "reserve_parameter", reserve_parameter)
        object.__setattr__(self, "reserve_value", reserve)

    def evaluate(self, values: Mapping[str, object]) -> bool:
        reserve = (
            _finite(values[self.reserve_parameter], self.reserve_parameter)
            if self.reserve_parameter is not None
            else self.reserve_value
        )
        if not 0 <= reserve <= 1:
            return False
        invested = sum(_finite(values[item], item) for item in self.parameters)
        return invested <= 1.0 - reserve + 1e-12

    def referenced_parameters(self) -> tuple[str, ...]:
        return self.parameters + ((self.reserve_parameter,) if self.reserve_parameter else ())

    def to_dict(self) -> dict[str, Any]:
        return {
            "constraint_type": "budget",
            "parameters": list(self.parameters),
            "reserve_parameter": self.reserve_parameter,
            "reserve_value": self.reserve_value,
            "reason_code": self.reason_code,
        }


@dataclass(frozen=True)
class MonotonicConstraint:
    parameters: tuple[str, ...]
    direction: MonotonicDirection | str = MonotonicDirection.NON_DECREASING
    reason_code: str = "MONOTONIC_CONSTRAINT_FAILED"

    def __post_init__(self) -> None:
        parameters = tuple(_safe_name(item, "monotonic parameter") for item in self.parameters)
        if len(parameters) < 2 or len(parameters) != len(set(parameters)):
            raise InvalidParameterConstraintError(
                "monotonic parameters must contain at least two unique names"
            )
        try:
            direction = (
                self.direction
                if isinstance(self.direction, MonotonicDirection)
                else MonotonicDirection(self.direction)
            )
        except (TypeError, ValueError) as exc:
            raise InvalidParameterConstraintError("monotonic direction is invalid") from exc
        object.__setattr__(self, "parameters", parameters)
        object.__setattr__(self, "direction", direction)

    def evaluate(self, values: Mapping[str, object]) -> bool:
        ordered = tuple(_finite(values[item], item) for item in self.parameters)
        pairs = zip(ordered, ordered[1:])
        if self.direction is MonotonicDirection.NON_DECREASING:
            return all(left <= right for left, right in pairs)
        return all(left >= right for left, right in pairs)

    def referenced_parameters(self) -> tuple[str, ...]:
        return self.parameters

    def to_dict(self) -> dict[str, Any]:
        return {
            "constraint_type": "monotonic",
            "parameters": list(self.parameters),
            "direction": self.direction.value,
            "reason_code": self.reason_code,
        }


GridConstraint = (
    BoundConstraint | SumConstraint | OrderingConstraint | BudgetConstraint | MonotonicConstraint
)


def constraint_from_dict(payload: object) -> GridConstraint:
    if not isinstance(payload, Mapping):
        raise InvalidParameterConstraintError("structured constraint must be an object")
    if set(payload) & {"expression", "code", "formula", "eval", "exec"}:
        raise InvalidParameterConstraintError("executable constraint expressions are not allowed")
    kind = payload.get("constraint_type")
    common = (
        {"reason_code": str(payload.get("reason_code", ""))} if payload.get("reason_code") else {}
    )
    if kind == "bound":
        return BoundConstraint(
            parameter=str(payload.get("parameter", "")),
            minimum=payload.get("minimum"),
            maximum=payload.get("maximum"),
            minimum_inclusive=payload.get("minimum_inclusive", True),
            maximum_inclusive=payload.get("maximum_inclusive", True),
            **common,
        )
    if kind == "sum":
        return SumConstraint(
            parameters=tuple(payload.get("parameters", ())),
            operator=payload.get("operator", ""),
            limit=payload.get("limit"),
            **common,
        )
    if kind == "ordering":
        return OrderingConstraint(
            left=str(payload.get("left", "")),
            operator=payload.get("operator", ""),
            right=str(payload.get("right", "")),
            **common,
        )
    if kind == "budget":
        return BudgetConstraint(
            parameters=tuple(payload.get("parameters", ())),
            reserve_parameter=payload.get("reserve_parameter"),
            reserve_value=payload.get("reserve_value", 0.0),
            **common,
        )
    if kind == "monotonic":
        return MonotonicConstraint(
            parameters=tuple(payload.get("parameters", ())),
            direction=payload.get("direction", MonotonicDirection.NON_DECREASING.value),
            **common,
        )
    raise InvalidParameterConstraintError("structured constraint type is unsupported")


@dataclass(frozen=True)
class GridSearchDefinition:
    """Immutable execution definition layered on one frozen Experiment."""

    experiment_id: str
    experiment_hash: str
    strategy_bindings: tuple[ParameterBinding, ...] = ()
    backtest_bindings: tuple[BacktestParameterBinding, ...] = ()
    fixed_parameters: Mapping[str, Any] = None  # type: ignore[assignment]
    constraints: tuple[GridConstraint, ...] = ()
    max_candidates: int = DEFAULT_MAX_CANDIDATES
    theoretical_guard: int = DEFAULT_THEORETICAL_GUARD
    schema_version: str = GRID_SEARCH_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if not isinstance(self.experiment_id, str) or not self.experiment_id.strip():
            raise InvalidParameterSpaceError("experiment_id must be non-empty")
        if not isinstance(self.experiment_hash, str) or not _HASH.fullmatch(self.experiment_hash):
            raise InvalidParameterSpaceError("experiment_hash must be a SHA-256 digest")
        strategy = ParameterBindingSet(self.strategy_bindings).bindings
        backtest = BacktestParameterBindingSet(self.backtest_bindings).bindings
        names = [item.parameter_name for item in strategy] + [
            item.parameter_name for item in backtest
        ]
        if len(names) != len(set(names)):
            raise InvalidParameterSpaceError("a tunable parameter may have only one owner")
        fixed = {} if self.fixed_parameters is None else dict(self.fixed_parameters)
        for key in fixed:
            _safe_name(key, "fixed parameter")
            if _SENSITIVE.search(key):
                raise InvalidParameterSpaceError("fixed parameters contain sensitive fields")
        try:
            canonical_json(fixed)
        except (TypeError, ValueError, OverflowError) as exc:
            raise InvalidParameterSpaceError("fixed parameters must be canonical JSON") from exc
        constraints = tuple(self.constraints)
        if not all(
            isinstance(
                item,
                (
                    BoundConstraint,
                    SumConstraint,
                    OrderingConstraint,
                    BudgetConstraint,
                    MonotonicConstraint,
                ),
            )
            for item in constraints
        ):
            raise InvalidParameterSpaceError("constraints must be structured constraints")
        if (
            isinstance(self.max_candidates, bool)
            or not 1 <= self.max_candidates <= HARD_MAX_CANDIDATES
        ):
            raise InvalidParameterSpaceError(
                f"max_candidates must be between 1 and {HARD_MAX_CANDIDATES}"
            )
        if isinstance(self.theoretical_guard, bool) or self.theoretical_guard < self.max_candidates:
            raise InvalidParameterSpaceError("theoretical_guard must cover max_candidates")
        if self.schema_version != GRID_SEARCH_SCHEMA_VERSION:
            raise InvalidParameterSpaceError("grid search schema version is unsupported")
        object.__setattr__(self, "strategy_bindings", strategy)
        object.__setattr__(self, "backtest_bindings", backtest)
        object.__setattr__(self, "fixed_parameters", MappingProxyType(dict(sorted(fixed.items()))))
        object.__setattr__(
            self,
            "constraints",
            tuple(sorted(constraints, key=lambda item: canonical_json(item.to_dict()))),
        )

    @property
    def tunable_parameter_names(self) -> tuple[str, ...]:
        return tuple(
            sorted(
                [item.parameter_name for item in self.strategy_bindings]
                + [item.parameter_name for item in self.backtest_bindings]
            )
        )

    @property
    def definition_hash(self) -> str:
        return sha256_hash(self.to_dict())

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "experiment_id": self.experiment_id,
            "experiment_hash": self.experiment_hash,
            "strategy_bindings": [item.to_dict() for item in self.strategy_bindings],
            "backtest_bindings": [item.to_dict() for item in self.backtest_bindings],
            "fixed_parameters": dict(self.fixed_parameters),
            "constraints": [item.to_dict() for item in self.constraints],
            "max_candidates": self.max_candidates,
            "theoretical_guard": self.theoretical_guard,
        }

    @classmethod
    def from_dict(cls, payload: object) -> GridSearchDefinition:
        if not isinstance(payload, Mapping):
            raise InvalidParameterSpaceError("grid search definition must be an object")
        return cls(
            experiment_id=str(payload.get("experiment_id", "")),
            experiment_hash=str(payload.get("experiment_hash", "")),
            strategy_bindings=tuple(
                ParameterBinding.from_dict(item) for item in payload.get("strategy_bindings", ())
            ),
            backtest_bindings=tuple(
                BacktestParameterBinding.from_dict(item)
                for item in payload.get("backtest_bindings", ())
            ),
            fixed_parameters=payload.get("fixed_parameters", {}),
            constraints=tuple(
                constraint_from_dict(item) for item in payload.get("constraints", ())
            ),
            max_candidates=payload.get("max_candidates", DEFAULT_MAX_CANDIDATES),
            theoretical_guard=payload.get("theoretical_guard", DEFAULT_THEORETICAL_GUARD),
            schema_version=str(payload.get("schema_version", GRID_SEARCH_SCHEMA_VERSION)),
        )


@dataclass(frozen=True)
class GridCandidatePlan:
    candidate_index: int
    source_index: int
    parameter_set: ParameterSet
    semantic_hash: str
    derived_strategy_configuration_hash: str
    backtest_configuration_hash: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "candidate_index": self.candidate_index,
            "source_index": self.source_index,
            "parameter_set_hash": self.parameter_set.content_hash,
            "semantic_hash": self.semantic_hash,
            "derived_strategy_configuration_hash": self.derived_strategy_configuration_hash,
            "backtest_configuration_hash": self.backtest_configuration_hash,
        }


@dataclass(frozen=True)
class GridSearchPreflight:
    definition: GridSearchDefinition
    parameter_space_hash: str
    theoretical_count: int
    pruned_count: int
    valid_count: int
    duplicate_count: int
    executable_count: int
    pruning_reasons: Mapping[str, int]
    duplicate_samples: tuple[Mapping[str, Any], ...]
    plans: tuple[GridCandidatePlan, ...]
    status: GridPreflightStatus
    issues: tuple[str, ...] = ()

    @property
    def can_execute(self) -> bool:
        return self.status is GridPreflightStatus.READY

    @property
    def candidate_set(self) -> ParameterCandidateSet:
        return ParameterCandidateSet(
            parameter_space=self.plans[0].parameter_set.parameter_space
            if self.plans
            else self._space,
            theoretical_candidate_count=self.theoretical_count,
            candidates=tuple(item.parameter_set for item in self.plans),
        )

    @property
    def _space(self):
        raise InvalidParameterSpaceError("zero-candidate preflight has no executable candidate set")

    def to_dict(self, *, include_candidates: bool = True) -> dict[str, Any]:
        payload = {
            "definition_hash": self.definition.definition_hash,
            "parameter_space_hash": self.parameter_space_hash,
            "theoretical_count": self.theoretical_count,
            "pruned_count": self.pruned_count,
            "valid_count": self.valid_count,
            "duplicate_count": self.duplicate_count,
            "executable_count": self.executable_count,
            "max_allowed": self.definition.max_candidates,
            "hard_maximum": HARD_MAX_CANDIDATES,
            "theoretical_guard": self.definition.theoretical_guard,
            "pruning_reasons": dict(self.pruning_reasons),
            "duplicate_samples": [dict(item) for item in self.duplicate_samples],
            "status": self.status.value,
            "can_execute": self.can_execute,
            "issues": list(self.issues),
        }
        if include_candidates:
            payload["candidates"] = [item.to_dict() for item in self.plans]
        return payload


def preflight_grid_search(
    experiment: Experiment,
    definition: GridSearchDefinition,
    base_strategy_version: StrategyVersion,
) -> GridSearchPreflight:
    """Expand, prune, materialize, and deduplicate without executing a backtest."""
    if not isinstance(experiment, Experiment) or not isinstance(
        base_strategy_version, StrategyVersion
    ):
        raise TypeError("preflight requires Experiment and StrategyVersion values")
    if experiment.status not in {
        ExperimentStatus.SPACE_FROZEN,
        ExperimentStatus.CANDIDATES_GENERATED,
    }:
        raise InvalidParameterSpaceError(
            "grid preflight requires a frozen, not-yet-running experiment"
        )
    if (
        definition.experiment_id != experiment.experiment_id
        or definition.experiment_hash != experiment.content_hash
    ):
        raise InvalidParameterSpaceError("grid definition does not bind the frozen experiment")
    space = experiment.parameter_space
    theoretical = theoretical_candidate_count(space)
    if theoretical > definition.theoretical_guard:
        return _failed_preflight(
            definition,
            space.content_hash,
            theoretical,
            "THEORETICAL_COUNT_EXCEEDS_GUARD",
        )
    names = {item.name for item in space.parameters}
    if set(definition.tunable_parameter_names) != names:
        raise InvalidParameterSpaceError(
            "every ParameterSpace parameter must have exactly one strategy or backtest binding"
        )
    referenced = {name for item in definition.constraints for name in item.referenced_parameters()}
    if not referenced.issubset(names):
        raise InvalidParameterSpaceError("structured constraints reference unknown parameters")
    if (
        base_strategy_version.version_id != experiment.base_strategy_version_id
        or base_strategy_version.content_hash != experiment.base_strategy_version_hash
    ):
        raise InvalidParameterSpaceError("base strategy identity does not match experiment")

    parameter_names = tuple(item.name for item in space.parameters)
    domains = tuple(parameter_values(item) for item in space.parameters)
    pruned = Counter()
    valid: list[tuple[int, ParameterSet]] = []
    for source_index, combination in enumerate(product(*domains)):
        values = dict(zip(parameter_names, combination, strict=True))
        if not parameter_constraints_hold(space.constraints, values):
            pruned["PARAMETER_CONSTRAINT_FAILED"] += 1
            continue
        failed = next(
            (item.reason_code for item in definition.constraints if not item.evaluate(values)), None
        )
        if failed is not None:
            pruned[failed] += 1
            continue
        valid.append((source_index, ParameterSet(values, space)))

    plans: list[GridCandidatePlan] = []
    semantic_owner: dict[str, GridCandidatePlan] = {}
    duplicates: list[Mapping[str, Any]] = []
    strategy_names = {item.parameter_name for item in definition.strategy_bindings}
    strategy_bindings = ParameterBindingSet(definition.strategy_bindings)
    backtest_bindings = BacktestParameterBindingSet(definition.backtest_bindings)
    for source_index, parameter_set in valid:
        try:
            strategy_values = {
                name: value
                for name, value in parameter_set.values.items()
                if name in strategy_names
            }
            derived = materialize_strategy_version(
                base_strategy_version,
                ParameterSet(strategy_values),
                strategy_bindings,
            )
            config = materialize_backtest_config(
                experiment.backtest_configuration,
                parameter_set,
                backtest_bindings,
                strategy_version_id=derived.version_id,
            )
        except Exception:
            pruned["MATERIALIZATION_INVALID"] += 1
            continue
        strategy_configuration_hash = sha256_hash(derived.configuration.to_dict())
        config_payload = config.snapshot({})
        config_payload["strategy_version_id"] = "<materialized-strategy>"
        semantic_hash = sha256_hash(
            {
                "strategy_configuration": derived.configuration.to_dict(),
                "backtest_configuration": config_payload,
                "is_start": experiment.is_start_date,
                "is_end": experiment.is_end_date,
            }
        )
        if semantic_hash in semantic_owner:
            owner = semantic_owner[semantic_hash]
            if len(duplicates) < 20:
                duplicates.append(
                    MappingProxyType(
                        {
                            "parameter_set_hash": parameter_set.content_hash,
                            "canonical_candidate_index": owner.candidate_index,
                            "semantic_hash": semantic_hash,
                        }
                    )
                )
            continue
        plan = GridCandidatePlan(
            candidate_index=len(plans),
            source_index=source_index,
            parameter_set=parameter_set,
            semantic_hash=semantic_hash,
            derived_strategy_configuration_hash=strategy_configuration_hash,
            backtest_configuration_hash=backtest_config_hash(
                config, strategy_version_id=experiment.base_strategy_version_id
            ),
        )
        plans.append(plan)
        semantic_owner[semantic_hash] = plan

    valid_count = len(valid)
    duplicate_count = valid_count - pruned["MATERIALIZATION_INVALID"] - len(plans)
    issues: list[str] = []
    if not plans:
        issues.append("NO_EXECUTABLE_CANDIDATES")
    if len(plans) > definition.max_candidates:
        issues.append("VALID_CANDIDATE_COUNT_EXCEEDS_LIMIT")
    status = GridPreflightStatus.FAILED if issues else GridPreflightStatus.READY
    return GridSearchPreflight(
        definition=definition,
        parameter_space_hash=space.content_hash,
        theoretical_count=theoretical,
        pruned_count=sum(pruned.values()),
        valid_count=valid_count,
        duplicate_count=duplicate_count,
        executable_count=len(plans),
        pruning_reasons=MappingProxyType(dict(sorted(pruned.items()))),
        duplicate_samples=tuple(duplicates),
        plans=tuple(plans),
        status=status,
        issues=tuple(issues),
    )


def _failed_preflight(
    definition: GridSearchDefinition,
    parameter_space_hash: str,
    theoretical_count: int,
    issue: str,
) -> GridSearchPreflight:
    return GridSearchPreflight(
        definition=definition,
        parameter_space_hash=parameter_space_hash,
        theoretical_count=theoretical_count,
        pruned_count=0,
        valid_count=0,
        duplicate_count=0,
        executable_count=0,
        pruning_reasons=MappingProxyType({}),
        duplicate_samples=(),
        plans=(),
        status=GridPreflightStatus.FAILED,
        issues=(issue,),
    )


__all__ = [
    "DEFAULT_MAX_CANDIDATES",
    "DEFAULT_THEORETICAL_GUARD",
    "GRID_SEARCH_SCHEMA_VERSION",
    "HARD_MAX_CANDIDATES",
    "BoundConstraint",
    "BudgetConstraint",
    "GridCandidatePlan",
    "GridPreflightStatus",
    "GridSearchDefinition",
    "GridSearchPreflight",
    "MonotonicConstraint",
    "MonotonicDirection",
    "OrderingConstraint",
    "SumConstraint",
    "constraint_from_dict",
    "preflight_grid_search",
]
