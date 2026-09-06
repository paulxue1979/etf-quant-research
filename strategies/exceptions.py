"""Domain errors for Strategy Engine V1.0 models."""


class StrategyModelError(ValueError):
    """Base error for invalid strategy domain model data."""


class InvalidAssetError(StrategyModelError):
    """Raised when an asset reference is malformed."""


class InvalidOperandError(StrategyModelError):
    """Raised when an operand has an invalid type, period, or field."""


class InvalidConditionError(StrategyModelError):
    """Raised when a condition structure is invalid."""


class InvalidThresholdError(StrategyModelError):
    """Raised when a threshold is malformed or non-finite."""


class InvalidRuleGroupError(StrategyModelError):
    """Raised when a recursive rule group is malformed."""


class InvalidAllocationError(StrategyModelError):
    """Raised when an allocation entry is malformed."""


class InvalidRuleError(StrategyModelError):
    """Raised when an allocation rule is malformed."""


class InvalidRebalancePolicyError(StrategyModelError):
    """Raised when a rebalance policy is malformed."""


class InvalidStrategyError(StrategyModelError):
    """Raised when a strategy definition is malformed."""


class InvalidStrategyVersionError(StrategyModelError):
    """Raised when a strategy version is malformed or hash-inconsistent."""
