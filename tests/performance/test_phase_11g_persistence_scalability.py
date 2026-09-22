from __future__ import annotations

import json
import sqlite3
import time

from backend.app.backtest_models import METADATA_METRIC_NAMES
from backend.app.backtest_repository import BacktestRepository


def _seed_metadata_database(path, count: int) -> None:
    repository = BacktestRepository(path)
    connection = sqlite3.connect(path)
    try:
        rows = []
        projections = []
        for index in range(count):
            run_id = f"synthetic-run-{index:04d}"
            created_at = f"2026-01-{(index % 28) + 1:02d}T00:00:{index % 60:02d}+00:00"
            rows.append(
                (
                    run_id,
                    "synthetic-strategy",
                    "synthetic-v1",
                    created_at,
                    "h" * 64,
                    "2025-01-01",
                    "2025-12-31",
                    "adjusted_close",
                    "phase-3",
                    "phase-4i.0",
                    "{}",
                )
            )
            payload = {
                "configuration_snapshot": {},
                "data_snapshot_reference": {},
                "provenance": {},
                "metrics": {
                    name: (
                        {"value": float(index) / count, "status": "available", "reason": None}
                        if name == "cagr"
                        else {
                            "value": None,
                            "status": "not_evaluable",
                            "reason": "synthetic fixture",
                        }
                    )
                    for name in METADATA_METRIC_NAMES
                },
                "projection_status": "available",
                "projection_version": "phase-11g.1",
                "result_available": True,
            }
            projections.append(
                (
                    run_id,
                    "synthetic-strategy",
                    "synthetic-v1",
                    created_at,
                    "h" * 64,
                    "2025-01-01",
                    "2025-12-31",
                    "adjusted_close",
                    "phase-3",
                    "phase-4i.0",
                    100_000.0,
                    100_000.0 + index,
                    None,
                    None,
                    None,
                    "phase-11g.1",
                    json.dumps(payload, sort_keys=True, separators=(",", ":")),
                    float(index) / count,
                    *([None] * 13),
                )
            )
        connection.executemany(
            """
            INSERT INTO backtest_runs(
                backtest_run_id, strategy_id, strategy_version_id, created_at,
                strategy_version_content_hash, start_date, end_date, price_field_used,
                engine_version, analysis_version, run_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            rows,
        )
        connection.executemany(
            """
            INSERT INTO backtest_run_metadata(
                backtest_run_id, strategy_id, strategy_version_id, created_at,
                strategy_version_content_hash, start_date, end_date, price_field_used,
                engine_version, analysis_version, initial_capital, final_equity,
                experiment_id, candidate_id, candidate_index, metadata_projection_version,
                metadata_json, cagr_value, sharpe_ratio_value, sortino_ratio_value,
                max_drawdown_value, total_return_value, annualized_volatility_value,
                calmar_ratio_value, win_rate_value, profit_factor_value,
                average_trade_return_value, best_trade_value, worst_trade_value,
                average_holding_period_value, turnover_value
            ) VALUES (
                ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
            )
            """,
            projections,
        )
        connection.commit()
    finally:
        connection.close()
    assert repository.list_metadata(limit=1).total == count


def test_history_query_is_bounded_and_does_not_select_run_json(tmp_path, monkeypatch) -> None:
    path = tmp_path / "scale.db"
    _seed_metadata_database(path, 100)
    repository = BacktestRepository(path)
    statements: list[str] = []
    original_connect = repository._connect

    def traced_connect():
        connection = original_connect()
        connection.set_trace_callback(statements.append)
        return connection

    monkeypatch.setattr(repository, "_connect", traced_connect)
    page = repository.list_metadata(limit=25, offset=50)

    assert page.total == 100
    assert len(page.items) == 25
    selects = [
        statement.upper()
        for statement in statements
        if statement.lstrip().upper().startswith("SELECT")
    ]
    assert selects
    assert all("RUN_JSON" not in statement for statement in selects)


def test_history_scales_at_synthetic_100_500_1000_rows(tmp_path) -> None:
    durations: list[float] = []
    for count in (100, 500, 1000):
        path = tmp_path / f"scale-{count}.db"
        _seed_metadata_database(path, count)
        repository = BacktestRepository(path)
        started = time.perf_counter()
        page = repository.list_metadata(limit=50, offset=count // 2)
        durations.append(time.perf_counter() - started)
        assert page.total == count
        assert len(page.items) == 50
    assert all(duration < 1.0 for duration in durations)


def test_history_plan_uses_created_index(tmp_path) -> None:
    path = tmp_path / "plan.db"
    _seed_metadata_database(path, 100)
    with sqlite3.connect(path) as connection:
        plan = connection.execute(
            """
            EXPLAIN QUERY PLAN
            SELECT r.backtest_run_id, r.created_at
            FROM backtest_runs r
            LEFT JOIN backtest_run_metadata m ON m.backtest_run_id = r.backtest_run_id
            ORDER BY r.created_at DESC, r.backtest_run_id DESC
            LIMIT 50 OFFSET 0
            """
        ).fetchall()
    details = " ".join(str(row[-1]) for row in plan)
    assert "idx_backtest_runs_created" in details
