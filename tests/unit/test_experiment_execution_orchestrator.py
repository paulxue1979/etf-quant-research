from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

from backend.app.backtest_repository import BacktestRepository
from backend.app.candidate_execution_repository import CandidateExecutionRepository
from backend.app.experiment_execution_orchestrator import (
    ExperimentExecutionApiError,
    ExperimentExecutionOrchestrator,
)
from backend.app.experiment_repository import ExperimentRepository
from backend.app.experiment_result_repository import ExperimentResultRepository
from backend.app.research_protocol import ResearchProtocolRepository
from backend.app.strategy_repository import StrategyRepository
from research import generate_candidates
from research.execution import CandidateExecutionStatus
from research.execution_service import ExperimentExecutionService
from research.result_finalization_service import ExperimentResultFinalizationService
from tests.unit.test_experiment_execution_service import (
    NOW,
    _binding,
    _definition,
    _experiment,
    _LocalDataService,
    _protocol,
)


def _setup_orchestrator(tmp_path: Path, *, frozen_bindings: bool = True):
    database = tmp_path / "research.db"
    protocols = ResearchProtocolRepository(database)
    strategies = StrategyRepository(database)
    experiments = ExperimentRepository(database)
    executions = CandidateExecutionRepository(database)
    results = ExperimentResultRepository(database)
    backtests = BacktestRepository(database)

    protocol = _protocol()
    protocols.create_protocol(protocol)
    version = strategies.create(_definition())
    from backend.app.research_protocol import CandidateSet

    candidate_set = CandidateSet(
        candidate_set_id="protocol-candidates-8d5",
        protocol_id=protocol.protocol_id,
        strategy_version_ids=(version.version_id,),
        strategy_version_content_hashes={version.version_id: version.content_hash or ""},
        created_at=NOW,
    )
    protocols.create_candidate_set(candidate_set)
    protocols.lock_candidate_set(candidate_set.candidate_set_id)
    protocols.transition_protocol(protocol.protocol_id, "frozen")

    experiment = _experiment(version)
    if frozen_bindings:
        provenance = replace(
            experiment.provenance,
            parameter_bindings=(dict(_binding().to_dict()),),
        )
        experiment = replace(experiment, provenance=provenance)
    experiments.create(experiment, generate_candidates(experiment))
    data = _LocalDataService()
    execution_service = ExperimentExecutionService(
        experiment_repository=experiments,
        protocol_repository=protocols,
        strategy_repository=strategies,
        candidate_execution_repository=executions,
        data_service=data,
        clock=lambda: NOW,
    )
    finalizer = ExperimentResultFinalizationService(
        backtest_repository=backtests,
        experiment_result_repository=results,
        candidate_execution_repository=executions,
        clock=lambda: NOW,
    )
    orchestrator = ExperimentExecutionOrchestrator(
        experiment_repository=experiments,
        execution_repository=executions,
        result_repository=results,
        execution_service=execution_service,
        finalization_service=finalizer,
        clock=lambda: NOW,
    )
    candidate = experiments.list_candidates(experiment.experiment_id)[0]
    return orchestrator, execution_service, experiments, executions, results, experiment, candidate


def test_execute_restores_frozen_bindings_and_persists_official_result(tmp_path: Path) -> None:
    orchestrator, _, _, executions, results, experiment, candidate = _setup_orchestrator(tmp_path)

    response = orchestrator.execute(experiment.experiment_id, candidate_id=candidate_id(candidate))

    assert response["candidate_id"] == candidate_id(candidate)
    assert response["execution"]["status"] == CandidateExecutionStatus.COMPLETED.value
    assert response["result"]["candidate_id"] == candidate_id(candidate)
    assert results.get_by_candidate(experiment.experiment_id, candidate_id(candidate)) is not None
    assert len(executions.list_events(response["execution"]["execution_id"])) == 3


def test_duplicate_execute_returns_same_result_without_running_engine_again(tmp_path: Path) -> None:
    orchestrator, execution_service, _, _, _, experiment, candidate = _setup_orchestrator(tmp_path)
    candidate_id_value = candidate_id(candidate)
    first = orchestrator.execute(experiment.experiment_id, candidate_id_value)

    def unexpected_execute(*args, **kwargs):
        raise AssertionError("duplicate execution must use the immutable stored result")

    execution_service.execute = unexpected_execute
    second = orchestrator.execute(experiment.experiment_id, candidate_id_value)

    assert second["result"]["experiment_result_id"] == first["result"]["experiment_result_id"]
    assert second["execution"]["status"] == CandidateExecutionStatus.COMPLETED.value


def test_missing_frozen_bindings_is_a_structured_precondition_error(tmp_path: Path) -> None:
    orchestrator, _, _, _, _, experiment, candidate = _setup_orchestrator(
        tmp_path, frozen_bindings=False
    )

    with pytest.raises(ExperimentExecutionApiError) as error:
        orchestrator.execute(experiment.experiment_id, candidate_id(candidate))

    assert error.value.status_code == 422
    assert error.value.code == "FROZEN_BINDINGS_MISSING"


def test_unknown_experiment_and_candidate_are_rejected(tmp_path: Path) -> None:
    orchestrator, _, _, _, _, experiment, candidate = _setup_orchestrator(tmp_path)

    with pytest.raises(ExperimentExecutionApiError) as unknown_experiment:
        orchestrator.get_execution("missing", candidate_id(candidate))
    with pytest.raises(ExperimentExecutionApiError) as unknown_candidate:
        orchestrator.get_result(experiment.experiment_id, "0" * 64)

    assert unknown_experiment.value.code == "EXPERIMENT_NOT_FOUND"
    assert unknown_candidate.value.code == "CANDIDATE_NOT_FOUND"


def test_get_operations_do_not_execute_or_change_state(tmp_path: Path) -> None:
    orchestrator, execution_service, _, executions, _, experiment, candidate = _setup_orchestrator(
        tmp_path
    )
    candidate_id_value = candidate_id(candidate)
    created = orchestrator.execute(experiment.experiment_id, candidate_id_value)
    events_before = executions.list_events(created["execution"]["execution_id"])

    def unexpected_execute(*args, **kwargs):
        raise AssertionError("GET must not execute a candidate")

    execution_service.execute = unexpected_execute
    execution = orchestrator.get_execution(experiment.experiment_id, candidate_id_value)
    result = orchestrator.get_result(experiment.experiment_id, candidate_id_value)

    assert execution["status"] == CandidateExecutionStatus.COMPLETED.value
    assert result["candidate_id"] == candidate_id_value
    assert executions.list_events(created["execution"]["execution_id"]) == events_before


def candidate_id(candidate) -> str:
    from research.execution import candidate_id_for

    return candidate_id_for(
        candidate.experiment_id, candidate.candidate_index, candidate.parameter_set_hash
    )
