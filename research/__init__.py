"""PHASE 8A research domain contracts."""

from research.candidates import ParameterCandidateSet, generate_candidates
from research.canonical import canonical_json, sha256_hash
from research.enums import (
    ConstraintOperator,
    ExperimentMethod,
    ExperimentStatus,
    MetricDirection,
    ParameterType,
)
from research.exceptions import (
    CandidateGenerationError,
    ExperimentDomainError,
    ExperimentNotFrozenError,
    InvalidExperimentError,
    InvalidExperimentProvenanceError,
    InvalidObjectiveSpecificationError,
    InvalidParameterConstraintError,
    InvalidParameterDefinitionError,
    InvalidParameterSetError,
    InvalidParameterSpaceError,
    ParameterSpaceTooLargeError,
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
    "CandidateGenerationError",
    "canonical_json",
    "Experiment",
    "ExperimentDomainError",
    "ExperimentMethod",
    "ExperimentNotFrozenError",
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
    "ParameterCandidateSet",
    "ParameterDefinition",
    "ParameterSet",
    "ParameterSpace",
    "ParameterSpaceTooLargeError",
    "ParameterType",
    "sha256_hash",
    "generate_candidates",
]
