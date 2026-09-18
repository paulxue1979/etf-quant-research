from __future__ import annotations

from dataclasses import replace
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


def test_holding_report_supports_pagination_status_filter_and_sorting(client: TestClient) -> None:
    response = client.get(
        "/research/backtests/repo-run/report/holdings"
        "?status=OPEN&symbol=qqq&limit=1&offset=0&sort_by=entry_date&order=asc"
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "available"
    assert payload["total"] == 1
    assert payload["limit"] == 1
    assert payload["offset"] == 0
    assert payload["filters"] == {"status": "OPEN", "symbol": "QQQ"}
    assert payload["sort"] == {"by": "entry_date", "order": "asc"}
    assert payload["items"][0]["status"] == "OPEN"
    assert payload["items"][0]["symbol"] == "QQQ"
    assert payload["items"][0]["entry_signal_date"] == "2026-01-02"
    assert payload["items"][0]["entry_execution_date"] == "2026-01-03"


def test_holding_report_rejects_invalid_query_and_returns_legacy_unavailable(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    invalid = client.get("/research/backtests/repo-run/report/holdings?limit=0")
    run = _run()
    legacy = replace(
        run,
        backtest_result=replace(run.backtest_result, holding_segments=None),
    )
    monkeypatch.setattr(
        report_api,
        "backtest_repository",
        SimpleNamespace(get=lambda run_id: legacy if run_id == legacy.backtest_run_id else None),
    )
    unavailable = client.get("/research/backtests/repo-run/report/holdings")

    assert invalid.status_code == 422
    assert unavailable.status_code == 200
    assert unavailable.json()["status"] == "not_available"
    assert unavailable.json()["items"] == []


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
