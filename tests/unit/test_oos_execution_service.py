from __future__ import annotations

from dataclasses import replace
from datetime import UTC, date, datetime, timedelta

import pytest

from backend.app.oos_execution_repository import OosExecutionRepository
from backend.app.research_protocol import (
    CandidateSet,
    ProtocolStatus,
    ResearchProtocolRepository,
)
from data.models import (
    DataSource,
    HistoricalDataRequest,
    HistoricalDataSet,
    MarketDataPoint,
    PriceField,
)
from research.exceptions import OosExecutionError
from research.oos import OosEvaluationRange, OosEvaluationSpec
from research.oos_execution import OosExecutionStatus
from research.oos_execution_service import OosExecutionService
from tests.unit.test_oos_domain import (
    IS_END,
    IS_START,
    OOS_END,
    OOS_START,
    WARMUP_START,
    _freeze,
    _identity,
    _oos_config,
    _protocol,
    _provenance,
    _selection,
    _version,
)
from tests.unit.test_research_protocol import _run_for_version

NOW = datetime(2026, 9, 13, 12, 0, tzinfo=UTC)


def _dataset(symbol: str, *, end: date = OOS_END) -> HistoricalDataSet:
    points = tuple(
        MarketDataPoint(
            date=WARMUP_START + timedelta(days=index),
            open=100.0 + index,
            high=101.0 + index,
            low=99.0 + index,
            close=100.0 + index,
            volume=100.0,
            adj_open=100.0 + index,
            adj_high=101.0 + index,
            adj_low=99.0 + index,
            adj_close=100.0 + index,
            adj_volume=100.0,
            div_cash=0.0,
            split_factor=1.0,
        )
        for index in range((end - WARMUP_START).days + 1)
    )
    return HistoricalDataSet(
        HistoricalDataRequest(
            symbol, WARMUP_START, end, price_field_used=PriceField.ADJUSTED_CLOSE
        ),
        points,
        DataSource.CACHE,
    )


class FakeDataService:
    def __init__(self, datasets: dict[str, HistoricalDataSet]) -> None:
        self.datasets = datasets
        self.requests: list[HistoricalDataRequest] = []

    def get_history(self, request: HistoricalDataRequest) -> HistoricalDataSet:
        self.requests.append(request)
        return self.datasets[request.symbol]


class FakeStrategyRepository:
    def __init__(self, version) -> None:
        self.version = version

    def get_any_version(self, version_id: str):
        return self.version if version_id == self.version.version_id else None


def _protocol_repository(tmp_path):
    repository = ResearchProtocolRepository(tmp_path / "research.sqlite3")
    protocol = _protocol(status=ProtocolStatus.DRAFT)
    repository.create_protocol(protocol)
    return repository


def _service(tmp_path, data_service, *, execution_repository, protocol_repository, version):
    return OosExecutionService(
        protocol_repository=protocol_repository,
        execution_repository=execution_repository,
        strategy_repository=FakeStrategyRepository(version),
        data_service=data_service,
        clock=lambda: NOW,
    )


def _claimed(tmp_path):
    version = _version()
    protocol_repository = _protocol_repository(tmp_path)
    candidate_set = CandidateSet(
        candidate_set_id="candidate-set-8f1",
        protocol_id="protocol-8f1",
        strategy_version_ids=(version.version_id,),
        created_at=NOW,
        strategy_version_content_hashes={version.version_id: version.content_hash or ""},
    )
    protocol_repository.create_candidate_set(candidate_set)
    locked_candidate_set = protocol_repository.lock_candidate_set(candidate_set.candidate_set_id)
    protocol_repository.transition_protocol("protocol-8f1", ProtocolStatus.FROZEN)
    protocol_repository.transition_protocol("protocol-8f1", ProtocolStatus.IS_EVALUATED)
    is_run = _run_for_version(
        version,
        run_id="is-run-8f1",
        start_date=IS_START,
        end_date=IS_END,
    )
    selection = protocol_repository.create_selection(
        _selection(version),
        candidate_set=locked_candidate_set,
        runs=(is_run,),
        version=version,
    )
    protocol_repository.transition_protocol("protocol-8f1", ProtocolStatus.SELECTION_RECORDED)
    protocol_repository.create_freeze(
        _freeze(version),
        decision=selection,
        version=version,
    )
    execution_repository = OosExecutionRepository(tmp_path / "research.sqlite3")
    spec = OosEvaluationSpec(
        identity=_identity(version),
        evaluation_range=replace(
            OosEvaluationRange.from_protocol(_protocol(), warmup_start=WARMUP_START),
        ),
        configuration=_oos_config(),
        data_provenance=_provenance(),
    )
    execution_repository.get_or_create_execution(spec, now=NOW)
    claimed = execution_repository.claim_execution(
        execution_repository.get_execution_by_protocol("protocol-8f1").execution_id,
        "worker-8f3",
        NOW,
        NOW + timedelta(minutes=5),
    )
    return version, protocol_repository, execution_repository, spec, claimed


def test_execution_uses_exact_warmup_and_oos_boundaries_and_returns_internal_outcome(tmp_path):
    version, protocols, executions, spec, claimed = _claimed(tmp_path)
    data = {asset.symbol: _dataset(asset.symbol) for asset in version.configuration.assets}
    service = _service(
        tmp_path,
        FakeDataService(data),
        execution_repository=executions,
        protocol_repository=protocols,
        version=version,
    )

    outcome = service.execute(
        protocol_id="protocol-8f1",
        execution_id=claimed.execution_id,
        lease_token=claimed.lease_token,
        spec=spec,
    )

    assert outcome.oos_start == OOS_START
    assert outcome.oos_end == OOS_END
    assert outcome.backtest_result.start_date == OOS_START
    assert outcome.backtest_result.end_date == OOS_END
    assert outcome.performance_analysis.start_date == OOS_START
    assert outcome.strategy_provenance is not None
    assert outcome.strategy_provenance["source"] == "StrategyBacktestResult.signal_records"
    assert outcome.strategy_provenance["records"]
    assert executions.get_execution(claimed.execution_id).status is OosExecutionStatus.RUNNING


def test_stale_or_wrong_lease_fails_before_market_data_request(tmp_path):
    version, protocols, executions, spec, claimed = _claimed(tmp_path)
    data_service = FakeDataService(
        {asset.symbol: _dataset(asset.symbol) for asset in version.configuration.assets}
    )
    service = _service(
        tmp_path,
        data_service,
        execution_repository=executions,
        protocol_repository=protocols,
        version=version,
    )

    with pytest.raises(OosExecutionError):
        service.execute(
            protocol_id="protocol-8f1",
            execution_id=claimed.execution_id,
            lease_token="wrong-token",
            spec=spec,
        )
    assert data_service.requests == []


def test_data_request_never_crosses_frozen_oos_end(tmp_path):
    version, protocols, executions, spec, claimed = _claimed(tmp_path)
    datasets = {asset.symbol: _dataset(asset.symbol) for asset in version.configuration.assets}
    data_service = FakeDataService(datasets)
    service = _service(
        tmp_path,
        data_service,
        execution_repository=executions,
        protocol_repository=protocols,
        version=version,
    )

    service.execute(
        protocol_id="protocol-8f1",
        execution_id=claimed.execution_id,
        lease_token=claimed.lease_token,
        spec=spec,
    )
    assert data_service.requests
    assert all(request.start_date == WARMUP_START for request in data_service.requests)
    assert all(request.end_date == OOS_END for request in data_service.requests)


def test_outcome_deterministic_hash_is_stable_for_same_inputs(tmp_path):
    version, protocols, executions, spec, claimed = _claimed(tmp_path)
    data = {asset.symbol: _dataset(asset.symbol) for asset in version.configuration.assets}
    service = _service(
        tmp_path,
        FakeDataService(data),
        execution_repository=executions,
        protocol_repository=protocols,
        version=version,
    )
    first = service.execute(
        protocol_id="protocol-8f1",
        execution_id=claimed.execution_id,
        lease_token=claimed.lease_token,
        spec=spec,
    )
    second = service.execute(
        protocol_id="protocol-8f1",
        execution_id=claimed.execution_id,
        lease_token=claimed.lease_token,
        spec=spec,
    )
    assert first.outcome_hash == second.outcome_hash
    assert "final_equity" not in repr(first)
