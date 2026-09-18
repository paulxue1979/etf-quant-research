from __future__ import annotations

import json
import sqlite3
from dataclasses import replace
from datetime import UTC, date, datetime

import pytest

from analytics import analyze_backtest
from backend.app.backtest_models import BacktestRun
from backend.app.backtest_repository import BacktestPersistenceError, BacktestRepository
from backtest import BacktestConfig, BacktestEngine, RebalancePolicy, TargetAllocation
from data.models import (
    DataSource,
    HistoricalDataRequest,
    HistoricalDataSet,
    MarketDataPoint,
    PriceField,
)


def _data() -> HistoricalDataSet:
    points = tuple(
        MarketDataPoint(
            date=date(2026, 1, index),
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
        for index in (2, 3, 4)
    )
    return HistoricalDataSet(
        HistoricalDataRequest("QQQ", points[0].date, points[-1].date),
        points,
        DataSource.API_FRESH,
    )


def _run() -> BacktestRun:
    data = _data()
    config = BacktestConfig(
        strategy_version_id="repo-v1",
        start_date=data.points[0].date,
        end_date=data.points[-1].date,
        initial_capital=10_000.0,
        price_field_used=PriceField.ADJUSTED_CLOSE,
        rebalance_policy=RebalancePolicy(),
    )
    result = BacktestEngine().run(
        {"QQQ": data},
        (TargetAllocation.from_weights(data.points[0].date, {"QQQ": 1.0}),),
        config,
    )
    analysis = analyze_backtest(result, backtest_run_id="repo-run", strategy_id="repo")
    return BacktestRun(
        backtest_run_id="repo-run",
        strategy_id="repo",
        strategy_version_id="repo-v1",
        created_at=datetime(2026, 1, 5, tzinfo=UTC),
        strategy_version_content_hash="hash-repo-v1",
        backtest_result=result,
        performance_analysis=analysis,
        provenance={"source": "unit-test"},
    )


def test_repository_round_trip_is_immutable_and_cross_instance(tmp_path) -> None:
    repository = BacktestRepository(tmp_path / "backtests.db")
    run = _run()

    assert repository.create(run) == run
    restored = BacktestRepository(tmp_path / "backtests.db").get(run.backtest_run_id)

    assert restored == run
    assert restored is not run
    assert repository.list() == (run,)
    assert repository.list("repo") == (run,)


def test_repository_round_trips_strategy_execution_provenance_and_loads_legacy_runs(
    tmp_path,
) -> None:
    repository = BacktestRepository(tmp_path / "backtests.db")
    run = _run()
    provenance_run = BacktestRun(
        **{
            **run.__dict__,
            "strategy_provenance": {
                "source": "StrategyBacktestResult.signal_records",
                "records": (
                    {
                        "signal_date": "2026-01-02",
                        "matched_rule_id": "rule-1",
                        "allocation_source": "rule_match",
                        "target_allocation": {"QQQ": 1.0},
                        "execution_date": "2026-01-03",
                        "execution_status": "submitted",
                        "omission_reason": None,
                    },
                ),
            },
        }
    )

    repository.create(provenance_run)
    restored = BacktestRepository(tmp_path / "backtests.db").get(provenance_run.backtest_run_id)
    legacy_payload = run.to_dict()
    legacy_payload.pop("strategy_provenance")
    legacy = BacktestRun.from_dict(legacy_payload)

    assert restored is not None
    assert restored.strategy_provenance == provenance_run.strategy_provenance
    assert restored.strategy_provenance is not provenance_run.strategy_provenance
    assert legacy.strategy_provenance is None


def test_repository_round_trips_holding_segments_and_marks_legacy_result_unavailable(
    tmp_path,
) -> None:
    repository = BacktestRepository(tmp_path / "backtests.db")
    run = _run()

    repository.create(run)
    restored = repository.get(run.backtest_run_id)
    legacy_payload = run.to_dict()
    legacy_payload["backtest_result"].pop("holding_segments")
    legacy = BacktestRun.from_dict(legacy_payload)

    assert restored is not None
    assert restored.backtest_result.holding_segments == run.backtest_result.holding_segments
    assert restored.backtest_result.holding_segments is not run.backtest_result.holding_segments
    assert legacy.backtest_result.holding_segments is None


def test_repository_keeps_distinct_runs_even_for_identical_inputs(tmp_path) -> None:
    repository = BacktestRepository(tmp_path / "backtests.db")
    first = _run()
    analysis = replace(first.performance_analysis, backtest_run_id="repo-run-2")
    second = BacktestRun(
        **{**first.__dict__, "backtest_run_id": "repo-run-2", "performance_analysis": analysis},
    )
    repository.create(first)
    repository.create(second)

    assert [item.backtest_run_id for item in repository.list()] == ["repo-run-2", "repo-run"]


def test_repository_loads_requested_records_without_mutating_runs(tmp_path) -> None:
    repository = BacktestRepository(tmp_path / "backtests.db")
    first = _run()
    second_analysis = replace(first.performance_analysis, backtest_run_id="repo-run-2")
    second = BacktestRun(
        **{
            **first.__dict__,
            "backtest_run_id": "repo-run-2",
            "performance_analysis": second_analysis,
        },
    )
    repository.create(first)
    repository.create(second)

    records = repository.get_records(("repo-run-2", "repo-run", "missing"))

    assert [record.run.backtest_run_id for record in records] == ["repo-run-2", "repo-run"]
    assert all(record.analysis_version == "phase-4i.0" for record in records)
    assert [item.backtest_run_id for item in repository.list()] == ["repo-run-2", "repo-run"]


@pytest.mark.parametrize("stored_json", ["not-json", json.dumps({"backtest_run_id": "broken"})])
def test_corrupt_persisted_run_fails_with_safe_integrity_error(tmp_path, stored_json: str) -> None:
    db_path = tmp_path / "backtests.db"
    repository = BacktestRepository(db_path)
    run = _run()
    repository.create(run)

    with sqlite3.connect(db_path) as connection:
        connection.execute(
            "UPDATE backtest_runs SET run_json = ? WHERE backtest_run_id = ?",
            (stored_json, run.backtest_run_id),
        )

    with pytest.raises(BacktestPersistenceError, match="integrity checks"):
        repository.get(run.backtest_run_id)


def test_clear_removes_only_persisted_runs(tmp_path) -> None:
    repository = BacktestRepository(tmp_path / "backtests.db")
    repository.create(_run())
    repository.clear()
    assert repository.list() == ()
