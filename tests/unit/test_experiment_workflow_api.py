import sqlite3
from dataclasses import replace
from datetime import date

from fastapi.testclient import TestClient

from backend.app import experiment_workflow_api as api
from backend.app.experiment_repository import ExperimentRepository
from backend.app.main import app
from backend.app.research_protocol import ResearchProtocolRepository
from backend.app.strategy_repository import StrategyRepository
from research.grid_search import GridSearchDefinition, preflight_grid_search
from strategies import RebalanceFrequency, RebalancePolicy
from tests.unit.test_experiment_repository import _protocol
from tests.unit.test_semantic_bindings import _version as _semantic_version


def _body(version_id: str, version_hash: str) -> dict[str, object]:
    return {
        "protocol_id": "protocol-8c",
        "base_strategy_version_id": version_id,
        "name": "UI smoke experiment",
        "description": "Created through the immutable workflow.",
        "is_start": "2020-01-02",
        "is_end": "2020-12-31",
        "initial_capital": 10_000,
        "price_field": "adjusted_close",
        "parameter_space": {
            "parameters": [
                {"name": "threshold", "type": "discrete", "allowed_values": [0.01, 0.03]},
                {"name": "period", "type": "discrete", "allowed_values": [150, 200]},
            ],
            "constraints": [],
            "max_candidates": 100,
        },
        "objective_specification": {
            "primary_metric": "cagr",
            "metric_directions": {"cagr": "maximize"},
        },
        "grid_definition": {
            "semantic_bindings": [
                {
                    "parameter_name": "threshold",
                    "value_type": "relative_threshold",
                    "binding_version": "phase-13e-1-v1",
                    "descriptor": {
                        "target_kind": "nested_condition_threshold",
                        "base_strategy_version_id": version_id,
                        "base_strategy_version_hash": version_hash,
                        "transition_id": "normal-approaching",
                        "condition_path": [0],
                    },
                },
                {
                    "parameter_name": "period",
                    "value_type": "positive_integer",
                    "binding_version": "phase-13e-1-v1",
                    "descriptor": {
                        "target_kind": "nested_condition_period",
                        "base_strategy_version_id": version_id,
                        "base_strategy_version_hash": version_hash,
                        "transition_id": "normal-approaching",
                        "condition_path": [1],
                        "operand_side": "right",
                    },
                },
            ],
            "fixed_parameters": {},
            "constraints": [],
            "max_candidates": 100,
            "theoretical_guard": 1_000_000,
        },
    }


def test_workflow_persists_resolved_bindings_and_is_idempotent(tmp_path, monkeypatch) -> None:
    db_path = tmp_path / "workflow.db"
    protocols = ResearchProtocolRepository(db_path)
    protocols.create_protocol(_protocol())
    strategies = StrategyRepository(db_path)
    semantic_version = _semantic_version()
    version = strategies.create(semantic_version.configuration)
    experiments = ExperimentRepository(db_path)
    monkeypatch.setattr(api, "protocol_repository", protocols)
    monkeypatch.setattr(api, "strategy_repository", strategies)
    monkeypatch.setattr(api, "experiment_repository", experiments)

    client = TestClient(app)
    body = _body(version.version_id, version.content_hash)
    preview = client.post("/research/experiments/preflight", json=body)
    assert preview.status_code == 200
    assert preview.json()["status"] == "ready"
    assert preview.json()["theoretical_count"] == 4
    assert preview.json()["valid_count"] == 4
    assert preview.json()["executable_count"] == 4

    created = client.post("/research/experiments", json=body)
    assert created.status_code == 200
    assert created.json()["display_name"] == "UI smoke experiment"
    repeated = client.post("/research/experiments", json=body)
    assert repeated.status_code == 200
    assert repeated.json()["idempotent"] is True
    assert len(created.json()["provenance"]["parameter_bindings"]) == 2

    listed = client.get("/research/experiments?query=smoke")
    assert listed.status_code == 200
    assert listed.json()["total"] == 1
    assert listed.json()["items"][0]["is_start_date"] == date(2020, 1, 2).isoformat()
    loaded = client.get(f"/research/experiments/{created.json()['experiment_id']}")
    assert loaded.status_code == 200
    assert {
        item["parameter_name"] for item in loaded.json()["provenance"]["parameter_bindings"]
    } == {"period", "threshold"}
    restored = experiments.get(created.json()["experiment_id"])
    assert restored is not None
    definition = GridSearchDefinition.from_dict(
        {
            "experiment_id": restored.experiment_id,
            "experiment_hash": restored.content_hash,
            "strategy_bindings": restored.provenance.parameter_bindings,
            "backtest_bindings": [],
            "fixed_parameters": {},
            "constraints": [],
            "max_candidates": 100,
            "theoretical_guard": 1_000_000,
        }
    )
    post_reload = preflight_grid_search(restored, definition, version)
    assert (
        post_reload.theoretical_count,
        post_reload.valid_count,
        post_reload.executable_count,
    ) == (4, 4, 4)


def test_library_read_model_skips_missing_historical_strategy_binding(
    tmp_path, monkeypatch
) -> None:
    db_path = tmp_path / "workflow-history.db"
    protocols = ResearchProtocolRepository(db_path)
    protocols.create_protocol(_protocol())
    strategies = StrategyRepository(db_path)
    version = strategies.create(_semantic_version().configuration)
    experiments = ExperimentRepository(db_path)
    monkeypatch.setattr(api, "protocol_repository", protocols)
    monkeypatch.setattr(api, "strategy_repository", strategies)
    monkeypatch.setattr(api, "experiment_repository", experiments)

    client = TestClient(app)
    created = client.post(
        "/research/experiments", json=_body(version.version_id, version.content_hash)
    )
    assert created.status_code == 200

    connection = sqlite3.connect(db_path)
    connection.execute("DELETE FROM strategy_versions WHERE version_id = ?", (version.version_id,))
    connection.commit()
    connection.close()

    response = client.get("/research/experiments")
    assert response.status_code == 200
    assert response.json()["items"] == []
    assert response.json()["skipped_count"] == 1
    assert response.json()["warnings"]


def test_workflow_backtest_policy_follows_weekly_strategy(tmp_path, monkeypatch) -> None:
    db_path = tmp_path / "weekly-workflow.db"
    protocols = ResearchProtocolRepository(db_path)
    protocols.create_protocol(_protocol())
    strategies = StrategyRepository(db_path)
    base = _semantic_version()
    weekly = replace(
        base.configuration,
        rebalance_policy=RebalancePolicy(RebalanceFrequency.WEEKLY),
    )
    version = strategies.create(weekly)
    experiments = ExperimentRepository(db_path)
    monkeypatch.setattr(api, "protocol_repository", protocols)
    monkeypatch.setattr(api, "strategy_repository", strategies)
    monkeypatch.setattr(api, "experiment_repository", experiments)

    experiment = api._build_experiment(_body(version.version_id, version.content_hash))

    assert experiment.backtest_configuration.rebalance_policy.frequency.value == "weekly"
    assert experiment.provenance.rebalance_policy["frequency"] == "weekly"
