from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from backend.app import strategy_lab
from backend.app.main import app
from backend.app.strategy_repository import StrategyRepository
from tests.unit.test_value_zones import _strategy


def test_strategy_version_semantic_target_discovery_hides_array_paths(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        strategy_lab, "strategy_version_store", StrategyRepository(tmp_path / "strategy.db")
    )
    client = TestClient(app)
    version = client.post("/strategy-lab/versions", json={"strategy": _strategy().to_dict()}).json()

    response = client.get(
        f"/strategy-lab/strategies/{version['strategy_id']}/versions/{version['version_id']}/semantic-targets"
    )

    assert response.status_code == 200
    body = response.json()
    assert body["strategy_version_hash"] == version["content_hash"]
    assert body["targets"]
    assert any(item["target_kind"] == "state_allocation_weight" for item in body["targets"])
    assert all("target_path" not in item for item in body["targets"])
    assert body["unsupported"]
