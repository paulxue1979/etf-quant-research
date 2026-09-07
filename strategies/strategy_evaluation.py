"""Deterministic, no-lookahead orchestration for PHASE 4G."""

from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date

from data.exceptions import DataValidationError
from data.models import HistoricalDataSet, PriceField
from data.validation import validate_historical_data
from indicators.models import IndicatorPoint, IndicatorSeries
from strategies.allocation_resolver import resolve_allocations
from strategies.enums import LogicalOperator, OperandType, StrategyEvaluationStatus
from strategies.evaluation import (
    ConditionResult,
    EvaluationContext,
    IndicatorKey,
    OperandValue,
    RuleGroupResult,
    TargetAllocationResult,
)
from strategies.exceptions import EvaluationError, StrategyEvaluationInputError
from strategies.models import Condition, RuleGroup, RuleNode, StrategyVersion
from strategies.rule_group_evaluator import evaluate_rule_group
from strategies.signal_engine import StrategySignal, build_signal
from strategies.validation import validate_strategy_definition


@dataclass(frozen=True)
class EvaluationFailure:
    """A serializable summary of an evaluation failure without sensitive data."""

    code: str
    message: str

    def __post_init__(self) -> None:
        if not isinstance(self.code, str) or not self.code.strip():
            raise ValueError("evaluation failure code must not be empty")
        if not isinstance(self.message, str) or not self.message.strip():
            raise ValueError("evaluation failure message must not be empty")
        object.__setattr__(self, "code", self.code.strip())
        object.__setattr__(self, "message", self.message.strip())

    def to_dict(self) -> dict[str, str]:
        return {"code": self.code, "message": self.message}


@dataclass(frozen=True)
class StrategyEvaluationResult:
    """One immutable, explainable strategy outcome for a real market-data date."""

    date: date
    strategy_version_id: str
    status: StrategyEvaluationStatus
    rule_group_results: tuple[RuleGroupResult, ...]
    target_allocation: TargetAllocationResult | None
    signal: StrategySignal | None
    explanation: str
    failure: EvaluationFailure | None = None
    source_data_reference: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.date, date):
            raise TypeError("evaluation date must be a date")
        if not isinstance(self.strategy_version_id, str) or not self.strategy_version_id.strip():
            raise ValueError("strategy_version_id must not be empty")
        if not isinstance(self.status, StrategyEvaluationStatus):
            object.__setattr__(self, "status", StrategyEvaluationStatus(self.status))
        results = tuple(self.rule_group_results)
        if not all(isinstance(result, RuleGroupResult) for result in results):
            raise TypeError("rule_group_results must contain RuleGroupResult values")
        if len({result.rule_group_id for result in results}) != len(results):
            raise ValueError("rule_group_results must have unique rule_group_id values")
        if not all(result.date == self.date for result in results):
            raise ValueError("rule_group_results must match the evaluation date")
        if not isinstance(self.explanation, str) or not self.explanation.strip():
            raise ValueError("evaluation explanation must not be empty")
        if self.failure is not None and not isinstance(self.failure, EvaluationFailure):
            raise TypeError("failure must be an EvaluationFailure or None")
        if self.source_data_reference is not None and (
            not isinstance(self.source_data_reference, str)
            or not self.source_data_reference.strip()
        ):
            raise ValueError("source_data_reference must be a non-empty string or None")

        if self.status is StrategyEvaluationStatus.EVALUATED:
            if self.target_allocation is None or self.signal is None or self.failure is not None:
                raise ValueError("evaluated results require allocation and signal without failure")
        elif self.target_allocation is not None or self.signal is not None or self.failure is None:
            raise ValueError("non-evaluated results require a failure without allocation or signal")

        if self.target_allocation is not None and self.target_allocation.date != self.date:
            raise ValueError("target_allocation must match the evaluation date")
        if self.signal is not None:
            if self.signal.date != self.date:
                raise ValueError("signal must match the evaluation date")
            if self.signal.strategy_version_id != self.strategy_version_id:
                raise ValueError("signal strategy version must match the evaluation result")
            if self.target_allocation != self.signal.target_allocation:
                raise ValueError(
                    "target_allocation must match the allocation carried by the signal"
                )

        object.__setattr__(self, "strategy_version_id", self.strategy_version_id.strip())
        object.__setattr__(self, "rule_group_results", results)
        if self.source_data_reference is not None:
            object.__setattr__(self, "source_data_reference", self.source_data_reference.strip())

    @property
    def matched_rule_id(self) -> str | None:
        """Expose the selected rule without inventing a portfolio state."""
        return self.signal.matched_rule_id if self.signal is not None else None

    def to_dict(self) -> dict[str, object]:
        return {
            "date": self.date.isoformat(),
            "strategy_version_id": self.strategy_version_id,
            "status": self.status.value,
            "rule_group_results": [
                _rule_group_to_dict(result) for result in self.rule_group_results
            ],
            "target_allocation": (
                _target_allocation_to_dict(self.target_allocation)
                if self.target_allocation is not None
                else None
            ),
            "signal": self.signal.to_dict() if self.signal is not None else None,
            "explanation": self.explanation,
            "failure": self.failure.to_dict() if self.failure is not None else None,
            "source_data_reference": self.source_data_reference,
        }


@dataclass(frozen=True)
class StrategyEvaluationTimeline:
    """A deterministic, immutable sequence of one strategy version's evaluations."""

    strategy_version_id: str
    start_date: date
    end_date: date
    evaluations: tuple[StrategyEvaluationResult, ...]
    source_data_reference: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.strategy_version_id, str) or not self.strategy_version_id.strip():
            raise ValueError("strategy_version_id must not be empty")
        if not isinstance(self.start_date, date) or not isinstance(self.end_date, date):
            raise TypeError("timeline bounds must be date values")
        if self.start_date > self.end_date:
            raise ValueError("start_date must be on or before end_date")
        evaluations = tuple(self.evaluations)
        if not all(isinstance(item, StrategyEvaluationResult) for item in evaluations):
            raise TypeError("evaluations must contain StrategyEvaluationResult values")
        dates = tuple(item.date for item in evaluations)
        if dates != tuple(sorted(dates)) or len(dates) != len(set(dates)):
            raise ValueError("timeline evaluation dates must be sorted and unique")
        if not all(self.start_date <= item.date <= self.end_date for item in evaluations):
            raise ValueError("timeline evaluations must fall within its requested date range")
        if not all(item.strategy_version_id == self.strategy_version_id for item in evaluations):
            raise ValueError("timeline evaluations must preserve strategy_version_id")
        if self.source_data_reference is not None and (
            not isinstance(self.source_data_reference, str)
            or not self.source_data_reference.strip()
        ):
            raise ValueError("source_data_reference must be a non-empty string or None")

        object.__setattr__(self, "strategy_version_id", self.strategy_version_id.strip())
        object.__setattr__(self, "evaluations", evaluations)
        if self.source_data_reference is not None:
            object.__setattr__(self, "source_data_reference", self.source_data_reference.strip())

    def to_dict(self) -> dict[str, object]:
        return {
            "strategy_version_id": self.strategy_version_id,
            "start_date": self.start_date.isoformat(),
            "end_date": self.end_date.isoformat(),
            "evaluations": [item.to_dict() for item in self.evaluations],
            "source_data_reference": self.source_data_reference,
        }


def evaluate_strategy(
    strategy_version: StrategyVersion,
    context: EvaluationContext,
    start_date: date,
    end_date: date,
    *,
    source_data_reference: str | None = None,
) -> StrategyEvaluationTimeline:
    """Evaluate a strategy version over aligned real input dates without lookahead.

    The caller owns data and indicator preparation. This function uses exact date
    matches only, never fills dates or derives values from future observations.
    """
    _validate_inputs(strategy_version, context, start_date, end_date, source_data_reference)
    strategy = strategy_version.configuration
    asset_data = _required_asset_data(strategy_version, context)
    candidate_dates = _aligned_dates(asset_data, start_date, end_date)
    mismatch = _price_field_mismatch(strategy.price_field, asset_data)
    requirements = _indicator_requirements(strategy_version)

    evaluations = tuple(
        _evaluate_date(
            strategy_version,
            context,
            as_of_date,
            requirements,
            mismatch,
            source_data_reference,
        )
        for as_of_date in candidate_dates
    )
    return StrategyEvaluationTimeline(
        strategy_version_id=strategy_version.version_id,
        start_date=start_date,
        end_date=end_date,
        evaluations=evaluations,
        source_data_reference=source_data_reference,
    )


def _validate_inputs(
    strategy_version: StrategyVersion,
    context: EvaluationContext,
    start_date: date,
    end_date: date,
    source_data_reference: str | None,
) -> None:
    if not isinstance(strategy_version, StrategyVersion):
        raise StrategyEvaluationInputError("strategy_version must be a StrategyVersion")
    if not isinstance(context, EvaluationContext):
        raise StrategyEvaluationInputError("context must be an EvaluationContext")
    if not isinstance(start_date, date) or not isinstance(end_date, date):
        raise StrategyEvaluationInputError("start_date and end_date must be date values")
    if start_date > end_date:
        raise StrategyEvaluationInputError("start_date must be on or before end_date")
    if source_data_reference is not None and (
        not isinstance(source_data_reference, str) or not source_data_reference.strip()
    ):
        raise StrategyEvaluationInputError(
            "source_data_reference must be a non-empty string or None"
        )
    validation = validate_strategy_definition(strategy_version.configuration)
    if not validation.is_valid:
        details = "; ".join(f"{item.code}: {item.message}" for item in validation.errors)
        raise StrategyEvaluationInputError(f"strategy definition is invalid: {details}")
    for dataset in context.market_data.values():
        try:
            validate_historical_data(dataset)
        except DataValidationError as exc:
            raise StrategyEvaluationInputError(f"market data is invalid: {exc}") from exc
    for series in context.indicators.values():
        _validate_indicator_series(series)


def _required_asset_data(
    strategy_version: StrategyVersion, context: EvaluationContext
) -> dict[str, HistoricalDataSet]:
    required: dict[str, HistoricalDataSet] = {}
    for asset in strategy_version.configuration.assets:
        dataset = context.market_data.get(asset.symbol)
        if dataset is None:
            raise StrategyEvaluationInputError(
                f"market data is missing for declared asset {asset.symbol}"
            )
        required[asset.symbol] = dataset
    return required


def _aligned_dates(
    asset_data: Mapping[str, HistoricalDataSet], start_date: date, end_date: date
) -> tuple[date, ...]:
    date_sets = [{point.date for point in dataset.points} for dataset in asset_data.values()]
    common = set.intersection(*date_sets)
    return tuple(sorted(day for day in common if start_date <= day <= end_date))


def _price_field_mismatch(
    expected: PriceField, asset_data: Mapping[str, HistoricalDataSet]
) -> EvaluationFailure | None:
    mismatches = sorted(
        symbol
        for symbol, dataset in asset_data.items()
        if dataset.request.price_field_used is not expected
    )
    if not mismatches:
        return None
    return EvaluationFailure(
        "PRICE_FIELD_MISMATCH",
        f"strategy requires {expected.value}; input data uses a different price field for "
        + ", ".join(mismatches),
    )


def _indicator_requirements(strategy_version: StrategyVersion) -> tuple[IndicatorKey, ...]:
    requirements: set[IndicatorKey] = set()
    for rule in strategy_version.configuration.rules:
        if rule.condition is None:
            continue
        for operand in _operands(rule.condition):
            if operand.operand_type in (OperandType.MA, OperandType.EMA):
                requirements.add(
                    IndicatorKey(
                        operand.asset,
                        "ma" if operand.operand_type is OperandType.MA else "ema",
                        operand.period,
                        operand.price_field,
                    )
                )
    return tuple(sorted(requirements, key=lambda item: (item.symbol, item.kind.value, item.period)))


def required_indicators(strategy_version: StrategyVersion) -> tuple[IndicatorKey, ...]:
    """Return the indicator inputs a caller must prepare before evaluation."""
    if not isinstance(strategy_version, StrategyVersion):
        raise StrategyEvaluationInputError("strategy_version must be a StrategyVersion")
    return _indicator_requirements(strategy_version)


def _operands(node: RuleNode) -> tuple[object, ...]:
    if isinstance(node, Condition):
        return (node.left, node.right)
    return tuple(operand for child in node.children for operand in _operands(child))


def _evaluate_date(
    strategy_version: StrategyVersion,
    context: EvaluationContext,
    as_of_date: date,
    requirements: tuple[IndicatorKey, ...],
    price_field_mismatch: EvaluationFailure | None,
    source_data_reference: str | None,
) -> StrategyEvaluationResult:
    if price_field_mismatch is not None:
        return _failure_result(
            strategy_version,
            as_of_date,
            StrategyEvaluationStatus.ERROR,
            price_field_mismatch,
            source_data_reference,
        )

    availability = _indicator_availability(context, requirements, as_of_date)
    if availability is not None:
        status, failure = availability
        return _failure_result(strategy_version, as_of_date, status, failure, source_data_reference)

    rule_results: dict[str, RuleGroupResult] = {}
    try:
        for rule in strategy_version.configuration.rules:
            if rule.condition is None:
                continue
            group = _as_rule_group(rule.condition)
            rule_results[rule.rule_id] = evaluate_rule_group(
                group, context, as_of_date, rule_group_id=rule.rule_id
            )
        target_allocation = resolve_allocations(
            strategy_version.configuration, rule_results, as_of_date
        )
        aggregate = _aggregate_rule_results(as_of_date, rule_results)
        signal = build_signal(
            strategy_version,
            aggregate,
            target_allocation,
            source_data_reference=source_data_reference,
        )
    except EvaluationError as exc:
        return _failure_result(
            strategy_version,
            as_of_date,
            StrategyEvaluationStatus.ERROR,
            EvaluationFailure(exc.code, str(exc)),
            source_data_reference,
            tuple(rule_results.values()),
        )

    ordered_results = tuple(
        rule_results[rule.rule_id]
        for rule in strategy_version.configuration.rules
        if rule.condition is not None
    )
    return StrategyEvaluationResult(
        date=as_of_date,
        strategy_version_id=strategy_version.version_id,
        status=StrategyEvaluationStatus.EVALUATED,
        rule_group_results=ordered_results,
        target_allocation=target_allocation,
        signal=signal,
        explanation=(
            f"Strategy evaluation completed for {as_of_date.isoformat()}; "
            f"{target_allocation.explanation}"
        ),
        source_data_reference=source_data_reference,
    )


def _indicator_availability(
    context: EvaluationContext, requirements: tuple[IndicatorKey, ...], as_of_date: date
) -> tuple[StrategyEvaluationStatus, EvaluationFailure] | None:
    for key in requirements:
        series = context.indicators.get(key)
        if series is None:
            available_fields = _indicator_price_fields(context, key)
            if available_fields:
                field_names = ", ".join(field.value for field in available_fields)
                return (
                    StrategyEvaluationStatus.ERROR,
                    EvaluationFailure(
                        "PRICE_FIELD_MISMATCH",
                        f"{key.kind.value.upper()}({key.symbol},{key.period}) uses "
                        f"[{field_names}], expected {key.price_field.value}",
                    ),
                )
            return (
                StrategyEvaluationStatus.ERROR,
                EvaluationFailure(
                    "MISSING_INDICATOR",
                    f"{key.kind.value.upper()}({key.symbol},{key.period}) is missing from the "
                    "evaluation context",
                ),
            )
        point = next((item for item in series.points if item.date == as_of_date), None)
        if point is None:
            return (
                StrategyEvaluationStatus.ERROR,
                EvaluationFailure(
                    "MISSING_INDICATOR_DATE",
                    f"{key.kind.value.upper()}({key.symbol},{key.period}) has no point on "
                    f"{as_of_date.isoformat()}",
                ),
            )
        if point.value is None:
            return (
                StrategyEvaluationStatus.NOT_EVALUABLE,
                EvaluationFailure(
                    "INDICATOR_WARMUP",
                    f"{key.kind.value.upper()}({key.symbol},{key.period}) is unavailable on "
                    f"{as_of_date.isoformat()} because its window is not formed",
                ),
            )
    return None


def _indicator_price_fields(
    context: EvaluationContext, requirement: IndicatorKey
) -> tuple[PriceField, ...]:
    """Return alternative price fields for the same configured indicator."""
    fields = {
        key.price_field
        for key in context.indicators
        if (
            key.symbol == requirement.symbol
            and key.kind is requirement.kind
            and key.period == requirement.period
            and key.price_field is not requirement.price_field
        )
    }
    return tuple(sorted(fields, key=lambda field: field.value))


def _as_rule_group(node: RuleNode) -> RuleGroup:
    if isinstance(node, RuleGroup):
        return node
    return RuleGroup(LogicalOperator.AND, (node,))


def _aggregate_rule_results(
    as_of_date: date, rule_results: Mapping[str, RuleGroupResult]
) -> RuleGroupResult | None:
    results = tuple(rule_results.values())
    if not results:
        return None
    passed = any(result.passed for result in results)
    summary = "; ".join(
        f"{result.rule_group_id}={'TRUE' if result.passed else 'FALSE'}" for result in results
    )
    return RuleGroupResult(
        rule_group_id="strategy-conditions",
        date=as_of_date,
        operator=LogicalOperator.OR,
        passed=passed,
        child_results=results,
        explanation=(
            "Strategy conditional-rule aggregate (OR; allocation priority is resolved "
            f"separately): {summary}; result={'TRUE' if passed else 'FALSE'}"
        ),
    )


def _failure_result(
    strategy_version: StrategyVersion,
    as_of_date: date,
    status: StrategyEvaluationStatus,
    failure: EvaluationFailure,
    source_data_reference: str | None,
    rule_group_results: tuple[RuleGroupResult, ...] = (),
) -> StrategyEvaluationResult:
    return StrategyEvaluationResult(
        date=as_of_date,
        strategy_version_id=strategy_version.version_id,
        status=status,
        rule_group_results=rule_group_results,
        target_allocation=None,
        signal=None,
        explanation=(
            f"Strategy evaluation {status.value} for {as_of_date.isoformat()}: {failure.code}"
        ),
        failure=failure,
        source_data_reference=source_data_reference,
    )


def _validate_indicator_series(series: IndicatorSeries) -> None:
    previous: date | None = None
    seen: set[date] = set()
    for point in series.points:
        if not isinstance(point, IndicatorPoint) or not isinstance(point.date, date):
            raise StrategyEvaluationInputError("indicator series contains an invalid date point")
        if point.date in seen or (previous is not None and point.date <= previous):
            raise StrategyEvaluationInputError("indicator series dates must be strictly ascending")
        if point.value is not None and (
            isinstance(point.value, bool)
            or not isinstance(point.value, (int, float))
            or not math.isfinite(float(point.value))
        ):
            raise StrategyEvaluationInputError("indicator series values must be finite or None")
        seen.add(point.date)
        previous = point.date


def _rule_group_to_dict(result: RuleGroupResult) -> dict[str, object]:
    return {
        "rule_group_id": result.rule_group_id,
        "date": result.date.isoformat(),
        "operator": result.operator.value,
        "passed": result.passed,
        "child_results": [
            _condition_to_dict(child)
            if isinstance(child, ConditionResult)
            else _rule_group_to_dict(child)
            for child in result.child_results
        ],
        "explanation": result.explanation,
    }


def _condition_to_dict(result: ConditionResult) -> dict[str, object]:
    return {
        "condition_id": result.condition_id,
        "date": result.date.isoformat(),
        "passed": result.passed,
        "left_operand_result": _operand_to_dict(result.left_operand_result),
        "right_operand_result": _operand_to_dict(result.right_operand_result),
        "operator": result.operator.value,
        "threshold": result.threshold.to_dict() if result.threshold is not None else None,
        "effective_right_value": result.effective_right_value,
        "explanation": result.explanation,
    }


def _operand_to_dict(result: OperandValue) -> dict[str, object]:
    return {
        "value": result.value,
        "asset": result.asset,
        "operand_type": result.operand_type.value,
        "price_field_used": (
            result.price_field_used.value if result.price_field_used is not None else None
        ),
        "date": result.date.isoformat(),
    }


def _target_allocation_to_dict(result: TargetAllocationResult) -> dict[str, object]:
    return {
        "date": result.date.isoformat(),
        "matched_rule_id": result.matched_rule_id,
        "used_fallback": result.used_fallback,
        "allocations": [item.to_dict() for item in result.allocations],
        "remaining_weight": result.remaining_weight,
        "cash_buffer": result.cash_buffer,
        "explanation": result.explanation,
    }


__all__ = [
    "EvaluationFailure",
    "StrategyEvaluationResult",
    "StrategyEvaluationTimeline",
    "evaluate_strategy",
    "required_indicators",
]
