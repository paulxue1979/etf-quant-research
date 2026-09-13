from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from backend.app.backtest_repository import BacktestRepository
from backend.app.candidate_execution_repository import CandidateExecutionRepository
from backend.app.experiment_result_repository import ExperimentResultRepository
from backend.app.experiment_results_read_service import ExperimentResultsReadService
from backend.app.experiment_selection_handoff_service import ExperimentSelectionHandoffService
from backend.app.experiment_selection_repository import ExperimentSelectionRepository
from backend.app.research_experiment_api import ResearcherSelectionRequest
from backend.app.research_protocol import ProtocolStatus, ResearchProtocolRepository
from backend.app.strategy_repository import StrategyRepository
from research import ExperimentStatus
from research.experiment_compatibility import CompatibilityStatus
from research.experiment_selection import ExperimentSelectionDecision
from research.objective_evaluation import ObjectiveEvaluationState, evaluate_candidate_objective
from tests.unit.test_experiment_selection_persistence import _decision_fixture


@pytest.fixture
def completed_chain(tmp_path: Path):
    database, decision, experiments = _decision_fixture(tmp_path)
    selections = ExperimentSelectionRepository(database)
    stored_selection = selections.create(decision)

    protocols = ResearchProtocolRepository(database)
    protocols.transition_protocol(decision.protocol_id, ProtocolStatus.IS_EVALUATED)
    handoff = ExperimentSelectionHandoffService(database)
    stored_decision, stored_freeze = handoff.handoff_experiment_selection(
        decision.experiment_id
    )
    return database, stored_selection, stored_decision, stored_freeze, experiments


def test_complete_experiment_selection_handoff_preserves_exact_identity_chain(
    completed_chain,
) -> None:
    database, selection, protocol_decision, freeze, experiments = completed_chain
    result = ExperimentResultRepository(database).get(selection.selected_experiment_result_id)
    assert result is not None
    derived = StrategyRepository(database).get_any_version(result.derived_strategy_version_id)
    assert derived is not None
    read_model = ExperimentResultsReadService(
        experiment_repository=experiments,
        candidate_execution_repository=CandidateExecutionRepository(database),
        experiment_result_repository=ExperimentResultRepository(database),
        backtest_repository=BacktestRepository(database),
        strategy_repository=StrategyRepository(database),
    ).get(selection.experiment_id)
    assert tuple(item.candidate_index for item in read_model.candidates) == (0,)
    candidate = read_model.candidates[0]
    assert candidate.experiment_result_id == result.experiment_result_id
    from backend.app.experiment_compatibility_service import ExperimentCompatibilityService

    compatibility = ExperimentCompatibilityService().diagnose(read_model)
    assert compatibility.status is CompatibilityStatus.COMPATIBLE
    objective = evaluate_candidate_objective(experiments.get(selection.experiment_id), candidate)
    assert objective.overall_constraint_state is ObjectiveEvaluationState.PASS

    assert experiments.get(selection.experiment_id).status is ExperimentStatus.SELECTION_RECORDED
    assert result.experiment_id == selection.experiment_id
    assert result.candidate_id == selection.selected_candidate_id
    assert result.parameter_set_hash == selection.selected_parameter_set_hash
    assert result.parameter_space_hash == selection.parameter_space_hash
    assert result.candidate_set_hash == selection.candidate_set_hash
    experiment = experiments.get(selection.experiment_id)
    assert experiment is not None
    assert result.base_strategy_version_id == experiment.base_strategy_version_id
    assert result.base_strategy_version_hash == experiment.base_strategy_version_hash
    assert result.derived_strategy_version_id == selection.selected_derived_strategy_version_id
    assert result.derived_strategy_version_hash == selection.selected_derived_strategy_content_hash

    assert protocol_decision.protocol_id == selection.protocol_id
    assert protocol_decision.selected_strategy_version_id == result.derived_strategy_version_id
    assert protocol_decision.is_backtest_run_ids == (result.backtest_run_id,)
    provenance = dict(protocol_decision.data_provenance)
    assert provenance["experiment_selection_id"] == selection.selection_id
    assert provenance["experiment_result_id"] == result.experiment_result_id
    assert provenance["result_hash"] == result.result_hash
    assert provenance["candidate_set_hash"] == result.candidate_set_hash
    assert provenance["parameter_space_hash"] == result.parameter_space_hash
    assert provenance["objective_hash"] == experiment.objective_spec_hash

    assert freeze.protocol_id == selection.protocol_id
    assert freeze.selection_decision_id == protocol_decision.decision_id
    assert freeze.strategy_version_id == result.derived_strategy_version_id
    assert freeze.strategy_version_content_hash == result.derived_strategy_version_hash
    assert freeze.strategy_version_id == derived.version_id
    assert freeze.strategy_version_content_hash == derived.content_hash


def test_selection_and_handoff_are_restart_safe_without_duplicate_governance_truth(
    completed_chain,
) -> None:
    database, selection, first_decision, first_freeze, experiments = completed_chain

    reopened = ExperimentSelectionHandoffService(database)
    second_decision, second_freeze = reopened.handoff_experiment_selection(selection.experiment_id)

    assert second_decision == first_decision
    assert second_freeze == first_freeze
    protocols = ResearchProtocolRepository(database)
    assert protocols.list_selections(selection.protocol_id) == (first_decision,)
    assert protocols.list_freezes(selection.protocol_id) == (first_freeze,)
    assert experiments.get(selection.experiment_id).status is ExperimentStatus.SELECTION_RECORDED
    events = experiments.list_events(selection.experiment_id)
    assert sum(event.event_type == "SELECTION_RECORDED" for event in events) == 1


def test_experiment_result_remains_thin_and_backtest_is_accounting_source_of_truth(
    completed_chain,
) -> None:
    database, selection, _, _, _ = completed_chain
    result = ExperimentResultRepository(database).get(selection.selected_experiment_result_id)
    assert result is not None
    run = BacktestRepository(database).get(result.backtest_run_id)
    assert run is not None
    assert run.strategy_version_id == result.derived_strategy_version_id
    assert run.backtest_result.start_date == result.is_start
    assert run.backtest_result.end_date == result.is_end

    result_payload = result.to_dict()
    for detailed_field in ("equity_curve", "orders", "fills", "trades", "portfolio_snapshots"):
        assert detailed_field not in result_payload
    stored_summary = dict(result_payload["performance_summary"])
    canonical_summary = run.performance_analysis.to_dict()
    for identity_field in ("backtest_run_id", "strategy_id"):
        stored_summary.pop(identity_field, None)
        canonical_summary.pop(identity_field, None)
    assert stored_summary == canonical_summary


def test_is_only_firewall_has_no_oos_observation_or_oos_selection_evidence(
    completed_chain,
) -> None:
    database, selection, protocol_decision, _, _ = completed_chain
    protocols = ResearchProtocolRepository(database)
    assert protocols.list_oos_evaluations(selection.protocol_id) == ()
    assert protocols.get_protocol(selection.protocol_id).status == ProtocolStatus.SELECTION_RECORDED

    result = ExperimentResultRepository(database).get(selection.selected_experiment_result_id)
    assert result is not None
    evidence_payload = selection.evidence.to_dict()
    result_payload = result.to_dict()
    for payload in (evidence_payload, result_payload):
        assert not any("oos" in str(key).lower() for key in payload)
        assert not any("oos" in str(value).lower() for value in payload.values())

    provenance = dict(protocol_decision.data_provenance)
    assert provenance["is_start_date"] == result.is_start.isoformat()
    assert provenance["is_end_date"] == result.is_end.isoformat()
    assert "oos_start_date" not in provenance
    assert "oos_end_date" not in provenance


def test_research_selection_request_cannot_inject_server_resolved_identity() -> None:
    request = ResearcherSelectionRequest.model_validate(
        {
            "selected_candidate_id": "candidate-0",
            "selection_method": "researcher_judgment",
            "researcher_rationale": "Reviewed the frozen IS evidence.",
        }
    )
    assert set(request.model_fields_set) == {
        "selected_candidate_id",
        "selection_method",
        "researcher_rationale",
    }
    with pytest.raises(ValidationError):
        ResearcherSelectionRequest.model_validate(
            {
                "selected_candidate_id": "candidate-0",
                "selection_method": "researcher_judgment",
                "researcher_rationale": "Reviewed the frozen IS evidence.",
                "strategy_version_id": "caller-controlled",
                "selected_result_hash": "caller-controlled",
                "oos_start_date": "2099-01-01",
            }
        )


def test_integrity_audit_scope_does_not_add_ranking_or_recommendation_fields() -> None:
    source_files = (
        Path("backend/app/research_experiment_api.py"),
        Path("research/experiment_selection.py"),
        Path("frontend/src/research/types.ts"),
    )
    forbidden_response_fields = {"rank", "winner", "best", "recommended", "recommendation"}
    for path in source_files:
        source = path.read_text()
        assert not any(f'"{field}"' in source for field in forbidden_response_fields)

    assert ExperimentSelectionDecision.__doc__
