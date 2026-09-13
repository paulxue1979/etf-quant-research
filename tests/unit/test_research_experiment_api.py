from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from backend.app import research_experiment_api as api
from backend.app.experiment_results_read_service import ExperimentResultsReadModelError
from backend.app.main import app
from research.experiment_selection import ExperimentSelectionMethod
from research.objective_evaluation import ObjectiveEvaluationState


class _FakeModel:
    def __init__(self, candidates: tuple[object, ...] = ()) -> None:
        self.candidates = candidates

    def to_dict(self) -> dict[str, object]:
        return {
            "experiment_id": "experiment-1",
            "protocol_id": "protocol-1",
            "experiment_status": "completed",
            "is_start": "2026-01-01",
            "is_end": "2026-01-31",
            "parameter_space_hash": "a" * 64,
            "objective_spec_hash": "b" * 64,
            "base_strategy_version_id": "base-v1",
            "base_strategy_version_hash": "c" * 64,
            "engine_version": "phase-3",
            "analysis_version": "phase-4i",
            "candidates": [
                {
                    "candidate_id": item.candidate_id,
                    "candidate_index": item.candidate_index,
                    "candidate_set_hash": item.candidate_set_hash,
                    "result_status": "completed",
                }
                for item in self.candidates
            ],
            "summary": {"candidate_count": len(self.candidates)},
        }


def _candidate(candidate_id: str = "candidate-0", index: int = 0) -> SimpleNamespace:
    return SimpleNamespace(
        candidate_id=candidate_id,
        candidate_index=index,
        candidate_set_hash="d" * 64,
        derived_strategy_version_id="derived-v1",
    )


def _experiment() -> SimpleNamespace:
    objective = SimpleNamespace(
        to_dict=lambda: {"primary_metric": "cagr", "hard_constraints": {"cagr": 0.1}}
    )
    return SimpleNamespace(
        experiment_id="experiment-1",
        protocol_id="protocol-1",
        objective_spec_hash="b" * 64,
        objective_specification=objective,
    )


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.setattr(api, "_load_experiment", lambda experiment_id: _experiment())
    monkeypatch.setattr(
        api, "_load_model", lambda protocol_id, experiment_id: _FakeModel((_candidate(),))
    )
    return TestClient(app)


def test_routes_are_registered_under_research_namespace(client: TestClient) -> None:
    response = client.get("/research/protocols/protocol-1/experiments/experiment-1/results")

    assert response.status_code == 200
    assert response.json()["candidate_set"] == {"candidate_set_hash": "d" * 64}
    assert response.json()["provenance"]["candidate_order"] == "candidate_index_ascending"


def test_experiment_not_found_is_structured(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        api,
        "_load_model",
        lambda protocol_id, experiment_id: (_ for _ in ()).throw(
            api._safe_error(
                ExperimentResultsReadModelError("missing", code="EXPERIMENT_NOT_FOUND"),
                fallback_code="RESULT_READ_UNAVAILABLE",
                fallback_message="experiment results are unavailable",
            )
        ),
    )

    response = client.get("/research/protocols/protocol-1/experiments/missing/results")

    assert response.status_code == 404
    assert response.json() == {
        "detail": {"code": "EXPERIMENT_NOT_FOUND", "message": "experiment was not found"}
    }


def test_protocol_mismatch_is_not_silently_ignored(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        api,
        "_load_model",
        lambda protocol_id, experiment_id: (_ for _ in ()).throw(
            api._safe_error(
                ExperimentResultsReadModelError("mismatch", code="EXPERIMENT_PROTOCOL_MISMATCH"),
                fallback_code="RESULT_READ_UNAVAILABLE",
                fallback_message="experiment results are unavailable",
            )
        ),
    )

    response = client.get("/research/protocols/wrong/experiments/experiment-1/results")

    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "EXPERIMENT_PROTOCOL_MISMATCH"


def test_comparison_delegates_and_does_not_add_ranking_fields(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    diagnostic = SimpleNamespace(
        to_dict=lambda: {
            "status": "compatible",
            "is_compatible": True,
            "compared_candidate_ids": ["candidate-0"],
        }
    )
    monkeypatch.setattr(
        api,
        "ExperimentCompatibilityService",
        lambda: SimpleNamespace(diagnose=lambda model: diagnostic),
    )

    payload = client.get(
        "/research/protocols/protocol-1/experiments/experiment-1/comparison"
    ).json()

    assert payload["status"] == "compatible"
    assert "rank" not in payload
    assert "winner" not in payload
    assert "best" not in payload
    assert payload["provenance"]["is_only"] is True


def test_objective_endpoint_preserves_evaluation_state(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    evaluation = SimpleNamespace(
        to_dict=lambda: {"overall_constraint_state": ObjectiveEvaluationState.PASS.value}
    )
    monkeypatch.setattr(
        api, "evaluate_candidate_objective", lambda experiment, candidate: evaluation
    )

    payload = client.get(
        "/research/protocols/protocol-1/experiments/experiment-1/objective-evaluation"
    ).json()

    assert payload["objective_spec_hash"] == "b" * 64
    assert payload["evaluations"][0]["evaluation"]["overall_constraint_state"] == "pass"
    assert payload["provenance"]["is_only"] is True


def test_selection_rejects_caller_provided_provenance_fields(client: TestClient) -> None:
    response = client.post(
        "/research/protocols/protocol-1/experiments/experiment-1/selection",
        json={
            "selected_candidate_id": "candidate-0",
            "selection_method": "researcher_judgment",
            "researcher_rationale": "Reviewed the frozen IS evidence.",
            "strategy_version_id": "caller-controlled",
        },
    )

    assert response.status_code == 422


def test_selection_resolves_provenance_server_side_and_is_idempotent(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    candidate = _candidate()
    model = _FakeModel((candidate,))
    experiment = _experiment()
    monkeypatch.setattr(api, "_load_model", lambda protocol_id, experiment_id: model)
    monkeypatch.setattr(api, "_load_experiment", lambda experiment_id: experiment)
    evaluation = SimpleNamespace(
        experiment_id="experiment-1",
        candidate_id="candidate-0",
        to_dict=lambda: {"overall_constraint_state": "pass"},
    )
    monkeypatch.setattr(api, "evaluate_candidate_objective", lambda exp, item: evaluation)
    monkeypatch.setattr(
        api,
        "ExperimentCompatibilityService",
        lambda: SimpleNamespace(diagnose=lambda item: SimpleNamespace(status="compatible")),
    )
    monkeypatch.setattr(
        api,
        "strategy_repository",
        SimpleNamespace(get_any_version=lambda version_id: SimpleNamespace(version_id=version_id)),
    )
    seen: list[dict[str, object]] = []
    decision = SimpleNamespace(
        to_dict=lambda: {
            "selection_id": "selection-1",
            "experiment_id": "experiment-1",
            "selected_candidate_id": "candidate-0",
            "selected_result_hash": "e" * 64,
            "protocol_id": "protocol-1",
        }
    )

    def create_decision(**kwargs):
        seen.append(kwargs)
        return decision

    monkeypatch.setattr(api, "create_experiment_selection_decision", create_decision)
    monkeypatch.setattr(api, "selection_repository", SimpleNamespace(create=lambda item: item))

    request = {
        "selected_candidate_id": "candidate-0",
        "selection_method": ExperimentSelectionMethod.RESEARCHER_JUDGMENT.value,
        "researcher_rationale": "Reviewed the frozen IS evidence.",
    }
    first = client.post(
        "/research/protocols/protocol-1/experiments/experiment-1/selection", json=request
    )
    second = client.post(
        "/research/protocols/protocol-1/experiments/experiment-1/selection", json=request
    )

    assert first.status_code == second.status_code == 201
    assert first.json() == second.json()
    assert len(seen) == 2
    assert seen[0]["experiment"] is experiment
    assert seen[0]["selected_candidate_id"] == "candidate-0"
    assert "strategy_version_id" not in request
    assert "objective_hash" not in request


def test_get_selection_reports_missing_selection(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        api, "selection_repository", SimpleNamespace(get_by_experiment_id=lambda _: None)
    )

    response = client.get("/research/protocols/protocol-1/experiments/experiment-1/selection")

    assert response.status_code == 404
    assert response.json()["detail"]["code"] == "SELECTION_NOT_FOUND"


def test_handoff_accepts_empty_body_and_rejects_identity_override(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    decision = SimpleNamespace(
        to_dict=lambda: {"decision_id": "protocol-selection-1"},
        selected_strategy_version_id="derived-v1",
    )
    freeze = SimpleNamespace(
        to_dict=lambda: {"freeze_id": "freeze-1"}, strategy_version_content_hash="f" * 64
    )
    monkeypatch.setattr(
        api,
        "handoff_service",
        SimpleNamespace(handoff_experiment_selection=lambda experiment_id: (decision, freeze)),
    )

    accepted = client.post(
        "/research/protocols/protocol-1/experiments/experiment-1/handoff", json={}
    )
    rejected = client.post(
        "/research/protocols/protocol-1/experiments/experiment-1/handoff",
        json={"selected_candidate_id": "attacker-choice"},
    )

    assert accepted.status_code == 200
    assert accepted.json()["identity"]["strategy_version_id"] == "derived-v1"
    assert accepted.json()["provenance"]["oos_started"] is False
    assert rejected.status_code == 422
    assert rejected.json()["detail"]["code"] == "REQUEST_MUST_BE_EMPTY"


def test_handoff_domain_errors_are_structured_and_do_not_leak_internals(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    from backend.app.experiment_selection_handoff_service import ExperimentSelectionHandoffError

    monkeypatch.setattr(
        api,
        "handoff_service",
        SimpleNamespace(
            handoff_experiment_selection=lambda experiment_id: (_ for _ in ()).throw(
                ExperimentSelectionHandoffError(
                    "/private/research.db traceback", code="PROTOCOL_INVALID_STATE"
                )
            )
        ),
    )

    response = client.post("/research/protocols/protocol-1/experiments/experiment-1/handoff")

    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "PROTOCOL_INVALID_STATE"
    assert "/private/research.db" not in response.text
    assert "traceback" not in response.text.lower()


def test_api_has_no_oos_transition_or_automatic_selection_fields(client: TestClient) -> None:
    results = client.get("/research/protocols/protocol-1/experiments/experiment-1/results").json()
    comparison = client.get(
        "/research/protocols/protocol-1/experiments/experiment-1/comparison"
    ).json()

    serialized = str(results) + str(comparison)
    assert "oos" not in serialized.lower()
    assert "winner" not in serialized.lower()
    assert "recommendation" not in serialized.lower()
