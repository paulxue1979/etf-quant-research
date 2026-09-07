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


class EvaluationError(ValueError):
    """Base error for deterministic operand and condition evaluation failures."""

    code = "EVALUATION_ERROR"


class MissingMarketDataError(EvaluationError):
    """Raised when a requested asset or date is absent from the market context."""

    code = "MISSING_MARKET_DATA"


class MissingIndicatorError(EvaluationError):
    """Raised when a requested indicator series is absent from the context."""

    code = "MISSING_INDICATOR"


class MissingOperandValueError(EvaluationError):
    """Raised when an operand has no value on the requested date."""

    code = "MISSING_OPERAND_VALUE"


class InvalidEvaluationValueError(EvaluationError):
    """Raised when context data or an evaluated value is not finite."""

    code = "INVALID_NUMERIC_VALUE"


class ZeroReferenceValueError(EvaluationError):
    """Raised when a relative threshold reference is zero."""

    code = "ZERO_REFERENCE_VALUE"


class InvalidConditionConfigurationError(EvaluationError):
    """Raised when an operator and threshold combination is unsupported."""

    code = "INVALID_CONDITION_CONFIGURATION"


class RuleGroupEvaluationError(EvaluationError):
    """Raised when one or more children fail during full rule-group evaluation."""

    code = "RULE_GROUP_EVALUATION_ERROR"

    def __init__(
        self,
        message: str,
        *,
        rule_group_id: str,
        evaluation_date: object,
        child_errors: tuple[Exception, ...],
        evaluated_children: tuple[object, ...],
    ) -> None:
        super().__init__(message)
        self.rule_group_id = rule_group_id
        self.evaluation_date = evaluation_date
        self.child_errors = child_errors
        self.evaluated_children = evaluated_children


class AllocationResolutionError(EvaluationError):
    """Base error for deterministic target-allocation resolution failures."""

    code = "ALLOCATION_RESOLUTION_ERROR"


class InvalidAllocationConfigurationError(AllocationResolutionError):
    """Raised when allocation configuration violates resolver constraints."""

    code = "INVALID_ALLOCATION_CONFIGURATION"


class MissingRuleEvaluationError(AllocationResolutionError):
    """Raised when a conditional allocation rule has no supplied result."""

    code = "MISSING_RULE_EVALUATION"


class RuleEvaluationPropagationError(AllocationResolutionError):
    """Raised when an allocation rule's condition evaluation failed."""

    code = "RULE_EVALUATION_ERROR"

    def __init__(self, message: str, *, rule_id: str, cause: EvaluationError) -> None:
        super().__init__(message)
        self.rule_id = rule_id
        self.cause = cause
