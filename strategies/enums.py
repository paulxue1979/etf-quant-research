"""Enumerations used by Strategy Engine V1.0 domain models."""

from enum import StrEnum


class OperandType(StrEnum):
    """Supported value-reference types."""

    PRICE = "price"
    MA = "ma"
    EMA = "ema"
    CONSTANT = "constant"


class ComparisonOperator(StrEnum):
    """Supported condition comparison operators."""

    GREATER_THAN = "greater_than"
    GREATER_OR_EQUAL = "greater_or_equal"
    LESS_THAN = "less_than"
    LESS_OR_EQUAL = "less_or_equal"
    EQUAL = "equal"


class ThresholdType(StrEnum):
    """Threshold units supported by the model contract."""

    RELATIVE = "relative"
    ABSOLUTE = "absolute"


class LogicalOperator(StrEnum):
    """Boolean operators for recursive rule groups."""

    AND = "and"
    OR = "or"


class RebalanceFrequency(StrEnum):
    """Rebalance schedules supported by the strategy contract."""

    DAILY = "daily"
    WEEKLY = "weekly"
    MONTHLY = "monthly"
    ON_SIGNAL_CHANGE = "on_signal_change"


class StrategyStatus(StrEnum):
    """Lifecycle status reserved for versioned strategy definitions."""

    DRAFT = "draft"
    ACTIVE = "active"
    ARCHIVED = "archived"


class AllocationSource(StrEnum):
    """Provenance of the allocation carried by a strategy signal."""

    RULE_MATCH = "rule_match"
    FALLBACK = "fallback"
    HOLD_PREVIOUS = "hold_previous"


class NoMatchBehavior(StrEnum):
    """Strategy behavior when no allocation rule matches."""

    USE_FALLBACK = "use_fallback"
    HOLD_PREVIOUS_ALLOCATION = "hold_previous_allocation"


class StrategyEvaluationStatus(StrEnum):
    """Outcome of one point-in-time strategy evaluation."""

    EVALUATED = "evaluated"
    NOT_EVALUABLE = "not_evaluable"
    ERROR = "error"
