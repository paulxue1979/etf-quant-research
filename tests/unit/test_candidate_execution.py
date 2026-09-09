from __future__ import annotations

import sqlite3
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest

from backend.app.candidate_execution_repository import CandidateExecutionRepository
from backend.app.experiment_repository import ExperimentRepository
from research import (
    CandidateExecution,
    CandidateExecutionConflictError,
    CandidateExecutionPersistenceError,
    CandidateExecutionStatus,
    ExecutionEvent,
    ExecutionStateTransitionError,
    ParameterBindingSet,
    candidate_id_for,
    execution_id_for,
    generate_candidates,
    sha256_hash,
)
from tests.unit.test_experiment_repository import _create_dependencies, _experiment

NOW = datetime(2026, 9, 9, 12, tzinfo=UTC)
BINDING_HASH = ParameterBindingSet(()).binding_hash


def _setup(tmp_path):
    db_path, _, _, version = _create_dependencies(tmp_path)
    experiment = _experiment(version)
    candidates = generate_candidates(experiment)
    ExperimentRepository(db_path).create(experiment, candidates)
    candidate = candidates.candidates[0]
    candidate_id = candidate_id_for(experiment.experiment_id, 0, candidate.content_hash)
    execution = CandidateExecution.pending(
        experiment_id=experiment.experiment_id,
        experiment_hash=experiment.content_hash,
        candidate_id=candidate_id,
        candidate_index=0,
        parameter_set_hash=candidate.content_hash,
        parameter_binding_hash=BINDING_HASH,
        base_strategy_version_id=version.version_id,
        base_strategy_version_hash=version.content_hash or "",
        created_at=NOW,
    )
    return db_path, experiment, execution


def test_identity_is_deterministic() -> None:
    candidate_id = candidate_id_for("experiment", 2, "a" * 64)
    assert candidate_id == candidate_id_for("experiment", 2, "a" * 64)
    execution_id = execution_id_for("experiment", candidate_id, "a" * 64, "b" * 64)
    assert execution_id == execution_id_for("experiment", candidate_id, "a" * 64, "b" * 64)
    assert execution_id.startswith("execution-")
    assert execution_id != execution_id_for("experiment", candidate_id, "a" * 64, "c" * 64)


def test_domain_state_machine_and_round_trip() -> None:
    execution = CandidateExecution.pending(
        experiment_id="experiment",
        experiment_hash="a" * 64,
        candidate_id="b" * 64,
        candidate_index=0,
        parameter_set_hash="c" * 64,
        parameter_binding_hash="d" * 64,
        base_strategy_version_id="strategy-v1",
        base_strategy_version_hash="e" * 64,
        created_at=NOW,
    )
    running = execution.claim(
        claimed_at=NOW,
        claimed_by="worker-1",
        lease_expires_at=NOW + timedelta(minutes=5),
    )
    completed = running.complete(completed_at=NOW + timedelta(minutes=1), actor="worker-1", now=NOW)
    assert completed.status is CandidateExecutionStatus.COMPLETED
    assert CandidateExecution.from_dict(completed.to_dict()) == completed
    with pytest.raises(ExecutionStateTransitionError):
        completed.claim(
            claimed_at=NOW,
            claimed_by="worker-2",
            lease_expires_at=NOW + timedelta(minutes=5),
        )


def test_domain_failure_retry_and_expiry() -> None:
    execution = CandidateExecution.pending(
        experiment_id="experiment",
        experiment_hash="a" * 64,
        candidate_id="b" * 64,
        candidate_index=0,
        parameter_set_hash="c" * 64,
        parameter_binding_hash="d" * 64,
        base_strategy_version_id="strategy-v1",
        base_strategy_version_hash="e" * 64,
        created_at=NOW,
        max_retries=2,
    )
    running = execution.claim(
        claimed_at=NOW,
        claimed_by="worker-1",
        lease_expires_at=NOW + timedelta(minutes=5),
    )
    failed = running.fail(
        failed_at=NOW + timedelta(minutes=1),
        actor="worker-1",
        now=NOW,
        failure_code="TEMPORARY_FAILURE",
        failure_message="temporary failure",
        retryable=True,
    )
    assert failed.retry_count == 1
    assert failed.retry().status is CandidateExecutionStatus.PENDING
    recovered = running.recover_expired(now=NOW + timedelta(minutes=6))
    assert recovered.status is CandidateExecutionStatus.PENDING
    with pytest.raises(ExecutionStateTransitionError):
        running.recover_expired(now=NOW + timedelta(minutes=1))


def test_event_payload_is_immutable_and_round_trips() -> None:
    event = ExecutionEvent(
        event_id="event-1",
        execution_id="execution-1",
        experiment_id="experiment-1",
        event_type="TEST_EVENT",
        occurred_at=NOW,
        actor="worker",
        payload={"value": 1},
    )
    with pytest.raises(TypeError):
        event.payload["value"] = 2  # type: ignore[index]
    assert ExecutionEvent.from_dict(event.to_dict()) == event


def test_repository_create_restart_and_idempotency(tmp_path) -> None:
    db_path, _, execution = _setup(tmp_path)
    first = CandidateExecutionRepository(db_path).create_execution(execution)
    second = CandidateExecutionRepository(db_path).create_execution(execution)
    restored = CandidateExecutionRepository(db_path).get_execution(first.execution_id)
    assert first == second == restored
    events = CandidateExecutionRepository(db_path).list_events(execution.execution_id)
    assert [event.event_type for event in events] == ["EXECUTION_CREATED"]


def test_repository_rejects_mismatched_candidate_identity(tmp_path) -> None:
    db_path, _, execution = _setup(tmp_path)
    repository = CandidateExecutionRepository(db_path)
    repository.create_execution(execution)
    mismatched = replace(execution, parameter_binding_hash="f" * 64)
    with pytest.raises(CandidateExecutionConflictError):
        repository.create_execution(mismatched)


def test_repository_claim_renew_complete_and_append_events(tmp_path) -> None:
    db_path, _, execution = _setup(tmp_path)
    repository = CandidateExecutionRepository(db_path)
    repository.create_execution(execution)
    running = repository.claim_candidate(
        execution.experiment_id,
        execution.candidate_index,
        "worker-1",
        NOW,
        NOW + timedelta(minutes=5),
    )
    renewed = repository.renew_lease(
        running.execution_id, "worker-1", NOW + timedelta(minutes=1), NOW + timedelta(minutes=10)
    )
    completed = repository.mark_completed(
        renewed.execution_id, "worker-1", NOW + timedelta(minutes=2), NOW + timedelta(minutes=2)
    )
    assert completed.status is CandidateExecutionStatus.COMPLETED
    assert [event.event_type for event in repository.list_events(execution.execution_id)] == [
        "EXECUTION_CREATED",
        "EXECUTION_CLAIMED",
        "LEASE_RENEWED",
        "EXECUTION_COMPLETED",
    ]


def test_repository_failure_retry_and_max_retries(tmp_path) -> None:
    db_path, _, execution = _setup(tmp_path)
    repository = CandidateExecutionRepository(db_path)
    repository.create_execution(replace(execution, max_retries=1))
    running = repository.claim_candidate(
        execution.experiment_id,
        execution.candidate_index,
        "worker-1",
        NOW,
        NOW + timedelta(minutes=5),
    )
    failed = repository.mark_failed(
        running.execution_id,
        "worker-1",
        NOW,
        NOW,
        "TEMPORARY_FAILURE",
        "temporary failure",
        True,
    )
    pending = repository.retry_failed(failed.execution_id, now=NOW)
    running = repository.claim_candidate(
        execution.experiment_id,
        execution.candidate_index,
        "worker-2",
        NOW,
        NOW + timedelta(minutes=5),
    )
    failed_again = repository.mark_failed(
        running.execution_id,
        "worker-2",
        NOW,
        NOW,
        "TEMPORARY_FAILURE",
        "temporary failure",
        True,
    )
    assert pending.retry_count == 1
    assert failed_again.retry_count == 2
    with pytest.raises(ExecutionStateTransitionError):
        repository.retry_failed(failed_again.execution_id, now=NOW)


def test_repository_recovers_expired_lease_and_allows_reclaim(tmp_path) -> None:
    db_path, _, execution = _setup(tmp_path)
    repository = CandidateExecutionRepository(db_path)
    repository.create_execution(execution)
    repository.claim_candidate(
        execution.experiment_id,
        execution.candidate_index,
        "worker-1",
        NOW,
        NOW + timedelta(minutes=1),
    )
    recovered = repository.recover_expired_executions(NOW + timedelta(minutes=2))
    assert len(recovered) == 1
    reclaimed = repository.claim_candidate(
        execution.experiment_id,
        execution.candidate_index,
        "worker-2",
        NOW + timedelta(minutes=2),
        NOW + timedelta(minutes=5),
    )
    assert reclaimed.claimed_by == "worker-2"
    assert [event.event_type for event in repository.list_events(execution.execution_id)] == [
        "EXECUTION_CREATED",
        "EXECUTION_CLAIMED",
        "LEASE_RECOVERED",
        "EXECUTION_CLAIMED",
    ]


def test_repository_concurrent_claim_has_one_winner(tmp_path) -> None:
    db_path, _, execution = _setup(tmp_path)
    CandidateExecutionRepository(db_path).create_execution(execution)

    def claim(worker: str):
        try:
            return CandidateExecutionRepository(db_path).claim_candidate(
                execution.experiment_id,
                execution.candidate_index,
                worker,
                NOW,
                NOW + timedelta(minutes=5),
            )
        except Exception as exc:  # noqa: BLE001 - assert exactly one claim succeeds
            return exc

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(claim, ("worker-1", "worker-2")))
    assert sum(isinstance(result, CandidateExecution) for result in results) == 1
    assert sum(isinstance(result, ExecutionStateTransitionError) for result in results) == 1


def test_repository_corruption_is_rejected(tmp_path) -> None:
    db_path, _, execution = _setup(tmp_path)
    repository = CandidateExecutionRepository(db_path)
    repository.create_execution(execution)
    connection = sqlite3.connect(db_path)
    connection.execute(
        "UPDATE candidate_executions SET canonical_json = ? WHERE execution_id = ?",
        ('{"status":"tampered"}', execution.execution_id),
    )
    connection.commit()
    connection.close()
    with pytest.raises(CandidateExecutionPersistenceError):
        repository.get_execution(execution.execution_id)


def test_domain_rejects_malformed_identity() -> None:
    with pytest.raises(ValueError):
        CandidateExecution(
            execution_id="not-an-execution-id",
            experiment_id="experiment",
            experiment_hash="a" * 64,
            candidate_id="b" * 64,
            candidate_index=0,
            parameter_set_hash="c" * 64,
            parameter_binding_hash="d" * 64,
            base_strategy_version_id="strategy-v1",
            base_strategy_version_hash="e" * 64,
            derived_strategy_version_id=None,
            derived_strategy_version_hash=None,
            status=CandidateExecutionStatus.PENDING,
            created_at=NOW,
        )


def test_security_payload_rejects_secrets(tmp_path) -> None:
    db_path, _, execution = _setup(tmp_path)
    repository = CandidateExecutionRepository(db_path)
    with pytest.raises(ValueError):
        repository._safe_json({"api_key": "redacted"})


def test_identity_hash_is_stable() -> None:
    payload = {"x": 1, "y": 2}
    assert sha256_hash(payload) == sha256_hash({"y": 2, "x": 1})
