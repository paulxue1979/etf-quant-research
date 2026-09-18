from __future__ import annotations

from datetime import date, timedelta

import pytest

from backend.app.backtest_lab import BacktestRequest
from backend.app.backtest_repository import BacktestRepository
from backend.app.backtest_service import BacktestService, BacktestServiceError
from backend.app.strategy_repository import StrategyRepository
from data.exceptions import TiingoNetworkError
from data.models import (
    DataSource,
    HistoricalDataRequest,
    HistoricalDataSet,
    MarketDataPoint,
    PriceField,
)
from strategies import StrategyDefinition


def _definition() -> StrategyDefinition:
    return StrategyDefinition.from_dict(
        {
            "strategy_id": "service-fixture",
            "name": "Service fixture",
            "description": "Backtest service test",
            "assets": [{"symbol": "QQQ"}],
            "price_field": "adjusted_close",
            "rules": [],
            "fallback": {"name": "cash", "allocations": [{"symbol": "QQQ", "target_weight": 1}]},
            "rebalance_policy": {"frequency": "daily", "threshold": None},
        }
    )


def _dataset(symbol: str = "QQQ") -> HistoricalDataSet:
    start = date(2026, 2, 2)
    points = tuple(
        MarketDataPoint(
            date=start + timedelta(days=index),
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
        for index in range(4)
    )
    return HistoricalDataSet(
        HistoricalDataRequest(
            symbol,
            points[0].date - timedelta(days=45),
            points[-1].date,
            price_field_used=PriceField.ADJUSTED_CLOSE,
        ),
        points,
        DataSource.CACHE,
    )


class FakeDataService:
    def __init__(self, dataset: HistoricalDataSet) -> None:
        self.dataset = dataset
        self.requests: list[HistoricalDataRequest] = []

    def get_history(self, request: HistoricalDataRequest) -> HistoricalDataSet:
        self.requests.append(request)
        return self.dataset


class SymbolDataService:
    def __init__(self, datasets: dict[str, HistoricalDataSet]) -> None:
        self.datasets = datasets
        self.requests: list[HistoricalDataRequest] = []

    def get_history(self, request: HistoricalDataRequest) -> HistoricalDataSet:
        self.requests.append(request)
        return self.datasets[request.symbol]


class FailingBenchmarkDataService(SymbolDataService):
    def get_history(self, request: HistoricalDataRequest) -> HistoricalDataSet:
        self.requests.append(request)
        if request.symbol == "SPY":
            raise TiingoNetworkError("external benchmark network unavailable")
        return self.datasets[request.symbol]


def _request(
    version_id: str,
    *,
    price_field: PriceField = PriceField.ADJUSTED_CLOSE,
    benchmark_symbol: str | None = None,
) -> BacktestRequest:
    return BacktestRequest(
        strategy_id="service-fixture",
        strategy_version_id=version_id,
        start_date=date(2026, 2, 2),
        end_date=date(2026, 2, 5),
        initial_capital=10_000.0,
        price_field_used=price_field,
        **({"benchmark_symbol": benchmark_symbol} if benchmark_symbol is not None else {}),
    )


def test_service_orchestrates_existing_chain_and_preserves_warmup_scope(tmp_path) -> None:
    strategies = StrategyRepository(tmp_path / "strategy.db")
    version = strategies.create(_definition())
    runs = BacktestRepository(tmp_path / "strategy.db")
    data_service = FakeDataService(_dataset())
    service = BacktestService(
        strategy_repository=strategies,
        backtest_repository=runs,
        data_service=data_service,  # type: ignore[arg-type]
    )

    first = service.run(_request(version.version_id))
    second = service.run(_request(version.version_id))

    assert first.backtest_run_id != second.backtest_run_id
    assert first.backtest_result.start_date == date(2026, 2, 2)
    assert first.backtest_result.end_date == date(2026, 2, 5)
    assert first.provenance["warmup_start_date"] < "2026-02-02"
    assert first.strategy_provenance is not None
    assert first.strategy_provenance["source"] == "StrategyBacktestResult.signal_records"
    assert first.strategy_provenance["records"]
    assert data_service.requests[0].start_date < date(2026, 2, 2)
    assert len(runs.list("service-fixture")) == 2


def test_service_rejects_price_field_mismatch_before_fetching_data(tmp_path) -> None:
    strategies = StrategyRepository(tmp_path / "strategy.db")
    version = strategies.create(_definition())
    data_service = FakeDataService(_dataset())
    service = BacktestService(
        strategy_repository=strategies,
        backtest_repository=BacktestRepository(tmp_path / "strategy.db"),
        data_service=data_service,  # type: ignore[arg-type]
    )

    with pytest.raises(BacktestServiceError, match="price field"):
        service.run(_request(version.version_id, price_field=PriceField.RAW_CLOSE))
    assert data_service.requests == []


def test_service_deterministic_results_match_except_run_identity(tmp_path) -> None:
    strategies = StrategyRepository(tmp_path / "strategy.db")
    version = strategies.create(_definition())
    service = BacktestService(
        strategy_repository=strategies,
        backtest_repository=BacktestRepository(tmp_path / "strategy.db"),
        data_service=FakeDataService(_dataset()),  # type: ignore[arg-type]
    )

    first = service.run(_request(version.version_id))
    second = service.run(_request(version.version_id))

    assert first.backtest_result == second.backtest_result
    first_analysis = first.performance_analysis.to_dict() | {
        "backtest_run_id": None,
        "strategy_id": None,
    }
    second_analysis = second.performance_analysis.to_dict() | {
        "backtest_run_id": None,
        "strategy_id": None,
    }
    assert first_analysis == second_analysis


def test_service_persists_explicit_benchmark_on_the_strategy_timeline(tmp_path) -> None:
    strategies = StrategyRepository(tmp_path / "strategy.db")
    version = strategies.create(_definition())
    data_service = SymbolDataService({"QQQ": _dataset(), "SPY": _dataset("SPY")})
    service = BacktestService(
        strategy_repository=strategies,
        backtest_repository=BacktestRepository(tmp_path / "strategy.db"),
        data_service=data_service,  # type: ignore[arg-type]
    )

    run = service.run(_request(version.version_id, benchmark_symbol="spy"))

    assert run.benchmark_evaluation is not None
    assert run.benchmark_evaluation["status"] == "available"
    assert run.benchmark_evaluation["benchmark_symbol"] == "SPY"
    assert run.benchmark_evaluation["provenance"]["common_timeline_enforced"] is True
    assert [request.symbol for request in data_service.requests] == ["QQQ", "SPY"]


def test_benchmark_data_failure_does_not_discard_the_strategy_run(tmp_path) -> None:
    strategies = StrategyRepository(tmp_path / "strategy.db")
    version = strategies.create(_definition())
    data_service = FailingBenchmarkDataService({"QQQ": _dataset()})
    runs = BacktestRepository(tmp_path / "strategy.db")
    service = BacktestService(
        strategy_repository=strategies,
        backtest_repository=runs,
        data_service=data_service,  # type: ignore[arg-type]
    )

    run = service.run(_request(version.version_id, benchmark_symbol="SPY"))

    assert run.backtest_result.final_equity > 0
    assert run.benchmark_evaluation["status"] == "not_evaluable"
    assert run.benchmark_evaluation["reason"] == (
        "benchmark market data unavailable: TiingoNetworkError"
    )
    assert runs.get(run.backtest_run_id) == run
