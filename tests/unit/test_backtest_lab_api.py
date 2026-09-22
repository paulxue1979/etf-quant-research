from __future__ import annotations

from dataclasses import replace
from types import MappingProxyType, SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from analytics.models import MetricValue
from backend.app import backtest_lab
from backend.app.backtest_models import BacktestRunMetadata
from backend.app.backtest_repository import (
    BacktestMetadataPage,
    BacktestPersistenceError,
    BacktestRunRecord,
)
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
            list_records=lambda strategy_id=None: (),
            list_metadata=lambda strategy_id=None, **kwargs: BacktestMetadataPage(
                items=(),
                total=0,
                limit=kwargs.get("limit", 50),
                offset=kwargs.get("offset", 0),
                sort_by=kwargs.get("sort_by", "created_at"),
                order=kwargs.get("order", "desc"),
            ),
            get_records=lambda run_ids: (),
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


def test_backtest_request_normalizes_an_explicit_benchmark_symbol() -> None:
    request = backtest_lab.BacktestRequest.model_validate(
        _request_payload() | {"benchmark_symbol": " spy "}
    )

    assert request.benchmark_symbol == "SPY"


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
    def fail(strategy_id: str, **kwargs: object) -> tuple[object, ...]:
        raise BacktestPersistenceError("database path leaked")

    monkeypatch.setattr(backtest_lab.backtest_repository, "list_metadata", fail)

    response = client.get("/strategies/repo/backtests")

    assert response.status_code == 500
    assert response.json() == {"detail": "backtest persistence is unavailable"}


def _record(
    run_id: str = "repo-run",
    *,
    initial_capital: float = 10_000.0,
    cagr_not_evaluable: bool = False,
) -> BacktestRunRecord:
    source = _run()
    snapshot = dict(source.backtest_result.configuration_snapshot)
    snapshot["initial_capital"] = initial_capital
    result = replace(
        source.backtest_result,
        initial_capital=initial_capital,
        configuration_snapshot=MappingProxyType(snapshot),
    )
    analysis = replace(
        source.performance_analysis,
        backtest_run_id=run_id,
        initial_capital=initial_capital,
    )
    if cagr_not_evaluable:
        analysis = replace(analysis, cagr=MetricValue.not_evaluable("fixture unavailable metric"))
    run = replace(
        source,
        backtest_run_id=run_id,
        backtest_result=result,
        performance_analysis=analysis,
    )
    return BacktestRunRecord(run=run, analysis_version="phase-4i.0")


def test_research_history_returns_saved_runs_without_running_backtests(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    record = _record(cagr_not_evaluable=True)
    metadata = BacktestRunMetadata.from_run(record.run)
    monkeypatch.setattr(
        backtest_lab.backtest_repository,
        "list_metadata",
        lambda strategy_id=None, **kwargs: BacktestMetadataPage(
            items=(metadata,),
            total=1,
            limit=kwargs.get("limit", 50),
            offset=kwargs.get("offset", 0),
            sort_by=kwargs.get("sort_by", "created_at"),
            order=kwargs.get("order", "desc"),
        ),
    )
    monkeypatch.setattr(
        backtest_lab,
        "_service",
        lambda: (_ for _ in ()).throw(AssertionError("research history must not run backtests")),
    )

    response = client.get("/research/backtests?sort_by=cagr&order=desc&limit=10")

    assert response.status_code == 200
    payload = response.json()
    assert payload["total"] == 1
    assert payload["items"][0]["backtest_run_id"] == "repo-run"
    assert payload["items"][0]["metrics"]["cagr"]["status"] == "not_evaluable"


def test_research_comparison_reads_existing_runs_and_reports_compatibility(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    records = (
        _record("repo-run-a", cagr_not_evaluable=True),
        _record("repo-run-b", cagr_not_evaluable=True),
    )
    monkeypatch.setattr(backtest_lab.backtest_repository, "get_records", lambda run_ids: records)

    response = client.post(
        "/research/comparisons",
        json={"backtest_run_ids": ["repo-run-a", "repo-run-b"]},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["comparison_schema_version"] == "2.0"
    assert payload["comparable"] is True
    assert [item["backtest_run_id"] for item in payload["runs"]] == ["repo-run-a", "repo-run-b"]
    assert payload["series"][0]["equity_curve"] == []
    assert payload["series"][0]["portfolio_value"]["status"] == "excluded"
    assert payload["series"][0]["twr"]["status"] == "available"
    assert payload["runs"][0]["metrics"]["cagr"]["value"] is None


def test_research_comparison_surfaces_configuration_mismatches(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    records = (_record("repo-run-a"), _record("repo-run-b", initial_capital=25_000.0))
    monkeypatch.setattr(backtest_lab.backtest_repository, "get_records", lambda run_ids: records)

    response = client.post(
        "/research/comparisons",
        json={"backtest_run_ids": ["repo-run-a", "repo-run-b"]},
    )

    assert response.status_code == 200
    assert response.json()["comparable"] is False
    assert "initial_capital_mismatch" in {
        reason["code"] for reason in response.json()["incompatibility_reasons"]
    }


@pytest.mark.parametrize(
    "payload, status_code",
    [
        ({"backtest_run_ids": ["one"]}, 422),
        ({"backtest_run_ids": [str(index) for index in range(11)]}, 422),
        ({"backtest_run_ids": ["same", "same"]}, 422),
        ({"backtest_run_ids": ["one", "two"], "unexpected": True}, 422),
        (
            {
                "backtest_run_ids": ["one", "two"],
                "include": {"twr": True, "unexpected": True},
            },
            422,
        ),
        ({"backtest_run_ids": ["one", "two"], "include": {"twr": "yes"}}, 422),
    ],
)
def test_research_comparison_validates_request_shape(
    client: TestClient, payload: dict[str, object], status_code: int
) -> None:
    response = client.post("/research/comparisons", json=payload)

    assert response.status_code == status_code


def test_research_comparison_returns_404_for_unknown_runs(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        backtest_lab.backtest_repository,
        "get_records",
        lambda run_ids: (_record("one"),),
    )

    response = client.post("/research/comparisons", json={"backtest_run_ids": ["one", "missing"]})

    assert response.status_code == 404
    assert response.json() == {"detail": "one or more backtest runs were not found"}
