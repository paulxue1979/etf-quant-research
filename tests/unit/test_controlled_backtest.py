"""Controlled backtest tests use isolated SQLite and real production backtest logic."""

from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from mcp import Client

from backend.app.backtest_lab import BacktestRequest
from backend.app.backtest_repository import BacktestRepository
from backend.app.backtest_service import BacktestService
from backend.app.controlled_backtest_api import get_service, router
from backend.app.controlled_backtest_repository import (
    ControlledBacktestRepository,
    ControlledBacktestStorageError,
)
from backend.app.controlled_backtest_service import (
    ControlledBacktestError,
    ControlledBacktestService,
)
from backend.app.operator_read_service import OperatorReadService
from backend.app.strategy_repository import StrategyRepository
from backend.operator_mcp import create_server
from tests.unit.test_backtest_service import FakeDataService, _dataset, _definition


class ReadyAvailability:
    def get_data_availability(self, strategy_id, version_id, start, end):
        return {
            "status": "READY",
            "effective_start_expected": start.isoformat(),
            "effective_end_expected": end.isoformat(),
            "warmup_start": "2025-01-01",
            "blocking_issues": [],
        }


@pytest.fixture
def controlled(tmp_path: Path):
    db = tmp_path / "strategy.db"
    strategies = StrategyRepository(db)
    version = strategies.create(_definition())
    runs = BacktestRepository(db)
    executor = BacktestService(
        strategy_repository=strategies,
        backtest_repository=runs,
        data_service=FakeDataService(_dataset()),  # type: ignore[arg-type]
    )
    plans = ControlledBacktestRepository(db)
    service = ControlledBacktestService(
        plans=plans,
        strategies=strategies,
        read=ReadyAvailability(),  # type: ignore[arg-type]
        backtests=runs,
        executor=executor,
    )
    return service, plans, strategies, runs, version


def prepare(service, version, **overrides):
    return service.prepare(
        **{
            "strategy_id": version.strategy_id,
            "strategy_version_id": version.version_id,
            "requested_start": "2026-02-02",
            "requested_end": "2026-02-05",
            "initial_capital": 10000,
            **overrides,
        }
    )


def test_prepare_persists_exact_version_and_zero_execution(controlled) -> None:
    service, plans, strategies, runs, version = controlled
    before = strategies.get_strategy(version.strategy_id)
    plan = prepare(
        service,
        version,
        contribution_enabled=True,
        contribution_frequency="monthly",
        contribution_amount="500",
    )
    assert plan["status"] == "PROPOSED"
    assert plan["strategy_version_hash"] == version.content_hash
    assert plan["contribution_start_date"] == "2026-02-02"
    assert plan["contribution_end_date"] == "2026-02-05"
    assert (
        plans.get(plan["config_id"])["backtest_request"]["contribution_schedule"]["frequency"]
        == "monthly"
    )
    assert runs.list(version.strategy_id) == ()
    assert strategies.get_strategy(version.strategy_id) == before
    assert len(strategies.list_version_records(version.strategy_id)) == 1


def test_reject_is_terminal_and_does_not_run(controlled) -> None:
    service, _, _, runs, version = controlled
    plan = prepare(service, version)
    assert service.reject(plan["config_id"])["status"] == "REJECTED"
    with pytest.raises(ControlledBacktestError, match="PLAN_NOT_PROPOSED"):
        service.approve_and_run(plan["config_id"])
    assert runs.list(version.strategy_id) == ()


def test_approve_runs_once_and_reads_canonical_analytics(controlled) -> None:
    service, _, strategies, runs, version = controlled
    before = strategies.get_strategy(version.strategy_id)
    plan = prepare(
        service,
        version,
        contribution_enabled=True,
        contribution_frequency="monthly",
        contribution_amount="500",
    )
    first = service.approve_and_run(plan["config_id"])
    second = service.approve_and_run(plan["config_id"])
    assert first["status"] == second["status"] == "COMPLETED"
    assert first["backtest_run_id"] == second["backtest_run_id"]
    assert len(runs.list(version.strategy_id)) == 1
    result = service.get_result(plan["config_id"])
    assert result["run_id"] == first["backtest_run_id"]
    assert result["total_contributions"] == 500
    assert result["cagr"] == runs.get(result["run_id"]).performance_analysis.cagr.to_dict()
    assert strategies.get_strategy(version.strategy_id) == before
    restored = ControlledBacktestRepository(runs.db_path)
    assert restored.get(plan["config_id"])["status"] == "COMPLETED"


def test_completed_plan_and_result_survive_service_restart(controlled) -> None:
    service, _, strategies, runs, version = controlled
    plan = prepare(service, version)
    completed = service.approve_and_run(plan["config_id"])
    restarted = ControlledBacktestService(
        plans=ControlledBacktestRepository(runs.db_path),
        strategies=strategies,
        read=ReadyAvailability(),  # type: ignore[arg-type]
        backtests=runs,
    )
    assert restarted.get(plan["config_id"])["backtest_run_id"] == completed["backtest_run_id"]
    assert restarted.get_result(plan["config_id"])["run_id"] == completed["backtest_run_id"]


def test_operator_matches_direct_production_engine_with_and_without_dca(controlled) -> None:
    service, _, _, runs, version = controlled
    for schedule in (None, {"frequency": "monthly", "amount": "500"}):
        options = (
            {
                "contribution_enabled": True,
                "contribution_frequency": "monthly",
                "contribution_amount": "500",
            }
            if schedule
            else {}
        )
        plan = prepare(service, version, **options)
        operator = service.approve_and_run(plan["config_id"])
        direct = service._executor.run(BacktestRequest.model_validate(plan["backtest_request"]))
        observed = runs.get(operator["backtest_run_id"])
        assert observed.backtest_result == direct.backtest_result
        assert (
            observed.performance_analysis.total_return == direct.performance_analysis.total_return
        )
        assert observed.performance_analysis.xirr == direct.performance_analysis.xirr


@pytest.mark.parametrize(
    "options,code",
    [
        (
            {
                "contribution_enabled": True,
                "contribution_frequency": "weekly",
                "contribution_amount": "500",
            },
            "PLAN_INVALID_CONTRIBUTION",
        ),
        (
            {
                "contribution_enabled": True,
                "contribution_frequency": "monthly",
                "contribution_amount": "500",
                "contribution_start_date": "2026-02-03",
            },
            "PLAN_UNSUPPORTED_CONTRIBUTION_WINDOW",
        ),
        (
            {
                "contribution_enabled": True,
                "contribution_frequency": "monthly",
                "contribution_amount": "0",
            },
            "PLAN_INVALID_CONTRIBUTION",
        ),
        ({"requested_start": "around 2007"}, "BACKTEST_PLAN_AMBIGUOUS_DATE"),
        ({"initial_capital": 0}, "PLAN_INVALID_CAPITAL"),
    ],
)
def test_unsupported_or_ambiguous_inputs_fail_closed(controlled, options, code) -> None:
    service, _, _, runs, version = controlled
    with pytest.raises(ControlledBacktestError, match=code):
        prepare(service, version, **options)
    assert runs.list(version.strategy_id) == ()


def test_post_commit_response_failure_does_not_mark_completed_run_failed(controlled) -> None:
    service, plans, _, runs, version = controlled

    class LostResponse(ControlledBacktestRepository):
        def complete_with_run(self, config_id, execution_id, run):
            super().complete_with_run(config_id, execution_id, run)
            raise OSError("response transport lost")

    service._plans = LostResponse(plans.path)
    plan = prepare(service, version)
    result = service.approve_and_run(plan["config_id"])
    assert result["status"] == "COMPLETED"
    assert len(runs.list(version.strategy_id)) == 1


def test_failed_execution_is_terminal_and_does_not_persist_run(controlled) -> None:
    service, _, _, runs, version = controlled

    class FailingExecutor:
        def build_run(self, request):
            raise RuntimeError("temporary engine failure")

    service._executor = FailingExecutor()
    plan = prepare(service, version)
    with pytest.raises(ControlledBacktestError, match="BACKTEST_EXECUTION_FAILED"):
        service.approve_and_run(plan["config_id"])
    failed = service.get(plan["config_id"])
    assert failed["status"] == "FAILED"
    assert failed["failure_stage"] == "EXECUTION"
    assert "temporary engine failure" not in failed["failure_message"]
    assert runs.list(version.strategy_id) == ()


def test_blocked_availability_never_claims_a_run(controlled) -> None:
    service, _, _, runs, version = controlled

    class BlockedAvailability:
        def get_data_availability(self, *args):
            return {
                "status": "BLOCKED",
                "effective_start_expected": None,
                "effective_end_expected": None,
                "warmup_start": "2025-01-01",
                "blocking_issues": [{"code": "ACTIVE_ASSET_UNAVAILABLE", "message": "cache miss"}],
            }

    service._read = BlockedAvailability()
    plan = prepare(service, version)
    assert plan["validation_status"] == "BLOCKED"
    with pytest.raises(ControlledBacktestError, match="PLAN_DATA_BLOCKED"):
        service.approve_and_run(plan["config_id"])
    assert service.get(plan["config_id"])["status"] == "PROPOSED"
    assert runs.list(version.strategy_id) == ()


def test_reading_missing_store_does_not_create_a_database(tmp_path) -> None:
    path = tmp_path / "missing.db"
    plans = ControlledBacktestRepository(path)
    assert plans.list() == []
    with pytest.raises(ControlledBacktestStorageError, match="PLAN_NOT_FOUND"):
        plans.get("unknown")
    assert not path.exists()


def test_parallel_approval_persists_at_most_one_run(controlled) -> None:
    service, _, _, runs, version = controlled
    plan = prepare(service, version)

    def approve() -> object:
        try:
            return service.approve_and_run(plan["config_id"])
        except ControlledBacktestError as exc:
            return str(exc)

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = tuple(pool.map(lambda _: approve(), range(2)))
    assert any(isinstance(item, dict) and item["status"] == "COMPLETED" for item in results)
    assert len(runs.list(version.strategy_id)) == 1


def test_tampered_plan_is_not_read_or_approved(controlled) -> None:
    service, plans, _, runs, version = controlled
    plan = prepare(service, version)
    import sqlite3

    with sqlite3.connect(plans.path) as connection:
        connection.execute(
            "UPDATE pending_backtest_configurations SET config_json = ? WHERE config_id = ?",
            ('{"tampered":true}', plan["config_id"]),
        )
    with pytest.raises(ControlledBacktestStorageError, match="PLAN_INTEGRITY_ERROR"):
        service.approve_and_run(plan["config_id"])
    assert runs.list(version.strategy_id) == ()


def test_changed_version_identity_marks_plan_stale_without_execution(controlled) -> None:
    service, _, _, runs, version = controlled
    plan = prepare(service, version)
    original = service.get_strategy_version

    def changed(strategy_id, version_id=""):
        return {**original(strategy_id, version_id), "strategy_version_hash": "different"}

    service.get_strategy_version = changed
    with pytest.raises(ControlledBacktestError, match="PLAN_VERSION_MISMATCH"):
        service.approve_and_run(plan["config_id"])
    assert service.get(plan["config_id"])["status"] == "STALE"
    assert runs.list(version.strategy_id) == ()


def test_changed_data_evidence_marks_plan_stale_without_execution(controlled) -> None:
    service, _, _, runs, version = controlled
    plan = prepare(service, version)

    class ChangedAvailability(ReadyAvailability):
        def get_data_availability(self, strategy_id, version_id, start, end):
            return {
                **super().get_data_availability(strategy_id, version_id, start, end),
                "effective_end_expected": "2026-02-04",
            }

    service._read = ChangedAvailability()
    with pytest.raises(ControlledBacktestError, match="PLAN_DATA_CHANGED"):
        service.approve_and_run(plan["config_id"])
    assert service.get(plan["config_id"])["status"] == "STALE"
    assert runs.list(version.strategy_id) == ()


def test_browser_origin_and_single_approval_endpoint(controlled) -> None:
    service, _, _, runs, version = controlled
    plan = prepare(service, version)
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_service] = lambda: service
    client = TestClient(app)
    endpoint = f"/backtest-operator/configurations/{plan['config_id']}"
    assert client.get(endpoint).json()["status"] == "PROPOSED"
    assert client.post(endpoint + "/approve-run").status_code == 403
    assert (
        client.post(endpoint + "/approve-run", headers={"Origin": "http://evil.local"}).status_code
        == 403
    )
    assert (
        client.post(
            endpoint + "/approve-run", headers={"Origin": "http://127.0.0.1:5173"}, json={}
        ).status_code
        == 400
    )
    assert runs.list(version.strategy_id) == ()
    response = client.post(endpoint + "/approve-run", headers={"Origin": "http://127.0.0.1:5173"})
    assert response.status_code == 200
    assert response.json()["status"] == "COMPLETED"
    assert (
        client.post(endpoint + "/approve-run", headers={"Origin": "http://127.0.0.1:5173"}).json()[
            "backtest_run_id"
        ]
        == response.json()["backtest_run_id"]
    )
    assert len(runs.list(version.strategy_id)) == 1


def test_direct_browser_approval_rejects_blocked_preflight_without_run(controlled) -> None:
    service, _, _, runs, version = controlled

    class BlockedAvailability:
        def get_data_availability(self, *args):
            return {
                "status": "BLOCKED",
                "effective_start_expected": None,
                "effective_end_expected": None,
                "warmup_start": "2025-01-01",
                "blocking_issues": [
                    {"code": "ACTIVE_ASSET_UNAVAILABLE", "message": "TQQQ cache miss"}
                ],
            }

    service._read = BlockedAvailability()
    plan = prepare(service, version)
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_service] = lambda: service
    endpoint = f"/backtest-operator/configurations/{plan['config_id']}/approve-run"
    client = TestClient(app)
    for availability in (BlockedAvailability(), ReadyAvailability()):
        service._read = availability
        response = client.post(endpoint, headers={"Origin": "http://127.0.0.1:5173"})
        assert response.status_code == 422
        assert response.json()["detail"]["code"] == "PLAN_DATA_BLOCKED"
    assert service.get(plan["config_id"])["status"] == "PROPOSED"
    assert runs.list(version.strategy_id) == ()


def test_mcp_can_prepare_and_read_but_cannot_approve_or_run(controlled) -> None:
    service, _, _, runs, version = controlled
    read = OperatorReadService(db_path=runs.db_path)

    async def exercise() -> None:
        async with Client(create_server(read, backtest_service=service)) as client:
            tools = (await client.list_tools()).tools
            names = {tool.name for tool in tools}
            assert "quant_prepare_backtest_configuration" in names
            assert "quant_get_backtest_result" in names
            assert not any(
                "approve" in name or "run_backtest" in name or "execute_backtest" in name
                for name in names
            )
            version_result = await client.call_tool(
                "quant_get_strategy_version",
                {
                    "strategy_id": version.strategy_id,
                    "version_id": version.version_id,
                },
            )
            assert (
                version_result.structured_content["strategy_version_hash"] == version.content_hash
            )
            prepared = await client.call_tool(
                "quant_prepare_backtest_configuration",
                {
                    "strategy_id": version.strategy_id,
                    "strategy_version_id": version.version_id,
                    "requested_start": "2026-02-02",
                    "requested_end": "2026-02-05",
                    "initial_capital": 10000,
                    "contribution_enabled": True,
                    "contribution_amount": "500",
                    "contribution_frequency": "monthly",
                },
            )
            assert not prepared.is_error
            config_id = prepared.structured_content["config_id"]
            fetched = await client.call_tool(
                "quant_get_backtest_configuration", {"config_id": config_id}
            )
            assert fetched.structured_content["status"] == "PROPOSED"
            assert runs.list(version.strategy_id) == ()
            service.approve_and_run(config_id)
            result = await client.call_tool("quant_get_backtest_result", {"config_id": config_id})
            assert result.structured_content["total_contributions"] == 500

    asyncio.run(exercise())
