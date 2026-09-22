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
    assert restored.contribution_provenance_available is True
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


def test_repository_marks_pre_contribution_artifacts_as_provenance_unavailable() -> None:
    run = _run()
    payload = run.to_dict()
    payload.pop("contribution_provenance_available")
    for key in (
        "contribution_events",
        "external_cash_flows",
        "cumulative_contributions",
        "total_capital_invested",
        "investment_profit",
    ):
        payload["backtest_result"].pop(key)

    restored = BacktestRun.from_dict(payload)

    assert restored.contribution_provenance_available is False


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


def test_metadata_history_is_paged_without_loading_full_artifacts(tmp_path) -> None:
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

    first_page = repository.list_metadata(limit=1, offset=0)
    page = repository.list_metadata(limit=1, offset=1)
    empty_page = repository.list_metadata(limit=1, offset=2)

    assert page.total == 2
    assert first_page.items[0].backtest_run_id == "repo-run-2"
    assert len(page.items) == 1
    assert page.items[0].backtest_run_id == "repo-run"
    assert empty_page.items == ()
    assert {first_page.items[0].backtest_run_id, page.items[0].backtest_run_id} == {
        "repo-run",
        "repo-run-2",
    }
    assert page.items[0].projection_status == "available"
    assert page.items[0].metrics["total_return"]["value"] is not None
    assert repository.get(first.backtest_run_id) == first
    with sqlite3.connect(repository.db_path) as connection:
        metadata_json = connection.execute(
            "SELECT metadata_json FROM backtest_run_metadata WHERE backtest_run_id = ?",
            (first.backtest_run_id,),
        ).fetchone()[0]
    assert '"projection_version":"phase-11g.1"' in metadata_json
    assert "equity_curve" not in metadata_json
    assert '"orders"' not in metadata_json
    assert '"fills"' not in metadata_json
    with pytest.raises(BacktestPersistenceError, match="invalid backtest metadata page"):
        repository.list_metadata(limit=101)


def test_legacy_metadata_is_explicitly_backfillable_and_deterministic(tmp_path) -> None:
    db_path = tmp_path / "backtests.db"
    repository = BacktestRepository(db_path)
    run = _run()
    repository.create(run)
    with sqlite3.connect(db_path) as connection:
        canonical_before = connection.execute(
            "SELECT run_json FROM backtest_runs WHERE backtest_run_id = ?",
            (run.backtest_run_id,),
        ).fetchone()[0]
        connection.execute(
            "DELETE FROM backtest_run_metadata WHERE backtest_run_id = ?",
            (run.backtest_run_id,),
        )

    missing = repository.list_metadata(limit=10)
    assert missing.items[0].projection_status == "missing"

    report = repository.backfill_metadata(limit=1)
    restored = repository.list_metadata(limit=10)
    assert report.scanned == 1
    assert report.inserted == 1
    assert restored.items[0].projection_status == "available"
    assert restored.items[0].final_equity == run.backtest_result.final_equity
    assert repository.backfill_metadata(limit=1).inserted == 0
    with sqlite3.connect(db_path) as connection:
        canonical_after = connection.execute(
            "SELECT run_json FROM backtest_runs WHERE backtest_run_id = ?",
            (run.backtest_run_id,),
        ).fetchone()[0]
    assert canonical_after == canonical_before


def test_metadata_list_ignores_corrupt_canonical_json_until_detail_is_requested(tmp_path) -> None:
    db_path = tmp_path / "backtests.db"
    repository = BacktestRepository(db_path)
    run = _run()
    repository.create(run)
    with sqlite3.connect(db_path) as connection:
        connection.execute(
            "UPDATE backtest_runs SET run_json = 'not-json' WHERE backtest_run_id = ?",
            (run.backtest_run_id,),
        )

    assert repository.list_metadata(limit=10).items[0].backtest_run_id == run.backtest_run_id
    with pytest.raises(BacktestPersistenceError, match="integrity checks"):
        repository.get(run.backtest_run_id)


def test_metadata_projection_failure_rolls_back_canonical_insert(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    db_path = tmp_path / "backtests.db"
    repository = BacktestRepository(db_path)

    def fail_projection(cls, metadata):
        raise ValueError("injected projection failure")

    monkeypatch.setattr(BacktestRepository, "_metadata_values", classmethod(fail_projection))
    with pytest.raises(BacktestPersistenceError, match="could not persist backtest run"):
        repository.create(_run())

    with sqlite3.connect(db_path) as connection:
        assert connection.execute("SELECT COUNT(*) FROM backtest_runs").fetchone()[0] == 0
        assert connection.execute("SELECT COUNT(*) FROM backtest_run_metadata").fetchone()[0] == 0


def test_corrupt_legacy_artifact_fails_backfill_without_partial_projection(tmp_path) -> None:
    db_path = tmp_path / "backtests.db"
    repository = BacktestRepository(db_path)
    run = _run()
    repository.create(run)
    with sqlite3.connect(db_path) as connection:
        connection.execute("DELETE FROM backtest_run_metadata")
        connection.execute(
            "UPDATE backtest_runs SET run_json = 'not-json' WHERE backtest_run_id = ?",
            (run.backtest_run_id,),
        )

    with pytest.raises(BacktestPersistenceError, match="metadata backfill failed for run repo-run"):
        repository.backfill_metadata(limit=10)
    with sqlite3.connect(db_path) as connection:
        assert connection.execute("SELECT COUNT(*) FROM backtest_run_metadata").fetchone()[0] == 0
