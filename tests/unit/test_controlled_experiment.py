"""PHASE 13U pending-grid safety tests; every write uses tmp_path."""

from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import UTC, date, datetime

import pytest
from fastapi.testclient import TestClient
from mcp import Client

from backend.app.backtest_repository import BacktestRepository
from backend.app.candidate_execution_repository import CandidateExecutionRepository
from backend.app.controlled_experiment_api import get_service
from backend.app.controlled_experiment_repository import ControlledExperimentError
from backend.app.controlled_experiment_service import ControlledExperimentService
from backend.app.experiment_repository import ExperimentPersistenceError, ExperimentRepository
from backend.app.experiment_result_repository import ExperimentResultRepository
from backend.app.grid_search_repository import GridSearchRepository
from backend.app.main import app
from backend.app.research_protocol import (
    CandidateSet,
    ProtocolStatus,
    ResearchEvaluationConfig,
    ResearchProtocolRepository,
)
from backend.app.strategy_repository import StrategyRepository
from backend.operator_mcp import create_server
from data.trading_calendar import default_trading_calendar
from research.execution_service import ExperimentExecutionService
from research.experiments import Experiment, ParameterSet, ParameterSpace
from research.grid_search import GridSearchDefinition, preflight_grid_search
from research.grid_search_service import GridSearchService
from research.result_finalization_service import ExperimentResultFinalizationService
from tests.unit.test_experiment_execution_service import _LocalDataService
from tests.unit.test_research_protocol import _protocol
from tests.unit.test_semantic_bindings import _version


class CacheReady:
    snapshot = "cache-snapshot-1"

    def get_data_availability(
        self, strategy_id: str, version_id: str, start: date, end: date
    ) -> dict[str, object]:
        return {
            "status": "READY",
            "effective_start_expected": start.isoformat(),
            "effective_end_expected": end.isoformat(),
            "cache_only": True,
            "blocking_issues": [],
            "data_snapshot_reference": self.snapshot,
        }


class CacheBlocked(CacheReady):
    def get_data_availability(
        self, strategy_id: str, version_id: str, start: date, end: date
    ) -> dict[str, object]:
        return {
            **super().get_data_availability(strategy_id, version_id, start, end),
            "status": "BLOCKED",
            "blocking_issues": ["CACHE_MISS"],
        }


class TradingDataService(_LocalDataService):
    def get_history(self, request):
        dataset = super().get_history(request)
        sessions = set(default_trading_calendar().sessions(request.start_date, request.end_date))
        return replace(
            dataset, points=tuple(point for point in dataset.points if point.date in sessions)
        )


def _setup(tmp_path, *, ready: bool = True):
    path = tmp_path / "controlled-experiment.db"
    strategies = StrategyRepository(path)
    version = strategies.create(_version().configuration)
    protocols = ResearchProtocolRepository(path)
    base = _protocol()
    evaluation = ResearchEvaluationConfig(
        price_field_used=version.configuration.price_field.value,
        initial_capital=10_000,
        commission={"rate": 0.0, "per_order": 0.0},
        slippage=0,
        execution_rule="next_trading_day_open",
        fractional_shares=False,
        rebalance_policy=version.configuration.rebalance_policy.to_dict(),
        engine_version="phase-3.0",
    )
    protocol = replace(
        base,
        is_start_date=date(2020, 1, 2),
        is_end_date=date(2020, 12, 31),
        oos_start_date=date(2021, 1, 4),
        oos_end_date=date(2021, 12, 31),
        evaluation_config=evaluation,
    )
    protocols.create_protocol(protocol)
    protocols.create_candidate_set(
        CandidateSet(
            candidate_set_id="controlled-candidates",
            protocol_id=protocol.protocol_id,
            strategy_version_ids=(version.version_id,),
            strategy_version_content_hashes={version.version_id: version.content_hash},
            created_at=datetime(2026, 1, 1, tzinfo=UTC),
        )
    )
    protocols.lock_candidate_set("controlled-candidates")
    protocols.transition_protocol(protocol.protocol_id, "frozen")
    read = CacheReady() if ready else CacheBlocked()
    service = ControlledExperimentService(path=path, read=read, grid=object())
    return service, version, protocol


def _request(service, version, protocol, *, key="intent-1") -> dict[str, object]:
    from research.semantic_bindings import discover_semantic_bindings

    catalog = discover_semantic_bindings(version).supported
    threshold = next(item for item in catalog if item.value_type.value == "relative_threshold")
    period = next(item for item in catalog if item.value_type.value == "positive_integer")
    return {
        "strategy_id": version.strategy_id,
        "strategy_version_id": version.version_id,
        "strategy_version_hash": version.content_hash,
        "protocol_id": protocol.protocol_id,
        "requested_is_start": "2020-01-02",
        "requested_is_end": "2020-12-31",
        "initial_capital": 10_000,
        "axes": [
            {"semantic_target_id": threshold.parameter_name, "unit": "percent", "values": [3, 4]},
            {"semantic_target_id": period.parameter_name, "unit": "native", "values": [10, 20]},
        ],
        "client_request_key": key,
    }


def test_prepare_is_durable_idempotent_and_does_not_create_experiment(tmp_path) -> None:
    service, version, protocol = _setup(tmp_path)
    request = _request(service, version, protocol)
    first = service.prepare(**request)
    second = ControlledExperimentService(
        path=service._path, read=CacheReady(), grid=object()
    ).prepare(**request)
    assert first["config_id"] == second["config_id"]
    assert first["validation_status"] == "READY"
    assert first["candidate_count"] == first["grid_preflight"]["executable_count"] == 4
    assert first["axes"][0]["canonical_values"] == [0.03, 0.04]
    assert first["strategy_version_hash"] == version.content_hash
    assert service._experiments.get(first["experiment_id"]) is None
    loaded = service.get(first["config_id"])
    assert loaded["approval_status"] == "PROPOSED"
    assert [event["event_type"] for event in loaded["audit_events"]] == ["PREPARED"]
    assert second["audit_events"] == loaded["audit_events"]
    with pytest.raises(ControlledExperimentError, match="IDEMPOTENCY_KEY_CONFLICT"):
        service.prepare(**{**request, "initial_capital": 11_000})
    with pytest.raises(ControlledExperimentError, match="ACTIVE_PROPOSAL_EXISTS"):
        service.prepare(**{**request, "client_request_key": "new-intent"})


def test_reject_and_blocked_preflight_never_create_experiment(tmp_path) -> None:
    service, version, protocol = _setup(tmp_path, ready=False)
    blocked = service.prepare(**_request(service, version, protocol))
    assert blocked["validation_status"] == "BLOCKED"
    app.dependency_overrides[get_service] = lambda: service
    try:
        response = TestClient(app).post(
            f"/experiment-operator/configurations/{blocked['config_id']}/approve-run",
            headers={"Origin": "http://127.0.0.1:5173"},
        )
        assert response.status_code == 422
        assert response.json()["detail"]["code"] == "EXPERIMENT_PREFLIGHT_BLOCKED"
    finally:
        app.dependency_overrides.clear()
    with pytest.raises(ControlledExperimentError, match="EXPERIMENT_PREFLIGHT_BLOCKED"):
        service.approve_and_run(blocked["config_id"])
    rejected = service.reject(blocked["config_id"])
    assert rejected["approval_status"] == "REJECTED"
    with pytest.raises(ControlledExperimentError, match="EXPERIMENT_CONFIG_NOT_PROPOSED"):
        service.approve_and_run(blocked["config_id"])
    assert service._experiments.get(blocked["experiment_id"]) is None


def test_approval_rejects_changed_data_snapshot_without_creating_experiment(tmp_path) -> None:
    service, version, protocol = _setup(tmp_path)
    plan = service.prepare(**_request(service, version, protocol))
    service._read.snapshot = "cache-snapshot-2"
    with pytest.raises(ControlledExperimentError, match="DATA_PREFLIGHT_CHANGED"):
        service.approve_and_run(plan["config_id"])
    assert service.get(plan["config_id"])["approval_status"] == "STALE"
    assert service._experiments.get(plan["experiment_id"]) is None


def test_exact_binding_unit_and_protocol_fail_closed(tmp_path) -> None:
    service, version, protocol = _setup(tmp_path)
    request = _request(service, version, protocol)
    for patch, code in (
        ({"strategy_version_hash": "0" * 64}, "STRATEGY_VERSION_HASH_MISMATCH"),
        ({"protocol_id": ""}, "FROZEN_PROTOCOL_REQUIRED"),
        ({"requested_is_end": "2020-12-30"}, "PROTOCOL_IS_RANGE_MISMATCH"),
        ({"axes": [{**request["axes"][0], "unit": "native"}]}, "INVALID_AXIS_UNIT"),
        ({"axes": [{**request["axes"][0], "semantic_target_id": "unknown"}]}, "UNSUPPORTED_TARGET"),
    ):
        with pytest.raises(ControlledExperimentError, match=code):
            service.prepare(**{**request, **patch, "client_request_key": code})
    assert service.list() == []


def test_browser_origin_required_and_reject_is_persisted(tmp_path) -> None:
    service, version, protocol = _setup(tmp_path)
    plan = service.prepare(**_request(service, version, protocol))
    app.dependency_overrides[get_service] = lambda: service
    client = TestClient(app)
    try:
        path = f"/experiment-operator/configurations/{plan['config_id']}"
        assert client.post(path + "/reject").status_code == 403
        assert (
            client.post(path + "/reject", headers={"Origin": "http://127.0.0.1:5173"}).status_code
            == 200
        )
        assert client.get(path).json()["approval_status"] == "REJECTED"
    finally:
        app.dependency_overrides.clear()


def _canonical_grid(path):
    experiments = ExperimentRepository(path)
    executions = CandidateExecutionRepository(path)
    results = ExperimentResultRepository(path)
    backtests = BacktestRepository(path)
    grid = GridSearchService(
        experiment_repository=experiments,
        strategy_repository=StrategyRepository(path),
        execution_repository=executions,
        result_repository=results,
        grid_repository=GridSearchRepository(path),
        execution_service=ExperimentExecutionService(
            experiment_repository=experiments,
            protocol_repository=ResearchProtocolRepository(path),
            strategy_repository=StrategyRepository(path),
            candidate_execution_repository=executions,
            data_service=TradingDataService(),
        ),
        finalization_service=ExperimentResultFinalizationService(
            backtest_repository=backtests,
            experiment_result_repository=results,
            candidate_execution_repository=executions,
            experiment_repository=experiments,
        ),
    )
    return grid


def test_human_approval_uses_canonical_grid_and_is_idempotent(tmp_path) -> None:
    service, version, protocol = _setup(tmp_path)
    plan = service.prepare(**_request(service, version, protocol))
    service._grid = _canonical_grid(service._path)
    results = ExperimentResultRepository(service._path)
    backtests = BacktestRepository(service._path)
    first = service.approve_and_run(plan["config_id"])
    second = service.approve_and_run(plan["config_id"])
    assert first["approval_status"] == second["approval_status"] == "APPROVED"
    assert first["execution_status"] == second["execution_status"] == "COMPLETED"
    assert first["experiment_id"] == second["experiment_id"]
    assert len(results.list(plan["experiment_id"])) == 4
    assert len(backtests.list_records()) == 4
    assert service.get_result(plan["config_id"])["summary"]["completed_count"] == 4
    assert (
        StrategyRepository(service._path).get(version.strategy_id, version.version_id).content_hash
        == version.content_hash
    )
    assert (
        ResearchProtocolRepository(service._path).get_protocol(protocol.protocol_id).status
        == ProtocolStatus.FROZEN
    )


def test_invalid_combinations_are_visible_and_block_the_entire_grid(tmp_path) -> None:
    service, version, protocol = _setup(tmp_path)
    request = _request(service, version, protocol)
    request["axes"] = [
        {"semantic_target_id": "state_recovery_QQQ_weight", "unit": "percent", "values": [50, 90]},
        {"semantic_target_id": "state_recovery_SGOV_weight", "unit": "percent", "values": [25, 50]},
    ]
    plan = service.prepare(**request)
    assert plan["candidate_count"] == 4
    assert plan["validation_status"] == "BLOCKED"
    assert len(plan["invalid_candidates"]) == 3
    assert all(item["reason"] == "MATERIALIZATION_INVALID" for item in plan["invalid_candidates"])
    with pytest.raises(ControlledExperimentError, match="EXPERIMENT_PREFLIGHT_BLOCKED"):
        service.approve_and_run(plan["config_id"])
    assert service._experiments.get(plan["experiment_id"]) is None


def test_nine_candidates_budget_and_concurrent_prepare(tmp_path) -> None:
    service, version, protocol = _setup(tmp_path)
    request = _request(service, version, protocol)
    request["axes"][0]["values"] = [2, 3, 4]
    request["axes"][1]["values"] = [10, 15, 20]
    with ThreadPoolExecutor(max_workers=2) as pool:
        ids = list(pool.map(lambda _: service.prepare(**request)["config_id"], range(2)))
    assert ids[0] == ids[1]
    assert service.get(ids[0])["candidate_count"] == 9
    request["client_request_key"] = "too-large"
    request["axes"][0]["values"] = [1, 2, 3, 4, 5]
    request["axes"][1]["values"] = [10, 15, 20, 25]
    with pytest.raises(ControlledExperimentError, match="CANDIDATE_BUDGET_EXCEEDED"):
        service.prepare(**request)


def test_transient_failure_recovers_same_canonical_experiment(tmp_path) -> None:
    service, version, protocol = _setup(tmp_path)
    plan = service.prepare(**_request(service, version, protocol))

    class FailOnce:
        def prepare(self, experiment_id, definition):
            raise RuntimeError("injected interruption before grid preparation")

    service._grid = FailOnce()
    with pytest.raises(ControlledExperimentError, match="GRID_EXECUTION_FAILED"):
        service.approve_and_run(plan["config_id"])
    assert service.get(plan["config_id"])["execution_status"] == "FAILED"
    assert service._experiments.get(plan["experiment_id"]) is not None
    assert len(service._experiments.list_for_read(protocol.protocol_id).items) == 1

    service._grid = _canonical_grid(service._path)
    recovered = service.approve_and_run(plan["config_id"])
    assert recovered["execution_status"] == "COMPLETED"
    assert len(service._experiments.list_for_read(protocol.protocol_id).items) == 1
    assert service.approve_and_run(plan["config_id"])["execution_status"] == "COMPLETED"


def test_cross_space_persistence_failure_resumes_same_approved_grid(tmp_path, monkeypatch) -> None:
    service, version, protocol = _setup(tmp_path)
    plan = service.prepare(**_request(service, version, protocol))
    experiment = Experiment.from_dict(plan["experiment_snapshot"])
    definition = GridSearchDefinition.from_dict(plan["grid_definition"])
    first_candidate = preflight_grid_search(
        experiment, definition, version
    ).candidate_set.candidates[0]
    historical_space = ParameterSpace(
        parameters=experiment.parameter_space.parameters,
        constraints=experiment.parameter_space.constraints,
        max_candidates=1,
    )
    historical = replace(
        experiment,
        experiment_id="historical-experiment",
        parameter_space=historical_space,
        parameter_set=ParameterSet(first_candidate.values, historical_space),
        provenance=replace(
            experiment.provenance, parameter_space_hash=historical_space.content_hash
        ),
    )
    service._experiments.create(historical)
    assert historical_space.content_hash != experiment.parameter_space_hash
    grid = _canonical_grid(service._path)
    original_insert = grid._experiments._insert_parameter_set
    interrupted = False

    def fail_cross_space_once(connection, parameter_set):
        nonlocal interrupted
        if not interrupted and parameter_set.content_hash == first_candidate.content_hash:
            interrupted = True
            raise ExperimentPersistenceError("injected cross-space persistence interruption")
        return original_insert(connection, parameter_set)

    monkeypatch.setattr(grid._experiments, "_insert_parameter_set", fail_cross_space_once)
    service._grid = grid
    with pytest.raises(ControlledExperimentError, match="GRID_EXECUTION_FAILED"):
        service.approve_and_run(plan["config_id"])
    failed = service.get(plan["config_id"])
    assert failed["approval_status"] == "APPROVED"
    assert failed["execution_status"] == "FAILED"
    assert failed["proposal_hash"] == plan["proposal_hash"]
    assert failed["experiment_id"] == plan["experiment_id"]
    assert service._experiments.get(plan["experiment_id"]).status.value == "space_frozen"
    assert GridSearchRepository(service._path).get_definition(plan["experiment_id"]) is None
    assert service._experiments.list_candidates(plan["experiment_id"]) == ()
    assert service._experiments.get(historical.experiment_id) == historical

    recovered_service = ControlledExperimentService(
        path=service._path, read=CacheReady(), grid=_canonical_grid(service._path)
    )
    with ThreadPoolExecutor(max_workers=2) as pool:
        responses = list(
            pool.map(lambda _: recovered_service.approve_and_run(plan["config_id"]), range(2))
        )
    assert {item["experiment_id"] for item in responses} == {plan["experiment_id"]}
    assert recovered_service.get(plan["config_id"])["execution_status"] == "COMPLETED"
    assert len(recovered_service._experiments.list_candidates(plan["experiment_id"])) == 4
    assert len(ExperimentResultRepository(service._path).list(plan["experiment_id"])) == 4
    assert len(BacktestRepository(service._path).list_records()) == 4
    assert len(recovered_service._experiments.list_for_read(protocol.protocol_id).items) == 2
    assert recovered_service._experiments.get(historical.experiment_id) == historical
    assert recovered_service.approve_and_run(plan["config_id"])["execution_status"] == "COMPLETED"
    assert len(ExperimentResultRepository(service._path).list(plan["experiment_id"])) == 4


def test_concurrent_browser_approvals_create_one_experiment(tmp_path) -> None:
    service, version, protocol = _setup(tmp_path)
    plan = service.prepare(**_request(service, version, protocol))
    service._grid = _canonical_grid(service._path)
    with ThreadPoolExecutor(max_workers=2) as pool:
        responses = list(pool.map(lambda _: service.approve_and_run(plan["config_id"]), range(2)))
    assert {item["experiment_id"] for item in responses} == {plan["experiment_id"]}
    assert service.get(plan["config_id"])["execution_status"] == "COMPLETED"
    assert len(ExperimentResultRepository(service._path).list(plan["experiment_id"])) == 4
    assert len(BacktestRepository(service._path).list_records()) == 4


def test_mcp_can_prepare_and_read_but_has_no_execution_tool(tmp_path) -> None:
    service, version, protocol = _setup(tmp_path)
    request = _request(service, version, protocol)

    async def exercise():
        async with Client(create_server(experiment_service=service)) as client:
            tools = (await client.list_tools()).tools
            names = {tool.name for tool in tools}
            assert "quant_prepare_experiment_configuration" in names
            assert "quant_get_experiment_configuration" in names
            assert "quant_get_experiment_result" in names
            assert not any("approve" in name or "run_grid" in name for name in names)
            prepared = await client.call_tool("quant_prepare_experiment_configuration", request)
            assert not prepared.is_error
            config_id = prepared.structured_content["config_id"]
            loaded = await client.call_tool(
                "quant_get_experiment_configuration", {"config_id": config_id}
            )
            assert loaded.structured_content["approval_status"] == "PROPOSED"
            assert service._experiments.get(prepared.structured_content["experiment_id"]) is None

    asyncio.run(exercise())
