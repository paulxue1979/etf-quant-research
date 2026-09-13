"""Domain errors for PHASE 8A experiment records."""


class ExperimentDomainError(ValueError):
    """Base error for invalid experiment-domain data."""


class InvalidParameterDefinitionError(ExperimentDomainError):
    """Raised when a parameter definition is malformed."""


class InvalidParameterSetError(ExperimentDomainError):
    """Raised when concrete parameter values are malformed."""


class InvalidParameterConstraintError(ExperimentDomainError):
    """Raised when a safe parameter constraint is malformed."""


class InvalidParameterSpaceError(ExperimentDomainError):
    """Raised when a parameter space is malformed."""


class InvalidObjectiveSpecificationError(ExperimentDomainError):
    """Raised when an objective specification is malformed."""


class InvalidExperimentProvenanceError(ExperimentDomainError):
    """Raised when experiment provenance is incomplete or inconsistent."""


class InvalidExperimentError(ExperimentDomainError):
    """Raised when an experiment binding or lifecycle transition is invalid."""


class CandidateGenerationError(ExperimentDomainError):
    """Base error for deterministic PHASE 8B candidate generation."""

    code = "CANDIDATE_GENERATION_ERROR"


class ExperimentNotFrozenError(CandidateGenerationError):
    """Raised when candidate generation is requested before space freeze."""

    code = "EXPERIMENT_NOT_SPACE_FROZEN"


class ParameterSpaceTooLargeError(CandidateGenerationError):
    """Raised when the theoretical Cartesian product exceeds its declared limit."""

    code = "PARAMETER_SPACE_TOO_LARGE"


class ParameterBindingError(ExperimentDomainError):
    """Base error for safe, explicit parameter-to-strategy bindings."""

    code = "PARAMETER_BINDING_ERROR"


class InvalidParameterBindingError(ParameterBindingError):
    """Raised when a binding contract is malformed or duplicated."""

    code = "INVALID_PARAMETER_BINDING"


class UnsupportedParameterBindingTargetError(ParameterBindingError):
    """Raised when a target path is outside the PHASE 8D-1 whitelist."""

    code = "PARAMETER_BINDING_TARGET_UNSUPPORTED"


class ParameterBindingTypeMismatchError(ParameterBindingError):
    """Raised when a candidate value cannot be applied to its declared target."""

    code = "PARAMETER_BINDING_TYPE_MISMATCH"


class StrategyMaterializationError(ParameterBindingError):
    """Raised when immutable strategy materialization cannot complete atomically."""

    code = "STRATEGY_MATERIALIZATION_ERROR"

    def __init__(self, message: str, *, validation_result: object | None = None) -> None:
        super().__init__(message)
        self.validation_result = validation_result


class CandidateExecutionError(ExperimentDomainError):
    """Base error for PHASE 8D-2 candidate execution records."""

    code = "CANDIDATE_EXECUTION_ERROR"


class InvalidCandidateExecutionError(CandidateExecutionError):
    """Raised when an execution identity or serialized record is malformed."""

    code = "INVALID_CANDIDATE_EXECUTION"


class ExecutionStateTransitionError(CandidateExecutionError):
    """Raised when a candidate execution attempts an illegal state transition."""

    code = "EXECUTION_STATE_TRANSITION_INVALID"


class CandidateExecutionConflictError(CandidateExecutionError):
    """Raised when an execution claim or identity conflicts with another owner."""

    code = "CANDIDATE_EXECUTION_CONFLICT"


class CandidateExecutionPersistenceError(CandidateExecutionError):
    """Raised when an execution record cannot be safely persisted or restored."""


class ExperimentResultError(ExperimentDomainError):
    """Base error for immutable PHASE 8D-4 experiment results."""


class ExperimentResultConflictError(ExperimentResultError):
    """Raised when one candidate is assigned conflicting official results."""


class ExperimentResultPersistenceError(ExperimentResultError):
    """Raised when an experiment result cannot be safely stored or restored."""


class ExperimentFinalizationError(ExperimentResultError):
    """Raised when a completed candidate outcome cannot be finalized safely."""

    code = "CANDIDATE_EXECUTION_PERSISTENCE_ERROR"


class ExperimentSelectionError(ExperimentDomainError):
    """Base error for the PHASE 8E-1D researcher-selection domain."""

    code = "EXPERIMENT_SELECTION_ERROR"

    def __init__(
        self,
        message: str,
        *,
        code: str | None = None,
        details: object | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code or self.code
        self.details = details


class InvalidExperimentSelectionError(ExperimentSelectionError):
    """Raised when a researcher selection or its evidence is invalid."""


class SelectionEligibilityError(ExperimentSelectionError):
    """Raised when the selected candidate is not eligible for selection."""


class ExperimentSelectionPersistenceError(ExperimentSelectionError):
    """Raised when an immutable experiment selection cannot be stored or restored."""

    code = "EXPERIMENT_SELECTION_PERSISTENCE_ERROR"


class ExperimentSelectionConflictError(ExperimentSelectionPersistenceError):
    """Raised when an experiment already has a different official selection."""

    code = "SELECTION_CONFLICT"


class OosDomainError(ExperimentDomainError):
    """Base error for PHASE 8F pure OOS domain contracts."""

    code = "OOS_DOMAIN_ERROR"

    def __init__(
        self, message: str, *, code: str | None = None, details: object | None = None
    ) -> None:
        super().__init__(message)
        self.code = code or self.code
        self.details = details


class OosPreconditionError(OosDomainError):
    """Raised when frozen protocol prerequisites are not satisfied."""

    code = "OOS_PRECONDITION_FAILED"


class OosStrategyIdentityMismatchError(OosDomainError):
    """Raised when OOS identity does not match the exact frozen strategy."""

    code = "OOS_STRATEGY_IDENTITY_MISMATCH"


class OosRangeMismatchError(OosDomainError):
    """Raised when OOS dates or warm-up boundaries violate the frozen split."""

    code = "OOS_RANGE_MISMATCH"


class OosConfigurationMismatchError(OosDomainError):
    """Raised when result-affecting configuration differs from the frozen contract."""

    code = "OOS_CONFIGURATION_MISMATCH"


class OosProvenanceError(OosDomainError):
    """Raised when OOS provenance is unsafe or inconsistent."""

    code = "OOS_PROVENANCE_MISMATCH"


class OosResultIntegrityError(OosDomainError):
    """Raised when an immutable OOS result is malformed or hash-inconsistent."""

    code = "OOS_RESULT_INTEGRITY_ERROR"


class OosAlreadyObservedError(OosDomainError):
    """Raised when an official OOS result would be replaced."""

    code = "OOS_ALREADY_OBSERVED"


class OosExecutionError(OosDomainError):
    """Base error for PHASE 8F-2 execution control and persistence."""

    code = "OOS_EXECUTION_ERROR"


class OosExecutionIdentityConflictError(OosExecutionError):
    """Raised when a protocol lineage is bound to another immutable spec."""

    code = "OOS_EXECUTION_IDENTITY_CONFLICT"


class OosExecutionInProgressError(OosExecutionError):
    """Raised when an active lease prevents a second claim."""

    code = "OOS_EXECUTION_IN_PROGRESS"


class OosExecutionNotRunningError(OosExecutionError):
    """Raised when a lease-bound mutation requires RUNNING state."""

    code = "OOS_EXECUTION_NOT_RUNNING"


class OosExecutionLeaseMismatchError(OosExecutionError):
    """Raised when a worker presents an invalid or stale lease token."""

    code = "OOS_EXECUTION_LEASE_MISMATCH"


class OosExecutionStaleWriterError(OosExecutionError):
    """Raised when a worker tries to mutate an execution after lease expiry."""

    code = "OOS_EXECUTION_STALE_WRITER"


class OosExecutionNotRetryableError(OosExecutionError):
    """Raised when a FAILED or BLOCKED execution cannot be retried."""

    code = "OOS_EXECUTION_NOT_RETRYABLE"


class OosExecutionBlockedError(OosExecutionError):
    """Raised when an execution is permanently blocked by a safe failure."""

    code = "OOS_EXECUTION_BLOCKED"


class OosExecutionNotFoundError(OosExecutionError):
    """Raised when an execution identity does not exist."""

    code = "OOS_EXECUTION_NOT_FOUND"


class OosExecutionIntegrityError(OosExecutionError):
    """Raised when persisted execution or event data fails integrity checks."""

    code = "OOS_EXECUTION_INTEGRITY_ERROR"


class OosExecutionPersistenceError(OosExecutionError):
    """Raised when SQLite execution persistence fails safely."""

    code = "OOS_EXECUTION_PERSISTENCE_ERROR"


class OosProtocolStateError(OosExecutionError):
    """Raised when protocol governance does not permit execution control."""

    code = "OOS_PROTOCOL_STATE_INVALID"


class OosOfficialResultExistsError(OosExecutionError):
    """Raised when official OOS observation already exists for the protocol."""

    code = "OOS_OFFICIAL_RESULT_EXISTS"


class OosFinalizationError(OosExecutionError):
    """Raised when an internal OOS outcome cannot become an official result."""

    code = "OOS_FINALIZATION_ERROR"


class OosFinalizationConflictError(OosFinalizationError):
    """Raised when a protocol already has a different official result."""

    code = "OOS_FINALIZATION_CONFLICT"


class OosFinalizationStaleWriterError(OosFinalizationError):
    """Raised when a worker finalizes after losing its lease."""

    code = "OOS_FINALIZATION_STALE_WRITER"


class OosFinalizationPersistenceError(OosFinalizationError):
    """Raised when the atomic finalization transaction cannot be persisted."""

    code = "OOS_FINALIZATION_ATOMICITY_ERROR"
