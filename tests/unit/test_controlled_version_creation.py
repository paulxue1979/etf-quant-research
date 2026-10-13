"""Controlled version creation uses isolated SQLite stores only."""

from __future__ import annotations

import asyncio
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Barrier
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from mcp import Client

from backend.app.controlled_version_api import get_service, router
from backend.app.controlled_version_repository import (
    ControlledVersionRepository,
    VersionRequestError,
)
from backend.app.controlled_version_service import ControlledVersionService
from backend.app.operator_read_service import OperatorReadService
from backend.app.strategy_repository import StrategyRepository
from backend.operator_mcp import create_server
from data.cache import DiskCache
from strategies import StrategyValidator
from tests.unit.test_strategy_repository import _definition


def _seed(tmp_path: Path) -> tuple[ControlledVersionService, StrategyRepository, str, str]:
    path = tmp_path / "strategy.db"
    repository = StrategyRepository(path)
    definition = _definition("phase-13t-uat")
    repository.create_strategy(definition.strategy_id, definition.name, definition.description)
    draft = repository.save_draft(
        strategy_id=definition.strategy_id,
        expected_revision=0,
        active_mode="rule_based",
        rule_based_draft={"assets": ["QQQ", "TQQQ"]},
        regime_draft={},
        active_strategy_payload=definition.to_dict(),
        source_metadata={},
        name=definition.name,
        description=definition.description,
    )
    return ControlledVersionService(path), repository, definition.strategy_id, draft.draft_hash


def _prepare(service: ControlledVersionService, strategy_id: str, draft_hash: str, **base):
    return service.prepare(
        strategy_id=strategy_id,
        expected_draft_revision=1,
        expected_draft_hash=draft_hash,
        base_latest_version_id=base.get("version_id"),
        base_latest_version_hash=base.get("content_hash"),
    )


def _count(path: Path, table: str) -> int:
    with sqlite3.connect(path) as connection:
        return connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]


def test_prepare_is_durable_exact_and_has_no_version_side_effect(tmp_path: Path) -> None:
    service, repository, strategy_id, draft_hash = _seed(tmp_path)
    prepared = _prepare(service, strategy_id, draft_hash)
    assert prepared["status"] == "PROPOSED"
    assert prepared["source"] == "CODEX_MCP"
    assert prepared["expected_draft_revision"] == 1
    assert prepared["expected_draft_hash"] == draft_hash
    assert prepared["base_latest_version_id"] is None
    assert prepared["approval_gate"]["can_approve"] is True
    assert repository.get_draft(strategy_id).draft_revision == 1
    assert repository.list_version_records(strategy_id) == ()
    assert _prepare(service, strategy_id, draft_hash)["request_id"] == prepared["request_id"]
    assert _count(tmp_path / "strategy.db", "pending_strategy_version_creations") == 1
    assert (
        ControlledVersionService(tmp_path / "strategy.db").get(prepared["request_id"]) == prepared
    )


def test_rejected_legacy_request_can_be_followed_by_explicit_new_intent(tmp_path: Path) -> None:
    service, repository, strategy_id, draft_hash = _seed(tmp_path)
    original = _prepare(service, strategy_id, draft_hash)
    service.reject(strategy_id, original["request_id"])
    assert _prepare(service, strategy_id, draft_hash)["status"] == "REJECTED"

    arguments = {
        "strategy_id": strategy_id,
        "expected_draft_revision": 1,
        "expected_draft_hash": draft_hash,
        "base_latest_version_id": None,
        "base_latest_version_hash": None,
        "client_request_key": "uat-b-new-intent-1",
    }
    proposed = service.prepare(**arguments)
    assert proposed["request_id"] != original["request_id"]
    assert proposed["status"] == "PROPOSED"
    assert proposed["client_request_key"] == "uat-b-new-intent-1"
    assert proposed["expected_draft_revision"] == original["expected_draft_revision"]
    assert proposed["expected_draft_hash"] == original["expected_draft_hash"]
    assert service.get(original["request_id"])["status"] == "REJECTED"
    assert repository.get_draft(strategy_id).draft_revision == 1
    assert repository.list_version_records(strategy_id) == ()
    assert _count(tmp_path / "strategy.db", "pending_strategy_version_creations") == 2

    restarted = ControlledVersionService(tmp_path / "strategy.db")
    assert restarted.prepare(**arguments) == proposed
    assert restarted.get(original["request_id"])["status"] == "REJECTED"
    assert _count(tmp_path / "strategy.db", "pending_strategy_version_creations") == 2
    with pytest.raises(VersionRequestError, match="IDEMPOTENCY_KEY_CONFLICT"):
        restarted.prepare(**{**arguments, "expected_draft_hash": "b" * 64})
    with pytest.raises(VersionRequestError, match="ACTIVE_VERSION_REQUEST_EXISTS"):
        restarted.prepare(**{**arguments, "client_request_key": "uat-b-other-intent"})


def test_concurrent_same_key_creates_one_request_and_retry_keeps_rejected_status(
    tmp_path: Path,
) -> None:
    service, repository, strategy_id, draft_hash = _seed(tmp_path)
    arguments = {
        "strategy_id": strategy_id,
        "expected_draft_revision": 1,
        "expected_draft_hash": draft_hash,
        "base_latest_version_id": None,
        "base_latest_version_hash": None,
        "client_request_key": "concurrent-prepare-1",
    }
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: service.prepare(**arguments), range(2)))
    assert results[0]["request_id"] == results[1]["request_id"]
    assert _count(tmp_path / "strategy.db", "pending_strategy_version_creations") == 1
    service.reject(strategy_id, results[0]["request_id"])
    assert (
        ControlledVersionService(tmp_path / "strategy.db").prepare(**arguments)["status"]
        == "REJECTED"
    )
    assert _count(tmp_path / "strategy.db", "pending_strategy_version_creations") == 1
    assert repository.list_version_records(strategy_id) == ()


def test_keyed_retry_after_creation_returns_one_canonical_version(tmp_path: Path) -> None:
    service, repository, strategy_id, draft_hash = _seed(tmp_path)
    arguments = {
        "strategy_id": strategy_id,
        "expected_draft_revision": 1,
        "expected_draft_hash": draft_hash,
        "base_latest_version_id": None,
        "base_latest_version_hash": None,
        "client_request_key": "created-retry-1",
    }
    prepared = service.prepare(**arguments)
    created = service.approve(strategy_id, prepared["request_id"])
    assert ControlledVersionService(tmp_path / "strategy.db").prepare(**arguments) == created
    assert _count(tmp_path / "strategy.db", "pending_strategy_version_creations") == 1
    assert len(repository.list_version_records(strategy_id)) == 1


def test_reject_is_terminal_and_cannot_create_a_version(tmp_path: Path) -> None:
    service, repository, strategy_id, draft_hash = _seed(tmp_path)
    request_id = _prepare(service, strategy_id, draft_hash)["request_id"]
    assert service.reject(strategy_id, request_id)["status"] == "REJECTED"
    with pytest.raises(VersionRequestError, match="VERSION_REQUEST_NOT_PROPOSED"):
        service.approve(strategy_id, request_id)
    assert (
        ControlledVersionService(tmp_path / "strategy.db").get(request_id)["status"] == "REJECTED"
    )
    assert repository.list_version_records(strategy_id) == ()


def test_draft_revision_and_version_baseline_stale_are_fail_closed(tmp_path: Path) -> None:
    service, repository, strategy_id, draft_hash = _seed(tmp_path)
    request_id = _prepare(service, strategy_id, draft_hash)["request_id"]
    definition = _definition(strategy_id)
    repository.save_draft(
        strategy_id=strategy_id,
        expected_revision=1,
        active_mode="rule_based",
        rule_based_draft={},
        regime_draft={},
        active_strategy_payload=definition.to_dict(),
        source_metadata={"changed": True},
        name=definition.name,
        description=definition.description,
    )
    assert service.get(request_id)["approval_gate"]["reason"] == "DRAFT_STALE"
    assert service.approve(strategy_id, request_id)["status"] == "STALE"
    assert repository.list_version_records(strategy_id) == ()

    second = service.prepare(
        strategy_id=strategy_id,
        expected_draft_revision=2,
        expected_draft_hash=repository.get_draft(strategy_id).draft_hash,
        base_latest_version_id=None,
        base_latest_version_hash=None,
    )
    existing = repository.create_version_from_draft(
        strategy_id=strategy_id,
        expected_revision=2,
        definition=definition,
    )
    assert service.get(second["request_id"])["approval_gate"]["reason"] == "VERSION_BASE_STALE"
    assert service.approve(strategy_id, second["request_id"])["status"] == "STALE"
    assert [item.version.version_id for item in repository.list_version_records(strategy_id)] == [
        existing.version_id
    ]


def test_approve_is_atomic_idempotent_and_preserves_old_versions(tmp_path: Path) -> None:
    service, repository, strategy_id, draft_hash = _seed(tmp_path)
    definition = _definition(strategy_id)
    original = repository.create_version_from_draft(
        strategy_id=strategy_id,
        expected_revision=1,
        definition=definition,
    )
    prepared = _prepare(
        service,
        strategy_id,
        draft_hash,
        version_id=original.version_id,
        content_hash=original.content_hash,
    )
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(
            pool.map(lambda _: service.approve(strategy_id, prepared["request_id"]), range(2))
        )
    assert results[0]["status"] == results[1]["status"] == "CREATED"
    assert results[0]["created_version_id"] == results[1]["created_version_id"]
    assert _count(tmp_path / "strategy.db", "strategy_versions") == 2
    assert repository.get(strategy_id, original.version_id).content_hash == original.content_hash
    created = repository.get(strategy_id, results[0]["created_version_id"])
    assert created.content_hash == results[0]["created_version_hash"]
    assert created.configuration == definition
    assert (
        ControlledVersionService(tmp_path / "strategy.db").get(prepared["request_id"])["status"]
        == "CREATED"
    )


def test_transaction_failure_rolls_back_version_and_request(tmp_path: Path, monkeypatch) -> None:
    service, repository, strategy_id, draft_hash = _seed(tmp_path)
    request_id = _prepare(service, strategy_id, draft_hash)["request_id"]
    original_event = ControlledVersionRepository._event

    def fail_after_version(connection, current_id, event_type, **detail):
        if event_type == "CREATED":
            raise sqlite3.OperationalError("injected transaction failure")
        return original_event(connection, current_id, event_type, **detail)

    monkeypatch.setattr(ControlledVersionRepository, "_event", staticmethod(fail_after_version))
    with pytest.raises(sqlite3.OperationalError, match="injected transaction failure"):
        service.approve(strategy_id, request_id)
    assert repository.list_version_records(strategy_id) == ()
    persisted = ControlledVersionService(tmp_path / "strategy.db").get(request_id)
    assert persisted["status"] == "PROPOSED"
    assert persisted["created_version_id"] is None
    assert persisted["created_version_hash"] is None
    assert persisted["error_code"] is None
    assert _count(tmp_path / "strategy.db", "strategy_version_creation_events") == 1
    monkeypatch.setattr(ControlledVersionRepository, "_event", staticmethod(original_event))
    assert service.approve(strategy_id, request_id)["status"] == "CREATED"
    assert _count(tmp_path / "strategy.db", "strategy_versions") == 1


def test_browser_origin_and_backend_gate_use_isolated_store(tmp_path: Path) -> None:
    service, repository, strategy_id, draft_hash = _seed(tmp_path)
    request_id = _prepare(service, strategy_id, draft_hash)["request_id"]
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_service] = lambda: service
    client = TestClient(app)
    url = f"/strategy-operator/version-requests/strategies/{strategy_id}/{request_id}"
    assert client.post(f"{url}/approve").status_code == 403
    assert repository.list_version_records(strategy_id) == ()
    assert client.get(url).json()["approval_gate"]["can_approve"] is True
    created = client.post(f"{url}/approve", headers={"Origin": "http://127.0.0.1:5173"})
    assert created.status_code == 200
    assert created.json()["status"] == "CREATED"
    repeated = client.post(f"{url}/approve", headers={"Origin": "http://127.0.0.1:5173"})
    assert repeated.status_code == 200
    assert repeated.json() == created.json()
    assert len(repository.list_version_records(strategy_id)) == 1
    assert repository.list_version_records(strategy_id)[0].version.version_number == 1


def test_lost_http_response_then_retry_returns_persisted_created_version(tmp_path: Path) -> None:
    service, repository, strategy_id, draft_hash = _seed(tmp_path)
    prepared = _prepare(service, strategy_id, draft_hash)
    request_id = prepared["request_id"]
    assert repository.list_version_records(strategy_id) == ()
    app = FastAPI()
    app.include_router(router)
    db_path = tmp_path / "strategy.db"
    app.dependency_overrides[get_service] = lambda: ControlledVersionService(db_path)
    url = f"/strategy-operator/version-requests/strategies/{strategy_id}/{request_id}/approve"
    headers = {"Origin": "http://127.0.0.1:5173"}

    # The server commits, but the caller discards the first successful response.
    TestClient(app).post(url, headers=headers)
    committed = ControlledVersionService(tmp_path / "strategy.db").get(request_id)
    assert committed["status"] == "CREATED"
    assert committed["created_version_id"] is not None
    assert committed["created_version_hash"] is not None
    assert len(repository.list_version_records(strategy_id)) == 1

    retried = TestClient(app).post(url, headers=headers)
    assert retried.status_code == 200
    assert retried.json() == committed
    assert len(repository.list_version_records(strategy_id)) == 1
    assert (
        repository.get(strategy_id, committed["created_version_id"]).content_hash
        == committed["created_version_hash"]
    )


def test_concurrent_http_approvals_create_exactly_one_version(tmp_path: Path) -> None:
    service, repository, strategy_id, draft_hash = _seed(tmp_path)
    request_id = _prepare(service, strategy_id, draft_hash)["request_id"]
    assert repository.list_version_records(strategy_id) == ()
    app = FastAPI()
    app.include_router(router)
    db_path = tmp_path / "strategy.db"
    app.dependency_overrides[get_service] = lambda: ControlledVersionService(db_path)
    url = f"/strategy-operator/version-requests/strategies/{strategy_id}/{request_id}/approve"
    barrier = Barrier(2)

    def approve_once(_index: int):
        client = TestClient(app)
        barrier.wait(timeout=5)
        return client.post(url, headers={"Origin": "http://127.0.0.1:5173"})

    with ThreadPoolExecutor(max_workers=2) as pool:
        responses = list(pool.map(approve_once, range(2)))
    assert [response.status_code for response in responses] == [200, 200]
    assert responses[0].json() == responses[1].json()
    created = responses[0].json()
    assert created["status"] == "CREATED"
    assert created["created_version_id"] is not None
    assert created["created_version_hash"] is not None
    versions = repository.list_version_records(strategy_id)
    assert len(versions) == 1
    assert versions[0].version.version_number == 1
    assert versions[0].version.version_id == created["created_version_id"]
    assert versions[0].version.content_hash == created["created_version_hash"]
    assert ControlledVersionService(tmp_path / "strategy.db").get(request_id) == created


def test_mcp_has_prepare_and_read_but_no_approval_tool(tmp_path: Path) -> None:
    service, repository, strategy_id, draft_hash = _seed(tmp_path)

    async def exercise() -> None:
        async with Client(
            create_server(
                service=OperatorReadService(
                    db_path=tmp_path / "strategy.db", cache=DiskCache(tmp_path / "cache")
                ),
                version_service=service,
            )
        ) as client:
            tools = {tool.name: tool for tool in (await client.list_tools()).tools}
            assert "quant_prepare_strategy_version_creation" in tools
            assert "quant_get_strategy_version_creation_request" in tools
            assert not any("approve_strategy_version" in name for name in tools)
            assert not any("create_strategy_version" in name for name in tools)
            assert (
                "client_request_key"
                in tools["quant_prepare_strategy_version_creation"].input_schema["properties"]
            )
            prepared = await client.call_tool(
                "quant_prepare_strategy_version_creation",
                {
                    "strategy_id": strategy_id,
                    "expected_draft_revision": 1,
                    "expected_draft_hash": draft_hash,
                },
            )
            request_id = prepared.structured_content["request_id"]
            assert prepared.structured_content["status"] == "PROPOSED"
            loaded = await client.call_tool(
                "quant_get_strategy_version_creation_request", {"request_id": request_id}
            )
            assert loaded.structured_content["request_id"] == request_id
            assert (
                prepared.structured_content["request_id"]
                == (
                    await client.call_tool(
                        "quant_prepare_strategy_version_creation",
                        {
                            "strategy_id": strategy_id,
                            "expected_draft_revision": 1,
                            "expected_draft_hash": draft_hash,
                        },
                    )
                ).structured_content["request_id"]
            )
            service.reject(strategy_id, request_id)
            new_intent = await client.call_tool(
                "quant_prepare_strategy_version_creation",
                {
                    "strategy_id": strategy_id,
                    "expected_draft_revision": 1,
                    "expected_draft_hash": draft_hash,
                    "client_request_key": "mcp-explicit-new-intent",
                },
            )
            assert new_intent.structured_content["status"] == "PROPOSED"
            assert new_intent.structured_content["request_id"] != request_id
            assert service.get(request_id)["status"] == "REJECTED"

    asyncio.run(exercise())
    assert repository.list_version_records(strategy_id) == ()


def test_prepare_rejects_wrong_identity_revision_hash_and_baseline(tmp_path: Path) -> None:
    service, repository, strategy_id, draft_hash = _seed(tmp_path)
    for changes, code in (
        ({"strategy_id": "missing-strategy"}, "STRATEGY_NOT_FOUND"),
        ({"expected_draft_revision": 2}, "DRAFT_STALE"),
        ({"expected_draft_hash": "b" * 64}, "DRAFT_STALE"),
        (
            {"base_latest_version_id": "other-v1", "base_latest_version_hash": "b" * 64},
            "VERSION_BASE_STALE",
        ),
    ):
        arguments = {
            "strategy_id": strategy_id,
            "expected_draft_revision": 1,
            "expected_draft_hash": draft_hash,
            "base_latest_version_id": None,
            "base_latest_version_hash": None,
            **changes,
        }
        with pytest.raises(VersionRequestError, match=code):
            service.prepare(**arguments)
    with pytest.raises(VersionRequestError, match="INVALID_STRATEGY_ID"):
        service.prepare(
            strategy_id="invalid/id",
            expected_draft_revision=1,
            expected_draft_hash=draft_hash,
            base_latest_version_id=None,
            base_latest_version_hash=None,
        )
    with pytest.raises(VersionRequestError, match="INVALID_DRAFT_REVISION"):
        service.prepare(
            strategy_id=strategy_id,
            expected_draft_revision="1",  # type: ignore[arg-type]
            expected_draft_hash=draft_hash,
            base_latest_version_id=None,
            base_latest_version_hash=None,
        )
    with pytest.raises(VersionRequestError, match="INVALID_VERSION_BASELINE"):
        service.prepare(
            strategy_id=strategy_id,
            expected_draft_revision=1,
            expected_draft_hash=draft_hash,
            base_latest_version_id=1,  # type: ignore[arg-type]
            base_latest_version_hash="b" * 64,
        )
    assert repository.list_version_records(strategy_id) == ()
    with sqlite3.connect(tmp_path / "strategy.db") as connection:
        assert (
            connection.execute(
                "SELECT COUNT(*) FROM sqlite_master WHERE name = ?",
                ("pending_strategy_version_creations",),
            ).fetchone()[0]
            == 0
        )


def test_same_name_does_not_cross_strategy_identity(tmp_path: Path) -> None:
    service, repository, strategy_id, draft_hash = _seed(tmp_path)
    other = _definition("another-strategy-id")
    repository.create_strategy(other.strategy_id, _definition(strategy_id).name, other.description)
    repository.save_draft(
        strategy_id=other.strategy_id,
        expected_revision=0,
        active_mode="rule_based",
        rule_based_draft={},
        regime_draft={},
        active_strategy_payload=other.to_dict(),
        source_metadata={},
        name=other.name,
        description=other.description,
    )
    prepared = _prepare(service, strategy_id, draft_hash)
    assert service.list(other.strategy_id) == []
    with pytest.raises(VersionRequestError, match="VERSION_REQUEST_NOT_FOUND"):
        service.approve(other.strategy_id, prepared["request_id"])
    assert repository.list_version_records(other.strategy_id) == ()


def test_missing_invalid_or_inactive_draft_cannot_prepare(tmp_path: Path) -> None:
    service, repository, strategy_id, draft_hash = _seed(tmp_path)
    other = _definition("without-draft")
    repository.create_strategy(other.strategy_id, other.name, other.description)
    with pytest.raises(VersionRequestError, match="DRAFT_NOT_FOUND"):
        _prepare(service, other.strategy_id, draft_hash)
    broken = repository.save_draft(
        strategy_id=strategy_id,
        expected_revision=1,
        active_mode="rule_based",
        rule_based_draft={},
        regime_draft={},
        active_strategy_payload={"invalid": True},
        source_metadata={},
        name=other.name,
        description=other.description,
    )
    with pytest.raises(VersionRequestError, match="DRAFT_INVALID"):
        service.prepare(
            strategy_id=strategy_id,
            expected_draft_revision=broken.draft_revision,
            expected_draft_hash=broken.draft_hash,
            base_latest_version_id=None,
            base_latest_version_hash=None,
        )
    assert repository.trash(strategy_id) == "trashed"
    with pytest.raises(VersionRequestError, match="STRATEGY_INACTIVE"):
        _prepare(service, strategy_id, draft_hash)


def test_deterministic_revalidation_failure_records_failed_without_version(
    tmp_path: Path, monkeypatch
) -> None:
    service, repository, strategy_id, draft_hash = _seed(tmp_path)
    request_id = _prepare(service, strategy_id, draft_hash)["request_id"]
    monkeypatch.setattr(
        "backend.app.controlled_version_repository.StrategyValidator.validate",
        lambda self, payload: SimpleNamespace(is_valid=False),
    )
    assert service.approve(strategy_id, request_id)["status"] == "FAILED"
    assert service.get(request_id)["error_code"] == "DRAFT_INVALID"
    assert repository.list_version_records(strategy_id) == ()
    with pytest.raises(VersionRequestError, match="VERSION_REQUEST_NOT_PROPOSED"):
        service.approve(strategy_id, request_id)


def test_direct_browser_approval_of_stale_request_fails_and_creates_nothing(tmp_path: Path) -> None:
    service, repository, strategy_id, draft_hash = _seed(tmp_path)
    request_id = _prepare(service, strategy_id, draft_hash)["request_id"]
    definition = _definition(strategy_id)
    repository.save_draft(
        strategy_id=strategy_id,
        expected_revision=1,
        active_mode="rule_based",
        rule_based_draft={},
        regime_draft={},
        active_strategy_payload=definition.to_dict(),
        source_metadata={"new": "revision"},
        name=definition.name,
        description=definition.description,
    )
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_service] = lambda: service
    response = TestClient(app).post(
        f"/strategy-operator/version-requests/strategies/{strategy_id}/{request_id}/approve",
        headers={"Origin": "http://127.0.0.1:5173"},
    )
    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "DRAFT_STALE"
    assert service.get(request_id)["status"] == "STALE"
    assert repository.list_version_records(strategy_id) == ()


def test_direct_approval_of_r2_request_after_r3_save_fails_closed(tmp_path: Path) -> None:
    service, repository, strategy_id, _ = _seed(tmp_path)
    definition = _definition(strategy_id)
    draft_r2 = repository.save_draft(
        strategy_id=strategy_id,
        expected_revision=1,
        active_mode="rule_based",
        rule_based_draft={},
        regime_draft={},
        active_strategy_payload=definition.to_dict(),
        source_metadata={"uat": "r2"},
        name=definition.name,
        description=definition.description,
    )
    assert draft_r2.draft_revision == 2
    assert StrategyValidator().validate(draft_r2.active_strategy_payload).is_valid
    prepared = service.prepare(
        strategy_id=strategy_id,
        expected_draft_revision=draft_r2.draft_revision,
        expected_draft_hash=draft_r2.draft_hash,
        base_latest_version_id=None,
        base_latest_version_hash=None,
        client_request_key="isolated-r2-stale-gate",
    )
    assert prepared["status"] == "PROPOSED"
    assert len(repository.list_version_records(strategy_id)) == 0

    draft_r3 = repository.save_draft(
        strategy_id=strategy_id,
        expected_revision=2,
        active_mode="rule_based",
        rule_based_draft={},
        regime_draft={},
        active_strategy_payload=definition.to_dict(),
        source_metadata={"uat": "r3"},
        name=definition.name,
        description=definition.description,
    )
    assert draft_r3.draft_revision == 3
    assert draft_r3.draft_hash != draft_r2.draft_hash

    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_service] = lambda: service
    client = TestClient(app)
    url = f"/strategy-operator/version-requests/strategies/{strategy_id}/{prepared['request_id']}"
    before = client.get(url).json()
    assert before["status"] == "PROPOSED"
    assert before["approval_gate"] == {"can_approve": False, "reason": "DRAFT_STALE"}

    first = client.post(f"{url}/approve", headers={"Origin": "http://127.0.0.1:5173"})
    assert first.status_code == 409
    assert first.json()["detail"]["code"] == "DRAFT_STALE"
    second = client.post(f"{url}/approve", headers={"Origin": "http://127.0.0.1:5173"})
    assert second.status_code == 409
    assert second.json()["detail"]["code"] == "VERSION_REQUEST_NOT_PROPOSED"

    after = client.get(url).json()
    assert after["status"] == "STALE"
    assert after["expected_draft_revision"] == 2
    assert after["expected_draft_hash"] == draft_r2.draft_hash
    assert after["created_version_id"] is None
    assert after["created_version_hash"] is None
    assert repository.list_version_records(strategy_id) == ()
    current_draft = repository.get_draft(strategy_id)
    assert current_draft.draft_revision == 3
    assert current_draft.draft_hash == draft_r3.draft_hash


def test_no_approval_creates_no_research_or_backtest_rows(tmp_path: Path) -> None:
    service, repository, strategy_id, draft_hash = _seed(tmp_path)
    prepared = _prepare(service, strategy_id, draft_hash)
    assert prepared["status"] == "PROPOSED"
    assert repository.list_version_records(strategy_id) == ()
    with sqlite3.connect(tmp_path / "strategy.db") as connection:
        names = {
            row[0]
            for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
    assert "backtest_runs" not in names
    assert "experiments" not in names
    assert "research_selection_decisions" not in names
    assert "research_strategy_freezes" not in names
    assert "research_oos_results" not in names
