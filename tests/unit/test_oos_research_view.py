from __future__ import annotations

import sqlite3

import pytest
from fastapi.testclient import TestClient

from backend.app.backtest_repository import BacktestRepository
from backend.app.main import app
from backend.app.oos_research_view import OosResearchViewError, OosResearchViewService
from backend.app.oos_result_repository import OosResultRepository
from tests.unit.test_oos_execution_service import FakeStrategyRepository
from tests.unit.test_oos_finalization_service import _prepared


def _finalized(tmp_path):
    finalizer, outcome, protocols, executions = _prepared(tmp_path)
    token = executions.get_execution(outcome.execution_id).lease_token or ""
    result = finalizer.finalize_oos_observation(
        protocol_id=outcome.protocol_id,
        execution_id=outcome.execution_id,
        lease_token=token,
        outcome=outcome,
    )
    version = finalizer._strategies.get_any_version(result.strategy_version_id)
    view = OosResearchViewService(
        protocols=protocols,
        results=OosResultRepository(tmp_path / "research.sqlite3"),
        backtests=BacktestRepository(tmp_path / "research.sqlite3"),
        strategies=FakeStrategyRepository(version),
    )
    return view, result, protocols


def test_read_view_uses_only_finalized_official_result_and_backtest(tmp_path) -> None:
    view, result, _ = _finalized(tmp_path)

    payload = view.get(result.protocol_id)

    assert payload["read_only"] is True
    assert payload["oos_result"]["result_hash"] == result.result_hash
    assert payload["backtest_run"]["backtest_run_id"] == result.backtest_run_id
    assert payload["backtest_run"]["backtest_result"]["trades"] == []
    assert payload["provenance"]["portfolio_initialization"] == "fresh_capital"
    assert payload["provenance"]["oos_signal_policy"] == "oos_signals_only"
    assert payload["provenance"]["asset_universe"] == ["QQQ", "TQQQ"]


def test_unfinalized_protocol_is_not_available(tmp_path) -> None:
    finalizer, outcome, protocols, _ = _prepared(tmp_path)
    version = finalizer._strategies.get_any_version(outcome.strategy_version_id)
    view = OosResearchViewService(
        protocols=protocols,
        results=OosResultRepository(tmp_path / "research.sqlite3"),
        backtests=BacktestRepository(tmp_path / "research.sqlite3"),
        strategies=FakeStrategyRepository(version),
    )

    with pytest.raises(OosResearchViewError, match="not been finalized") as exc_info:
        view.get(outcome.protocol_id)
    assert exc_info.value.code == "OOS_NOT_FINALIZED"


def test_missing_official_backtest_is_rejected(tmp_path) -> None:
    view, result, _ = _finalized(tmp_path)
    connection = sqlite3.connect(tmp_path / "research.sqlite3")
    try:
        connection.execute(
            "DELETE FROM backtest_runs WHERE backtest_run_id = ?",
            (result.backtest_run_id,),
        )
        connection.commit()
    finally:
        connection.close()

    with pytest.raises(OosResearchViewError) as exc_info:
        view.get(result.protocol_id)
    assert exc_info.value.code == "OOS_BACKTEST_RUN_NOT_FOUND"


def test_tampered_official_result_is_rejected(tmp_path) -> None:
    view, result, _ = _finalized(tmp_path)
    connection = sqlite3.connect(tmp_path / "research.sqlite3")
    try:
        connection.execute(
            "UPDATE research_oos_results SET result_hash = ? WHERE protocol_id = ?",
            ("0" * 64, result.protocol_id),
        )
        connection.commit()
    finally:
        connection.close()

    with pytest.raises(OosResearchViewError) as exc_info:
        view.get(result.protocol_id)
    assert exc_info.value.code == "OOS_RESULT_INTEGRITY_ERROR"


def test_protocol_and_strategy_provenance_mismatch_is_rejected(tmp_path) -> None:
    view, result, protocols = _finalized(tmp_path)
    original = view.strategies.get_any_version(result.strategy_version_id)
    assert original is not None
    view = OosResearchViewService(
        protocols=protocols,
        results=view.results,
        backtests=view.backtests,
        strategies=FakeStrategyRepository(None),
    )

    with pytest.raises(OosResearchViewError) as exc_info:
        view.get(result.protocol_id)
    assert exc_info.value.code == "OOS_PROVENANCE_INTEGRITY_ERROR"


def test_http_api_is_get_only_and_returns_structured_errors(tmp_path, monkeypatch) -> None:
    view, result, _ = _finalized(tmp_path)
    import backend.app.oos_research_api as api

    monkeypatch.setattr(api, "_service_instance", view)
    client = TestClient(app)

    response = client.get(f"/research/protocols/{result.protocol_id}/oos")
    assert response.status_code == 200
    assert response.json()["oos_result"]["backtest_run_id"] == result.backtest_run_id

    missing = client.get("/research/protocols/unknown-protocol/oos")
    assert missing.status_code == 404
    assert missing.json()["detail"]["code"] == "OOS_RESULT_NOT_FOUND"

    mutation = client.post(f"/research/protocols/{result.protocol_id}/oos")
    assert mutation.status_code == 405
