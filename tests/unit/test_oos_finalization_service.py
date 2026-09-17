from __future__ import annotations

from dataclasses import replace
from datetime import timedelta

import pytest

from backend.app.backtest_repository import BacktestRepository
from backend.app.oos_result_repository import OosResultRepository
from backend.app.research_protocol import ProtocolStatus
from research.exceptions import (
    OosAlreadyObservedError,
    OosFinalizationPersistenceError,
    OosFinalizationStaleWriterError,
)
from research.oos import OosExecutionStatus
from research.oos_finalization_service import OosFinalizationService
from tests.unit.test_oos_execution_service import (
    NOW,
    FakeDataService,
    FakeStrategyRepository,
    _claimed,
    _dataset,
    _service,
)


def _prepared(tmp_path):
    version, protocols, executions, spec, claimed = _claimed(tmp_path)
    data = {asset.symbol: _dataset(asset.symbol) for asset in version.configuration.assets}
    execution_service = _service(
        tmp_path,
        FakeDataService(data),
        execution_repository=executions,
        protocol_repository=protocols,
        version=version,
    )
    outcome = execution_service.execute(
        protocol_id=claimed.protocol_id,
        execution_id=claimed.execution_id,
        lease_token=claimed.lease_token or "",
        spec=spec,
    )
    finalizer = OosFinalizationService(
        protocol_repository=protocols,
        execution_repository=executions,
        result_repository=OosResultRepository(tmp_path / "research.sqlite3"),
        backtest_repository=BacktestRepository(tmp_path / "research.sqlite3"),
        strategy_repository=FakeStrategyRepository(version),
        clock=lambda: NOW + timedelta(minutes=1),
    )
    return finalizer, outcome, protocols, executions


def test_finalization_atomically_persists_all_official_records(tmp_path):
    finalizer, outcome, protocols, executions = _prepared(tmp_path)

    result = finalizer.finalize_oos_observation(
        protocol_id=outcome.protocol_id,
        execution_id=outcome.execution_id,
        lease_token=executions.get_execution(outcome.execution_id).lease_token or "",
        outcome=outcome,
    )

    assert executions.get_execution(outcome.execution_id).status is OosExecutionStatus.COMPLETED
    assert protocols.get_protocol(outcome.protocol_id).status == ProtocolStatus.OOS_EVALUATED
    assert OosResultRepository(tmp_path / "research.sqlite3").get_by_protocol(
        outcome.protocol_id
    ) == result
    run = BacktestRepository(tmp_path / "research.sqlite3").get(result.backtest_run_id)
    assert run is not None
    assert run.backtest_result == outcome.backtest_result
    assert run.performance_analysis.backtest_run_id == result.backtest_run_id
    assert run.strategy_provenance == outcome.strategy_provenance
    assert len(protocols.list_oos_evaluations(outcome.protocol_id)) == 1


def test_finalization_retry_is_idempotent_and_deterministic(tmp_path):
    finalizer, outcome, _, executions = _prepared(tmp_path)
    token = executions.get_execution(outcome.execution_id).lease_token or ""
    first = finalizer.finalize_oos_observation(
        protocol_id=outcome.protocol_id,
        execution_id=outcome.execution_id,
        lease_token=token,
        outcome=outcome,
    )
    second = finalizer.finalize_oos_observation(
        protocol_id=outcome.protocol_id,
        execution_id=outcome.execution_id,
        lease_token=token,
        outcome=outcome,
    )

    assert second == first
    assert OosResultRepository(tmp_path / "research.sqlite3").get_by_protocol(
        outcome.protocol_id
    ) is not None
    assert len(BacktestRepository(tmp_path / "research.sqlite3").list()) == 1


def test_stale_lease_is_rejected_before_persistence(tmp_path):
    finalizer, outcome, protocols, executions = _prepared(tmp_path)
    stale = OosFinalizationService(
        protocol_repository=protocols,
        execution_repository=executions,
        result_repository=OosResultRepository(tmp_path / "research.sqlite3"),
        backtest_repository=BacktestRepository(tmp_path / "research.sqlite3"),
        strategy_repository=finalizer._strategies,
        clock=lambda: NOW + timedelta(minutes=10),
    )
    token = executions.get_execution(outcome.execution_id).lease_token or ""

    with pytest.raises(OosFinalizationStaleWriterError):
        stale.finalize_oos_observation(
            protocol_id=outcome.protocol_id,
            execution_id=outcome.execution_id,
            lease_token=token,
            outcome=outcome,
        )
    assert executions.get_execution(outcome.execution_id).status is OosExecutionStatus.RUNNING
    assert protocols.get_protocol(outcome.protocol_id).status == ProtocolStatus.SELECTION_RECORDED
    assert OosResultRepository(tmp_path / "research.sqlite3").get_by_protocol(
        outcome.protocol_id
    ) is None


def test_result_write_failure_rolls_back_backtest_and_execution(monkeypatch, tmp_path):
    finalizer, outcome, protocols, executions = _prepared(tmp_path)
    token = executions.get_execution(outcome.execution_id).lease_token or ""

    def fail_result_write(cls, connection, result):
        raise OosFinalizationPersistenceError("injected result persistence failure")

    monkeypatch.setattr(
        OosResultRepository,
        "persist_in_transaction",
        classmethod(fail_result_write),
    )
    with pytest.raises(OosFinalizationPersistenceError):
        finalizer.finalize_oos_observation(
            protocol_id=outcome.protocol_id,
            execution_id=outcome.execution_id,
            lease_token=token,
            outcome=outcome,
        )

    assert executions.get_execution(outcome.execution_id).status is OosExecutionStatus.RUNNING
    assert protocols.get_protocol(outcome.protocol_id).status == ProtocolStatus.SELECTION_RECORDED
    assert OosResultRepository(tmp_path / "research.sqlite3").get_by_protocol(
        outcome.protocol_id
    ) is None
    assert BacktestRepository(tmp_path / "research.sqlite3").list() == ()


def test_terminal_event_failure_rolls_back_every_record(monkeypatch, tmp_path):
    finalizer, outcome, protocols, executions = _prepared(tmp_path)
    token = executions.get_execution(outcome.execution_id).lease_token or ""

    def fail_transition(*args, **kwargs):
        raise RuntimeError("injected terminal event failure")

    monkeypatch.setattr(finalizer, "_transition_protocol", fail_transition)
    with pytest.raises(OosFinalizationPersistenceError):
        finalizer.finalize_oos_observation(
            protocol_id=outcome.protocol_id,
            execution_id=outcome.execution_id,
            lease_token=token,
            outcome=outcome,
        )

    assert executions.get_execution(outcome.execution_id).status is OosExecutionStatus.RUNNING
    assert protocols.get_protocol(outcome.protocol_id).status == ProtocolStatus.SELECTION_RECORDED
    assert OosResultRepository(tmp_path / "research.sqlite3").get_by_protocol(
        outcome.protocol_id
    ) is None
    assert BacktestRepository(tmp_path / "research.sqlite3").list() == ()


def test_conflicting_retry_cannot_replace_official_result(tmp_path):
    finalizer, outcome, _, executions = _prepared(tmp_path)
    token = executions.get_execution(outcome.execution_id).lease_token or ""
    first = finalizer.finalize_oos_observation(
        protocol_id=outcome.protocol_id,
        execution_id=outcome.execution_id,
        lease_token=token,
        outcome=outcome,
    )
    changed = replace(outcome, analytics_version="analytics-different", outcome_hash=None)

    with pytest.raises(OosAlreadyObservedError):
        finalizer.finalize_oos_observation(
            protocol_id=outcome.protocol_id,
            execution_id=outcome.execution_id,
            lease_token=token,
            outcome=changed,
        )
    assert OosResultRepository(tmp_path / "research.sqlite3").get_by_protocol(
        outcome.protocol_id
    ) == first


def test_result_and_backtest_hashes_round_trip_from_canonical_storage(tmp_path):
    finalizer, outcome, _, executions = _prepared(tmp_path)
    token = executions.get_execution(outcome.execution_id).lease_token or ""
    result = finalizer.finalize_oos_observation(
        protocol_id=outcome.protocol_id,
        execution_id=outcome.execution_id,
        lease_token=token,
        outcome=outcome,
    )

    restored = OosResultRepository(tmp_path / "research.sqlite3").get_by_protocol(
        outcome.protocol_id
    )
    assert restored is not None
    assert restored.result_hash == result.result_hash
    assert restored.to_dict() == result.to_dict()
    assert BacktestRepository(tmp_path / "research.sqlite3").get(result.backtest_run_id) is not None
