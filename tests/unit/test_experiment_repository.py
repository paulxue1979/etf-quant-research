from __future__ import annotations

import json
import sqlite3
from dataclasses import replace
from datetime import UTC, date, datetime

import pytest

from backend.app.experiment_repository import (
    ExperimentPersistenceError,
    ExperimentRepository,
)
from backend.app.research_protocol import (
    ResearchProtocol,
    ResearchProtocolRepository,
)
from backend.app.strategy_repository import StrategyRepository
from backtest import BacktestConfig, RebalanceFrequency, RebalancePolicy
from data.models import PriceField
from research import (
    Experiment,
    ExperimentMethod,
    ExperimentProvenance,
    ExperimentStatus,
    MetricDirection,
    ObjectiveSpecification,
    ParameterDefinition,
    ParameterSpace,
    ParameterType,
    generate_candidates,
)
from research.canonical import canonical_json
from tests.unit.test_strategy_repository import _definition


def _protocol(protocol_id: str = "protocol-8c") -> ResearchProtocol:
    return ResearchProtocol(
        protocol_id=protocol_id,
        protocol_version=1,
        created_at=datetime(2026, 9, 8, tzinfo=UTC),
        is_start_date=date(2020, 1, 2),
        is_end_date=date(2020, 12, 31),
        oos_start_date=date(2021, 1, 4),
        oos_end_date=date(2021, 12, 31),
        selection_rules=("human review of IS metrics",),
        allowed_metrics=("cagr",),
        forbidden_actions=("oos_back_selection",),
    )


def _space() -> ParameterSpace:
    return ParameterSpace(
        parameters=(
            ParameterDefinition("fast", ParameterType.INTEGER, min=5, max=10, step=5),
            ParameterDefinition("threshold", ParameterType.FLOAT, min=0.0, max=0.1, step=0.1),
        ),
        max_candidates=4,
    )


def _objective() -> ObjectiveSpecification:
    return ObjectiveSpecification(
        primary_metric="cagr",
        metric_directions={"cagr": MetricDirection.MAXIMIZE},
    )


def _create_dependencies(tmp_path, protocol_id: str = "protocol-8c"):
    db_path = tmp_path / "experiment.db"
    protocols = ResearchProtocolRepository(db_path)
    strategies = StrategyRepository(db_path)
    protocols.create_protocol(_protocol(protocol_id))
    version = strategies.create(_definition("strategy-8c"))
    return db_path, protocols, strategies, version


def _experiment(
    version,
    protocol_id: str = "protocol-8c",
    experiment_id: str = "experiment-8c",
) -> Experiment:
    space = _space()
    objective = _objective()
    return Experiment(
        experiment_id=experiment_id,
        protocol_id=protocol_id,
        strategy_definition_id=version.strategy_id,
        base_strategy_version_id=version.version_id,
        base_strategy_version_hash=version.content_hash or "",
        parameter_space=space,
        objective_specification=objective,
        is_start_date=date(2020, 1, 2),
        is_end_date=date(2020, 12, 31),
        backtest_configuration=BacktestConfig(
            strategy_version_id=version.version_id,
            start_date=date(2020, 1, 2),
            end_date=date(2020, 12, 31),
            initial_capital=10_000,
            price_field_used=PriceField.ADJUSTED_CLOSE,
            rebalance_policy=RebalancePolicy(RebalanceFrequency.MONTHLY),
        ),
        engine_version="phase-3.0",
        analysis_version="phase-4i.0",
        status=ExperimentStatus.SPACE_FROZEN,
        created_at=datetime(2026, 9, 8, tzinfo=UTC),
        method=ExperimentMethod.GRID,
        provenance=ExperimentProvenance(
            protocol_id=protocol_id,
            strategy_definition_id=version.strategy_id,
            base_strategy_version_id=version.version_id,
            base_strategy_version_hash=version.content_hash or "",
            parameter_space_hash=space.content_hash,
            objective_spec_hash=objective.content_hash,
            is_start_date=date(2020, 1, 2),
            is_end_date=date(2020, 12, 31),
            price_field_used=PriceField.ADJUSTED_CLOSE,
            initial_capital=10_000,
            commission={"rate": 0.0, "per_order": 0.0},
            slippage=0.0,
            execution_rule="next_trading_day_open",
            fractional_shares=False,
            rebalance_policy={"frequency": "monthly", "threshold": None},
            engine_version="phase-3.0",
            analysis_version="phase-4i.0",
            data_snapshot_reference={"symbol": "QQQ", "frequency": "daily"},
        ),
    )


def test_create_restart_read_preserves_hashes_and_candidate_order(tmp_path) -> None:
    db_path, _, _, version = _create_dependencies(tmp_path)
    experiment = _experiment(version)
    candidates = generate_candidates(experiment)
    repository = ExperimentRepository(db_path)
    repository.create(experiment, candidates)

    reopened = ExperimentRepository(db_path)
    restored = reopened.get(experiment.experiment_id)
    restored_candidates = reopened.get_candidate_set(experiment.experiment_id)
    records = reopened.list_candidates(experiment.experiment_id)

    assert restored == experiment
    assert restored.content_hash == experiment.content_hash
    assert restored.parameter_space_hash == experiment.parameter_space.content_hash
    assert restored_candidates == candidates
    assert [record.candidate_index for record in records] == [0, 1, 2, 3]
    assert [record.parameter_set_hash for record in records] == [
        item.content_hash for item in candidates.candidates
    ]


def test_parameter_space_and_parameter_set_can_be_loaded_by_hash(tmp_path) -> None:
    db_path, _, _, version = _create_dependencies(tmp_path)
    experiment = _experiment(version)
    generated = generate_candidates(experiment)
    repository = ExperimentRepository(db_path)
    repository.create(experiment, generated)

    restored_space = repository.get_parameter_space(experiment.parameter_space_hash)
    restored_set = repository.get_parameter_set(generated.candidates[2].content_hash)

    assert restored_space == experiment.parameter_space
    assert restored_set == generated.candidates[2]


def test_protocol_and_strategy_version_bindings_are_required(tmp_path) -> None:
    db_path = tmp_path / "missing.db"
    strategies = StrategyRepository(db_path)
    version = strategies.create(_definition("strategy-8c"))
    experiment = _experiment(version, protocol_id="missing-protocol")

    with pytest.raises(ExperimentPersistenceError, match="RESEARCH_PROTOCOL_NOT_FOUND"):
        ExperimentRepository(db_path).create(experiment)

    db_path, _, _, version = _create_dependencies(tmp_path / "bound")
    repository = ExperimentRepository(db_path)
    valid_experiment = _experiment(version)
    mismatched = replace(
        valid_experiment,
        base_strategy_version_hash="0" * 64,
        provenance=replace(valid_experiment.provenance, base_strategy_version_hash="0" * 64),
    )
    with pytest.raises(ExperimentPersistenceError, match="STRATEGY_VERSION_HASH_MISMATCH"):
        repository.create(mismatched)


def test_duplicate_experiment_and_corrupted_json_fail_explicitly(tmp_path) -> None:
    db_path, _, _, version = _create_dependencies(tmp_path)
    experiment = _experiment(version)
    repository = ExperimentRepository(db_path)
    repository.create(experiment)

    with pytest.raises(ExperimentPersistenceError, match="already exists"):
        repository.create(experiment)

    connection = sqlite3.connect(db_path)
    connection.execute(
        "UPDATE experiments SET experiment_json = ? WHERE experiment_id = ?",
        ('{"status":"tampered"}', experiment.experiment_id),
    )
    connection.commit()
    connection.close()

    with pytest.raises(ExperimentPersistenceError, match="integrity|canonical"):
        repository.get(experiment.experiment_id)


def test_atomic_candidate_failure_leaves_no_experiment(tmp_path, monkeypatch) -> None:
    db_path, _, _, version = _create_dependencies(tmp_path)
    experiment = _experiment(version)
    generated = generate_candidates(experiment)
    repository = ExperimentRepository(db_path)
    original_insert = repository._insert_candidate
    calls = 0

    def fail_after_first(*args, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise RuntimeError("simulated candidate failure")
        return original_insert(*args, **kwargs)

    monkeypatch.setattr(repository, "_insert_candidate", fail_after_first)
    with pytest.raises(ExperimentPersistenceError, match="atomically"):
        repository.create(experiment, generated)

    connection = sqlite3.connect(db_path)
    assert (
        connection.execute(
            "SELECT COUNT(*) FROM experiments WHERE experiment_id = ?", (experiment.experiment_id,)
        ).fetchone()[0]
        == 0
    )
    assert connection.execute("SELECT COUNT(*) FROM experiment_candidates").fetchone()[0] == 0
    connection.close()


def test_events_are_append_only_and_status_is_not_overwritten(tmp_path) -> None:
    db_path, _, _, version = _create_dependencies(tmp_path)
    experiment = _experiment(version)
    repository = ExperimentRepository(db_path)
    repository.create(experiment, generate_candidates(experiment))

    events = repository.list_events(experiment.experiment_id)
    assert [event.event_type for event in events] == [
        "EXPERIMENT_CREATED",
        "SPACE_FROZEN",
        "CANDIDATES_GENERATED",
    ]
    assert repository.get(experiment.experiment_id).status is ExperimentStatus.SPACE_FROZEN


def test_protocol_and_experiment_candidate_isolation(tmp_path) -> None:
    db_path, protocols, _, version = _create_dependencies(tmp_path, "protocol-8c-a")
    protocols.create_protocol(_protocol("protocol-8c-b"))
    experiment_a = _experiment(version, "protocol-8c-a", "experiment-8c-a")
    experiment_b = _experiment(version, "protocol-8c-b", "experiment-8c-b")
    repository = ExperimentRepository(db_path)
    repository.create(experiment_a, generate_candidates(experiment_a))
    repository.create(experiment_b, generate_candidates(experiment_b))

    assert repository.list("protocol-8c-a") == (experiment_a,)
    assert repository.list("protocol-8c-b") == (experiment_b,)
    assert {record.experiment_id for record in repository.list_candidates("experiment-8c-a")} == {
        "experiment-8c-a"
    }
    assert {record.experiment_id for record in repository.list_candidates("experiment-8c-b")} == {
        "experiment-8c-b"
    }
    assert repository.list_candidates("unknown-experiment") == ()


def test_database_constraints_reject_duplicate_candidate_index_and_parameter_set(tmp_path) -> None:
    db_path, _, _, version = _create_dependencies(tmp_path)
    experiment = _experiment(version)
    generated = generate_candidates(experiment)
    ExperimentRepository(db_path).create(experiment, generated)
    candidate_hash = generated.candidates[0].content_hash

    connection = sqlite3.connect(db_path)
    with pytest.raises(sqlite3.IntegrityError):
        connection.execute(
            "INSERT INTO experiment_candidates VALUES (?, ?, ?, ?, ?)",
            (experiment.experiment_id, 0, candidate_hash, generated.candidate_set_hash, "pending"),
        )
    connection.rollback()
    with pytest.raises(sqlite3.IntegrityError):
        connection.execute(
            "INSERT INTO experiment_candidates VALUES (?, ?, ?, ?, ?)",
            (experiment.experiment_id, 99, candidate_hash, generated.candidate_set_hash, "pending"),
        )
    connection.close()


def test_frozen_experiment_cannot_be_overwritten_by_a_new_snapshot(tmp_path) -> None:
    db_path, _, _, version = _create_dependencies(tmp_path)
    experiment = _experiment(version)
    repository = ExperimentRepository(db_path)
    repository.create(experiment, generate_candidates(experiment))

    with pytest.raises(ExperimentPersistenceError, match="already exists"):
        repository.create(experiment.with_status(ExperimentStatus.CANDIDATES_GENERATED))

    assert repository.get(experiment.experiment_id) == experiment
    assert [event.event_type for event in repository.list_events(experiment.experiment_id)] == [
        "EXPERIMENT_CREATED",
        "SPACE_FROZEN",
        "CANDIDATES_GENERATED",
    ]


def test_sensitive_provenance_is_rejected(tmp_path) -> None:
    db_path, _, _, version = _create_dependencies(tmp_path)
    experiment = _experiment(version)
    unsafe = replace(
        experiment,
        provenance=replace(
            experiment.provenance,
            data_snapshot_reference={"symbol": "QQQ", "api_key": "must-not-persist"},
        ),
    )

    with pytest.raises(ExperimentPersistenceError, match="sensitive"):
        ExperimentRepository(db_path).create(unsafe)


def test_candidate_set_metadata_is_canonical_json(tmp_path) -> None:
    db_path, _, _, version = _create_dependencies(tmp_path)
    experiment = _experiment(version)
    generated = generate_candidates(experiment)
    repository = ExperimentRepository(db_path)
    repository.create(experiment, generated)

    connection = sqlite3.connect(db_path)
    raw = connection.execute(
        "SELECT canonical_json FROM experiment_candidate_sets WHERE experiment_id = ?",
        (experiment.experiment_id,),
    ).fetchone()[0]
    assert json.loads(raw)["candidate_set_hash"] == generated.candidate_set_hash
    connection.close()


def test_parameter_space_and_candidate_hash_corruption_are_rejected_on_read(tmp_path) -> None:
    db_path, _, _, version = _create_dependencies(tmp_path)
    experiment = _experiment(version)
    generated = generate_candidates(experiment)
    repository = ExperimentRepository(db_path)
    repository.create(experiment, generated)

    connection = sqlite3.connect(db_path)
    connection.execute(
        "UPDATE experiment_parameter_spaces SET canonical_json = ? WHERE parameter_space_hash = ?",
        (
            canonical_json({"parameters": [], "constraints": [], "max_candidates": 4}),
            experiment.parameter_space_hash,
        ),
    )
    connection.commit()
    connection.close()

    with pytest.raises(ExperimentPersistenceError, match="parameter space.*hash"):
        repository.get(experiment.experiment_id)

    db_path, _, _, version = _create_dependencies(tmp_path / "candidate-hash")
    experiment = _experiment(version)
    generated = generate_candidates(experiment)
    repository = ExperimentRepository(db_path)
    repository.create(experiment, generated)
    connection = sqlite3.connect(db_path)
    connection.execute(
        "UPDATE experiment_candidate_sets SET candidate_set_hash = ? WHERE experiment_id = ?",
        ("0" * 64, experiment.experiment_id),
    )
    connection.commit()
    connection.close()

    with pytest.raises(ExperimentPersistenceError, match="candidate set metadata hash mismatch"):
        repository.get_candidate_set(experiment.experiment_id)


def test_parameter_set_space_binding_and_candidate_metadata_content_are_checked(tmp_path) -> None:
    db_path, _, _, version = _create_dependencies(tmp_path)
    experiment = _experiment(version)
    generated = generate_candidates(experiment)
    repository = ExperimentRepository(db_path)
    repository.create(experiment, generated)

    connection = sqlite3.connect(db_path)
    connection.execute(
        "UPDATE experiment_parameter_sets SET parameter_space_hash = NULL "
        "WHERE parameter_set_hash = ?",
        (generated.candidates[0].content_hash,),
    )
    connection.commit()
    connection.close()

    with pytest.raises(ExperimentPersistenceError, match="parameter set parameter space mismatch"):
        repository.list_candidates(experiment.experiment_id)

    db_path, _, _, version = _create_dependencies(tmp_path / "candidate-content")
    experiment = _experiment(version)
    generated = generate_candidates(experiment)
    repository = ExperimentRepository(db_path)
    repository.create(experiment, generated)
    connection = sqlite3.connect(db_path)
    raw = connection.execute(
        "SELECT canonical_json FROM experiment_candidate_sets WHERE experiment_id = ?",
        (experiment.experiment_id,),
    ).fetchone()[0]
    payload = json.loads(raw)
    payload["candidates"] = list(reversed(payload["candidates"]))
    connection.execute(
        "UPDATE experiment_candidate_sets SET canonical_json = ? WHERE experiment_id = ?",
        (canonical_json(payload), experiment.experiment_id),
    )
    connection.commit()
    connection.close()

    with pytest.raises(ExperimentPersistenceError, match="candidate set content mismatch"):
        repository.get_candidate_set(experiment.experiment_id)
