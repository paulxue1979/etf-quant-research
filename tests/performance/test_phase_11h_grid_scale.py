from __future__ import annotations

from time import perf_counter

import pytest

from backend.app.experiment_repository import ExperimentRepository
from backend.app.grid_search_repository import GridSearchRepository
from backend.app.research_protocol import CandidateSet, ProtocolStatus, ResearchProtocolRepository
from backend.app.strategy_repository import StrategyRepository
from research import (
    BacktestBindingValueType,
    BacktestParameterBinding,
    GridSearchDefinition,
    ParameterDefinition,
    ParameterSpace,
    ParameterType,
    preflight_grid_search,
)
from tests.unit.test_experiment_execution_service import NOW, _definition, _experiment, _protocol


def _scale_case(tmp_path, count: int):
    database = tmp_path / f"grid-{count}.db"
    version = StrategyRepository(database).create(_definition())
    space = ParameterSpace(
        parameters=(
            ParameterDefinition("turnover_budget", ParameterType.INTEGER, min=1, max=count, step=1),
        ),
        max_candidates=count,
    )
    base = _experiment(version)
    from dataclasses import replace

    experiment = replace(
        base,
        parameter_space=space,
        provenance=replace(base.provenance, parameter_space_hash=space.content_hash),
    )
    definition = GridSearchDefinition(
        experiment_id=experiment.experiment_id,
        experiment_hash=experiment.content_hash,
        backtest_bindings=(
            BacktestParameterBinding(
                "turnover_budget",
                "position_rebalance_policy.maximum_turnover",
                BacktestBindingValueType.OPTIONAL_NON_NEGATIVE_FLOAT,
            ),
        ),
        fixed_parameters={"signal_asset": "QQQ", "execution_assets": ["QQQ", "TQQQ"]},
        max_candidates=count,
    )
    return database, version, experiment, definition


@pytest.mark.parametrize("count", [100, 500, 1000])
def test_grid_preflight_scales_linearly_without_backtests(tmp_path, count: int) -> None:
    _, version, experiment, definition = _scale_case(tmp_path, count)

    started = perf_counter()
    result = preflight_grid_search(experiment, definition, version)
    elapsed = perf_counter() - started

    assert result.can_execute
    assert result.theoretical_count == result.executable_count == count
    assert result.pruned_count == result.duplicate_count == 0
    assert elapsed < 15.0


def test_thousand_candidate_plan_persistence_and_summary_are_bounded(tmp_path) -> None:
    database, version, experiment, definition = _scale_case(tmp_path, 1000)
    protocols = ResearchProtocolRepository(database)
    protocol = _protocol()
    protocols.create_protocol(protocol)
    phase7_candidates = CandidateSet(
        candidate_set_id="phase-11h-scale-candidates",
        protocol_id=protocol.protocol_id,
        strategy_version_ids=(version.version_id,),
        strategy_version_content_hashes={version.version_id: version.content_hash or ""},
        created_at=NOW,
    )
    protocols.create_candidate_set(phase7_candidates)
    protocols.lock_candidate_set(phase7_candidates.candidate_set_id)
    protocols.transition_protocol(protocol.protocol_id, ProtocolStatus.FROZEN)
    experiments = ExperimentRepository(database)
    experiments.create(experiment)
    preflight = preflight_grid_search(experiment, definition, version)

    persistence_started = perf_counter()
    experiments.attach_candidate_set(
        experiment.experiment_id,
        preflight.candidate_set,
        transition_key=f"scale:{definition.definition_hash}",
    )
    grids = GridSearchRepository(database)
    grids.save_preflight(preflight)
    persistence_elapsed = perf_counter() - persistence_started
    summary_started = perf_counter()
    progress = grids.progress(experiment.experiment_id)
    summary_elapsed = perf_counter() - summary_started

    assert progress["executable"] == progress["pending"] == 1000
    assert persistence_elapsed < 15.0
    assert summary_elapsed < 1.0
