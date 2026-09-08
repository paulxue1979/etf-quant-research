"""Enumerations for the PHASE 8A experiment domain."""

from enum import StrEnum


class ExperimentStatus(StrEnum):
    """Experiment lifecycle; independent from Research Protocol lifecycle."""

    DRAFT = "draft"
    DEFINED = "defined"
    SPACE_FROZEN = "space_frozen"
    CANDIDATES_GENERATED = "candidates_generated"
    RUNNING = "running"
    COMPLETED = "completed"
    SELECTION_RECORDED = "selection_recorded"
    OOS_CONTAMINATED = "oos_contaminated"
    INVALID = "invalid"
    CLOSED = "closed"


class ExperimentMethod(StrEnum):
    """Declared experiment construction method, without executing it."""

    GRID = "grid"
    MANUAL = "manual"


class ParameterType(StrEnum):
    """Parameter value domains supported by the initial contract."""

    INTEGER = "integer"
    FLOAT = "float"
    ENUM = "enum"
    DISCRETE = "discrete"


class ConstraintOperator(StrEnum):
    """Safe, serializable binary parameter constraint operators."""

    LESS_THAN = "less_than"
    LESS_OR_EQUAL = "less_or_equal"
    GREATER_THAN = "greater_than"
    GREATER_OR_EQUAL = "greater_or_equal"
    EQUAL = "equal"
    NOT_EQUAL = "not_equal"


class MetricDirection(StrEnum):
    """Direction used by a future selection process."""

    MAXIMIZE = "maximize"
    MINIMIZE = "minimize"
