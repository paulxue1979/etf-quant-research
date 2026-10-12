"""Local browser API decisions never touch the canonical project databases."""

from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.app.operator_change_set_api import get_apply_service, router
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
