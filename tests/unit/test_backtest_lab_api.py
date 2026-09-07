from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from backend.app import backtest_lab
from backend.app.backtest_repository import BacktestPersistenceError
from backend.app.backtest_service import BacktestServiceError
from backend.app.main import app
from tests.unit.test_backtest_repository import _run


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.setattr(
        backtest_lab,
        "strategy_repository",
        SimpleNamespace(
            catalog=lambda: (
                {
                    "strategy_id": "repo",
                    "name": "Repository fixture",
                    "version_count": 2,
                    "latest_version": 2,
                },
            )
        ),
    )
    monkeypatch.setattr(
        backtest_lab,
        "backtest_repository",
        SimpleNamespace(
            get=lambda run_id: None,
            list=lambda strategy_id: (),
        ),
    )
    return TestClient(app)


def _request_payload() -> dict[str, object]:
    return {
        "strategy_id": "repo",
        "strategy_version_id": "repo-v1",
        "start_date": "2026-01-02",
        "end_date": "2026-01-04",
        "initial_capital": 10_000,
        "commission": {"rate": 0.001, "per_order": 1.0},
        "slippage": 0.0005,
        "price_field_used": "adjusted_close",
    }


def test_strategy_catalog_returns_minimal_metadata(client: TestClient) -> None:
    response = client.get("/strategies")

    assert response.status_code == 200
    assert response.json() == [
        {
            "strategy_id": "repo",
            "name": "Repository fixture",
            "version_count": 2,
            "latest_version": 2,
        }
    ]


def test_backtest_request_validation_rejects_unknown_fields(client: TestClient) -> None:
    payload = _request_payload() | {"unexpected": True}

    response = client.post("/backtests", json=payload)

    assert response.status_code == 422


def test_create_backtest_returns_persisted_run(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    run = _run()
    service = SimpleNamespace(run=lambda request: run)
    monkeypatch.setattr(backtest_lab, "_service", lambda: service)

    response = client.post("/backtests", json=_request_payload())

    assert response.status_code == 201
    assert response.json()["backtest_run_id"] == run.backtest_run_id
    assert "performance_analysis" in response.json()


def test_create_backtest_exposes_safe_domain_error(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        backtest_lab,
        "_service",
        lambda: SimpleNamespace(
            run=lambda request: (_ for _ in ()).throw(
                BacktestServiceError("strategy version was not found")
            )
        ),
    )

    response = client.post("/backtests", json=_request_payload())

    assert response.status_code == 422
    assert response.json() == {"detail": "strategy version was not found"}


def test_create_backtest_hides_unexpected_service_details(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    def fail(request: object) -> None:
        raise RuntimeError("/private/secret/path and sqlite details")

    monkeypatch.setattr(
        backtest_lab,
        "_service",
        lambda: SimpleNamespace(run=fail),
    )

    response = client.post("/backtests", json=_request_payload())

    assert response.status_code == 503
    assert response.json() == {"detail": "backtest service is unavailable"}
    assert "/private/secret/path" not in response.text


def test_get_backtest_returns_404_for_unknown_run(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(backtest_lab.backtest_repository, "get", lambda run_id: None)

    response = client.get("/backtests/missing-run")

    assert response.status_code == 404
    assert response.json() == {"detail": "backtest run was not found"}


def test_list_backtests_returns_safe_persistence_error(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    def fail(strategy_id: str) -> tuple[object, ...]:
        raise BacktestPersistenceError("database path leaked")

    monkeypatch.setattr(backtest_lab.backtest_repository, "list", fail)

    response = client.get("/strategies/repo/backtests")

    assert response.status_code == 500
    assert response.json() == {"detail": "backtest persistence is unavailable"}
