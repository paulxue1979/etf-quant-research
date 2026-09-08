"""Deterministic PHASE 8B parameter candidate generation only."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import ROUND_FLOOR, Decimal
from itertools import product
from typing import Any

from research.canonical import canonical_json, sha256_hash
from research.enums import ConstraintOperator, ExperimentStatus, ParameterType
from research.exceptions import (
    ExperimentNotFrozenError,
    InvalidParameterConstraintError,
    InvalidParameterSpaceError,
    ParameterSpaceTooLargeError,
)
from research.experiments import (
    Experiment,
    ParameterConstraint,
    ParameterDefinition,
    ParameterSet,
    ParameterSpace,
)


@dataclass(frozen=True)
class ParameterCandidateSet:
    """One ordered, hashable candidate sequence derived from a parameter space."""

    parameter_space: ParameterSpace
    theoretical_candidate_count: int
    candidates: tuple[ParameterSet, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.parameter_space, ParameterSpace):
            raise InvalidParameterSpaceError("parameter_space must be a ParameterSpace")
        if (
            isinstance(self.theoretical_candidate_count, bool)
            or not isinstance(self.theoretical_candidate_count, int)
            or self.theoretical_candidate_count < 0
        ):
            raise InvalidParameterSpaceError("theoretical_candidate_count must be non-negative")
        if not isinstance(self.candidates, tuple) or not all(
            isinstance(candidate, ParameterSet) for candidate in self.candidates
        ):
            raise InvalidParameterSpaceError("candidates must contain ParameterSet values")
        if any(candidate.parameter_space != self.parameter_space for candidate in self.candidates):
            raise InvalidParameterSpaceError("each candidate must bind this parameter space")

    @property
    def candidate_count(self) -> int:
        return len(self.candidates)

    def to_dict(self) -> dict[str, Any]:
        return {
            "parameter_space_hash": self.parameter_space.content_hash,
            "theoretical_candidate_count": self.theoretical_candidate_count,
            "candidate_count": self.candidate_count,
            "candidates": [candidate.to_dict() for candidate in self.candidates],
        }

    def canonical_json(self) -> str:
        return canonical_json(self.to_dict())

    @property
    def content_hash(self) -> str:
        return sha256_hash(
            {
                "parameter_space_hash": self.parameter_space.content_hash,
                "candidates": [candidate.to_dict() for candidate in self.candidates],
            }
        )

    @property
    def candidate_set_hash(self) -> str:
        return self.content_hash


def generate_candidates(experiment: Experiment) -> ParameterCandidateSet:
    """Generate an ordered finite Cartesian product for a frozen experiment."""
    if not isinstance(experiment, Experiment):
        raise TypeError("experiment must be an Experiment")
    if experiment.status is not ExperimentStatus.SPACE_FROZEN:
        raise ExperimentNotFrozenError(
            "candidate generation requires experiment status space_frozen"
        )

    space = experiment.parameter_space
    theoretical_count = _theoretical_count(space)
    if theoretical_count > space.max_candidates:
        raise ParameterSpaceTooLargeError(
            "theoretical candidate count exceeds parameter space max_candidates"
        )

    parameter_names = tuple(item.name for item in space.parameters)
    domains = tuple(_parameter_values(definition) for definition in space.parameters)
    candidates = tuple(
        ParameterSet(_candidate_values(parameter_names, values), space)
        for values in product(*domains)
        if _constraints_hold(space.constraints, _candidate_values(parameter_names, values))
    )
    return ParameterCandidateSet(space, theoretical_count, candidates)


def _theoretical_count(space: ParameterSpace) -> int:
    count = 1
    for definition in space.parameters:
        count *= _parameter_value_count(definition)
    return count


def _parameter_value_count(definition: ParameterDefinition) -> int:
    if definition.parameter_type is ParameterType.INTEGER:
        return ((definition.max_value - definition.min_value) // definition.step) + 1
    if definition.parameter_type is ParameterType.FLOAT:
        span = _decimal(definition.max_value) - _decimal(definition.min_value)
        return int(
            (span / _decimal(definition.step)).to_integral_value(rounding=ROUND_FLOOR)
        ) + 1
    return len(definition.allowed_values)


def _parameter_values(definition: ParameterDefinition) -> tuple[object, ...]:
    if definition.parameter_type is ParameterType.INTEGER:
        return tuple(range(definition.min_value, definition.max_value + 1, definition.step))
    if definition.parameter_type is ParameterType.FLOAT:
        start = _decimal(definition.min_value)
        step = _decimal(definition.step)
        count = _parameter_value_count(definition)
        return tuple(float(start + step * index) for index in range(count))
    return tuple(sorted(definition.allowed_values, key=_value_order_key))


def _candidate_values(names: tuple[str, ...], values: tuple[object, ...]) -> dict[str, object]:
    return dict(zip(names, values, strict=True))


def _value_order_key(value: object) -> tuple[int, Decimal | str, int]:
    """Provide an explicit total order for the PHASE 8A JSON scalar domain."""
    if isinstance(value, int) and not isinstance(value, bool):
        return (0, Decimal(value), 0)
    if isinstance(value, float):
        return (0, Decimal(str(value)), 1)
    return (1, canonical_json(value), 0)


def _constraints_hold(
    constraints: tuple[ParameterConstraint, ...], values: dict[str, object]
) -> bool:
    return all(_constraint_holds(constraint, values) for constraint in constraints)


def _constraint_holds(constraint: ParameterConstraint, values: dict[str, object]) -> bool:
    left = values[constraint.left]
    right = values[constraint.right]
    try:
        if constraint.operator is ConstraintOperator.LESS_THAN:
            return left < right
        if constraint.operator is ConstraintOperator.LESS_OR_EQUAL:
            return left <= right
        if constraint.operator is ConstraintOperator.GREATER_THAN:
            return left > right
        if constraint.operator is ConstraintOperator.GREATER_OR_EQUAL:
            return left >= right
        if constraint.operator is ConstraintOperator.EQUAL:
            return left == right
        return left != right
    except TypeError as exc:
        raise InvalidParameterConstraintError(
            "constraint values must support the declared comparison"
        ) from exc


def _decimal(value: int | float | None) -> Decimal:
    return Decimal(str(value))
