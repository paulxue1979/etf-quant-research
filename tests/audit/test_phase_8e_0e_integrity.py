from __future__ import annotations

import sqlite3
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace

import pytest

from backend.app.backtest_repository import BacktestRepository
from backend.app.experiment_repository import ExperimentPersistenceError, ExperimentRepository
from backend.app.experiment_result_repository import ExperimentResultRepository
from backend.app.research_protocol import (
    CandidateSet,
    ProtocolStatus,
    ResearchProtocolError,
    ResearchProtocolRepository,
    SelectionDecision,
    StrategyFreezeRecord,
)
from backend.app.strategy_repository import StrategyRepository
from research import ExperimentResultFinalizationService, ExperimentStatus
from research.exceptions import ExperimentFinalizationError
from research.execution import CandidateExecutionStatus
from tests.unit.test_experiment_execution_service import (
    IS_END,
    IS_START,
    NOW,
    OOS_END,
    OOS_START,
    _binding,
    _LocalDataService,
    _setup,
)
from tests.unit.test_experiment_execution_service import (
    _protocol as execution_protocol,
)


def _running_actual(tmp_path):
    database = tmp_path / "research.db"
    data = _LocalDataService()
    service, experiments, protocols, executions, _, experiment = _setup(tmp_path, data)
    experiments.transition_status(
        experiment.experiment_id,
        ExperimentStatus.SPACE_FROZEN,
        ExperimentStatus.CANDIDATES_GENERATED,
        "CANDIDATES_GENERATED",
        "audit-candidates",
    )
    experiments.transition_status(
        experiment.experiment_id,
        ExperimentStatus.CANDIDATES_GENERATED,
        ExperimentStatus.RUNNING,
        "RUNNING",
        "audit-running",
    )
    outcome = service.execute(
        experiment_id=experiment.experiment_id,
        candidate_index=0,
        parameter_bindings=(_binding(),),
    )
    assert outcome.succeeded
    finalizer = ExperimentResultFinalizationService(
        backtest_repository=BacktestRepository(database),
        experiment_result_repository=ExperimentResultRepository(database),
        candidate_execution_repository=executions,
        experiment_repository=experiments,
        clock=lambda: NOW,
    )
    return database, service, experiments, protocols, executions, experiment, outcome, finalizer


def _selection_handoff(database, protocols, result, run, derived):
    protocol = replace(
        execution_protocol(),
        protocol_id="protocol-8e-0e-handoff",
    )
    protocols.create_protocol(protocol)
    candidate_set = CandidateSet(
        candidate_set_id="candidate-set-8e-0e-handoff",
        protocol_id=protocol.protocol_id,
        strategy_version_ids=(derived.version_id,),
        strategy_version_content_hashes={derived.version_id: derived.content_hash or ""},
        created_at=NOW,
    )
    protocols.create_candidate_set(candidate_set)
    locked = protocols.lock_candidate_set(candidate_set.candidate_set_id)
    protocols.transition_protocol(protocol.protocol_id, ProtocolStatus.FROZEN)
    protocols.transition_protocol(protocol.protocol_id, ProtocolStatus.IS_EVALUATED)
    decision = SelectionDecision(
        decision_id="selection-8e-0e",
        protocol_id=protocol.protocol_id,
        candidate_set_id=locked.candidate_set_id,
        selected_strategy_version_id=derived.version_id,
        is_backtest_run_ids=(run.backtest_run_id,),
        selected_metrics={"cagr": 0.12},
        rationale="Human selection uses the immutable IS result only.",
        created_at=NOW,
        data_provenance={"split": "is"},
    )
    stored_decision = protocols.create_selection(
        decision,
        candidate_set=locked,
        runs=(run,),
        version=derived,
    )
    protocols.transition_protocol(protocol.protocol_id, ProtocolStatus.SELECTION_RECORDED)
    freeze = StrategyFreezeRecord(
        freeze_id="freeze-8e-0e",
        protocol_id=protocol.protocol_id,
        strategy_version_id=derived.version_id,
        strategy_version_content_hash=derived.content_hash or "",
        selection_decision_id=stored_decision.decision_id,
        frozen_at=NOW,
        reason="Freeze the exact persisted candidate before OOS.",
    )
    stored_freeze = protocols.create_freeze(freeze, decision=stored_decision, version=derived)
    return stored_decision, stored_freeze


def test_materialized_version_and_result_identity_survive_restart(tmp_path) -> None:
    database, _, experiments, _, executions, _, outcome, finalizer = _running_actual(tmp_path)

    result = finalizer.finalize(outcome)
    execution = executions.get_execution(outcome.candidate_execution_id or "")
    derived = StrategyRepository(database).get_any_version(result.derived_strategy_version_id)
    restored_result = ExperimentResultRepository(database).get(result.experiment_result_id)
    restored_experiment = ExperimentRepository(database).get(result.experiment_id)

    assert derived is not None
    assert restored_result == result
    assert restored_experiment is not None
    assert execution is not None and execution.status is CandidateExecutionStatus.COMPLETED
    assert execution.experiment_id == result.experiment_id
    assert execution.candidate_id == result.candidate_id
    assert execution.parameter_set_hash == result.parameter_set_hash
    assert execution.base_strategy_version_id == result.base_strategy_version_id
    assert execution.base_strategy_version_hash == result.base_strategy_version_hash
    assert execution.derived_strategy_version_id == result.derived_strategy_version_id
    assert execution.derived_strategy_version_hash == result.derived_strategy_version_hash
    assert derived.version_id == result.derived_strategy_version_id
    assert derived.content_hash == result.derived_strategy_version_hash
    assert derived.materialization_provenance is not None
    assert (
        derived.materialization_provenance.base_strategy_version_id
        == result.base_strategy_version_id
    )
    assert (
        derived.materialization_provenance.parameter_set_hash
        == result.parameter_set_hash
    )
    assert experiments.get(result.experiment_id).status is ExperimentStatus.COMPLETED


def test_derived_versions_are_not_added_to_ordinary_strategy_catalog(tmp_path) -> None:
    database, _, _, _, _, experiment, outcome, finalizer = _running_actual(tmp_path)
    result = finalizer.finalize(outcome)
    strategies = StrategyRepository(database)
    catalog = strategies.catalog()

    assert len(catalog) == 1
    assert catalog[0]["strategy_id"] == experiment.strategy_definition_id
    assert catalog[0]["version_count"] == 1
    assert strategies.get_any_version(result.derived_strategy_version_id) is not None
    assert strategies.list(experiment.strategy_definition_id)[0].version_id == (
        experiment.base_strategy_version_id
    )


def test_backtest_run_is_accounting_source_of_truth_for_result(tmp_path) -> None:
    database, _, _, _, _, _, outcome, finalizer = _running_actual(tmp_path)
    result = finalizer.finalize(outcome)
    run = BacktestRepository(database).get(result.backtest_run_id)

    assert run is not None
    assert run.backtest_result == outcome.backtest_result.backtest_result
    assert run.performance_analysis.to_dict() == outcome.performance_analysis_result.to_dict() | {
        "backtest_run_id": result.backtest_run_id,
        "strategy_id": outcome.backtest_result.strategy_id,
    }
    assert result.backtest_run_id == run.backtest_run_id
    assert "equity_curve" not in result.to_dict()
    assert "orders" not in result.to_dict()
    assert result.to_dict()["performance_summary"] == outcome.performance_analysis_result.to_dict()


def test_is_boundary_warmup_and_next_open_execution_are_preserved(tmp_path) -> None:
    database, service, _, _, _, _, outcome, finalizer = _running_actual(tmp_path)
    result = finalizer.finalize(outcome)
    requests = service._data.requests
    run = BacktestRepository(database).get(result.backtest_run_id)

    assert requests
    assert all(request.end_date == IS_END for request in requests)
    assert all(request.start_date < IS_START for request in requests)
    assert result.warmup_end < result.is_start
    assert run is not None
    assert run.backtest_result.start_date == IS_START
    assert run.backtest_result.end_date == IS_END
    assert all(order.date <= IS_END for order in run.backtest_result.orders)
    assert all(order.date > order.signal_date for order in run.backtest_result.orders)
    assert not any(
        order.signal_date == IS_END and order.date > IS_END
        for order in run.backtest_result.orders
    )
    assert all(point.date <= IS_END for point in run.backtest_result.equity_curve)


def test_result_can_handoff_exactly_to_selection_and_strategy_freeze(tmp_path) -> None:
    database, _, _, protocols, _, _, outcome, finalizer = _running_actual(tmp_path)
    result = finalizer.finalize(outcome)
    run = BacktestRepository(database).get(result.backtest_run_id)
    derived = StrategyRepository(database).get_any_version(result.derived_strategy_version_id)
    assert run is not None and derived is not None

    decision, freeze = _selection_handoff(database, protocols, result, run, derived)

    assert decision.selected_strategy_version_id == result.derived_strategy_version_id
    assert decision.is_backtest_run_ids == (result.backtest_run_id,)
    assert freeze.strategy_version_id == result.derived_strategy_version_id
    assert freeze.strategy_version_content_hash == result.derived_strategy_version_hash
    assert protocols.get_selection(decision.decision_id) == decision
    assert protocols.get_freeze(freeze.freeze_id) == freeze


def test_selection_handoff_rejects_oos_backtest_for_same_candidate(tmp_path) -> None:
    database, _, _, protocols, _, _, outcome, finalizer = _running_actual(tmp_path)
    result = finalizer.finalize(outcome)
    run = BacktestRepository(database).get(result.backtest_run_id)
    derived = StrategyRepository(database).get_any_version(result.derived_strategy_version_id)
    assert run is not None and derived is not None
    protocol = replace(execution_protocol(), protocol_id="protocol-8e-0e-oos-reject")
    protocols.create_protocol(protocol)
    candidate_set = CandidateSet(
        candidate_set_id="candidate-set-8e-0e-oos-reject",
        protocol_id=protocol.protocol_id,
        strategy_version_ids=(derived.version_id,),
        strategy_version_content_hashes={derived.version_id: derived.content_hash or ""},
        created_at=NOW,
    )
    protocols.create_candidate_set(candidate_set)
    locked = protocols.lock_candidate_set(candidate_set.candidate_set_id)
    protocols.transition_protocol(protocol.protocol_id, ProtocolStatus.FROZEN)
    protocols.transition_protocol(protocol.protocol_id, ProtocolStatus.IS_EVALUATED)
    oos_run = replace(
        run,
        backtest_result=replace(
            run.backtest_result,
            start_date=OOS_START,
            end_date=OOS_END,
        ),
        performance_analysis=replace(
            run.performance_analysis,
            start_date=OOS_START,
            end_date=OOS_END,
        ),
    )
    decision = SelectionDecision(
        decision_id="selection-8e-0e-oos-reject",
        protocol_id=protocol.protocol_id,
        candidate_set_id=locked.candidate_set_id,
        selected_strategy_version_id=derived.version_id,
        is_backtest_run_ids=(oos_run.backtest_run_id,),
        selected_metrics={"cagr": 0.99},
        rationale="This must not be accepted as IS evidence.",
        created_at=NOW,
    )

    with pytest.raises(ResearchProtocolError, match="IS backtest runs only"):
        protocols.create_selection(decision, candidate_set=locked, runs=(oos_run,), version=derived)

    assert protocols.list_selections(protocol.protocol_id) == ()


def test_general_lifecycle_transition_is_distinct_from_terminal_result_finalization(
    tmp_path,
) -> None:
    _, _, experiments, _, _, experiment, _, _ = _running_actual(tmp_path)

    with pytest.raises(ExperimentPersistenceError) as error:
        experiments.transition_status(
            experiment.experiment_id,
            ExperimentStatus.RUNNING,
            ExperimentStatus.COMPLETED,
            "COMPLETED",
            "audit-direct-completed",
        )
    assert error.value.code == "EXPERIMENT_RESULT_REQUIRED"
    events = experiments.list_events(experiment.experiment_id)
    assert not any(event.to_status is ExperimentStatus.COMPLETED for event in events)


def test_finalization_retry_does_not_duplicate_backtest_result_or_terminal_event(tmp_path) -> None:
    database, _, experiments, _, _, _, outcome, finalizer = _running_actual(tmp_path)
    first = finalizer.finalize(outcome)
    second = ExperimentResultFinalizationService(
        backtest_repository=BacktestRepository(database),
        experiment_result_repository=ExperimentResultRepository(database),
        candidate_execution_repository=finalizer._executions,
        experiment_repository=ExperimentRepository(database),
        clock=lambda: NOW,
    ).finalize(outcome)

    assert second == first
    assert len(BacktestRepository(database).list()) == 1
    assert len(ExperimentResultRepository(database).list()) == 1
    assert sum(
        event.to_status is ExperimentStatus.COMPLETED
        for event in experiments.list_events(outcome.experiment_id)
    ) == 1


def test_finalization_failure_rolls_back_result_and_terminal_lifecycle_state(
    tmp_path, monkeypatch
) -> None:
    database, _, experiments, _, _, _, outcome, finalizer = _running_actual(tmp_path)

    def fail_result(*args, **kwargs):
        raise RuntimeError("audit result write failure")

    monkeypatch.setattr(ExperimentResultRepository, "persist_in_transaction", fail_result)
    with pytest.raises(ExperimentFinalizationError, match="EXPERIMENT_RESULT_PERSISTENCE_ERROR"):
        finalizer.finalize(outcome)

    assert experiments.get(outcome.experiment_id).status is ExperimentStatus.RUNNING
    assert ExperimentResultRepository(database).list() == ()
    assert not any(
        event.to_status is ExperimentStatus.COMPLETED
        for event in experiments.list_events(outcome.experiment_id)
    )
    assert len(BacktestRepository(database).list()) == 1


def test_selection_protocol_unique_index_and_concurrent_different_decisions(tmp_path) -> None:
    database, _, _, protocols, _, _, outcome, finalizer = _running_actual(tmp_path)
    result = finalizer.finalize(outcome)
    run = BacktestRepository(database).get(result.backtest_run_id)
    derived = StrategyRepository(database).get_any_version(result.derived_strategy_version_id)
    assert run is not None and derived is not None
    protocol = replace(execution_protocol(), protocol_id="protocol-8e-0e-race")
    protocols.create_protocol(protocol)
    candidate_set = CandidateSet(
        candidate_set_id="candidate-set-8e-0e-race",
        protocol_id=protocol.protocol_id,
        strategy_version_ids=(derived.version_id,),
        strategy_version_content_hashes={derived.version_id: derived.content_hash or ""},
        created_at=NOW,
    )
    protocols.create_candidate_set(candidate_set)
    locked = protocols.lock_candidate_set(candidate_set.candidate_set_id)
    protocols.transition_protocol(protocol.protocol_id, ProtocolStatus.FROZEN)
    protocols.transition_protocol(protocol.protocol_id, ProtocolStatus.IS_EVALUATED)

    def submit(decision_id: str):
        decision = SelectionDecision(
            decision_id=decision_id,
            protocol_id=protocol.protocol_id,
            candidate_set_id=locked.candidate_set_id,
            selected_strategy_version_id=derived.version_id,
            is_backtest_run_ids=(run.backtest_run_id,),
            selected_metrics={"cagr": 0.12 if decision_id.endswith("a") else 0.13},
            rationale=decision_id,
            created_at=NOW,
        )
        try:
            protocols_for_thread = ResearchProtocolRepository(database)
            protocols_for_thread.create_selection(
                decision, candidate_set=locked, runs=(run,), version=derived
            )
            return "success"
        except ResearchProtocolError as error:
            return error.code

    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(pool.map(submit, ("selection-a", "selection-b")))

    assert sorted(outcomes) == ["SELECTION_CONFLICT", "success"]
    with sqlite3.connect(database) as connection:
        indexes = connection.execute(
            "PRAGMA index_list('research_selection_decisions')"
        ).fetchall()
        count = connection.execute(
            "SELECT COUNT(*) FROM research_selection_decisions WHERE protocol_id = ?",
            (protocol.protocol_id,),
        ).fetchone()[0]
    assert any(row[1] == "uq_research_selection_protocol" and row[2] == 1 for row in indexes)
    assert count == 1


def test_result_and_provenance_reject_non_finite_or_sensitive_values(tmp_path) -> None:
    database, _, _, _, _, _, outcome, finalizer = _running_actual(tmp_path)
    with pytest.raises(ValueError, match="sensitive data"):
        invalid = replace(
            outcome,
            provenance={"data_snapshot_reference": {"api_key": "must-not-persist"}},
        )
        finalizer.finalize(invalid)

    assert ExperimentResultRepository(database).list() == ()
