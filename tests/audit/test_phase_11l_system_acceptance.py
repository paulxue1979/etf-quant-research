from __future__ import annotations

from dataclasses import replace
from datetime import UTC, date, datetime, timedelta

from backend.app.backtest_repository import BacktestRepository
from backend.app.experiment_compatibility_service import ExperimentCompatibilityService
from backend.app.experiment_results_read_service import ExperimentResultsReadService
from backend.app.experiment_selection_handoff_service import ExperimentSelectionHandoffService
from backend.app.experiment_selection_repository import ExperimentSelectionRepository
from backend.app.oos_execution_repository import OosExecutionRepository
from backend.app.oos_research_view import OosResearchViewService
from backend.app.oos_result_repository import OosResultRepository
from backend.app.optimization_research_repository import OptimizationResearchRepository
from backend.app.optimization_research_service import OptimizationResearchService
from backend.app.research_protocol import ProtocolStatus, ResearchProtocolRepository
from backend.app.strategy_repository import StrategyRepository
from data.models import PriceField, Timeframe
from data.trading_calendar import default_trading_calendar
from indicators.preparation import prepare_strategy_inputs
from research import create_experiment_selection_decision
from research.experiment_selection import ExperimentSelectionMethod
from research.objective_evaluation import evaluate_candidate_objective
from research.oos import (
    OosDataProvenance,
    OosEvaluationConfig,
    OosEvaluationIdentity,
    OosEvaluationRange,
    OosEvaluationSpec,
)
from research.oos_execution_service import OosExecutionService
from research.oos_finalization_service import OosFinalizationService
from research.optimization_analysis import CandidateFilter
from strategies import (
    Allocation,
    AllocationSpecification,
    ComparisonOperator,
    Condition,
    Operand,
    OperandType,
    RegimeDefinition,
    RegimeTransitionDefinition,
    StrategyEvaluationMode,
    StrategyVersion,
)
from tests.unit.test_experiment_execution_service import NOW, _LocalDataService
from tests.unit.test_grid_search import _service_setup
from tests.unit.test_oos_domain import OOS_END
from tests.unit.test_oos_execution_service import FakeDataService
from tests.unit.test_oos_execution_service import _claimed as _claimed_oos
from tests.unit.test_oos_execution_service import _dataset as _oos_dataset
from tests.unit.test_oos_execution_service import _service as _oos_service
from tests.unit.test_strategy_repository import _definition as repository_definition


def test_grid_is_selection_freeze_and_official_oos_share_one_frozen_identity(tmp_path) -> None:
    (
        grid,
        experiments,
        executions,
        results,
        _,
        backtests,
        experiment,
        definition,
    ) = _service_setup(tmp_path)
    database = experiments.db_path
    strategies = StrategyRepository(database)
    protocols = ResearchProtocolRepository(database)

    preflight = grid.prepare(experiment.experiment_id, definition)
    progress = grid.execute(experiment.experiment_id)
    read_model = ExperimentResultsReadService(
        experiment_repository=experiments,
        candidate_execution_repository=executions,
        experiment_result_repository=results,
        backtest_repository=backtests,
        strategy_repository=strategies,
    ).get(experiment.experiment_id, protocol_id=experiment.protocol_id)
    optimization = OptimizationResearchService(
        experiments,
        OptimizationResearchRepository(database),
    ).results(
        experiment.protocol_id,
        experiment.experiment_id,
        CandidateFilter(),
        ("cagr", "max_drawdown", "turnover"),
    )

    # The researcher chooses a fixed candidate after reviewing IS-only evidence.
    selected_candidate = read_model.candidates[0]
    derived = strategies.get_any_version(selected_candidate.derived_strategy_version_id or "")
    assert derived is not None
    objective = evaluate_candidate_objective(experiment, selected_candidate)
    compatibility = ExperimentCompatibilityService().diagnose(read_model)
    protocols.transition_protocol(experiment.protocol_id, ProtocolStatus.IS_EVALUATED)
    experiment_selection = create_experiment_selection_decision(
        experiment=experiment,
        selected_candidate_id=selected_candidate.candidate_id,
        candidates=read_model,
        objective_evaluation=objective,
        compatibility=compatibility,
        derived_strategy_version=derived,
        selection_method=ExperimentSelectionMethod.RESEARCHER_JUDGMENT,
        researcher_rationale="Fixed candidate selected from frozen IS evidence for PHASE 11L.",
        created_at=NOW,
    )
    selections = ExperimentSelectionRepository(database)
    selections.create(experiment_selection)
    protocol_selection, freeze = ExperimentSelectionHandoffService(
        experiment_repository=experiments,
        selection_repository=selections,
        result_repository=results,
        execution_repository=executions,
        backtest_repository=backtests,
        strategy_repository=strategies,
        protocol_repository=protocols,
    ).handoff_experiment_selection(experiment.experiment_id)

    protocol = protocols.get_protocol(experiment.protocol_id)
    warmup_start = protocol.oos_start_date - timedelta(days=10)
    spec = OosEvaluationSpec(
        identity=OosEvaluationIdentity(
            protocol_id=protocol.protocol_id,
            selection_decision_id=protocol_selection.decision_id,
            strategy_freeze_id=freeze.freeze_id,
            strategy_version_id=derived.version_id,
            strategy_content_hash=derived.content_hash or "",
        ),
        evaluation_range=OosEvaluationRange.from_protocol(
            protocol,
            warmup_start=warmup_start,
        ),
        configuration=OosEvaluationConfig.from_research_evaluation_config(
            protocol.evaluation_config,
            analytics_version=experiment.analysis_version,
        ),
        data_provenance=OosDataProvenance(
            source="deterministic-local-fixture",
            data_reference={
                "symbols": [asset.symbol for asset in derived.configuration.assets],
                "frequency": "daily",
                "price_field": PriceField.ADJUSTED_CLOSE.value,
            },
            requested_start=protocol.oos_start_date,
            requested_end=protocol.oos_end_date,
            warmup_start=warmup_start,
            frequency="daily",
            price_field=PriceField.ADJUSTED_CLOSE,
        ),
    )
    oos_executions = OosExecutionRepository(database)
    pending = oos_executions.get_or_create_execution(spec, now=NOW)
    claimed = oos_executions.claim_execution(
        pending.execution_id,
        "phase-11l-acceptance",
        NOW,
        NOW + timedelta(minutes=5),
    )
    oos_outcome = OosExecutionService(
        protocol_repository=protocols,
        execution_repository=oos_executions,
        strategy_repository=strategies,
        data_service=_LocalDataService(),
        clock=lambda: NOW,
    ).execute(
        protocol_id=protocol.protocol_id,
        execution_id=claimed.execution_id,
        lease_token=claimed.lease_token or "",
        spec=spec,
    )
    official = OosFinalizationService(
        protocol_repository=protocols,
        execution_repository=oos_executions,
        result_repository=OosResultRepository(database),
        backtest_repository=BacktestRepository(database),
        strategy_repository=strategies,
        clock=lambda: NOW,
    ).finalize_oos_observation(
        protocol_id=protocol.protocol_id,
        execution_id=claimed.execution_id,
        lease_token=claimed.lease_token or "",
        outcome=oos_outcome,
    )
    view = OosResearchViewService(
        protocols=protocols,
        results=OosResultRepository(database),
        backtests=BacktestRepository(database),
        strategies=strategies,
    ).get(protocol.protocol_id)

    assert preflight["executable_count"] == progress["completed"] == 2
    assert optimization["is_only"] is True
    assert optimization["source_count"] == 2
    projected_metrics = {
        metric for item in optimization["candidates"] for metric in item["metrics"]
    }
    assert all("oos" not in metric for metric in projected_metrics)
    assert protocol_selection.selected_strategy_version_id == derived.version_id
    assert freeze.strategy_version_content_hash == derived.content_hash
    assert oos_outcome.strategy_version_id == freeze.strategy_version_id
    assert oos_outcome.oos_start == protocol.oos_start_date
    assert oos_outcome.oos_end == protocol.oos_end_date
    assert official.strategy_content_hash == freeze.strategy_version_content_hash
    assert view["read_only"] is True
    assert view["oos_result"]["oos_result_id"] == official.oos_result_id
    assert protocols.get_protocol(protocol.protocol_id).status == ProtocolStatus.OOS_EVALUATED


def test_midweek_oos_clips_future_friday_before_weekly_regime_evaluation(tmp_path) -> None:
    base = repository_definition("oos-strategy")
    weekly_condition = Condition(
        Operand(
            "QQQ",
            OperandType.PRICE,
            price_field=PriceField.ADJUSTED_CLOSE,
            timeframe=Timeframe.WEEKLY,
        ),
        ComparisonOperator.GREATER_OR_EQUAL,
        Operand("QQQ", OperandType.CONSTANT, value=0.0),
    )
    definition = replace(
        base,
        strategy_schema_version="2.0",
        strategy_mode=StrategyEvaluationMode.REGIME_STATE_MACHINE,
        initial_regime="defensive",
        regimes=(
            RegimeDefinition(
                "defensive",
                "Defensive",
                AllocationSpecification((Allocation("QQQ", 1.0),)),
            ),
            RegimeDefinition(
                "risk-on",
                "Risk On",
                AllocationSpecification((Allocation("TQQQ", 1.0),)),
            ),
        ),
        transitions=(
            RegimeTransitionDefinition(
                "weekly-risk-on",
                "defensive",
                "risk-on",
                weekly_condition,
                1,
            ),
        ),
    )
    version = StrategyVersion(
        strategy_id=definition.strategy_id,
        version_id="phase-11l-weekly-oos-v1",
        version_number=1,
        created_at=datetime(2026, 9, 23, tzinfo=UTC),
        configuration=definition,
    )
    version, protocols, executions, spec, claimed = _claimed_oos(tmp_path, version=version)
    future_friday = OOS_END + timedelta(days=3)
    data = {}
    for asset in version.configuration.assets:
        dataset = _oos_dataset(asset.symbol)
        sessions = set(
            default_trading_calendar().sessions(
                dataset.request.start_date,
                dataset.request.end_date,
            )
        )
        dataset = replace(
            dataset,
            points=tuple(point for point in dataset.points if point.date in sessions),
        )
        future_point = replace(dataset.points[-1], date=future_friday)
        data[asset.symbol] = replace(dataset, points=dataset.points + (future_point,))
    outcome = _oos_service(
        tmp_path,
        FakeDataService(data),
        execution_repository=executions,
        protocol_repository=protocols,
        version=version,
    ).execute(
        protocol_id=claimed.protocol_id,
        execution_id=claimed.execution_id,
        lease_token=claimed.lease_token or "",
        spec=spec,
    )

    clipped = {
        symbol: replace(
            dataset,
            points=tuple(point for point in dataset.points if point.date <= OOS_END),
        )
        for symbol, dataset in data.items()
    }
    prepared = prepare_strategy_inputs(
        version,
        clipped,
        price_field=PriceField.ADJUSTED_CLOSE,
        cutoff=OOS_END,
    )
    weekly_dates = tuple(point.available_on for point in prepared.weekly_market_data["QQQ"].points)

    assert weekly_dates[-1] == date(2026, 2, 6)
    assert future_friday not in weekly_dates
    assert outcome.backtest_result.end_date == OOS_END
    assert all(point.date <= OOS_END for point in outcome.backtest_result.equity_curve)
