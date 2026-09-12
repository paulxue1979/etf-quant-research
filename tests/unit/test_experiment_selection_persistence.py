from __future__ import annotations

import sqlite3
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace

import pytest

from backend.app.backtest_repository import BacktestRepository
from backend.app.candidate_execution_repository import CandidateExecutionRepository
from backend.app.experiment_compatibility_service import ExperimentCompatibilityService
from backend.app.experiment_result_repository import ExperimentResultRepository
from backend.app.experiment_results_read_service import ExperimentResultsReadService
from backend.app.experiment_selection_repository import ExperimentSelectionRepository
from backend.app.strategy_repository import StrategyRepository
from research import ExperimentStatus
from research.canonical import sha256_hash
from research.exceptions import (
    ExperimentSelectionConflictError,
    ExperimentSelectionPersistenceError,
)
from research.experiment_selection import ExperimentSelectionMethod
from research.objective_evaluation import evaluate_candidate_objective
from tests.unit.test_experiment_result_persistence import _running_outcome


def _decision_fixture(tmp_path):
    finalizer, outcome, experiments, _ = _running_outcome(
        tmp_path,
        single_candidate=True,
    )
    result = finalizer.finalize(outcome)
    database = tmp_path / "research.db"
    experiment = experiments.get(result.experiment_id)
    assert experiment is not None
    strategies = StrategyRepository(database)
    results = ExperimentResultRepository(database)
    executions = CandidateExecutionRepository(database)
    backtests = BacktestRepository(database)
    read_service = ExperimentResultsReadService(
        experiment_repository=experiments,
        candidate_execution_repository=executions,
        experiment_result_repository=results,
        backtest_repository=backtests,
        strategy_repository=strategies,
    )
    model = read_service.get(experiment.experiment_id)
    candidate = model.candidates[0]
    objective = evaluate_candidate_objective(experiment, candidate)
    compatibility = ExperimentCompatibilityService().diagnose(model)
    derived = strategies.get_any_version(candidate.derived_strategy_version_id or "")
    assert derived is not None
    from research import create_experiment_selection_decision

    decision = create_experiment_selection_decision(
        experiment=experiment,
        selected_candidate_id=candidate.candidate_id,
        candidates=model,
        objective_evaluation=objective,
        compatibility=compatibility,
        derived_strategy_version=derived,
        selection_method=ExperimentSelectionMethod.RESEARCHER_JUDGMENT,
        researcher_rationale="Reviewed the frozen IS evidence and selected this candidate.",
        created_at=experiment.created_at,
    )
    return database, decision, experiments


def test_persist_selection_atomically_and_update_experiment_state(tmp_path) -> None:
    database, decision, experiments = _decision_fixture(tmp_path)

    stored = ExperimentSelectionRepository(database).create(decision)

    assert stored == decision
    assert ExperimentSelectionRepository(database).get(decision.selection_id) == decision
    assert (
        ExperimentSelectionRepository(database).get_by_experiment_id(decision.experiment_id)
        == decision
    )
    assert experiments.get(decision.experiment_id).status is ExperimentStatus.SELECTION_RECORDED
    events = experiments.list_events(decision.experiment_id)
    selection_events = [event for event in events if event.event_type == "SELECTION_RECORDED"]
    assert len(selection_events) == 1
    assert selection_events[0].to_status is ExperimentStatus.SELECTION_RECORDED


def test_selection_survives_restart_with_canonical_hash_and_provenance(tmp_path) -> None:
    database, decision, _ = _decision_fixture(tmp_path)
    ExperimentSelectionRepository(database).create(decision)

    restored = ExperimentSelectionRepository(database).get_by_experiment_id(decision.experiment_id)

    assert restored == decision
    assert restored is not None
    assert restored.selection_hash == decision.selection_hash
    assert restored.canonical_json() == decision.canonical_json()
    assert restored.evidence.data_snapshot_reference == decision.evidence.data_snapshot_reference


def test_identical_retry_is_idempotent_without_duplicate_event(tmp_path) -> None:
    database, decision, experiments = _decision_fixture(tmp_path)
    repository = ExperimentSelectionRepository(database)

    first = repository.create(decision)
    second = repository.create(decision)

    assert second == first
    assert len(repository.list(decision.protocol_id)) == 1
    assert sum(
        event.event_type == "SELECTION_RECORDED"
        for event in experiments.list_events(decision.experiment_id)
    ) == 1


def test_different_selection_conflicts_and_does_not_overwrite(tmp_path) -> None:
    database, decision, experiments = _decision_fixture(tmp_path)
    repository = ExperimentSelectionRepository(database)
    repository.create(decision)
    conflicting = replace(
        decision,
        selection_hash=sha256_hash(
            {
                **decision.semantic_payload(),
                "researcher_rationale": "A different explicit choice.",
            }
        ),
        selection_id="selection-"
        + sha256_hash(
            {
                **decision.semantic_payload(),
                "researcher_rationale": "A different explicit choice.",
            }
        )[:32],
        researcher_rationale="A different explicit choice.",
    )

    with pytest.raises(ExperimentSelectionConflictError) as error:
        repository.create(conflicting)

    assert error.value.code == "SELECTION_CONFLICT"
    assert repository.get_by_experiment_id(decision.experiment_id) == decision
    assert experiments.get(decision.experiment_id).status is ExperimentStatus.SELECTION_RECORDED


def test_only_completed_experiment_can_record_selection(tmp_path) -> None:
    database, decision, experiments = _decision_fixture(tmp_path)
    experiments.transition_status(
        decision.experiment_id,
        ExperimentStatus.COMPLETED,
        ExperimentStatus.CLOSED,
        "CLOSED",
        "close-before-selection",
    )

    with pytest.raises(ExperimentSelectionPersistenceError) as error:
        ExperimentSelectionRepository(database).create(decision)

    assert error.value.code == "EXPERIMENT_NOT_COMPLETED"
    assert ExperimentSelectionRepository(database).list() == ()


def test_db_unique_constraint_is_present(tmp_path) -> None:
    database, _, _ = _decision_fixture(tmp_path)
    ExperimentSelectionRepository(database)
    connection = sqlite3.connect(database)
    try:
        indexes = connection.execute(
            "PRAGMA index_list('research_experiment_selections')"
        ).fetchall()
    finally:
        connection.close()

    assert any(
        row[1] == "uq_research_experiment_selection_experiment" and row[2] == 1
        for row in indexes
    )


def test_stored_selection_tampering_is_rejected(tmp_path) -> None:
    database, decision, _ = _decision_fixture(tmp_path)
    repository = ExperimentSelectionRepository(database)
    repository.create(decision)
    connection = sqlite3.connect(database)
    try:
        connection.execute(
            "UPDATE research_experiment_selections SET selection_hash = ? WHERE selection_id = ?",
            ("f" * 64, decision.selection_id),
        )
        connection.commit()
    finally:
        connection.close()

    with pytest.raises(ExperimentSelectionPersistenceError) as error:
        repository.get(decision.selection_id)

    assert error.value.code == "PERSISTED_SELECTION_INTEGRITY_ERROR"


def test_selection_insert_failure_rolls_back_all_state(tmp_path, monkeypatch) -> None:
    database, decision, experiments = _decision_fixture(tmp_path)
    repository = ExperimentSelectionRepository(database)
    def fail_insert(*args, **kwargs):
        raise sqlite3.OperationalError("injected selection failure")

    monkeypatch.setattr(repository, "_insert_selection", fail_insert)
    with pytest.raises(ExperimentSelectionPersistenceError):
        repository.create(decision)

    assert repository.list() == ()
    assert experiments.get(decision.experiment_id).status is ExperimentStatus.COMPLETED


def test_event_failure_rolls_back_selection_and_status(tmp_path, monkeypatch) -> None:
    database, decision, experiments = _decision_fixture(tmp_path)
    repository = ExperimentSelectionRepository(database, experiment_repository=experiments)

    def fail_event(*args, **kwargs):
        raise RuntimeError("injected lifecycle event failure")

    monkeypatch.setattr(experiments, "_insert_event", fail_event)
    with pytest.raises(ExperimentSelectionPersistenceError):
        repository.create(decision)

    assert repository.list() == ()
    assert experiments.get(decision.experiment_id).status is ExperimentStatus.COMPLETED


def test_concurrent_identical_selection_writes_one_record(tmp_path) -> None:
    database, decision, experiments = _decision_fixture(tmp_path)

    def persist_once():
        return ExperimentSelectionRepository(database).create(decision)

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: persist_once(), range(2)))

    assert results[0] == results[1] == decision
    assert len(ExperimentSelectionRepository(database).list()) == 1
    assert sum(
        event.event_type == "SELECTION_RECORDED"
        for event in experiments.list_events(decision.experiment_id)
    ) == 1


def test_selection_does_not_create_phase_7_protocol_selection(tmp_path) -> None:
    database, decision, _ = _decision_fixture(tmp_path)
    ExperimentSelectionRepository(database).create(decision)
    connection = sqlite3.connect(database)
    try:
        count = connection.execute(
            "SELECT COUNT(*) FROM research_selection_decisions WHERE protocol_id = ?",
            (decision.protocol_id,),
        ).fetchone()[0]
    finally:
        connection.close()
    assert count == 0
