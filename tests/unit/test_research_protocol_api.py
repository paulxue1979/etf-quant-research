from __future__ import annotations

from dataclasses import replace
from datetime import date
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from backend.app import research_protocol_api
from backend.app.backtest_repository import BacktestRepository
from backend.app.main import app
from backend.app.research_protocol import ResearchProtocolRepository
from backend.app.strategy_repository import StrategyRepository
from research import ParameterDefinition, ParameterSet, ParameterType, materialize_strategy_version
from tests.unit.test_backtest_repository import _run
from tests.unit.test_materialization import _period_binding, _space, _version
from tests.unit.test_strategy_repository import _definition


@pytest.fixture
def client(tmp_path, monkeypatch: pytest.MonkeyPatch):
    db_path = tmp_path / "strategy.db"
    strategies = StrategyRepository(db_path)
    version = strategies.create(_definition("repo"))
    backtests = BacktestRepository(db_path)
    is_run = _backtest_run("is-run", version, date(2026, 1, 2), date(2026, 1, 4))
    oos_run = _backtest_run("oos-run", version, date(2026, 1, 6), date(2026, 1, 8))
    backtests.create(is_run)
    backtests.create(oos_run)
    protocols = ResearchProtocolRepository(db_path)
    monkeypatch.setattr(research_protocol_api, "strategy_repository", strategies)
    monkeypatch.setattr(research_protocol_api, "backtest_repository", backtests)
    monkeypatch.setattr(research_protocol_api, "research_protocol_repository", protocols)
    return TestClient(app), version, is_run, oos_run


def _backtest_run(run_id: str, version, start_date: date, end_date: date):
    source = _run()
    result = replace(
        source.backtest_result,
        strategy_version_id=version.version_id,
        start_date=start_date,
        end_date=end_date,
    )
    analysis = replace(
        source.performance_analysis,
        backtest_run_id=run_id,
        strategy_version_id=version.version_id,
        start_date=start_date,
        end_date=end_date,
    )
    return replace(
        source,
        backtest_run_id=run_id,
        strategy_version_id=version.version_id,
        strategy_version_content_hash=version.content_hash,
        backtest_result=result,
        performance_analysis=analysis,
    )


def _protocol_request() -> dict[str, object]:
    return {
        "is_start_date": "2026-01-02",
        "is_end_date": "2026-01-04",
        "oos_start_date": "2026-01-06",
        "oos_end_date": "2026-01-08",
        "selection_rules": ["human review of IS evidence"],
        "allowed_metrics": ["cagr", "max_drawdown"],
        "evaluation_config": {
            "price_field_used": "adjusted_close",
            "initial_capital": 10000,
            "commission": {"rate": 0, "per_order": 0},
            "slippage": 0,
            "execution_rule": "next_trading_day_open",
            "fractional_shares": False,
            "rebalance_policy": {"frequency": "daily", "threshold": None},
            "engine_version": "phase-3.0",
        },
    }


def _create_protocol(client: TestClient) -> str:
    response = client.post("/research/protocols", json=_protocol_request())
    assert response.status_code == 201
    return response.json()["protocol"]["protocol_id"]


def _advance_to_is(client: TestClient, version_id: str) -> tuple[str, str]:
    protocol_id = _create_protocol(client)
    candidates = client.post(
        f"/research/protocols/{protocol_id}/candidate-sets",
        json={"strategy_version_ids": [version_id]},
    )
    assert candidates.status_code == 201
    candidate_set_id = candidates.json()["candidate_sets"][0]["candidate_set_id"]
    assert client.post(f"/research/candidate-sets/{candidate_set_id}/lock").status_code == 200
    assert client.post(
        f"/research/protocols/{protocol_id}/transitions", json={"status": "frozen"}
    ).status_code == 200
    assert client.post(
        f"/research/protocols/{protocol_id}/transitions", json={"status": "is_evaluated"}
    ).status_code == 200
    return protocol_id, candidate_set_id


def test_protocol_api_records_append_only_is_selection_and_oos_lifecycle(client) -> None:
    http, version, is_run, oos_run = client
    protocol_id, candidate_set_id = _advance_to_is(http, version.version_id)

    selection = http.post(
        f"/research/protocols/{protocol_id}/selection-decisions",
        json={
            "candidate_set_id": candidate_set_id,
            "selected_strategy_version_id": version.version_id,
            "is_backtest_run_ids": [is_run.backtest_run_id],
            "selected_metrics": {"cagr": 0.12},
            "rationale": "Human review used IS evidence only.",
        },
    )
    assert selection.status_code == 201
    decision_id = selection.json()["selections"][0]["decision_id"]
    assert selection.json()["protocol"]["status"] == "is_evaluated"

    assert http.post(
        f"/research/protocols/{protocol_id}/transitions",
        json={"status": "selection_recorded"},
    ).status_code == 200
    freeze = http.post(
        f"/research/protocols/{protocol_id}/freeze",
        json={"selection_decision_id": decision_id, "reason": "Freeze before OOS observation."},
    )
    assert freeze.status_code == 201
    freeze_id = freeze.json()["freezes"][0]["freeze_id"]

    observed = http.post(
        f"/research/protocols/{protocol_id}/oos-evaluations",
        json={"freeze_id": freeze_id, "backtest_run_id": oos_run.backtest_run_id},
    )
    assert observed.status_code == 201
    assert observed.json()["oos_evaluations"][0]["untouched_oos"] is False
    assert observed.json()["oos_evaluations"][0]["provenance"]["selection_allowed"] is False

    assert http.post(
        f"/research/protocols/{protocol_id}/transitions",
        json={"status": "oos_evaluated"},
    ).status_code == 200
    duplicate = http.post(
        f"/research/protocols/{protocol_id}/oos-evaluations",
        json={"freeze_id": freeze_id, "backtest_run_id": oos_run.backtest_run_id},
    )
    assert duplicate.status_code == 422
    assert duplicate.json()["detail"] == {
        "code": "OOS_OBSERVATION_ALREADY_RECORDED",
        "message": "OOS has already been observed for this protocol",
    }


def test_protocol_api_rejects_oos_back_selection_and_unknown_versions(client) -> None:
    http, version, _, oos_run = client
    protocol_id, candidate_set_id = _advance_to_is(http, version.version_id)

    back_selection = http.post(
        f"/research/protocols/{protocol_id}/selection-decisions",
        json={
            "candidate_set_id": candidate_set_id,
            "selected_strategy_version_id": version.version_id,
            "is_backtest_run_ids": [oos_run.backtest_run_id],
            "rationale": "This must be rejected.",
        },
    )
    assert back_selection.status_code == 422
    assert "IS backtest runs only" in back_selection.json()["detail"]

    unknown = http.post(
        f"/research/protocols/{protocol_id}/candidate-sets",
        json={"strategy_version_ids": ["missing"]},
    )
    assert unknown.status_code == 404
    assert unknown.json() == {"detail": "strategy version was not found"}


def test_protocol_api_selects_and_freezes_exact_persisted_derived_version(client) -> None:
    http, _, _, _ = client
    strategies = research_protocol_api.strategy_repository
    base = strategies.create(_version().configuration)
    derived = materialize_strategy_version(
        base,
        ParameterSet(
            {"period": 20},
            _space(
                ParameterDefinition(
                    "period", ParameterType.INTEGER, min=1, max=200, step=1
                )
            ),
        ),
        (_period_binding(),),
    )
    persisted = strategies.persist_exact_strategy_version(derived)
    is_run = _backtest_run(
        "derived-is-run", persisted, date(2026, 1, 2), date(2026, 1, 4)
    )
    research_protocol_api.backtest_repository.create(is_run)

    protocol_id, candidate_set_id = _advance_to_is(http, persisted.version_id)
    selection = http.post(
        f"/research/protocols/{protocol_id}/selection-decisions",
        json={
            "candidate_set_id": candidate_set_id,
            "selected_strategy_version_id": persisted.version_id,
            "is_backtest_run_ids": [is_run.backtest_run_id],
            "selected_metrics": {"cagr": 0.12},
            "rationale": "Human review used the persisted IS candidate only.",
        },
    )
    assert selection.status_code == 201
    decision_id = selection.json()["selections"][0]["decision_id"]
    assert http.post(
        f"/research/protocols/{protocol_id}/transitions",
        json={"status": "selection_recorded"},
    ).status_code == 200

    freeze = http.post(
        f"/research/protocols/{protocol_id}/freeze",
        json={"selection_decision_id": decision_id, "reason": "Freeze exact IS candidate."},
    )

    assert freeze.status_code == 201
    record = freeze.json()["freezes"][0]
    assert record["strategy_version_id"] == persisted.version_id
    assert record["strategy_version_content_hash"] == persisted.content_hash


def test_protocol_api_hides_unexpected_persistence_details(
    client, monkeypatch: pytest.MonkeyPatch
) -> None:
    http, _, _, _ = client
    monkeypatch.setattr(
        research_protocol_api,
        "research_protocol_repository",
        SimpleNamespace(list_protocols=lambda: (_ for _ in ()).throw(RuntimeError("/private/db"))),
    )

    response = http.get("/research/protocols")

    assert response.status_code == 503
    assert response.json() == {"detail": "research protocol service is unavailable"}
    assert "/private/db" not in response.text
