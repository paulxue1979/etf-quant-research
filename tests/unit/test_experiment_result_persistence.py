from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor

import pytest

from backend.app.backtest_repository import BacktestRepository
from backend.app.experiment_repository import ExperimentPersistenceError, ExperimentRepository
from backend.app.experiment_result_repository import ExperimentResultRepository
from research import ExperimentResultFinalizationService, ExperimentStatus
from research.exceptions import ExperimentFinalizationError, ExperimentResultPersistenceError
from research.execution import CandidateExecutionStatus
from research.experiment_result import ExperimentResult
from tests.unit.test_experiment_execution_service import _binding, _setup


def _running_outcome(tmp_path):
    service, experiments, _, executions, _, experiment = _setup(tmp_path)
    experiments.transition_status(
        experiment.experiment_id,
        ExperimentStatus.SPACE_FROZEN,
        ExperimentStatus.CANDIDATES_GENERATED,
        "CANDIDATES_GENERATED",
        "phase-8e-0c-candidates",
    )
    experiments.transition_status(
        experiment.experiment_id,
        ExperimentStatus.CANDIDATES_GENERATED,
        ExperimentStatus.RUNNING,
        "RUNNING",
        "phase-8e-0c-running",
    )
    outcome = service.execute(
        experiment_id=experiment.experiment_id,
        candidate_index=0,
        parameter_bindings=(_binding(),),
    )
    finalizer = ExperimentResultFinalizationService(
        backtest_repository=BacktestRepository(tmp_path / "research.db"),
        experiment_result_repository=ExperimentResultRepository(tmp_path / "research.db"),
        candidate_execution_repository=executions,
        experiment_repository=experiments,
        clock=lambda: service._clock(),
    )
    return finalizer, outcome, experiments, executions


def test_finalize_result_atomically_persists_result_and_completed_state(tmp_path) -> None:
    finalizer, outcome, experiments, executions = _running_outcome(tmp_path)

    result = finalizer.finalize(outcome)

    reopened_experiments = ExperimentRepository(tmp_path / "research.db")
    reopened_results = ExperimentResultRepository(tmp_path / "research.db")
    assert reopened_results.get(result.experiment_result_id) == result
    assert reopened_experiments.get(outcome.experiment_id).status is ExperimentStatus.COMPLETED
    terminal = reopened_experiments.list_events(outcome.experiment_id)[-1]
    assert terminal.event_type == "EXPERIMENT_COMPLETED"
    assert terminal.to_status is ExperimentStatus.COMPLETED
    assert terminal.payload["result_id"] == result.experiment_result_id
    execution = executions.get_execution(outcome.candidate_execution_id or "")
    assert execution is not None and execution.status is CandidateExecutionStatus.COMPLETED


def test_finalize_result_retry_is_idempotent_after_restart(tmp_path) -> None:
    finalizer, outcome, _, _ = _running_outcome(tmp_path)
    first = finalizer.finalize(outcome)

    reopened = ExperimentResultFinalizationService(
        backtest_repository=BacktestRepository(tmp_path / "research.db"),
        experiment_result_repository=ExperimentResultRepository(tmp_path / "research.db"),
        candidate_execution_repository=finalizer._executions,
        experiment_repository=ExperimentRepository(tmp_path / "research.db"),
        clock=finalizer._clock,
    )
    second = reopened.finalize(outcome)

    assert second == first
    events = ExperimentRepository(tmp_path / "research.db").list_events(outcome.experiment_id)
    assert sum(event.to_status is ExperimentStatus.COMPLETED for event in events) == 1
    assert len(ExperimentResultRepository(tmp_path / "research.db").list()) == 1


def test_result_write_failure_rolls_back_result_and_experiment_transition(
    tmp_path, monkeypatch
) -> None:
    finalizer, outcome, experiments, _ = _running_outcome(tmp_path)

    def fail_result(*args, **kwargs):
        raise ExperimentResultPersistenceError("injected result write failure")

    monkeypatch.setattr(ExperimentResultRepository, "persist_in_transaction", fail_result)
    with pytest.raises(ExperimentFinalizationError, match="EXPERIMENT_RESULT_PERSISTENCE_ERROR"):
        finalizer.finalize(outcome)

    assert experiments.get(outcome.experiment_id).status is ExperimentStatus.RUNNING
    assert ExperimentResultRepository(tmp_path / "research.db").list() == ()
    assert not any(
        event.to_status is ExperimentStatus.COMPLETED
        for event in experiments.list_events(outcome.experiment_id)
    )


def test_terminal_event_failure_rolls_back_result_and_experiment_transition(
    tmp_path, monkeypatch
) -> None:
    finalizer, outcome, experiments, _ = _running_outcome(tmp_path)

    def fail_event(*args, **kwargs):
        raise RuntimeError("injected event failure")

    monkeypatch.setattr(experiments, "_insert_event", fail_event)
    with pytest.raises(ExperimentFinalizationError, match="EXPERIMENT_RESULT_PERSISTENCE_ERROR"):
        finalizer.finalize(outcome)

    assert experiments.get(outcome.experiment_id).status is ExperimentStatus.RUNNING
    assert ExperimentResultRepository(tmp_path / "research.db").list() == ()
    assert not any(
        event.to_status is ExperimentStatus.COMPLETED
        for event in experiments.list_events(outcome.experiment_id)
    )


def test_concurrent_identical_finalization_has_one_result_and_one_terminal_event(tmp_path) -> None:
    _, outcome, experiments, executions = _running_outcome(tmp_path)
    database = tmp_path / "research.db"

    def finalize_once():
        finalizer = ExperimentResultFinalizationService(
            backtest_repository=BacktestRepository(database),
            experiment_result_repository=ExperimentResultRepository(database),
            candidate_execution_repository=executions,
            experiment_repository=ExperimentRepository(database),
        )
        return finalizer.finalize(outcome)

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: finalize_once(), range(2)))

    assert results[0] == results[1]
    assert len(ExperimentResultRepository(database).list()) == 1
    assert sum(
        event.to_status is ExperimentStatus.COMPLETED
        for event in ExperimentRepository(database).list_events(outcome.experiment_id)
    ) == 1


def test_direct_completed_transition_is_blocked_without_result(tmp_path) -> None:
    _, experiments, _, _, _, experiment = _setup(tmp_path)

    with pytest.raises(ExperimentPersistenceError) as error:
        experiments.transition_status(
            experiment.experiment_id,
            ExperimentStatus.SPACE_FROZEN,
            ExperimentStatus.COMPLETED,
            "COMPLETED",
            "direct-completed",
        )

    assert error.value.code == "EXPERIMENT_RESULT_REQUIRED"


def test_conflicting_result_for_same_candidate_is_rejected(tmp_path) -> None:
    finalizer, outcome, experiments, _ = _running_outcome(tmp_path)
    stored = finalizer.finalize(outcome)
    conflicting = ExperimentResult.create(
        experiment_id=stored.experiment_id,
        candidate_id=stored.candidate_id,
        candidate_index=stored.candidate_index,
        parameter_set_hash=stored.parameter_set_hash,
        parameter_space_hash=stored.parameter_space_hash,
        candidate_set_hash=stored.candidate_set_hash,
        base_strategy_version_id=stored.base_strategy_version_id,
        base_strategy_version_hash=stored.base_strategy_version_hash,
        derived_strategy_version_id=stored.derived_strategy_version_id,
        derived_strategy_version_hash=stored.derived_strategy_version_hash,
        binding_hash=stored.binding_hash,
        backtest_run_id="backtest-conflicting-result",
        is_start=stored.is_start,
        is_end=stored.is_end,
        warmup_start=stored.warmup_start,
        warmup_end=stored.warmup_end,
        price_field_used=stored.price_field_used,
        backtest_configuration_hash=stored.backtest_configuration_hash,
        engine_version=stored.engine_version,
        analysis_version=stored.analysis_version,
        data_snapshot_reference=dict(stored.data_snapshot_reference),
        performance_summary=dict(stored.performance_summary),
        created_at=stored.created_at,
    )

    with pytest.raises(ExperimentPersistenceError) as error:
        experiments.finalize_result(conflicting)

    assert error.value.code == "EXPERIMENT_RESULT_CONFLICT"
    assert (
        ExperimentResultRepository(tmp_path / "research.db").get(stored.experiment_result_id)
        == stored
    )
