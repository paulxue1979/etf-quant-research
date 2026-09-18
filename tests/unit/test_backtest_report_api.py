from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

import backend.app.backtest_lab as backtest_lab
import backend.app.backtest_report_api as report_api
from backend.app.main import app
from tests.unit.test_backtest_repository import _run


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch) -> TestClient:
    run = _run()
    monkeypatch.setattr(
        report_api,
        "backtest_repository",
        SimpleNamespace(get=lambda run_id: run if run_id == run.backtest_run_id else None),
    )
    monkeypatch.setattr(
        backtest_lab,
        "backtest_repository",
        SimpleNamespace(get=lambda run_id: run if run_id == run.backtest_run_id else None),
    )
    return TestClient(app)


def test_report_summary_reads_persisted_run_without_running_backtest(client: TestClient) -> None:
    response = client.get("/research/backtests/repo-run/report")

    assert response.status_code == 200
    payload = response.json()
    assert payload["report_schema_version"] == "1.0"
    assert payload["identity"]["backtest_run_id"] == "repo-run"
    assert payload["strategy_provenance"]["status"] == "not_available"
    assert payload["allocations"]["target"]["status"] == "not_available"
    assert payload["allocations"]["actual"]["source"] == "BacktestResult.allocation_history"
    assert payload["allocations"]["cash_semantics"].endswith("SGOV remains an asset")


def test_report_series_rejects_unknown_include_and_invalid_date_range(client: TestClient) -> None:
    unknown = client.get("/research/backtests/repo-run/report/series?include=equity,unknown")
    invalid_range = client.get(
        "/research/backtests/repo-run/report/series?include=equity&from=2026-01-04&to=2026-01-02"
    )

    assert unknown.status_code == 422
    assert unknown.json()["detail"]["code"] == "INVALID_REPORT_SERIES_INCLUDE"
    assert invalid_range.status_code == 422
    assert invalid_range.json()["detail"]["code"] == "INVALID_REPORT_DATE_RANGE"


def test_report_returns_structured_not_found_error(client: TestClient) -> None:
    response = client.get("/research/backtests/missing/report")

    assert response.status_code == 404
    assert response.json() == {
        "detail": {"code": "REPORT_NOT_FOUND", "message": "backtest run was not found"}
    }


def test_existing_backtest_endpoint_does_not_expose_report_storage(client: TestClient) -> None:
    response = client.get("/backtests/repo-run")

    assert response.status_code == 200
    assert response.json()["backtest_run_id"] == "repo-run"
    assert "strategy_provenance" not in response.json()
