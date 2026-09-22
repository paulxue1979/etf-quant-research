"""Generic deterministic regime state-machine evaluation for PHASE 11D."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date
from typing import Any

from strategies.allocation_resolver import resolve_regime_allocation
from strategies.enums import LogicalOperator, StrategyEvaluationMode, StrategyEvaluationStatus
from strategies.evaluation import (
    EvaluationContext,
    RuleGroupResult,
    TargetAllocationResult,
)
from strategies.exceptions import EvaluationError, StrategyEvaluationInputError
from strategies.models import (
    AllocationSpecification,
    RegimeDefinition,
    RegimeTransitionDefinition,
    RuleGroup,
    StrategyVersion,
)
from strategies.rule_group_evaluator import evaluate_rule_group
from strategies.signal_engine import build_signal
from strategies.strategy_evaluation import (
    EvaluationFailure,
    StrategyEvaluationResult,
    StrategyEvaluationTimeline,
    rule_group_to_dict,
)
from strategies.validation import validate_strategy_definition


@dataclass(frozen=True)
class RegimeRuntimeState:
    """Run-local mutable-by-replacement state; never written to StrategyVersion."""

    current_state_id: str
    state_entry_date: date
    previous_state_id: str | None
    last_transition_id: str | None
    transition_count: int
    current_target_allocation: AllocationSpecification

    def to_dict(self) -> dict[str, object]:
        return {
            "current_state_id": self.current_state_id,
            "state_entry_date": self.state_entry_date.isoformat(),
            "previous_state_id": self.previous_state_id,
            "last_transition_id": self.last_transition_id,
            "transition_count": self.transition_count,
            "current_target_allocation": self.current_target_allocation.to_dict(),
        }


@dataclass(frozen=True)
class RegimeTransitionEvidence:
    """One outgoing edge's evaluated condition tree."""

    transition_id: str
    passed: bool
    result: RuleGroupResult

    def to_dict(self) -> dict[str, object]:
        return {
            "transition_id": self.transition_id,
            "passed": self.passed,
            "condition_evidence": rule_group_to_dict(self.result),
        }


@dataclass(frozen=True)
class RegimeTransitionEvent:
    """Immutable provenance emitted only when the active state changes."""

    evaluation_date: date
    transition_id: str
    from_state: str
    to_state: str
    state_entry_date: date
    evidence: tuple[RegimeTransitionEvidence, ...]
    target_allocation: TargetAllocationResult
    strategy_version_id: str
    strategy_version_hash: str
    transition_definition_hash: str

    def to_dict(self) -> dict[str, object]:
        return {
            "evaluation_date": self.evaluation_date.isoformat(),
            "transition_id": self.transition_id,
            "from_state": self.from_state,
            "to_state": self.to_state,
            "state_entry_date": self.state_entry_date.isoformat(),
            "evaluated_evidence": [item.to_dict() for item in self.evidence],
            "target_allocation": _target_allocation_to_dict(self.target_allocation),
            "strategy_version_id": self.strategy_version_id,
            "strategy_version_hash": self.strategy_version_hash,
            "transition_definition_hash": self.transition_definition_hash,
        }


def evaluate_regime_strategy(
    strategy_version: StrategyVersion,
    context: EvaluationContext,
    start_date: date,
    end_date: date,
    *,
    source_data_reference: str | None = None,
) -> StrategyEvaluationTimeline:
    """Evaluate at most one outgoing transition from the current state per date."""
    if (
        strategy_version.configuration.strategy_mode
        is not StrategyEvaluationMode.REGIME_STATE_MACHINE
    ):
        raise StrategyEvaluationInputError("strategy is not a regime state-machine strategy")
    validation = validate_strategy_definition(strategy_version.configuration)
    if not validation.is_valid:
        details = "; ".join(f"{item.code}: {item.message}" for item in validation.errors)
        raise StrategyEvaluationInputError(f"strategy definition is invalid: {details}")
    dates = _aligned_dates(context.market_data, start_date, end_date)
    if not dates:
        return StrategyEvaluationTimeline(
            strategy_version.version_id,
            start_date,
            end_date,
            (),
            source_data_reference,
        )
    regimes = {regime.state_id: regime for regime in strategy_version.configuration.regimes}
    initial = regimes[strategy_version.configuration.initial_regime]
    runtime = RegimeRuntimeState(
        current_state_id=initial.state_id,
        state_entry_date=dates[0],
        previous_state_id=None,
        last_transition_id=None,
        transition_count=0,
        current_target_allocation=initial.target_allocation,
    )
    evaluations: list[StrategyEvaluationResult] = []
    for as_of_date in dates:
        evaluation, runtime = _evaluate_date(
            strategy_version,
            context,
            as_of_date,
            runtime,
            regimes,
            source_data_reference,
        )
        evaluations.append(evaluation)
    return StrategyEvaluationTimeline(
        strategy_version.version_id,
        start_date,
        end_date,
        tuple(evaluations),
        source_data_reference,
    )


def _evaluate_date(
    strategy_version: StrategyVersion,
    context: EvaluationContext,
    as_of_date: date,
    runtime: RegimeRuntimeState,
    regimes: Mapping[str, RegimeDefinition],
    source_data_reference: str | None,
) -> tuple[StrategyEvaluationResult, RegimeRuntimeState]:
    strategy = strategy_version.configuration
    outgoing = tuple(
        sorted(
            (item for item in strategy.transitions if item.from_state == runtime.current_state_id),
            key=lambda item: (item.priority, item.transition_id),
        )
    )
    evidence: list[RegimeTransitionEvidence] = []
    try:
        for transition in outgoing:
            node = (
                transition.condition
                if isinstance(transition.condition, RuleGroup)
                else RuleGroup(LogicalOperator.AND, (transition.condition,))
            )
            result = evaluate_rule_group(
                node,
                context,
                as_of_date,
                rule_group_id=f"transition:{transition.transition_id}",
            )
            evidence.append(
                RegimeTransitionEvidence(transition.transition_id, result.passed, result)
            )
    except EvaluationError as exc:
        failure = EvaluationFailure(exc.code, str(exc))
        return (
            StrategyEvaluationResult(
                date=as_of_date,
                strategy_version_id=strategy_version.version_id,
                status=StrategyEvaluationStatus.NOT_EVALUABLE,
                rule_group_results=tuple(item.result for item in evidence),
                target_allocation=None,
                signal=None,
                explanation=f"Regime evaluation is not evaluable: {failure.code}",
                failure=failure,
                source_data_reference=source_data_reference,
            ),
            runtime,
        )

    selected = next((item for item in evidence if item.passed), None)
    event: RegimeTransitionEvent | None = None
    if selected is not None:
        transition = next(item for item in outgoing if item.transition_id == selected.transition_id)
        next_regime = regimes[transition.to_state]
        target = resolve_regime_allocation(
            strategy,
            next_regime.state_id,
            next_regime.target_allocation,
            as_of_date,
        )
        event = RegimeTransitionEvent(
            evaluation_date=as_of_date,
            transition_id=transition.transition_id,
            from_state=runtime.current_state_id,
            to_state=next_regime.state_id,
            state_entry_date=as_of_date,
            evidence=tuple(evidence),
            target_allocation=target,
            strategy_version_id=strategy_version.version_id,
            strategy_version_hash=strategy_version.content_hash or "",
            transition_definition_hash=_transition_hash(transition),
        )
        runtime = RegimeRuntimeState(
            current_state_id=next_regime.state_id,
            state_entry_date=as_of_date,
            previous_state_id=runtime.current_state_id,
            last_transition_id=transition.transition_id,
            transition_count=runtime.transition_count + 1,
            current_target_allocation=next_regime.target_allocation,
        )
    else:
        current = regimes[runtime.current_state_id]
        target = resolve_regime_allocation(
            strategy,
            current.state_id,
            current.target_allocation,
            as_of_date,
        )

    condition_result = _aggregate_evidence(as_of_date, tuple(item.result for item in evidence))
    provenance = {
        "active_state_id": runtime.current_state_id,
        "state_entry_date": runtime.state_entry_date.isoformat(),
        "previous_state_id": runtime.previous_state_id,
        "last_transition_id": runtime.last_transition_id,
        "transition_count": runtime.transition_count,
        "evaluated_transitions": [item.to_dict() for item in evidence],
        "transition_event": event.to_dict() if event is not None else None,
    }
    signal = build_signal(
        strategy_version,
        condition_result,
        target,
        source_data_reference=source_data_reference,
        regime_provenance=provenance,
    )
    return (
        StrategyEvaluationResult(
            date=as_of_date,
            strategy_version_id=strategy_version.version_id,
            status=StrategyEvaluationStatus.EVALUATED,
            rule_group_results=tuple(item.result for item in evidence),
            target_allocation=target,
            signal=signal,
            explanation=(
                f"Regime {runtime.current_state_id} active on {as_of_date.isoformat()}; "
                f"transition={'none' if event is None else event.transition_id}"
            ),
            source_data_reference=source_data_reference,
            regime_provenance=provenance,
        ),
        runtime,
    )


def _aggregate_evidence(
    as_of_date: date, results: tuple[RuleGroupResult, ...]
) -> RuleGroupResult | None:
    if not results:
        return None
    return RuleGroupResult(
        rule_group_id="regime-transitions",
        date=as_of_date,
        operator=LogicalOperator.OR,
        passed=any(result.passed for result in results),
        child_results=results,
        explanation="; ".join(f"{result.rule_group_id}={result.passed}" for result in results),
    )


def _aligned_dates(
    market_data: Mapping[str, Any], start_date: date, end_date: date
) -> tuple[date, ...]:
    date_sets = [{point.date for point in dataset.points} for dataset in market_data.values()]
    if not date_sets:
        return ()
    common = set.intersection(*date_sets)
    return tuple(sorted(day for day in common if start_date <= day <= end_date))


def _transition_hash(transition: RegimeTransitionDefinition) -> str:
    payload = json.dumps(transition.to_dict(), sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


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
    "RegimeRuntimeState",
    "RegimeTransitionEvent",
    "RegimeTransitionEvidence",
    "evaluate_regime_strategy",
]
