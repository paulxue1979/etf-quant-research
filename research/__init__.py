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
    ExperimentSelectionConflictError,
    ExperimentSelectionError,
    ExperimentSelectionPersistenceError,
    InvalidExperimentError,
    InvalidExperimentProvenanceError,
    InvalidExperimentSelectionError,
    InvalidObjectiveSpecificationError,
    InvalidParameterBindingError,
    InvalidParameterConstraintError,
    InvalidParameterDefinitionError,
    InvalidParameterSetError,
    InvalidParameterSpaceError,
    OosAlreadyObservedError,
    OosConfigurationMismatchError,
    OosDomainError,
    OosPreconditionError,
    OosProvenanceError,
    OosRangeMismatchError,
    OosResultIntegrityError,
    OosStrategyIdentityMismatchError,
    ParameterBindingError,
    ParameterBindingTypeMismatchError,
    ParameterSpaceTooLargeError,
    SelectionEligibilityError,
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
from research.experiment_selection import (
    ExperimentSelectionDecision,
    ExperimentSelectionEvidence,
    ExperimentSelectionMethod,
    SelectionEligibility,
    SelectionEligibilityReasonCode,
    SelectionEligibilityStatus,
    assess_selection_eligibility,
    build_experiment_selection_decision,
    create_experiment_selection_decision,
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
from research.materialization import (
    BindingValueType,
    ParameterBinding,
    ParameterBindingSet,
    derived_strategy_version_hash,
    derived_strategy_version_id,
    materialization_spec_hash,
    materialize_strategy_version,
)
from research.objective_evaluation import (
    CandidateObjectiveEvaluation,
    ConstraintEvaluation,
    ObjectiveEvaluationError,
    ObjectiveEvaluationReasonCode,
    ObjectiveEvaluationState,
    evaluate_candidate_objective,
    evaluate_objective,
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
    "ExperimentSelectionError",
    "ExperimentSelectionConflictError",
    "ExperimentSelectionPersistenceError",
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
    "InvalidExperimentSelectionError",
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
    "SelectionEligibilityError",
    "UnsupportedParameterBindingTargetError",
    "materialize_strategy_version",
    "materialization_spec_hash",
    "derived_strategy_version_hash",
    "derived_strategy_version_id",
    "sha256_hash",
    "generate_candidates",
    "candidate_id_for",
    "execution_id_for",
    "CandidateObjectiveEvaluation",
    "ConstraintEvaluation",
    "ObjectiveEvaluationError",
    "ObjectiveEvaluationReasonCode",
    "ObjectiveEvaluationState",
    "evaluate_candidate_objective",
    "evaluate_objective",
    "ExperimentSelectionDecision",
    "ExperimentSelectionEvidence",
    "ExperimentSelectionMethod",
    "SelectionEligibility",
    "SelectionEligibilityReasonCode",
    "SelectionEligibilityStatus",
    "assess_selection_eligibility",
    "build_experiment_selection_decision",
    "create_experiment_selection_decision",
    "OosAlreadyObservedError",
    "OosBoundarySignalPolicy",
    "OosConfigurationMismatchError",
    "OosDataProvenance",
    "OosDomainError",
    "OosEvaluationConfig",
    "OosEvaluationIdentity",
    "OosEvaluationRange",
    "OosEvaluationResult",
    "OosEvaluationSpec",
    "OosExecutionStatus",
    "OosPerformanceSummary",
    "OosPreconditionError",
    "OosProvenanceError",
    "OosRangeMismatchError",
    "OosResultIntegrityError",
    "OosStrategyIdentityMismatchError",
    "PortfolioInitializationMode",
    "validate_one_shot_result",
    "validate_oos_configuration",
    "validate_oos_preconditions",
    "validate_oos_range",
    "validate_oos_strategy_identity",
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
    oos_names = {
        "OosAlreadyObservedError",
        "OosBoundarySignalPolicy",
        "OosConfigurationMismatchError",
        "OosDataProvenance",
        "OosDomainError",
        "OosEvaluationConfig",
        "OosEvaluationIdentity",
        "OosEvaluationRange",
        "OosEvaluationResult",
        "OosEvaluationSpec",
        "OosExecutionStatus",
        "OosPerformanceSummary",
        "OosPreconditionError",
        "OosProvenanceError",
        "OosRangeMismatchError",
        "OosResultIntegrityError",
        "OosStrategyIdentityMismatchError",
        "PortfolioInitializationMode",
        "validate_one_shot_result",
        "validate_oos_configuration",
        "validate_oos_preconditions",
        "validate_oos_range",
        "validate_oos_strategy_identity",
    }
    if name in oos_names:
        from research import oos

        return getattr(oos, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
