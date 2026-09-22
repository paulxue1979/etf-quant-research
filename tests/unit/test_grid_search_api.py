from __future__ import annotations

from fastapi.testclient import TestClient

from backend.app import grid_search_api
from backend.app.main import app
from tests.unit.test_grid_search import _service_setup


def test_grid_api_separates_preflight_prepare_execute_and_progress(tmp_path) -> None:
    service, _, _, _, _, _, experiment, definition = _service_setup(tmp_path)
    original = grid_search_api._grid_service
    grid_search_api._grid_service = service
    client = TestClient(app)
    try:
        template = client.get(f"/research/experiments/{experiment.experiment_id}/grid/template")
        preflight = client.post(
            f"/research/experiments/{experiment.experiment_id}/grid/preflight",
            json=definition.to_dict(),
        )
        missing = client.get(f"/research/experiments/{experiment.experiment_id}/grid/progress")
        prepared = client.post(
            f"/research/experiments/{experiment.experiment_id}/grid/prepare",
            json=definition.to_dict(),
        )
        progress = client.get(f"/research/experiments/{experiment.experiment_id}/grid/progress")
        executed = client.post(f"/research/experiments/{experiment.experiment_id}/grid/execute")
    finally:
        grid_search_api._grid_service = original

    assert template.status_code == 200
    assert template.json()["definition"]["experiment_hash"] == experiment.content_hash
    assert template.json()["parameter_space"] == experiment.parameter_space.to_dict()
    assert preflight.status_code == 200
    assert preflight.json()["can_execute"] is True
    assert missing.status_code == 404
    assert prepared.status_code == 200
    assert progress.json()["pending"] == 2
    assert executed.status_code == 200
    assert executed.json()["status"] == "completed"
    assert executed.json()["completed"] == 2


def test_grid_api_rejects_execution_overrides_and_arbitrary_binding_paths(tmp_path) -> None:
    service, _, _, _, _, _, experiment, definition = _service_setup(tmp_path)
    original = grid_search_api._grid_service
    grid_search_api._grid_service = service
    client = TestClient(app)
    try:
        override = client.post(
            f"/research/experiments/{experiment.experiment_id}/grid/execute",
            json={"max_candidates": 1},
        )
        payload = definition.to_dict()
        payload["strategy_bindings"][0]["target_path"] = "__class__.__dict__"
        unsafe = client.post(
            f"/research/experiments/{experiment.experiment_id}/grid/preflight",
            json=payload,
        )
    finally:
        grid_search_api._grid_service = original

    assert override.status_code == 422
    assert override.json()["detail"]["code"] == "REQUEST_MUST_BE_EMPTY"
    assert unsafe.status_code == 422
    assert unsafe.json()["detail"]["code"] == "INVALID_GRID_DEFINITION"


def test_cancel_endpoint_is_idempotent_and_does_not_start_candidates(tmp_path) -> None:
    service, _, _, results, _, _, experiment, definition = _service_setup(tmp_path)
    service.prepare(experiment.experiment_id, definition)
    original = grid_search_api._grid_service
    grid_search_api._grid_service = service
    client = TestClient(app)
    try:
        first = client.post(f"/research/experiments/{experiment.experiment_id}/grid/cancel")
        second = client.post(f"/research/experiments/{experiment.experiment_id}/grid/cancel")
    finally:
        grid_search_api._grid_service = original

    assert first.status_code == second.status_code == 200
    assert first.json()["status"] == second.json()["status"] == "cancelled"
    assert results.list(experiment.experiment_id) == ()
