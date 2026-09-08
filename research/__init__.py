"""PHASE 8A research domain contracts."""

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
from research.experiments import (
    Experiment,
    ExperimentProvenance,
    ObjectiveSpecification,
    ParameterConstraint,
    ParameterDefinition,
    ParameterSet,
    ParameterSpace,
)

__all__ = [
    "ConstraintOperator",
    "canonical_json",
    "Experiment",
    "ExperimentDomainError",
    "ExperimentMethod",
    "ExperimentProvenance",
    "ExperimentStatus",
    "InvalidExperimentError",
    "InvalidExperimentProvenanceError",
    "InvalidObjectiveSpecificationError",
    "InvalidParameterConstraintError",
    "InvalidParameterDefinitionError",
    "InvalidParameterSetError",
    "InvalidParameterSpaceError",
    "MetricDirection",
    "ObjectiveSpecification",
    "ParameterConstraint",
    "ParameterDefinition",
    "ParameterSet",
    "ParameterSpace",
    "ParameterType",
    "sha256_hash",
]
