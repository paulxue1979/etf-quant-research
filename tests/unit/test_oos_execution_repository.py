from __future__ import annotations

import json
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from backend.app.oos_execution_repository import OosExecutionRepository
from backend.app.research_protocol import (
    OOSObservationStatus,
    ProtocolStatus,
    ResearchProtocolRepository,
)
from research.exceptions import (
    OosExecutionError,
    OosExecutionInProgressError,
    OosExecutionIntegrityError,
    OosExecutionLeaseMismatchError,
    OosExecutionNotRetryableError,
    OosExecutionNotRunningError,
    OosOfficialResultExistsError,
    OosProtocolStateError,
)
from research.oos import OosEvaluationRange, OosEvaluationSpec, OosExecutionStatus
from research.oos_execution import OosExecution, oos_execution_id_for
from tests.unit.test_oos_domain import (
    WARMUP_START,
    _identity,
    _oos_config,
    _protocol,
    _provenance,
    _version,
)

NOW = datetime(2026, 9, 13, 12, 0, tzinfo=UTC)


def _spec(*, protocol_status: str = ProtocolStatus.SELECTION_RECORDED) -> OosEvaluationSpec:
    version = _version()
    protocol = _protocol(status=protocol_status)
    identity = _identity(version)
    evaluation_range = OosEvaluationRange.from_protocol(protocol, warmup_start=WARMUP_START)
    return OosEvaluationSpec(
        identity=identity,
        evaluation_range=evaluation_range,
        configuration=_oos_config(),
        data_provenance=_provenance(),
    )


def _repository(tmp_path: Path, *, protocol_status: str = ProtocolStatus.SELECTION_RECORDED):
    db_path = tmp_path / "oos-execution.sqlite3"
    protocol_repository = ResearchProtocolRepository(db_path)
    protocol_repository.create_protocol(_protocol(status=protocol_status))
    return OosExecutionRepository(db_path), protocol_repository


def test_domain_round_trip_and_deterministic_identity() -> None:
    spec = _spec()
    execution = OosExecution.pending(spec, now=NOW)

    assert oos_execution_id_for(spec) == execution.execution_id
    assert OosExecution.from_dict(execution.to_dict()) == execution


def test_get_or_create_is_idempotent_and_persists_creation_event(tmp_path: Path) -> None:
    repository, _ = _repository(tmp_path)
    spec = _spec()

    first = repository.get_or_create_execution(spec, now=NOW)
    second = repository.get_or_create_execution(spec, now=NOW + timedelta(minutes=1))

    assert first == second
    assert first.status is OosExecutionStatus.PENDING
    assert repository.list_events(first.execution_id)[0].event_type == "EXECUTION_CREATED"


def test_same_protocol_with_different_spec_is_rejected(tmp_path: Path) -> None:
    repository, _ = _repository(tmp_path)
    first = _spec()
    repository.get_or_create_execution(first, now=NOW)
    changed = OosEvaluationSpec(
        identity=first.identity,
        evaluation_range=first.evaluation_range,
        configuration=first.configuration,
        data_provenance=_provenance(source="different-cache"),
    )

    with pytest.raises(Exception, match="different OOS execution identity"):
        repository.get_or_create_execution(changed, now=NOW)


def test_protocol_state_and_official_observation_guards(tmp_path: Path) -> None:
    repository, protocol_repository = _repository(
        tmp_path, protocol_status=ProtocolStatus.IS_EVALUATED
    )
    with pytest.raises(OosProtocolStateError):
        repository.get_or_create_execution(_spec(), now=NOW)

    repository, _ = _repository(
        tmp_path / "observed", protocol_status=ProtocolStatus.SELECTION_RECORDED
    )
    spec = _spec()
    connection = sqlite3.connect(repository.db_path)
    try:
        connection.execute(
            "INSERT INTO research_oos_evaluations VALUES (?, ?, ?, ?)",
            (
                "oos-result-existing",
                spec.identity.protocol_id,
                NOW.isoformat(),
                json.dumps({"status": OOSObservationStatus.OBSERVED}),
            ),
        )
        connection.commit()
    finally:
        connection.close()

    with pytest.raises(OosOfficialResultExistsError):
        repository.get_or_create_execution(spec, now=NOW)
    assert protocol_repository.get_protocol(spec.identity.protocol_id) is not None


def test_claim_renew_and_duplicate_claim_are_lease_bound(tmp_path: Path) -> None:
    repository, _ = _repository(tmp_path)
    execution = repository.get_or_create_execution(_spec(), now=NOW)
    claimed = repository.claim_execution(
        execution.execution_id,
        owner="worker-a",
        now=NOW,
        lease_duration=timedelta(minutes=5),
    )

    assert claimed.status is OosExecutionStatus.RUNNING
    assert claimed.attempt_count == 1
    with pytest.raises(OosExecutionInProgressError):
        repository.claim_execution(
            execution.execution_id,
            owner="worker-b",
            now=NOW + timedelta(minutes=1),
            lease_duration=timedelta(minutes=5),
        )
    with pytest.raises(OosExecutionLeaseMismatchError):
        repository.renew_lease(
            execution.execution_id,
            "wrong-token",
            NOW + timedelta(minutes=1),
            NOW + timedelta(minutes=10),
        )
    renewed = repository.renew_lease(
        execution.execution_id,
        claimed.lease_token or "",
        NOW + timedelta(minutes=1),
        NOW + timedelta(minutes=10),
    )
    assert renewed.lease_expires_at == NOW + timedelta(minutes=10)


def test_stale_recovery_rejects_old_worker_and_allows_retry(tmp_path: Path) -> None:
    repository, _ = _repository(tmp_path)
    execution = repository.get_or_create_execution(_spec(), now=NOW)
    claimed = repository.claim_execution(
        execution.execution_id,
        owner="worker-a",
        now=NOW,
        lease_duration=timedelta(minutes=1),
    )
    recovered = repository.recover_stale_executions(NOW + timedelta(minutes=2))
    assert recovered[0].status is OosExecutionStatus.PENDING

    with pytest.raises(OosExecutionNotRunningError):
        repository.renew_lease(
            execution.execution_id,
            claimed.lease_token or "",
            NOW + timedelta(minutes=2),
            NOW + timedelta(minutes=5),
        )
    retried = repository.claim_execution(
        execution.execution_id,
        owner="worker-b",
        now=NOW + timedelta(minutes=2),
        lease_duration=timedelta(minutes=5),
    )
    assert retried.attempt_count == 2


def test_retryable_failure_can_retry_but_blocked_failure_cannot(tmp_path: Path) -> None:
    repository, _ = _repository(tmp_path)
    execution = repository.get_or_create_execution(_spec(), now=NOW)
    claimed = repository.claim_execution(
        execution.execution_id,
        owner="worker-a",
        now=NOW,
        lease_duration=timedelta(minutes=5),
    )
    failed = repository.mark_failed(
        execution.execution_id,
        claimed.lease_token or "",
        NOW + timedelta(minutes=1),
        "TEMPORARY_DATA_ERROR",
        "temporary data source failure",
        retryable=True,
    )
    assert failed.status is OosExecutionStatus.FAILED
    retried = repository.claim_execution(
        execution.execution_id,
        owner="worker-b",
        now=NOW + timedelta(minutes=2),
        lease_duration=timedelta(minutes=5),
    )
    blocked = repository.mark_failed(
        execution.execution_id,
        retried.lease_token or "",
        NOW + timedelta(minutes=3),
        "INVALID_FROZEN_INPUT",
        "frozen input is invalid",
        retryable=False,
    )
    assert blocked.status is OosExecutionStatus.BLOCKED
    with pytest.raises(OosExecutionNotRetryableError):
        repository.claim_execution(
            execution.execution_id,
            owner="worker-c",
            now=NOW + timedelta(minutes=4),
            lease_duration=timedelta(minutes=5),
        )


def test_concurrent_create_and_claim_have_one_lineage_and_one_winner(tmp_path: Path) -> None:
    repository, _ = _repository(tmp_path)
    spec = _spec()

    def create() -> str:
        return repository.get_or_create_execution(spec, now=NOW).execution_id

    with ThreadPoolExecutor(max_workers=4) as pool:
        created_ids = list(pool.map(lambda _: create(), range(4)))
    assert set(created_ids) == {created_ids[0]}

    def claim(worker: int):
        try:
            return repository.claim_execution(
                created_ids[0],
                owner=f"worker-{worker}",
                now=NOW,
                lease_duration=timedelta(minutes=5),
            )
        except OosExecutionInProgressError:
            return None

    with ThreadPoolExecutor(max_workers=4) as pool:
        claims = list(pool.map(claim, range(4)))
    assert sum(item is not None for item in claims) == 1


def test_state_and_event_are_atomic_and_event_log_is_append_only(
    tmp_path: Path, monkeypatch
) -> None:
    repository, _ = _repository(tmp_path)
    execution = repository.get_or_create_execution(_spec(), now=NOW)
    original_events = repository.list_events(execution.execution_id)

    def fail_event(*args, **kwargs):
        raise OosExecutionError("event write failed")

    monkeypatch.setattr(repository, "_insert_event", fail_event)
    with pytest.raises(OosExecutionError, match="event write failed"):
        repository.claim_execution(
            execution.execution_id,
            owner="worker-a",
            now=NOW,
            lease_duration=timedelta(minutes=5),
        )
    assert repository.get_execution(execution.execution_id) == execution
    assert repository.list_events(execution.execution_id) == original_events


def test_failure_payload_rejects_tracebacks_and_credentials(tmp_path: Path) -> None:
    repository, _ = _repository(tmp_path)
    execution = repository.get_or_create_execution(_spec(), now=NOW)
    claimed = repository.claim_execution(
        execution.execution_id,
        owner="worker-a",
        now=NOW,
        lease_duration=timedelta(minutes=5),
    )

    with pytest.raises(OosExecutionError):
        repository.mark_failed(
            execution.execution_id,
            claimed.lease_token or "",
            NOW + timedelta(minutes=1),
            "UNSAFE_FAILURE",
            "Traceback: secret details",
            retryable=False,
        )
    assert repository.get_execution(execution.execution_id) == claimed


def test_persistence_restart_and_corruption_detection(tmp_path: Path) -> None:
    repository, _ = _repository(tmp_path)
    execution = repository.get_or_create_execution(_spec(), now=NOW)
    restarted = OosExecutionRepository(repository.db_path)
    assert restarted.get_execution(execution.execution_id) == execution

    connection = sqlite3.connect(repository.db_path)
    try:
        connection.execute(
            "UPDATE research_oos_executions SET canonical_json = ? WHERE execution_id = ?",
            ("{}", execution.execution_id),
        )
        connection.commit()
    finally:
        connection.close()
    with pytest.raises(OosExecutionIntegrityError):
        restarted.get_execution(execution.execution_id)
