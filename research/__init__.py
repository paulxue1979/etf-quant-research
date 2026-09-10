"""PHASE 8A research domain contracts."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

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
    CandidateExecutionConflictError,
    CandidateExecutionError,
    CandidateExecutionPersistenceError,
    CandidateGenerationError,
    ExecutionStateTransitionError,
    ExperimentDomainError,
    ExperimentFinalizationError,
    ExperimentNotFrozenError,
    ExperimentResultConflictError,
    ExperimentResultError,
    ExperimentResultPersistenceError,
    InvalidExperimentError,
    InvalidExperimentProvenanceError,
    InvalidObjectiveSpecificationError,
    InvalidParameterBindingError,
    InvalidParameterConstraintError,
    InvalidParameterDefinitionError,
    InvalidParameterSetError,
    InvalidParameterSpaceError,
    ParameterBindingError,
    ParameterBindingTypeMismatchError,
    ParameterSpaceTooLargeError,
    StrategyMaterializationError,
    UnsupportedParameterBindingTargetError,
)
from research.execution import (
    CandidateExecution,
    CandidateExecutionStatus,
    ExecutionEvent,
    candidate_id_for,
    execution_id_for,
)
from research.execution_outcome import (
    ExperimentExecutionOutcome,
    ExperimentExecutionOutcomeStatus,
)
from research.experiment_result import ExperimentResult
from research.experiments import (
    Experiment,
    ExperimentProvenance,
    ObjectiveSpecification,
    ParameterConstraint,
    ParameterDefinition,
    ParameterSet,
    ParameterSpace,
)
from research.materialization import (
    BindingValueType,
    ParameterBinding,
    ParameterBindingSet,
    derived_strategy_version_hash,
    derived_strategy_version_id,
    materialization_spec_hash,
    materialize_strategy_version,
)

if TYPE_CHECKING:
    from research.execution_service import ENGINE_SERVICE_VERSION, ExperimentExecutionService
    from research.result_finalization_service import (
        ExperimentResultFinalizationService,
        ResultFinalizationService,
    )

__all__ = [
    "ConstraintOperator",
    "CandidateGenerationError",
    "CandidateExecution",
    "CandidateExecutionConflictError",
    "CandidateExecutionError",
    "CandidateExecutionPersistenceError",
    "CandidateExecutionStatus",
    "ExperimentExecutionOutcome",
    "ExperimentExecutionOutcomeStatus",
    "ExperimentResult",
    "ExperimentResultFinalizationService",
    "ExperimentResultError",
    "ExperimentResultConflictError",
    "ExperimentResultPersistenceError",
    "ExperimentFinalizationError",
    "ResultFinalizationService",
    "ExperimentExecutionService",
    "ENGINE_SERVICE_VERSION",
    "canonical_json",
    "Experiment",
    "ExperimentDomainError",
    "ExperimentMethod",
    "ExperimentNotFrozenError",
    "ExperimentProvenance",
    "ExperimentStatus",
    "ExecutionEvent",
    "ExecutionStateTransitionError",
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
    "BindingValueType",
    "InvalidParameterBindingError",
    "ParameterBinding",
    "ParameterBindingError",
    "ParameterBindingSet",
    "ParameterBindingTypeMismatchError",
    "StrategyMaterializationError",
    "UnsupportedParameterBindingTargetError",
    "materialize_strategy_version",
    "materialization_spec_hash",
    "derived_strategy_version_hash",
    "derived_strategy_version_id",
    "sha256_hash",
    "generate_candidates",
    "candidate_id_for",
    "execution_id_for",
]


def __getattr__(name: str) -> Any:
    """Lazily expose the service without importing persistence during package setup."""
    if name in {"ENGINE_SERVICE_VERSION", "ExperimentExecutionService"}:
        from research.execution_service import ENGINE_SERVICE_VERSION, ExperimentExecutionService

        return {
            "ENGINE_SERVICE_VERSION": ENGINE_SERVICE_VERSION,
            "ExperimentExecutionService": ExperimentExecutionService,
        }[name]
    if name in {"ExperimentResultFinalizationService", "ResultFinalizationService"}:
        from research.result_finalization_service import (
            ExperimentResultFinalizationService,
            ResultFinalizationService,
        )

        return {
            "ExperimentResultFinalizationService": ExperimentResultFinalizationService,
            "ResultFinalizationService": ResultFinalizationService,
        }[name]
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
