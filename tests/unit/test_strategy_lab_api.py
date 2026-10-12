from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from backend.app import backtest_lab, strategy_lab
from backend.app.main import app
from backend.app.operator_read_service import OperatorReadService
from backend.app.strategy_repository import StrategyRepository
from data.cache import DiskCache
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


@pytest.fixture(autouse=True)
def isolate_strategy_store(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        strategy_lab, "strategy_version_store", StrategyRepository(tmp_path / "strategy.db")
    )


def _draft_request(payload: dict[str, object], *, expected_revision: int = 0) -> dict[str, object]:
    return {
        "expected_revision": expected_revision,
        "active_mode": "rule_based",
        "strategy_id": payload["strategy_id"],
        "name": payload["name"],
        "description": payload["description"],
        "price_field": "adjusted_close",
        "rebalance_frequency": "weekly",
        "rebalance_threshold_percent": "5",
        "rule_based_draft": {"assets": ["QQQ", "TQQQ", "SGOV"], "editor_state": {"rules": []}},
        "regime_draft": {"assets": ["QQQ"], "editor_state": {"regime": {"transitions": []}}},
        "active_strategy_payload": payload,
        "source_metadata": {"source_schema_version": "2.0"},
    }


def test_strategy_lab_validation_delegates_to_the_domain_validator() -> None:
    client = TestClient(app)

    response = client.post("/strategy-lab/validate", json={"strategy": _strategy_payload()})

    assert response.status_code == 200
    assert response.json() == {"is_valid": True, "errors": [], "warnings": []}


def test_library_api_repository_and_operator_share_strategy_id_draft_identity(tmp_path) -> None:
    client = TestClient(app)
    payload = _strategy_payload()
    strategy_id = str(payload["strategy_id"])
    created = client.post(
        "/strategy-lab/strategies",
        json={"strategy_id": strategy_id, "name": payload["name"]},
    )
    assert created.status_code == 201

    for expected_revision in range(3):
        saved = client.put(
            f"/strategy-lab/strategies/{strategy_id}/draft",
            json=_draft_request(payload, expected_revision=expected_revision),
        )
        assert saved.status_code == 200

    library = client.get("/strategy-lab/strategies").json()
    row = next(item for item in library if item["strategy_id"] == strategy_id)
    browser = client.get(f"/strategy-lab/strategies/{strategy_id}/draft")
    canonical = strategy_lab.strategy_version_store.get_draft(strategy_id)
    operator = OperatorReadService(
        db_path=strategy_lab.strategy_version_store.db_path,
        cache=DiskCache(tmp_path / "cache"),
    ).get_draft(strategy_id)

    assert browser.status_code == 200
    assert canonical is not None
    assert (
        row["draft_revision"]
        == browser.json()["draft_revision"]
        == canonical.draft_revision
        == operator["revision"]
        == 3
    )
    assert browser.json()["draft_hash"] == canonical.draft_hash == operator["draft_hash"]
    assert operator["draft_id"] is None
    assert strategy_lab.strategy_version_store.list_version_records(strategy_id) == ()
    assert strategy_lab.strategy_version_store.get_draft(strategy_id).draft_revision == 3


def test_draft_get_distinguishes_missing_strategy_and_missing_draft() -> None:
    client = TestClient(app)
    assert client.get("/strategy-lab/strategies/null/draft").json() == {
        "detail": "STRATEGY_NOT_FOUND"
    }
    assert client.get("/strategy-lab/strategies/null/draft").status_code == 404
    client.post(
        "/strategy-lab/strategies",
        json={"strategy_id": "no-draft", "name": "No Draft"},
    )
    missing = client.get("/strategy-lab/strategies/no-draft/draft")
    assert missing.status_code == 404
    assert missing.json() == {"detail": "DRAFT_NOT_FOUND"}
    assert strategy_lab.strategy_version_store.get_draft("no-draft") is None


def test_strategy_lifecycle_api_supports_trash_restore_protect_and_diagnostics() -> None:
    client = TestClient(app)
    created = client.post(
        "/strategy-lab/strategies",
        json={"strategy_id": "lifecycle-api", "name": "Lifecycle API"},
    )
    assert created.status_code == 201
    assert created.json()["lifecycle_status"] == "ACTIVE"

    protected = client.post("/strategy-lab/strategies/lifecycle-api/protect")
    assert protected.json()["result"] == "updated"
    blocked = client.post("/strategy-lab/strategies/lifecycle-api/trash")
    assert blocked.json()["result"] == "protected"

    assert client.post("/strategy-lab/strategies/lifecycle-api/unprotect").json()["result"] == (
        "updated"
    )
    assert client.post("/strategy-lab/strategies/lifecycle-api/trash").json()["result"] == (
        "trashed"
    )
    trash = client.get("/strategy-lab/strategies?lifecycle=TRASHED")
    assert [item["strategy_id"] for item in trash.json()] == ["lifecycle-api"]
    assert client.post("/strategy-lab/strategies/lifecycle-api/restore").json()["result"] == (
        "restored"
    )
    diagnostics = client.get("/strategy-lab/diagnostics")
    assert diagnostics.status_code == 200
    assert diagnostics.json()["database_path"].endswith("strategy.db")


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
    assert strategy_lab.strategy_version_store.list("qqq-tqqq-sgov") == ()


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


def test_library_can_create_draft_clone_and_semantic_diff_from_versions() -> None:
    client = TestClient(app)
    first_payload = _strategy_payload()
    first = client.post("/strategy-lab/versions", json={"strategy": first_payload}).json()
    changed_payload = {**first_payload, "name": "Changed strategy"}
    second = client.post("/strategy-lab/versions", json={"strategy": changed_payload}).json()

    listed = client.get("/strategy-lab/strategies").json()
    source = next(item for item in listed if item["strategy_id"] == first["strategy_id"])
    assert source["latest_version_id"] == second["version_id"]

    draft = client.post(
        f"/strategy-lab/strategies/{first['strategy_id']}/drafts/from-version/{first['version_id']}",
        json={"expected_revision": 0},
    )
    assert draft.status_code == 200
    assert draft.json()["draft_revision"] == 1
    assert draft.json()["source_metadata"]["source_version_id"] == first["version_id"]

    conflict = client.post(
        f"/strategy-lab/strategies/{first['strategy_id']}/drafts/from-version/{second['version_id']}",
        json={"expected_revision": 1},
    )
    assert conflict.status_code == 409
    assert conflict.json()["detail"] == "DRAFT_REPLACE_CONFIRMATION_REQUIRED"

    cloned = client.post(
        f"/strategy-lab/strategies/{first['strategy_id']}/clone-from-version/{second['version_id']}",
        json={"new_strategy_id": "library-clone", "name": "Library clone", "description": "Copy"},
    )
    assert cloned.status_code == 201
    assert cloned.json()["draft_revision"] == 1

    diff = client.get(
        f"/strategy-lab/strategies/{first['strategy_id']}/versions/{first['version_id']}/diff/{second['version_id']}"
    )
    assert diff.status_code == 200
    assert any(item["path"] == "$.name" for item in diff.json()["items"])


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


def test_hold_previous_strategy_can_omit_fallback_and_preserve_invalid_fallback_draft() -> None:
    client = TestClient(app)
    payload = _strategy_payload()
    payload["no_match_behavior"] = "hold_previous_allocation"
    payload["initial_allocation"] = {"allocations": []}
    payload.pop("fallback")

    response = client.post("/strategy-lab/validate", json={"strategy": payload})

    assert response.status_code == 200
    assert response.json()["is_valid"] is True

    payload["fallback"] = {
        "name": "fallback",
        "allocations": [{"symbol": "SPY", "target_weight": 1.0}],
    }
    response = client.post("/strategy-lab/validate", json={"strategy": payload})
    assert response.json()["is_valid"] is True


def test_regime_validation_ignores_inactive_rule_and_fallback_semantics() -> None:
    client = TestClient(app)
    payload = _strategy_payload()
    payload.update(
        {
            "strategy_schema_version": "2.0",
            "strategy_mode": "regime_state_machine",
            "assets": [{"symbol": "QQQ", "role": "both"}],
            "initial_regime": "RISK",
            "regimes": [
                {
                    "state_id": "RISK",
                    "display_name": "Risk",
                    "target_allocation": {"allocations": [{"symbol": "QQQ", "target_weight": 1.0}]},
                    "metadata": {},
                }
            ],
            "transitions": [],
            "value_zones": [],
            "fallback": {"name": "inactive", "allocations": "malformed"},
        }
    )

    response = client.post("/strategy-lab/validate", json={"strategy": payload})

    assert response.status_code == 200
    assert response.json()["is_valid"] is True
    version = client.post("/strategy-lab/versions", json={"strategy": payload})
    assert version.status_code == 200
    assert version.json()["active_mode"] == "regime_state_machine"


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
    restored = strategy_lab.strategy_version_store.get(saved["strategy_id"], saved["version_id"])
    assert restored is not None
    assert restored.configuration.no_match_behavior.value == "hold_previous_allocation"


def test_strategy_draft_can_save_invalid_state_and_reject_stale_revision() -> None:
    client = TestClient(app)
    payload = _strategy_payload()
    identity = client.post(
        "/strategy-lab/strategies",
        json={
            "strategy_id": payload["strategy_id"],
            "name": payload["name"],
            "description": payload["description"],
        },
    )
    assert identity.status_code == 201

    invalid = _draft_request(payload)
    invalid["active_strategy_payload"] = None
    saved = client.put(f"/strategy-lab/strategies/{payload['strategy_id']}/draft", json=invalid)
    assert saved.status_code == 200
    assert saved.json()["draft_revision"] == 1
    assert saved.json()["active_strategy_payload"] is None
    assert (
        client.get(f"/strategy-lab/strategies/{payload['strategy_id']}/draft").json()
        == saved.json()
    )

    stale = client.put(f"/strategy-lab/strategies/{payload['strategy_id']}/draft", json=invalid)
    assert stale.status_code == 409
    assert stale.json()["detail"] == "DRAFT_REVISION_CONFLICT"


def test_valid_draft_creates_version_without_mutating_draft_or_identity() -> None:
    client = TestClient(app)
    payload = _strategy_payload()
    client.post(
        "/strategy-lab/strategies",
        json={
            "strategy_id": payload["strategy_id"],
            "name": payload["name"],
            "description": payload["description"],
        },
    )
    saved = client.put(
        f"/strategy-lab/strategies/{payload['strategy_id']}/draft",
        json=_draft_request(payload),
    )
    assert saved.status_code == 200
    created = client.post(
        f"/strategy-lab/strategies/{payload['strategy_id']}/versions/from-draft",
        json={"expected_revision": 1},
    )
    assert created.status_code == 200
    assert created.json()["version_number"] == 1
    assert (
        client.get(f"/strategy-lab/strategies/{payload['strategy_id']}/draft").json()[
            "draft_revision"
        ]
        == 1
    )


def test_hold_previous_draft_with_inactive_malformed_fallback_is_versionable() -> None:
    client = TestClient(app)
    payload = _strategy_payload()
    payload.update(
        {
            "no_match_behavior": "hold_previous_allocation",
            "initial_allocation": {"allocations": [{"symbol": "QQQ", "target_weight": 1.0}]},
            "fallback": {"name": "inactive", "allocations": "malformed"},
        }
    )
    client.post(
        "/strategy-lab/strategies",
        json={
            "strategy_id": payload["strategy_id"],
            "name": payload["name"],
            "description": payload["description"],
        },
    )

    draft = client.put(
        f"/strategy-lab/strategies/{payload['strategy_id']}/draft",
        json=_draft_request(payload),
    )
    assert draft.status_code == 200
    assert client.post("/strategy-lab/validate", json={"strategy": payload}).json()["is_valid"]

    version = client.post(
        f"/strategy-lab/strategies/{payload['strategy_id']}/versions/from-draft",
        json={"expected_revision": 1},
    )
    assert version.status_code == 200


def test_invalid_saved_draft_is_not_versionable() -> None:
    client = TestClient(app)
    payload = _strategy_payload()
    client.post(
        "/strategy-lab/strategies",
        json={
            "strategy_id": payload["strategy_id"],
            "name": payload["name"],
            "description": payload["description"],
        },
    )
    invalid = _draft_request(payload)
    invalid_payload = dict(payload)
    invalid_payload["rules"] = [
        dict(
            payload["rules"][0],
            allocations=[
                {"symbol": "QQQ", "target_weight": 0.8},
                {"symbol": "TQQQ", "target_weight": 0.3},
            ],
        )
    ]  # type: ignore[index]
    invalid["active_strategy_payload"] = invalid_payload
    saved = client.put(f"/strategy-lab/strategies/{payload['strategy_id']}/draft", json=invalid)
    assert saved.status_code == 200
    version = client.post(
        f"/strategy-lab/strategies/{payload['strategy_id']}/versions/from-draft",
        json={"expected_revision": 1},
    )
    assert version.status_code == 422
    assert version.json()["errors"][0]["code"] == "AllocationExceeds100Percent"
