from __future__ import annotations

import sqlite3
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import timedelta

import pytest

from backend.app.backtest_repository import BacktestRepository
from backend.app.oos_research_view import OosResearchViewService
from backend.app.oos_result_repository import OosResultRepository
from backtest import BacktestConfig, RebalanceFrequency, RebalancePolicy
from backtest.integration import run_strategy_backtest
from data.models import PriceField
from indicators import moving_average
from research.exceptions import OosAlreadyObservedError
from research.oos import OosExecutionStatus
from strategies import AssetReference, evaluate_strategy
from strategies.evaluation import EvaluationContext
from tests.integration.test_strategy_backtest_chain import START, _conditional_version
from tests.integration.test_strategy_backtest_chain import _dataset as chain_dataset
from tests.unit.test_oos_execution_service import (
    FakeDataService,
    FakeStrategyRepository,
    _claimed,
    _dataset,
    _service,
)
from tests.unit.test_oos_finalization_service import _prepared


def _table_snapshot(database):
    tables = (
        "research_protocols",
        "research_protocol_events",
        "research_selection_decisions",
        "research_strategy_freezes",
        "research_oos_executions",
        "research_oos_execution_events",
        "research_oos_results",
        "research_oos_evaluations",
        "backtest_runs",
    )
    with sqlite3.connect(database) as connection:
        return {
            table: tuple(connection.execute(f"SELECT * FROM {table} ORDER BY rowid"))
            for table in tables
        }


def test_oos_execution_clips_future_rows_before_any_calculation(tmp_path):
    version, protocols, executions, spec, claimed = _claimed(tmp_path)
    datasets = {}
    for asset in version.configuration.assets:
        dataset = _dataset(asset.symbol)
        future_point = replace(
            dataset.points[-1], date=dataset.request.end_date + timedelta(days=1)
        )
        datasets[asset.symbol] = replace(dataset, points=dataset.points + (future_point,))

    service = _service(
        tmp_path,
        FakeDataService(datasets),
        execution_repository=executions,
        protocol_repository=protocols,
        version=version,
    )

    outcome = service.execute(
        protocol_id=claimed.protocol_id,
        execution_id=claimed.execution_id,
        lease_token=claimed.lease_token or "",
        spec=spec,
    )

    assert outcome.oos_end == spec.evaluation_range.oos_end
    assert outcome.backtest_result.end_date == spec.evaluation_range.oos_end
    assert all(
        point.date <= spec.evaluation_range.oos_end
        for point in outcome.backtest_result.equity_curve
    )


def test_concurrent_finalization_publishes_one_official_result(tmp_path):
    finalizer, outcome, protocols, executions = _prepared(tmp_path)
    token = executions.get_execution(outcome.execution_id).lease_token or ""

    def finalize():
        try:
            return finalizer.finalize_oos_observation(
                protocol_id=outcome.protocol_id,
                execution_id=outcome.execution_id,
                lease_token=token,
                outcome=outcome,
            )
        except OosAlreadyObservedError:
            return None

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: finalize(), range(2)))

    published = [result for result in results if result is not None]
    assert published
    assert all(result == published[0] for result in published)
    stored = OosResultRepository(finalizer._results.db_path).get_by_protocol(outcome.protocol_id)
    assert stored == published[0]
    assert len(BacktestRepository(finalizer._backtests.db_path).list()) == 1
    assert len(protocols.list_oos_evaluations(outcome.protocol_id)) == 1
    assert executions.get_execution(outcome.execution_id).status is OosExecutionStatus.COMPLETED


def test_official_research_view_is_read_only_and_preserves_selection_chain(tmp_path):
    finalizer, outcome, protocols, executions = _prepared(tmp_path)
    token = executions.get_execution(outcome.execution_id).lease_token or ""
    result = finalizer.finalize_oos_observation(
        protocol_id=outcome.protocol_id,
        execution_id=outcome.execution_id,
        lease_token=token,
        outcome=outcome,
    )
    selection_before = protocols.list_selections(outcome.protocol_id)
    freeze_before = protocols.list_freezes(outcome.protocol_id)
    database_before = _table_snapshot(finalizer._results.db_path)
    view = OosResearchViewService(
        protocols=protocols,
        results=OosResultRepository(finalizer._results.db_path),
        backtests=BacktestRepository(finalizer._backtests.db_path),
        strategies=FakeStrategyRepository(
            finalizer._strategies.get_any_version(result.strategy_version_id)
        ),
    )

    payload = view.get(outcome.protocol_id)

    assert payload["read_only"] is True
    assert protocols.list_selections(outcome.protocol_id) == selection_before
    assert protocols.list_freezes(outcome.protocol_id) == freeze_before
    assert _table_snapshot(finalizer._results.db_path) == database_before


def test_finalized_oos_uses_fresh_capital_and_never_backselects(tmp_path):
    finalizer, outcome, protocols, executions = _prepared(tmp_path)
    token = executions.get_execution(outcome.execution_id).lease_token or ""
    result = finalizer.finalize_oos_observation(
        protocol_id=outcome.protocol_id,
        execution_id=outcome.execution_id,
        lease_token=token,
        outcome=outcome,
    )
    run = BacktestRepository(finalizer._backtests.db_path).get(result.backtest_run_id)
    assert run is not None
    first_equity = min(run.backtest_result.equity_curve, key=lambda point: point.date)
    assert first_equity.cash == pytest.approx(run.backtest_result.initial_capital)
    assert protocols.list_selections(outcome.protocol_id)[0].selected_strategy_version_id == (
        result.strategy_version_id
    )
    assert protocols.get_protocol(outcome.protocol_id).status == "oos_evaluated"


def test_five_asset_timeline_uses_only_common_dates_without_filling(tmp_path):
    base = _conditional_version()
    definition = replace(
        base.configuration,
        assets=tuple(AssetReference(symbol) for symbol in ("QQQ", "TQQQ", "SPY", "IWM", "SGOV")),
    )
    version = replace(base, configuration=definition, content_hash=None)
    prices = {asset.symbol: (100.0, 100.0, 120.0, 120.0, 120.0) for asset in definition.assets}
    data = {
        asset.symbol: chain_dataset(asset.symbol, prices[asset.symbol])
        for asset in definition.assets
    }
    data["TQQQ"] = replace(data["TQQQ"], points=data["TQQQ"].points[:1] + data["TQQQ"].points[2:])
    data["IWM"] = replace(data["IWM"], points=data["IWM"].points[:2] + data["IWM"].points[3:])
    context = EvaluationContext.from_components(
        data,
        (("QQQ", moving_average(data["QQQ"], period=2, price_field=PriceField.RAW_CLOSE)),),
    )

    timeline = evaluate_strategy(version, context, START, START + timedelta(days=4))
    common_dates = set.intersection(
        *({point.date for point in dataset.points} for dataset in data.values())
    )
    backtest_data = {
        symbol: replace(
            dataset,
            points=tuple(point for point in dataset.points if point.date in common_dates),
        )
        for symbol, dataset in data.items()
    }
    config = BacktestConfig(
        strategy_version_id=version.version_id,
        start_date=START,
        end_date=START + timedelta(days=4),
        initial_capital=10_000.0,
        price_field_used=PriceField.RAW_CLOSE,
        rebalance_policy=RebalancePolicy(RebalanceFrequency.DAILY),
    )
    result = run_strategy_backtest(version, timeline, backtest_data, config)

    assert common_dates == {START, START + timedelta(days=3), START + timedelta(days=4)}
    assert {item.date for item in timeline.evaluations} == common_dates
    assert not any(
        item.date in {START + timedelta(days=1), START + timedelta(days=2)}
        for item in timeline.evaluations
    )
    assert result.backtest_result.strategy_version_id == version.version_id
    assert {asset.symbol for asset in definition.assets} == {"QQQ", "TQQQ", "SPY", "IWM", "SGOV"}
