"""Local browser API decisions never touch the canonical project databases."""

from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.app.operator_change_set_api import get_apply_service, router
from backend.app.operator_change_set_service import DraftChangeOperation, OperationKind
from tests.unit.test_operator_change_set_apply import _fixture, _proposal

_ORIGIN = {"origin": "http://localhost:5173"}


def _client(tmp_path: Path):
    store, draft, proposals, prepare, apply = _fixture(tmp_path)
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_apply_service] = lambda: apply
    return TestClient(app), store, draft, proposals, prepare


def test_browser_list_get_approve_and_reject(tmp_path: Path) -> None:
    client, store, draft, proposals, prepare = _client(tmp_path)
    first = _proposal(prepare, draft)
    base = f"/strategy-lab/strategies/{draft.strategy_id}/change-sets"
    listing = client.get(base)
    assert listing.status_code == 200
    assert listing.json()[0]["approval_gate"] == {"can_approve": True, "reason": None}
    assert listing.json()[0]["human_readable_diff"]["changes"]
    assert client.get(f"{base}/{first['change_set_id']}").status_code == 200
    assert client.get(f"{base}/missing").status_code == 404
    assert (
        client.get(
            f"/strategy-lab/strategies/other/change-sets/{first['change_set_id']}"
        ).status_code
        == 404
    )

    decision_url = f"{base}/{first['change_set_id']}/approve"
    assert client.post(decision_url).status_code == 403
    assert client.post(decision_url, headers={"origin": "https://example.com"}).status_code == 403
    assert client.post(decision_url, headers=_ORIGIN, json={"operations": []}).status_code == 400
    applied = client.post(decision_url, headers=_ORIGIN)
    assert applied.status_code == 200
    assert applied.json()["result"] == "APPLIED"
    assert store.get_draft(draft.strategy_id).draft_revision == draft.draft_revision + 1
    assert client.post(decision_url, headers=_ORIGIN).json()["result"] == "ALREADY_APPLIED"
    assert proposals.get(first["change_set_id"])["status"] == "APPLIED"


def test_browser_reject_and_stale_conflict(tmp_path: Path) -> None:
    client, store, draft, proposals, prepare = _client(tmp_path)
    first = _proposal(prepare, draft)
    base = f"/strategy-lab/strategies/{draft.strategy_id}/change-sets/{first['change_set_id']}"
    rejected = client.post(f"{base}/reject", headers=_ORIGIN)
    assert rejected.status_code == 200
    assert rejected.json()["result"] == "REJECTED"
    assert store.get_draft(draft.strategy_id).draft_revision == draft.draft_revision
    assert client.post(f"{base}/reject", headers=_ORIGIN).json()["result"] == "ALREADY_REJECTED"
    assert client.post(f"{base}/approve", headers=_ORIGIN).status_code == 409
    assert proposals.get(first["change_set_id"])["status"] == "REJECTED"

    stale = _proposal(prepare, draft)
    store.save_draft(
        strategy_id=draft.strategy_id,
        expected_revision=draft.draft_revision,
        active_mode=draft.active_mode,
        rule_based_draft=draft.rule_based_draft,
        regime_draft=draft.regime_draft,
        active_strategy_payload=draft.active_strategy_payload,
        source_metadata=draft.source_metadata,
        name="updated",
        description="",
    )
    stale_url = f"/strategy-lab/strategies/{draft.strategy_id}/change-sets/{stale['change_set_id']}"
    assert client.get(stale_url).json()["approval_gate"]["reason"] == "CHANGESET_STALE"
    response = client.post(f"{stale_url}/approve", headers=_ORIGIN)
    assert response.status_code == 409
    assert response.json()["detail"] == "CHANGESET_STALE"


def test_failed_approval_is_reloaded_and_not_pending(tmp_path: Path) -> None:
    client, store, draft, proposals, prepare = _client(tmp_path)
    proposal = _proposal(prepare, draft)
    base = f"/strategy-lab/strategies/{draft.strategy_id}/change-sets"
    url = f"{base}/{proposal['change_set_id']}"
    service = client.app.dependency_overrides[get_apply_service]()
    service._preview._validator.validate = lambda _payload: type(
        "InvalidResult",
        (),
        {"to_dict": lambda self: {"is_valid": False, "errors": [], "warnings": []}},
    )()
    response = client.post(f"{url}/approve", headers=_ORIGIN)
    assert response.status_code == 422
    assert response.json()["detail"] == "CHANGESET_VALIDATION_FAILED"
    assert store.get_draft(draft.strategy_id).draft_revision == draft.draft_revision
    assert client.get(url).json()["status"] == "FAILED"
    assert client.get(url).json()["approval_gate"]["can_approve"] is False
    assert client.get(base).json()[0]["status"] == "FAILED"
    assert client.post(f"{url}/approve", headers=_ORIGIN).json()["detail"] == "CHANGESET_FAILED"
    assert proposals.get(proposal["change_set_id"])["failed_at"]


def test_post_commit_response_failure_remains_applied(tmp_path: Path) -> None:
    client, store, draft, proposals, prepare = _client(tmp_path)
    proposal = _proposal(prepare, draft)
    service = client.app.dependency_overrides[get_apply_service]()
    app = FastAPI()

    @app.post("/simulate-response-failure")
    def response_failure() -> None:
        service.approve(draft.strategy_id, proposal["change_set_id"])
        raise RuntimeError("response serialization failed after commit")

    response = TestClient(app, raise_server_exceptions=False).post("/simulate-response-failure")
    assert response.status_code == 500
    assert store.get_draft(draft.strategy_id).draft_revision == draft.draft_revision + 1
    assert proposals.get(proposal["change_set_id"])["status"] == "APPLIED"


def test_browser_can_attempt_invalid_proposal_without_draft_mutation(tmp_path: Path) -> None:
    client, store, draft, proposals, prepare = _client(tmp_path)
    proposal = prepare.prepare(
        draft.strategy_id,
        draft.draft_revision,
        [
            DraftChangeOperation(
                operation_type=OperationKind.SET_INITIAL_ALLOCATION,
                value={"QQQ": 0.8, "TQQQ": 0.8},
            )
        ],
    )
    assert proposal["validation_status"] == "FAIL"
    base = f"/strategy-lab/strategies/{draft.strategy_id}/change-sets/{proposal['change_set_id']}"
    assert client.get(base).json()["approval_gate"]["can_approve"] is True
    before = store.get_draft(draft.strategy_id)
    response = client.post(f"{base}/approve", headers=_ORIGIN)
    assert response.status_code == 422
    assert response.json()["detail"] == "CHANGESET_VALIDATION_FAILED"
    after = store.get_draft(draft.strategy_id)
    assert (after.draft_revision, after.draft_hash) == (before.draft_revision, before.draft_hash)
    assert proposals.get(proposal["change_set_id"])["status"] == "FAILED"
