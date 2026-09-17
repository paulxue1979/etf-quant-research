from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from backend.app import backtest_lab
from backend.app.main import app
from backend.app.strategy_lab import strategy_version_store
from tests.unit.test_backtest_repository import _run


def _strategy_payload() -> dict[str, object]:
    return {
        "strategy_id": "qqq-tqqq-sgov",
        "name": "Dynamic Allocation",
        "description": "Strategy Lab API fixture",
        "assets": [{"symbol": "QQQ"}, {"symbol": "TQQQ"}, {"symbol": "SGOV"}],
        "price_field": "adjusted_close",
        "rules": [
            {
                "rule_id": "risk-on",
                "name": "Risk On",
                "priority": 1,
                "condition": {
                    "type": "group",
                    "operator": "and",
                    "children": [
                        {
                            "type": "condition",
                            "left": {
                                "type": "price",
                                "asset": "QQQ",
                                "price_field": "adjusted_close",
                            },
                            "operator": "greater_than",
                            "right": {
                                "type": "ma",
                                "asset": "QQQ",
                                "period": 20,
                                "price_field": "adjusted_close",
                            },
                            "threshold": {"type": "relative", "value": 0.04},
                        }
                    ],
                },
                "allocations": [
                    {"symbol": "QQQ", "target_weight": 0.6},
                    {"symbol": "TQQQ", "target_weight": 0.3},
                    {"symbol": "SGOV", "target_weight": 0.1},
                ],
                "remaining": None,
            }
        ],
        "fallback": {"name": "fallback", "allocations": [{"symbol": "SGOV", "target_weight": 1}]},
        "rebalance_policy": {"frequency": "weekly", "threshold": 0.05},
    }


def setup_function() -> None:
    strategy_version_store.clear()


def test_strategy_lab_validation_delegates_to_the_domain_validator() -> None:
    client = TestClient(app)

    response = client.post("/strategy-lab/validate", json={"strategy": _strategy_payload()})

    assert response.status_code == 200
    assert response.json() == {"is_valid": True, "errors": [], "warnings": []}


def test_strategy_lab_validation_returns_structured_domain_errors() -> None:
    client = TestClient(app)
    payload = _strategy_payload()
    payload["assets"] = [{"symbol": "QQQ"}, {"symbol": "QQQ"}]

    response = client.post("/strategy-lab/validate", json={"strategy": payload})

    assert response.status_code == 200
    assert response.json()["is_valid"] is False
    assert response.json()["errors"][0]["code"] == "DuplicateAsset"


def test_invalid_strategy_cannot_create_a_version() -> None:
    client = TestClient(app)
    payload = _strategy_payload()
    payload["rules"][0]["allocations"] = [  # type: ignore[index]
        {"symbol": "QQQ", "target_weight": 0.8},
        {"symbol": "TQQQ", "target_weight": 0.3},
    ]

    response = client.post("/strategy-lab/versions", json={"strategy": payload})

    assert response.status_code == 422
    assert response.json()["is_valid"] is False
    assert response.json()["errors"][0]["code"] == "AllocationExceeds100Percent"
    assert strategy_version_store.list("qqq-tqqq-sgov") == ()


def test_strategy_versions_are_immutable_snapshots_with_history_and_retrieval() -> None:
    client = TestClient(app)
    first_payload = _strategy_payload()

    first = client.post("/strategy-lab/versions", json={"strategy": first_payload})
    assert first.status_code == 200
    first_body = first.json()

    second_payload = _strategy_payload()
    second_payload["rules"][0]["condition"]["children"][0]["right"]["period"] = 50  # type: ignore[index]
    second = client.post("/strategy-lab/versions", json={"strategy": second_payload})
    assert second.status_code == 200
    second_body = second.json()

    assert first_body["version_number"] == 1
    assert second_body["version_number"] == 2
    assert first_body["version_id"] != second_body["version_id"]
    assert first_body["content_hash"] != second_body["content_hash"]

    history = client.get("/strategy-lab/strategies/qqq-tqqq-sgov/versions")
    assert history.status_code == 200
    assert [item["version_number"] for item in history.json()] == [1, 2]

    restored = client.get(
        f"/strategy-lab/strategies/qqq-tqqq-sgov/versions/{first_body['version_id']}"
    )
    assert restored.status_code == 200
    configuration = restored.json()["configuration"]
    first_rule = configuration["rules"][0]
    first_condition = first_rule["condition"]["children"][0]
    assert first_condition["right"]["period"] == 20


def test_unknown_version_and_malformed_requests_are_not_silently_accepted() -> None:
    client = TestClient(app)

    missing = client.get("/strategy-lab/strategies/qqq/versions/missing")
    malformed = client.post("/strategy-lab/validate", json={"strategy": []})

    assert missing.status_code == 404
    assert malformed.status_code == 422


def test_stateful_strategy_validates_and_round_trips_as_immutable_versions() -> None:
    client = TestClient(app)
    first_payload = _strategy_payload()
    first_payload.update(
        {
            "name": "QQQ SMA200 Hysteresis",
            "no_match_behavior": "hold_previous_allocation",
            "initial_allocation": {"allocations": []},
            "rebalance_policy": {
                "frequency": "on_signal_change",
                "threshold": None,
            },
        }
    )
    first_condition = first_payload["rules"][0]["condition"]["children"][0]  # type: ignore[index]
    first_condition["right"]["period"] = 200

    validation = client.post("/strategy-lab/validate", json={"strategy": first_payload})
    first = client.post("/strategy-lab/versions", json={"strategy": first_payload})

    assert validation.status_code == 200
    assert validation.json()["is_valid"] is True
    assert first.status_code == 200
    first_body = first.json()
    restored = client.get(
        f"/strategy-lab/strategies/{first_body['strategy_id']}/versions/{first_body['version_id']}"
    )
    configuration = restored.json()["configuration"]
    assert configuration["no_match_behavior"] == "hold_previous_allocation"
    assert configuration["initial_allocation"] == {"allocations": []}
    assert configuration["rebalance_policy"]["frequency"] == "on_signal_change"
    assert configuration["rules"][0]["condition"]["children"][0]["threshold"] == {
        "type": "relative",
        "value": 0.04,
    }

    second_payload = dict(first_payload)
    second_payload["name"] = "QQQ SMA200 Hysteresis v2"
    second = client.post("/strategy-lab/versions", json={"strategy": second_payload})

    assert second.status_code == 200
    assert second.json()["version_number"] == 2
    assert second.json()["version_id"] != first_body["version_id"]
    assert (
        client.get(
            f"/strategy-lab/strategies/{first_body['strategy_id']}/versions/{first_body['version_id']}"
        ).json()["configuration"]["name"]
        == "QQQ SMA200 Hysteresis"
    )


def test_hold_previous_strategy_requires_initial_allocation() -> None:
    client = TestClient(app)
    payload = _strategy_payload()
    payload["no_match_behavior"] = "hold_previous_allocation"

    response = client.post("/strategy-lab/validate", json={"strategy": payload})

    assert response.status_code == 200
    assert response.json()["is_valid"] is False
    assert response.json()["errors"] == [
        {
            "code": "MissingInitialAllocation",
            "path": "initial_allocation",
            "message": "HOLD_PREVIOUS_ALLOCATION requires an initial allocation",
        }
    ]


def test_saved_stateful_version_id_is_handed_to_the_existing_backtest_api(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = TestClient(app)
    payload = _strategy_payload()
    payload.update(
        {
            "no_match_behavior": "hold_previous_allocation",
            "initial_allocation": {"allocations": []},
            "rebalance_policy": {"frequency": "on_signal_change", "threshold": None},
        }
    )
    saved = client.post("/strategy-lab/versions", json={"strategy": payload}).json()
    captured: dict[str, object] = {}

    def run_backtest(request: object) -> object:
        captured["strategy_id"] = request.strategy_id  # type: ignore[attr-defined]
        captured["strategy_version_id"] = request.strategy_version_id  # type: ignore[attr-defined]
        return _run()

    monkeypatch.setattr(
        backtest_lab,
        "_service",
        lambda: SimpleNamespace(run=run_backtest),
    )

    response = client.post(
        "/backtests",
        json={
            "strategy_id": saved["strategy_id"],
            "strategy_version_id": saved["version_id"],
            "start_date": "2026-01-02",
            "end_date": "2026-01-04",
            "initial_capital": 10_000,
            "price_field_used": "adjusted_close",
        },
    )

    assert response.status_code == 201
    assert captured == {
        "strategy_id": saved["strategy_id"],
        "strategy_version_id": saved["version_id"],
    }
    restored = strategy_version_store.get(saved["strategy_id"], saved["version_id"])
    assert restored is not None
    assert restored.configuration.no_match_behavior.value == "hold_previous_allocation"
