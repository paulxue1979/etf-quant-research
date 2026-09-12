from __future__ import annotations

from dataclasses import replace
from datetime import UTC, date, datetime

import pytest

from backtest import RebalanceFrequency
from data.models import PriceField
from research import (
    ExperimentSelectionMethod,
    SelectionEligibilityReasonCode,
    create_experiment_selection_decision,
)
from research.experiment_compatibility import CompatibilityDiagnostic, CompatibilityStatus
from research.experiment_read_model import ExperimentCandidateView, ExperimentResultStatus
from research.objective_evaluation import CandidateObjectiveEvaluation, ObjectiveEvaluationState
from strategies import (
    Allocation,
    AllocationRule,
    AssetReference,
    ComparisonOperator,
    Condition,
    FallbackAllocation,
    LogicalOperator,
    Operand,
    OperandType,
    RebalancePolicy,
    RuleGroup,
    StrategyDefinition,
    StrategyStatus,
    StrategyVersion,
    Threshold,
    ThresholdType,
)

HASH_A = "a" * 64
HASH_B = "b" * 64
IS_START = date(2020, 1, 2)
IS_END = date(2020, 12, 31)


def _experiment():
    from tests.unit.test_objective_evaluation import _experiment as make_experiment

    return make_experiment()


def _candidate(
    experiment,
    index: int = 0,
    status=ExperimentResultStatus.COMPLETED,
    derived_strategy_version_hash: str = HASH_A,
):
    completed = status is ExperimentResultStatus.COMPLETED
    return ExperimentCandidateView(
        experiment_id=experiment.experiment_id,
        protocol_id=experiment.protocol_id,
        candidate_id=f"candidate-{index}",
        candidate_index=index,
        parameter_set={"period": 20 + index},
        parameter_set_hash=HASH_A,
        candidate_set_hash=HASH_B,
        parameter_space_hash=experiment.parameter_space_hash,
        objective_spec_hash=experiment.objective_spec_hash,
        execution_status="completed"
        if completed
        else ("running" if status is ExperimentResultStatus.MISSING_RESULT else "failed"),
        result_status=status,
        experiment_result_id=f"result-{index}" if completed else None,
        result_hash=HASH_B if completed else None,
        backtest_run_id=f"run-{index}" if completed else None,
        base_strategy_version_id="base-v1",
        base_strategy_version_hash=HASH_A,
        derived_strategy_version_id=f"derived-{index}" if completed else None,
        derived_strategy_version_hash=derived_strategy_version_hash if completed else None,
        binding_hash=HASH_A if completed else None,
        is_start=IS_START if completed else None,
        is_end=IS_END if completed else None,
        price_field_used=PriceField.ADJUSTED_CLOSE if completed else None,
        backtest_configuration_hash=HASH_A if completed else None,
        engine_version="phase-3",
        analysis_version="phase-4i",
        data_snapshot_reference={"symbol": "QQQ"},
        performance_summary={"cagr": {"status": "available", "value": 0.1}} if completed else {},
    )


def _version(index: int = 0) -> StrategyVersion:
    definition = StrategyDefinition(
        strategy_id="strategy-1",
        name="Selection Test",
        description="test",
        assets=(AssetReference("QQQ"), AssetReference("SGOV")),
        price_field=PriceField.ADJUSTED_CLOSE,
        rules=(
            AllocationRule(
                "rule",
                "Rule",
                1,
                (Allocation("QQQ", 0.5),),
                condition=RuleGroup(
                    LogicalOperator.AND,
                    (
                        Condition(
                            Operand(
                                "QQQ", OperandType.PRICE, price_field=PriceField.ADJUSTED_CLOSE
                            ),
                            ComparisonOperator.GREATER_THAN,
                            Operand("QQQ", OperandType.MA, 20, PriceField.ADJUSTED_CLOSE),
                            Threshold(ThresholdType.RELATIVE, 0.01),
                        ),
                    ),
                ),
            ),
        ),
        fallback=FallbackAllocation((Allocation("SGOV", 1.0),)),
        rebalance_policy=RebalancePolicy(RebalanceFrequency.MONTHLY),
    )
    return StrategyVersion(
        "strategy-1",
        f"derived-{index}",
        1,
        datetime(2026, 9, 1, tzinfo=UTC),
        definition,
        status=StrategyStatus.ACTIVE,
    )


def _objective(candidate, experiment, state=ObjectiveEvaluationState.PASS):
    return CandidateObjectiveEvaluation(
        experiment_id=experiment.experiment_id,
        candidate_id=candidate.candidate_id,
        objective_hash=experiment.objective_spec_hash,
        experiment_result_id=candidate.experiment_result_id,
        result_hash=candidate.result_hash,
        constraint_results=(),
        overall_constraint_state=state,
    )


def _compat(experiment, status=CompatibilityStatus.COMPATIBLE):
    return CompatibilityDiagnostic(
        status, experiment.experiment_id, experiment.protocol_id, "candidate-0", (), (), (), ()
    )


def _make(**kwargs):
    experiment = _experiment()
    version = _version()
    candidate = _candidate(experiment, derived_strategy_version_hash=version.content_hash)
    values = dict(
        experiment=experiment,
        selected_candidate_id=candidate.candidate_id,
        candidates=(candidate,),
        objective_evaluation=_objective(candidate, experiment),
        compatibility=_compat(experiment),
        derived_strategy_version=version,
        selection_method=ExperimentSelectionMethod.RESEARCHER_JUDGMENT,
        researcher_rationale="Reviewed the IS evidence and selected this explicit candidate.",
        created_at=datetime(2026, 9, 12, tzinfo=UTC),
    )
    values.update(kwargs)
    return create_experiment_selection_decision(**values)


def test_explicit_human_selection_is_immutable_and_round_trips() -> None:
    decision = _make()
    assert decision.selected_candidate_id == "candidate-0"
    assert decision.selection_id.startswith("selection-")
    assert "score" not in decision.to_dict() and "rank" not in decision.to_dict()
    assert "oos" not in decision.canonical_json().lower()
    assert type(decision.evidence.data_snapshot_reference).__name__ == "mappingproxy"
    assert decision == type(decision).from_dict(decision.to_dict())


def test_semantic_hash_is_deterministic_and_excludes_created_at() -> None:
    first = _make(created_at=datetime(2026, 1, 1, tzinfo=UTC))
    second = _make(created_at=datetime(2030, 1, 1, tzinfo=UTC))
    assert first.selection_hash == second.selection_hash
    assert (
        _make(researcher_rationale="A different explicit reason").selection_hash
        != first.selection_hash
    )


@pytest.mark.parametrize(
    "status",
    [
        ExperimentResultStatus.FAILED,
        ExperimentResultStatus.MISSING_RESULT,
        ExperimentResultStatus.INCONSISTENT,
    ],
)
def test_non_completed_candidate_is_rejected(status) -> None:
    experiment = _experiment()
    candidate = _candidate(experiment, status=status)
    with pytest.raises(Exception) as error:
        _make(
            selected_candidate_id=candidate.candidate_id,
            candidates=(candidate,),
            objective_evaluation=_objective(candidate, experiment),
            derived_strategy_version=_version(),
        )
    assert (
        getattr(error.value, "code", "")
        == SelectionEligibilityReasonCode.CANDIDATE_NOT_TERMINAL.value
    )


def test_objective_and_compatibility_must_be_evaluable_and_passing() -> None:
    experiment = _experiment()
    candidate = _candidate(experiment)
    with pytest.raises(Exception) as error:
        _make(objective_evaluation=_objective(candidate, experiment, ObjectiveEvaluationState.FAIL))
    assert error.value.code == SelectionEligibilityReasonCode.OBJECTIVE_CONSTRAINT_FAILED.value
    with pytest.raises(Exception) as error:
        _make(compatibility=_compat(experiment, CompatibilityStatus.INCOMPATIBLE))
    assert error.value.code == SelectionEligibilityReasonCode.COMPATIBILITY_FAILED.value


def test_candidate_result_and_derived_strategy_bindings_are_exact() -> None:
    experiment = _experiment()
    candidate = _candidate(experiment)
    with pytest.raises(Exception) as error:
        _make(derived_strategy_version=_version(1))
    assert error.value.code == SelectionEligibilityReasonCode.DERIVED_STRATEGY_INTEGRITY_ERROR.value
    with pytest.raises(Exception) as error:
        _make(objective_evaluation=replace(_objective(candidate, experiment), result_hash=HASH_A))
    assert error.value.code == SelectionEligibilityReasonCode.RESULT_INTEGRITY_ERROR.value


def test_invalid_method_rationale_and_unknown_candidate_are_rejected() -> None:
    with pytest.raises(Exception):
        _make(selection_method="auto_best")
    with pytest.raises(Exception):
        _make(researcher_rationale="SECRET_SENTINEL_8E1D")
    with pytest.raises(Exception) as error:
        _make(selected_candidate_id="missing-candidate")
    assert error.value.code == SelectionEligibilityReasonCode.CANDIDATE_NOT_FOUND.value


def test_different_candidate_changes_identity_and_oos_cannot_enter_evidence() -> None:
    experiment = _experiment()
    first = _candidate(experiment, 0)
    second = _candidate(experiment, 1)
    second = replace(second, candidate_set_hash=HASH_B, derived_strategy_version_id="derived-1")
    with pytest.raises(Exception):
        _make(
            selected_candidate_id=second.candidate_id,
            candidates=(first, second),
            objective_evaluation=_objective(second, experiment),
            derived_strategy_version=_version(1),
            objective_evaluations={
                first.candidate_id: _objective(first, experiment),
                second.candidate_id: _objective(second, experiment),
            },
        )
    with pytest.raises(Exception):
        _make(candidates=(first,), researcher_rationale="OOS return changed my mind")


def test_candidate_universe_rejects_duplicate_ids_or_noncanonical_indices() -> None:
    experiment = _experiment()
    first = _candidate(experiment, 0)
    duplicate = replace(first, candidate_index=1)
    with pytest.raises(Exception, match="candidate IDs must be unique"):
        _make(candidates=(first, duplicate))

    second = _candidate(experiment, 2)
    with pytest.raises(Exception, match="canonical candidate order"):
        _make(candidates=(first, second))


def test_candidate_base_strategy_binding_is_required() -> None:
    experiment = _experiment()
    candidate = replace(_candidate(experiment), base_strategy_version_hash=HASH_B)
    with pytest.raises(Exception) as error:
        _make(candidates=(candidate,))
    assert error.value.code == SelectionEligibilityReasonCode.BASE_STRATEGY_BINDING_MISMATCH.value
