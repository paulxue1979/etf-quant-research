from __future__ import annotations

import json
import sqlite3
from concurrent.futures import ThreadPoolExecutor

import pytest

from backend.app.experiment_repository import ExperimentPersistenceError, ExperimentRepository
from research import ExperimentStatus
from research.canonical import canonical_json
from tests.unit.test_experiment_repository import (
    _create_dependencies,
    _experiment,
)


def _stored_experiment(tmp_path):
    db_path, _, _, version = _create_dependencies(tmp_path)
    experiment = _experiment(version)
    repository = ExperimentRepository(db_path)
    repository.create(experiment)
    return db_path, experiment, repository


def test_valid_transition_updates_snapshot_and_appends_complete_event(tmp_path) -> None:
    _, experiment, repository = _stored_experiment(tmp_path)

    updated = repository.transition_status(
        experiment.experiment_id,
        ExperimentStatus.SPACE_FROZEN,
        ExperimentStatus.CANDIDATES_GENERATED,
        "CANDIDATES_GENERATED",
        "transition-candidates-1",
        {"source": "phase-8e-0b-test"},
    )

    assert updated.status is ExperimentStatus.CANDIDATES_GENERATED
    assert updated.content_hash != experiment.content_hash
    assert updated.created_at == experiment.created_at
    before = experiment.to_dict()
    after = updated.to_dict()
    assert {key: before[key] for key in before if key != "status"} == {
        key: after[key] for key in after if key != "status"
    }
    restored = ExperimentRepository(repository.db_path).get(experiment.experiment_id)
    assert restored == updated
    assert restored.content_hash == sha256_content(restored)

    event = repository.list_events(experiment.experiment_id)[-1]
    assert event.event_type == "CANDIDATES_GENERATED"
    assert event.transition_key == "transition-candidates-1"
    assert event.from_status is ExperimentStatus.SPACE_FROZEN
    assert event.to_status is ExperimentStatus.CANDIDATES_GENERATED
    assert event.provenance == {"source": "phase-8e-0b-test"}


def test_invalid_domain_transition_is_rejected_without_event(tmp_path) -> None:
    _, experiment, repository = _stored_experiment(tmp_path)

    with pytest.raises(ExperimentPersistenceError) as error:
        repository.transition_status(
            experiment.experiment_id,
            ExperimentStatus.SPACE_FROZEN,
            ExperimentStatus.RUNNING,
            "RUNNING",
            "invalid-transition",
        )

    assert error.value.code == "INVALID_EXPERIMENT_TRANSITION"
    assert repository.get(experiment.experiment_id) == experiment
    assert len(repository.list_events(experiment.experiment_id)) == 2


def test_expected_status_prevents_stale_writer(tmp_path) -> None:
    _, experiment, repository = _stored_experiment(tmp_path)
    repository.transition_status(
        experiment.experiment_id,
        ExperimentStatus.SPACE_FROZEN,
        ExperimentStatus.CANDIDATES_GENERATED,
        "CANDIDATES_GENERATED",
        "first-transition",
    )

    with pytest.raises(ExperimentPersistenceError) as error:
        repository.transition_status(
            experiment.experiment_id,
            ExperimentStatus.SPACE_FROZEN,
            ExperimentStatus.INVALID,
            "INVALID",
            "stale-transition",
        )

    assert error.value.code == "EXPERIMENT_STALE_STATE"
    assert len(repository.list_events(experiment.experiment_id)) == 3


def test_same_transition_key_retry_is_idempotent(tmp_path) -> None:
    _, experiment, repository = _stored_experiment(tmp_path)
    provenance = {"request_id": "request-1"}
    first = repository.transition_status(
        experiment.experiment_id,
        ExperimentStatus.SPACE_FROZEN,
        ExperimentStatus.CANDIDATES_GENERATED,
        "CANDIDATES_GENERATED",
        "same-transition",
        provenance,
    )
    second = repository.transition_status(
        experiment.experiment_id,
        ExperimentStatus.SPACE_FROZEN,
        ExperimentStatus.CANDIDATES_GENERATED,
        "CANDIDATES_GENERATED",
        "same-transition",
        provenance,
    )

    assert second == first
    assert len(repository.list_events(experiment.experiment_id)) == 3


@pytest.mark.parametrize(
    "target,event_type,provenance",
    [
        (ExperimentStatus.INVALID, "INVALID", {}),
        (ExperimentStatus.CANDIDATES_GENERATED, "OTHER", {}),
        (ExperimentStatus.CANDIDATES_GENERATED, "CANDIDATES_GENERATED", {"x": 1}),
    ],
)
def test_same_transition_key_conflict_is_rejected(
    tmp_path, target: ExperimentStatus, event_type: str, provenance: dict[str, object]
) -> None:
    _, experiment, repository = _stored_experiment(tmp_path)
    repository.transition_status(
        experiment.experiment_id,
        ExperimentStatus.SPACE_FROZEN,
        ExperimentStatus.CANDIDATES_GENERATED,
        "CANDIDATES_GENERATED",
        "reused-key",
        {},
    )

    with pytest.raises(ExperimentPersistenceError) as error:
        repository.transition_status(
            experiment.experiment_id,
            ExperimentStatus.SPACE_FROZEN,
            target,
            event_type,
            "reused-key",
            provenance,
        )

    assert error.value.code == "EXPERIMENT_TRANSITION_CONFLICT"
    assert len(repository.list_events(experiment.experiment_id)) == 3


def test_event_failure_rolls_back_experiment_update(tmp_path, monkeypatch) -> None:
    _, experiment, repository = _stored_experiment(tmp_path)

    def fail_event(*args, **kwargs):
        raise RuntimeError("event insert failed")

    monkeypatch.setattr(repository, "_insert_event", fail_event)
    with pytest.raises(ExperimentPersistenceError) as error:
        repository.transition_status(
            experiment.experiment_id,
            ExperimentStatus.SPACE_FROZEN,
            ExperimentStatus.CANDIDATES_GENERATED,
            "CANDIDATES_GENERATED",
            "rollback-event",
        )

    assert error.value.code == "EXPERIMENT_TRANSITION_INTEGRITY_ERROR"
    assert repository.get(experiment.experiment_id) == experiment
    assert len(repository.list_events(experiment.experiment_id)) == 2


def test_experiment_update_failure_rolls_back_without_event(tmp_path, monkeypatch) -> None:
    _, experiment, repository = _stored_experiment(tmp_path)

    def fail_update(*args, **kwargs):
        raise RuntimeError("snapshot update failed")

    monkeypatch.setattr(repository, "_update_experiment_row", fail_update)
    with pytest.raises(ExperimentPersistenceError) as error:
        repository.transition_status(
            experiment.experiment_id,
            ExperimentStatus.SPACE_FROZEN,
            ExperimentStatus.CANDIDATES_GENERATED,
            "CANDIDATES_GENERATED",
            "rollback-update",
        )

    assert error.value.code == "EXPERIMENT_TRANSITION_INTEGRITY_ERROR"
    assert repository.get(experiment.experiment_id) == experiment
    assert len(repository.list_events(experiment.experiment_id)) == 2


def test_competing_concurrent_transitions_allow_only_one_winner(tmp_path) -> None:
    db_path, experiment, _ = _stored_experiment(tmp_path)

    def run(target: ExperimentStatus, key: str):
        repository = ExperimentRepository(db_path)
        try:
            return ("ok", repository.transition_status(
                experiment.experiment_id,
                ExperimentStatus.SPACE_FROZEN,
                target,
                target.value.upper(),
                key,
            ))
        except ExperimentPersistenceError as exc:
            return (exc.code, None)

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(
            pool.map(
                lambda item: run(*item),
                (
                    (ExperimentStatus.CANDIDATES_GENERATED, "race-a"),
                    (ExperimentStatus.INVALID, "race-b"),
                ),
            )
        )

    assert [result[0] for result in results].count("ok") == 1
    assert sum(result[0] == "EXPERIMENT_STALE_STATE" for result in results) == 1
    events = ExperimentRepository(db_path).list_events(experiment.experiment_id)
    assert len(events) == 3


def test_same_transition_concurrent_retry_is_idempotent(tmp_path) -> None:
    db_path, experiment, _ = _stored_experiment(tmp_path)

    def run() -> ExperimentStatus:
        repository = ExperimentRepository(db_path)
        return repository.transition_status(
            experiment.experiment_id,
            ExperimentStatus.SPACE_FROZEN,
            ExperimentStatus.CANDIDATES_GENERATED,
            "CANDIDATES_GENERATED",
            "concurrent-same-key",
        ).status

    with ThreadPoolExecutor(max_workers=2) as pool:
        statuses = list(pool.map(lambda _: run(), range(2)))

    assert statuses == [ExperimentStatus.CANDIDATES_GENERATED] * 2
    assert len(ExperimentRepository(db_path).list_events(experiment.experiment_id)) == 3


def test_restart_preserves_transition_and_retry_identity(tmp_path) -> None:
    db_path, experiment, repository = _stored_experiment(tmp_path)
    updated = repository.transition_status(
        experiment.experiment_id,
        ExperimentStatus.SPACE_FROZEN,
        ExperimentStatus.CANDIDATES_GENERATED,
        "CANDIDATES_GENERATED",
        "restart-key",
        {"source": "restart-test"},
    )

    reopened = ExperimentRepository(db_path)
    assert reopened.get(experiment.experiment_id) == updated
    assert reopened.list_events(experiment.experiment_id)[-1].transition_key == "restart-key"
    assert reopened.transition_status(
        experiment.experiment_id,
        ExperimentStatus.SPACE_FROZEN,
        ExperimentStatus.CANDIDATES_GENERATED,
        "CANDIDATES_GENERATED",
        "restart-key",
        {"source": "restart-test"},
    ) == updated
    assert len(reopened.list_events(experiment.experiment_id)) == 3


def test_v1_event_migration_preserves_legacy_events(tmp_path) -> None:
    db_path, experiment, repository = _stored_experiment(tmp_path)
    legacy_events = repository.list_events(experiment.experiment_id)
    connection = sqlite3.connect(db_path)
    connection.execute("PRAGMA foreign_keys = OFF")
    connection.execute("DROP TABLE experiment_events")
    connection.execute(
        """
        CREATE TABLE experiment_events (
            event_sequence INTEGER PRIMARY KEY AUTOINCREMENT,
            event_id TEXT NOT NULL UNIQUE,
            experiment_id TEXT NOT NULL,
            event_type TEXT NOT NULL,
            created_at TEXT NOT NULL,
            payload_json TEXT NOT NULL,
            FOREIGN KEY(experiment_id) REFERENCES experiments(experiment_id)
        )
        """
    )
    connection.executemany(
        """
        INSERT INTO experiment_events(
            event_id, experiment_id, event_type, created_at, payload_json
        ) VALUES (?, ?, ?, ?, ?)
        """,
        [
            (
                event.event_id,
                event.experiment_id,
                event.event_type,
                event.created_at.isoformat(),
                canonical_json(event.payload),
            )
            for event in legacy_events
        ],
    )
    connection.execute(
        "UPDATE schema_metadata SET schema_version = 1 WHERE schema_key = ?",
        ("experiment_repository",),
    )
    connection.commit()
    connection.close()

    migrated = ExperimentRepository(db_path)
    assert [event.event_type for event in migrated.list_events(experiment.experiment_id)] == [
        "EXPERIMENT_CREATED",
        "SPACE_FROZEN",
    ]
    with sqlite3.connect(db_path) as connection:
        assert connection.execute(
            "SELECT schema_version FROM schema_metadata WHERE schema_key = ?",
            ("experiment_repository",),
        ).fetchone()[0] == 2
        columns = {
            row[1]
            for row in connection.execute("PRAGMA table_info(experiment_events)").fetchall()
        }
    assert {"transition_key", "from_status", "to_status", "provenance_json"} <= columns


def test_transition_provenance_rejects_sensitive_and_nonfinite_values(tmp_path) -> None:
    _, experiment, repository = _stored_experiment(tmp_path)

    with pytest.raises(ExperimentPersistenceError, match="sensitive"):
        repository.transition_status(
            experiment.experiment_id,
            ExperimentStatus.SPACE_FROZEN,
            ExperimentStatus.CANDIDATES_GENERATED,
            "CANDIDATES_GENERATED",
            "secret-key",
            {"authorization": "SECRET_SENTINEL_8E0B"},
        )

    with pytest.raises(ExperimentPersistenceError):
        repository.transition_status(
            experiment.experiment_id,
            ExperimentStatus.SPACE_FROZEN,
            ExperimentStatus.CANDIDATES_GENERATED,
            "CANDIDATES_GENERATED",
            "nan-key",
            {"metric": float("nan")},
        )

    database_bytes = repository.db_path.read_bytes()
    assert b"SECRET_SENTINEL_8E0B" not in database_bytes


def test_event_rows_are_not_duplicated_or_mutated_by_retry(tmp_path) -> None:
    _, experiment, repository = _stored_experiment(tmp_path)
    repository.transition_status(
        experiment.experiment_id,
        ExperimentStatus.SPACE_FROZEN,
        ExperimentStatus.CANDIDATES_GENERATED,
        "CANDIDATES_GENERATED",
        "immutable-event",
        {"a": 1},
    )
    before = repository.list_events(experiment.experiment_id)
    repository.transition_status(
        experiment.experiment_id,
        ExperimentStatus.SPACE_FROZEN,
        ExperimentStatus.CANDIDATES_GENERATED,
        "CANDIDATES_GENERATED",
        "immutable-event",
        {"a": 1},
    )
    assert repository.list_events(experiment.experiment_id) == before


def sha256_content(experiment) -> str:
    from research.canonical import sha256_hash

    return sha256_hash(json.loads(experiment.canonical_json()))
