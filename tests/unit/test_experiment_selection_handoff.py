from __future__ import annotations

import sqlite3
from concurrent.futures import ThreadPoolExecutor

import pytest

from backend.app.experiment_repository import ExperimentRepository
from backend.app.experiment_selection_handoff_service import (
    ExperimentSelectionHandoffError,
    ExperimentSelectionHandoffService,
)
from backend.app.experiment_selection_repository import ExperimentSelectionRepository
from backend.app.research_protocol import ProtocolStatus, ResearchProtocolRepository
from tests.unit.test_experiment_selection_persistence import _decision_fixture


def _prepared_fixture(tmp_path):
    database, decision, _ = _decision_fixture(tmp_path)
    ExperimentSelectionRepository(database).create(decision)
    protocols = ResearchProtocolRepository(database)
    protocols.transition_protocol(decision.protocol_id, ProtocolStatus.IS_EVALUATED)
    return database, decision, protocols


def test_handoff_persists_exact_derived_strategy_selection_and_freeze(tmp_path) -> None:
    database, decision, protocols = _prepared_fixture(tmp_path)

    selection, freeze = ExperimentSelectionHandoffService(database).handoff_experiment_selection(
        decision.experiment_id
    )

    assert selection.protocol_id == decision.protocol_id
    assert selection.selected_strategy_version_id == decision.selected_derived_strategy_version_id
    assert len(selection.is_backtest_run_ids) == 1
    assert selection.selected_metrics.keys() == {"cagr"}
    assert selection.data_provenance["handoff"] is True
    assert selection.data_provenance["selection_hash"] == decision.selection_hash
    assert freeze.strategy_version_id == decision.selected_derived_strategy_version_id
    assert freeze.strategy_version_content_hash == decision.selected_derived_strategy_content_hash
    assert protocols.get_protocol(decision.protocol_id).status == ProtocolStatus.SELECTION_RECORDED
    assert protocols.list_selections(decision.protocol_id) == (selection,)
    assert protocols.list_freezes(decision.protocol_id) == (freeze,)


def test_handoff_retry_is_idempotent_and_creates_one_governance_event(tmp_path) -> None:
    database, decision, protocols = _prepared_fixture(tmp_path)
    service = ExperimentSelectionHandoffService(database)

    first = service.handoff_experiment_selection(decision.experiment_id)
    second = ExperimentSelectionHandoffService(database).handoff_experiment_selection(
        decision.experiment_id
    )

    assert second == first
    connection = sqlite3.connect(database)
    try:
        count = connection.execute(
            "SELECT COUNT(*) FROM research_protocol_events "
            "WHERE protocol_id = ? AND event_type = ?",
            (decision.protocol_id, ProtocolStatus.SELECTION_RECORDED),
        ).fetchone()[0]
    finally:
        connection.close()
    assert count == 1
    assert len(protocols.list_selections(decision.protocol_id)) == 1
    assert len(protocols.list_freezes(decision.protocol_id)) == 1


def test_handoff_rejects_selection_hash_tampering(tmp_path) -> None:
    database, decision, _ = _prepared_fixture(tmp_path)
    connection = sqlite3.connect(database)
    try:
        connection.execute(
            "UPDATE research_experiment_selections SET selection_hash = ? WHERE selection_id = ?",
            ("f" * 64, decision.selection_id),
        )
        connection.commit()
    finally:
        connection.close()

    with pytest.raises(Exception):
        ExperimentSelectionHandoffService(database).handoff_experiment_selection(
            decision.experiment_id
        )


def test_handoff_rejects_protocol_state_before_is_evaluated(tmp_path) -> None:
    database, decision, _ = _decision_fixture(tmp_path)
    ExperimentSelectionRepository(database).create(decision)

    with pytest.raises(ExperimentSelectionHandoffError) as error:
        ExperimentSelectionHandoffService(database).handoff_experiment_selection(
            decision.experiment_id
        )

    assert error.value.code == "PROTOCOL_INVALID_STATE"


def test_handoff_rejects_base_strategy_candidate_set_mismatch(tmp_path) -> None:
    database, decision, protocols = _prepared_fixture(tmp_path)
    experiment = ExperimentRepository(database).get(decision.experiment_id)
    connection = sqlite3.connect(database)
    try:
        row = connection.execute(
            "SELECT payload_json FROM research_candidate_sets WHERE protocol_id = ?",
            (decision.protocol_id,),
        ).fetchone()
        payload = row[0].replace(experiment.base_strategy_version_id, "wrong-version")
        connection.execute(
            "UPDATE research_candidate_sets SET payload_json = ? WHERE protocol_id = ?",
            (payload, decision.protocol_id),
        )
        connection.commit()
    finally:
        connection.close()

    with pytest.raises(Exception):
        ExperimentSelectionHandoffService(database).handoff_experiment_selection(
            decision.experiment_id
        )
    assert protocols.list_selections(decision.protocol_id) == ()


def test_handoff_rolls_back_when_freeze_insert_fails(tmp_path, monkeypatch) -> None:
    database, decision, protocols = _prepared_fixture(tmp_path)

    def fail_freeze(*args, **kwargs):
        raise sqlite3.OperationalError("injected freeze insert failure")

    monkeypatch.setattr(protocols, "_insert_handoff_freeze", fail_freeze)
    service = ExperimentSelectionHandoffService(database, protocol_repository=protocols)

    with pytest.raises(Exception):
        service.handoff_experiment_selection(decision.experiment_id)

    assert protocols.list_selections(decision.protocol_id) == ()
    assert protocols.list_freezes(decision.protocol_id) == ()
    assert protocols.get_protocol(decision.protocol_id).status == ProtocolStatus.IS_EVALUATED


def test_concurrent_identical_handoffs_create_one_protocol_selection(tmp_path) -> None:
    database, decision, protocols = _prepared_fixture(tmp_path)

    def handoff_once():
        return ExperimentSelectionHandoffService(database).handoff_experiment_selection(
            decision.experiment_id
        )

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: handoff_once(), range(2)))

    assert results[0] == results[1]
    assert len(protocols.list_selections(decision.protocol_id)) == 1
    assert len(protocols.list_freezes(decision.protocol_id)) == 1
