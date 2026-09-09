from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from backend.app import research_execution_api
from backend.app.experiment_execution_orchestrator import ExperimentExecutionApiError
from backend.app.main import app


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.setattr(
        research_execution_api,
        "_orchestrator",
        SimpleNamespace(
            execute=lambda experiment_id, candidate_id: {
                "candidate_id": candidate_id,
                "execution": {"status": "completed"},
                "result": {"candidate_id": candidate_id},
            },
            get_execution=lambda experiment_id, candidate_id: {
                "candidate_id": candidate_id,
                "status": "completed",
            },
            get_result=lambda experiment_id, candidate_id: {
                "candidate_id": candidate_id,
                "result_hash": "result-hash",
            },
        ),
    )
    return TestClient(app)


def test_execute_accepts_empty_or_omitted_body(client: TestClient) -> None:
    path = "/research/experiments/experiment/candidates/candidate/execute"

    omitted = client.post(path)
    empty = client.post(path, json={})

    assert omitted.status_code == 200
    assert empty.status_code == 200
    assert omitted.json()["execution"]["status"] == "completed"


def test_execute_rejects_configuration_overrides(client: TestClient) -> None:
    response = client.post(
        "/research/experiments/experiment/candidates/candidate/execute",
        json={"price_field_used": "raw_close"},
    )

    assert response.status_code == 422
    assert response.json() == {
        "detail": {
            "code": "REQUEST_MUST_BE_EMPTY",
            "message": "candidate execution does not accept configuration overrides",
        }
    }


def test_get_execution_and_result_are_read_only_routes(client: TestClient) -> None:
    execution = client.get(
        "/research/experiments/experiment/candidates/candidate/execution"
    )
    result = client.get("/research/experiments/experiment/candidates/candidate/result")

    assert execution.status_code == 200
    assert execution.json()["status"] == "completed"
    assert result.status_code == 200
    assert result.json()["result_hash"] == "result-hash"


def test_domain_error_is_returned_as_structured_detail(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    def fail(experiment_id: str, candidate_id: str):
        raise ExperimentExecutionApiError(409, "ALREADY_RUNNING", "candidate is running")

    monkeypatch.setattr(research_execution_api, "_orchestrator", SimpleNamespace(execute=fail))

    response = client.post(
        "/research/experiments/experiment/candidates/candidate/execute", json={}
    )

    assert response.status_code == 409
    assert response.json() == {
        "detail": {"code": "ALREADY_RUNNING", "message": "candidate is running"}
    }


def test_unexpected_dependency_error_does_not_leak_internal_details(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    def fail(experiment_id: str, candidate_id: str):
        raise RuntimeError("/private/research.db sqlite internal failure")

    monkeypatch.setattr(research_execution_api, "_orchestrator", SimpleNamespace(execute=fail))

    response = client.post(
        "/research/experiments/experiment/candidates/candidate/execute", json={}
    )

    assert response.status_code == 503
    assert response.json() == {
        "detail": {
            "code": "EXECUTION_SERVICE_UNAVAILABLE",
            "message": "experiment execution service is unavailable",
        }
    }
    assert "/private/research.db" not in response.text
    assert "sqlite" not in response.text
