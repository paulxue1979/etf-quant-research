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
