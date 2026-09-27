from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

import pytest

from backend.app.backtest_repository import BacktestRepository
from backend.app.candidate_execution_repository import CandidateExecutionRepository
from backend.app.experiment_compatibility_service import ExperimentCompatibilityService
from backend.app.experiment_repository import ExperimentRepository
from backend.app.experiment_result_repository import ExperimentResultRepository
from backend.app.experiment_results_read_service import ExperimentResultsReadService
from backend.app.experiment_selection_handoff_service import (
    ExperimentSelectionHandoffService,
)
from backend.app.experiment_selection_repository import ExperimentSelectionRepository
from backend.app.grid_search_repository import GridSearchRepository
from backend.app.oos_execution_repository import OosExecutionRepository
from backend.app.oos_research_view import OosResearchViewService
from backend.app.oos_result_repository import OosResultRepository
from backend.app.research_protocol import (
    CandidateSet,
    ProtocolStatus,
    ResearchEvaluationConfig,
    ResearchProtocol,
    ResearchProtocolRepository,
)
from backend.app.strategy_repository import StrategyRepository
from backtest import BacktestConfig, RebalanceFrequency, RebalancePolicy
from data.models import PriceField
from research import (
    BindingValueType,
    Experiment,
    ExperimentMethod,
    ExperimentProvenance,
    ExperimentStatus,
    MetricDirection,
    ObjectiveSpecification,
    ParameterDefinition,
    ParameterSpace,
    ParameterType,
)
from research.execution_service import ExperimentExecutionService
from research.experiment_selection import (
    ExperimentSelectionMethod,
    create_experiment_selection_decision,
)
from research.grid_search import GridSearchDefinition
from research.grid_search_service import GridSearchService
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
from research.result_finalization_service import ExperimentResultFinalizationService
from research.semantic_bindings import (
    SemanticParameterBinding,
    SemanticTargetDescriptor,
    SemanticTargetKind,
)
from strategies import (
    Allocation,
    AllocationSpecification,
    AssetRole,
    ComparisonOperator,
    Condition,
    FallbackAllocation,
    Operand,
    OperandType,
    RegimeDefinition,
    RegimeTransitionDefinition,
    StrategyAssetReference,
    StrategyDefinition,
    StrategyEvaluationMode,
    Threshold,
    ThresholdType,
)
from strategies import (
    RebalanceFrequency as StrategyRebalanceFrequency,
)
from strategies import (
    RebalancePolicy as StrategyRebalancePolicy,
)
from tests.unit.test_experiment_execution_service import _LocalDataService

CREATED_AT = datetime(2026, 9, 20, 12, tzinfo=UTC)
IS_START = date(2020, 1, 13)
IS_END = date(2020, 1, 31)
OOS_START = date(2020, 2, 3)
OOS_END = date(2020, 2, 21)
PRICE_FIELD = PriceField.ADJUSTED_CLOSE


def _condition(operator: ComparisonOperator, threshold: float) -> Condition:
    return Condition(
        Operand("QQQ", OperandType.PRICE, price_field=PRICE_FIELD),
        operator,
        Operand("QQQ", OperandType.MA, period=2, price_field=PRICE_FIELD),
        Threshold(ThresholdType.RELATIVE, threshold),
    )


def _regime_definition() -> StrategyDefinition:
    return StrategyDefinition(
        strategy_id="regime-grid-e2e",
        name="Regime Grid E2E",
        description="Deterministic semantic regime grid integration fixture.",
        assets=(
            StrategyAssetReference("QQQ", AssetRole.BOTH),
            StrategyAssetReference("TQQQ", AssetRole.EXECUTION_ASSET),
        ),
        price_field=PRICE_FIELD,
        rules=(),
        fallback=FallbackAllocation((Allocation("QQQ", 1.0),)),
        rebalance_policy=StrategyRebalancePolicy(StrategyRebalanceFrequency.DAILY),
        strategy_schema_version="2.0",
        strategy_mode=StrategyEvaluationMode.REGIME_STATE_MACHINE,
        initial_regime="RECOVERY_HOLD",
        regimes=(
            RegimeDefinition(
                "PROTECTIVE",
                "Protective",
                AllocationSpecification((Allocation("QQQ", 1.0),)),
            ),
            RegimeDefinition(
                "RECOVERY_HOLD",
                "Recovery Hold",
                AllocationSpecification((Allocation("QQQ", 0.5),)),
            ),
            RegimeDefinition(
                "FULL_RISK_ON",
                "Full Risk On",
                AllocationSpecification((Allocation("TQQQ", 1.0),)),
            ),
        ),
        transitions=(
            RegimeTransitionDefinition(
                "recovery-to-full-risk-on",
                "RECOVERY_HOLD",
                "FULL_RISK_ON",
                _condition(ComparisonOperator.GREATER_THAN, 0.01),
                1,
            ),
            RegimeTransitionDefinition(
                "full-risk-on-to-protective",
                "FULL_RISK_ON",
                "PROTECTIVE",
                _condition(ComparisonOperator.LESS_THAN, -0.02),
                1,
            ),
        ),
    )


def _evaluation_config() -> ResearchEvaluationConfig:
    return ResearchEvaluationConfig(
        price_field_used=PRICE_FIELD,
        initial_capital=10_000.0,
        commission={"rate": 0.0, "per_order": 0.0},
        slippage=0.0,
        execution_rule="next_trading_day_open",
        fractional_shares=False,
        rebalance_policy={"frequency": "daily", "threshold": None},
        engine_version="phase-3.0",
    )


def _protocol() -> ResearchProtocol:
    return ResearchProtocol(
        protocol_id="protocol-regime-grid-e2e",
        protocol_version=1,
        created_at=CREATED_AT,
        is_start_date=IS_START,
        is_end_date=IS_END,
        oos_start_date=OOS_START,
        oos_end_date=OOS_END,
        selection_rules=("researcher selects one frozen IS candidate",),
        allowed_metrics=("cagr", "max_drawdown"),
        forbidden_actions=("oos_back_selection", "oos_parameter_tuning"),
        evaluation_config=_evaluation_config(),
    )


def _experiment(version, protocol: ResearchProtocol) -> Experiment:
    space = ParameterSpace(
        parameters=(
            ParameterDefinition(
                "risk_on_threshold",
                ParameterType.DISCRETE,
                allowed_values=(0.01, 0.03),
            ),
            ParameterDefinition(
                "protective_threshold",
                ParameterType.DISCRETE,
                allowed_values=(-0.04, -0.02),
            ),
        ),
        max_candidates=4,
    )
    objective = ObjectiveSpecification(
        primary_metric="cagr", metric_directions={"cagr": MetricDirection.MAXIMIZE}
    )
    config = BacktestConfig(
        strategy_version_id=version.version_id,
        start_date=IS_START,
        end_date=IS_END,
        initial_capital=10_000.0,
        price_field_used=PRICE_FIELD,
        rebalance_policy=RebalancePolicy(RebalanceFrequency.DAILY),
    )
    return Experiment(
        experiment_id="experiment-regime-grid-e2e",
        protocol_id=protocol.protocol_id,
        strategy_definition_id=version.strategy_id,
        base_strategy_version_id=version.version_id,
        base_strategy_version_hash=version.content_hash or "",
        parameter_space=space,
        objective_specification=objective,
        is_start_date=IS_START,
        is_end_date=IS_END,
        backtest_configuration=config,
        engine_version="phase-3.0",
        analysis_version="phase-4i.0",
        status=ExperimentStatus.SPACE_FROZEN,
        created_at=CREATED_AT,
        method=ExperimentMethod.GRID,
        provenance=ExperimentProvenance(
            protocol_id=protocol.protocol_id,
            strategy_definition_id=version.strategy_id,
            base_strategy_version_id=version.version_id,
            base_strategy_version_hash=version.content_hash or "",
            parameter_space_hash=space.content_hash,
            objective_spec_hash=objective.content_hash,
            is_start_date=IS_START,
            is_end_date=IS_END,
            price_field_used=PRICE_FIELD,
            initial_capital=10_000.0,
            commission={"rate": 0.0, "per_order": 0.0},
            slippage=0.0,
            execution_rule="next_trading_day_open",
            fractional_shares=False,
            rebalance_policy={"frequency": "daily", "threshold": None},
            engine_version="phase-3.0",
            analysis_version="phase-4i.0",
            data_snapshot_reference={"source": "local-deterministic", "frequency": "daily"},
        ),
    )


def _semantic_binding(name: str, version, transition_id: str) -> SemanticParameterBinding:
    return SemanticParameterBinding(
        name,
        BindingValueType.RELATIVE_THRESHOLD,
        "phase-13e-1-v1",
        SemanticTargetDescriptor(
            SemanticTargetKind.TRANSITION_THRESHOLD,
            version.version_id,
            version.content_hash or "",
            transition_id=transition_id,
        ),
    )


def _oos_spec(protocol, selection, freeze, version) -> OosEvaluationSpec:
    identity = OosEvaluationIdentity(
        protocol_id=protocol.protocol_id,
        selection_decision_id=selection.decision_id,
        strategy_freeze_id=freeze.freeze_id,
        strategy_version_id=version.version_id,
        strategy_content_hash=version.content_hash or "",
    )
    warmup_start = OOS_START - timedelta(days=30)
    return OosEvaluationSpec(
        identity=identity,
        evaluation_range=OosEvaluationRange.from_protocol(protocol, warmup_start=warmup_start),
        configuration=OosEvaluationConfig.from_research_evaluation_config(
            protocol.evaluation_config, analytics_version="phase-4i.0"
        ),
        data_provenance=OosDataProvenance(
            source="local-deterministic",
            data_reference={"symbols": ["QQQ", "TQQQ"], "frequency": "daily"},
            requested_start=OOS_START,
            requested_end=OOS_END,
            warmup_start=warmup_start,
            frequency="daily",
            price_field=PRICE_FIELD,
        ),
    )


@pytest.mark.integration
def test_regime_semantic_grid_selection_freeze_oos_e2e(tmp_path) -> None:
    database = tmp_path / "regime-grid-e2e.db"
    protocols = ResearchProtocolRepository(database)
    strategies = StrategyRepository(database)
    experiments = ExperimentRepository(database)
    executions = CandidateExecutionRepository(database)
    results = ExperimentResultRepository(database)
    backtests = BacktestRepository(database)
    grids = GridSearchRepository(database)
    protocol = protocols.create_protocol(_protocol())
    base = strategies.create(_regime_definition())
    phase7_candidates = CandidateSet(
        candidate_set_id="protocol-candidates-regime-grid-e2e",
        protocol_id=protocol.protocol_id,
        strategy_version_ids=(base.version_id,),
        strategy_version_content_hashes={base.version_id: base.content_hash or ""},
        created_at=CREATED_AT,
    )
    protocols.create_candidate_set(phase7_candidates)
    protocols.lock_candidate_set(phase7_candidates.candidate_set_id)
    protocols.transition_protocol(protocol.protocol_id, ProtocolStatus.FROZEN)

    experiment = _experiment(base, protocol)
    experiments.create(experiment)
    execution_service = ExperimentExecutionService(
        experiment_repository=experiments,
        protocol_repository=protocols,
        strategy_repository=strategies,
        candidate_execution_repository=executions,
        data_service=_LocalDataService(),
        clock=lambda: CREATED_AT,
    )
    finalizer = ExperimentResultFinalizationService(
        backtest_repository=backtests,
        experiment_result_repository=results,
        candidate_execution_repository=executions,
        experiment_repository=experiments,
        clock=lambda: CREATED_AT,
    )
    grid = GridSearchService(
        experiment_repository=experiments,
        strategy_repository=strategies,
        execution_repository=executions,
        result_repository=results,
        grid_repository=grids,
        execution_service=execution_service,
        finalization_service=finalizer,
        clock=lambda: CREATED_AT,
    )
    definition = GridSearchDefinition(
        experiment_id=experiment.experiment_id,
        experiment_hash=experiment.content_hash,
        semantic_bindings=(
            _semantic_binding("risk_on_threshold", base, "recovery-to-full-risk-on"),
            _semantic_binding("protective_threshold", base, "full-risk-on-to-protective"),
        ),
        fixed_parameters={"strategy_mode": "regime_state_machine"},
        max_candidates=4,
        theoretical_guard=4,
    )

    prepared = grid.prepare(experiment.experiment_id, definition)
    assert prepared["theoretical_count"] == 4
    assert prepared["valid_count"] == 4
    assert prepared["executable_count"] == 4
    assert prepared["duplicate_count"] == 0
    assert len(prepared["semantic_binding_audit"]) == 2
    assert all(
        item["resolved_target_path"].startswith("transitions[")
        for item in prepared["semantic_binding_audit"]
    )
    assert all(
        item["base_strategy_version_id"] == base.version_id
        and item["base_strategy_version_hash"] == base.content_hash
        for item in prepared["semantic_binding_audit"]
    )

    progress = grid.execute(experiment.experiment_id)
    assert progress["status"] == "completed", progress
    assert progress["completed"] == 4
    candidate_records = experiments.list_candidates(experiment.experiment_id)
    persisted_results = results.list(experiment.experiment_id)
    assert len(persisted_results) == 4
    assert len({item.candidate_id for item in persisted_results}) == 4
    assert len({item.derived_strategy_version_id for item in persisted_results}) == 4
    assert all(item.derived_strategy_version_hash for item in persisted_results)

    by_values = {
        tuple(sorted(record.parameter_set.values.items())): record for record in candidate_records
    }
    expected_values = {
        (("protective_threshold", -0.04), ("risk_on_threshold", 0.01)),
        (("protective_threshold", -0.04), ("risk_on_threshold", 0.03)),
        (("protective_threshold", -0.02), ("risk_on_threshold", 0.01)),
        (("protective_threshold", -0.02), ("risk_on_threshold", 0.03)),
    }
    assert set(by_values) == expected_values
    result_by_candidate = {item.candidate_id: item for item in persisted_results}
    for record in candidate_records:
        result = result_by_candidate[
            next(
                execution.candidate_id
                for execution in (
                    executions.get_execution_by_candidate(
                        experiment.experiment_id, record.candidate_index
                    ),
                )
                if execution is not None
            )
        ]
        strategy = strategies.get_any_version(result.derived_strategy_version_id)
        assert strategy is not None
        assert strategy.materialization_provenance is not None
        audit = strategy.materialization_provenance.binding_audit
        assert {item["parameter_value"] for item in audit} == set(
            record.parameter_set.values.values()
        )
        assert result.backtest_run_id
        run = backtests.get(result.backtest_run_id)
        assert run is not None
        metadata = backtests.list_metadata(limit=20).items
        assert (
            next(item for item in metadata if item.backtest_run_id == run.backtest_run_id).run_type
            == "GRID_CANDIDATE"
        )

    read_model = ExperimentResultsReadService(
        experiment_repository=experiments,
        candidate_execution_repository=executions,
        experiment_result_repository=results,
        backtest_repository=backtests,
        strategy_repository=strategies,
    ).get(experiment.experiment_id, protocol_id=protocol.protocol_id)
    candidate = next(
        item
        for item in read_model.candidates
        if item.parameter_set_hash
        == by_values[
            (("protective_threshold", -0.04), ("risk_on_threshold", 0.03))
        ].parameter_set.content_hash
    )
    objective = evaluate_candidate_objective(experiment, candidate)
    compatibility = ExperimentCompatibilityService().diagnose(read_model)
    derived = strategies.get_any_version(candidate.derived_strategy_version_id or "")
    assert derived is not None
    protocols.transition_protocol(protocol.protocol_id, ProtocolStatus.IS_EVALUATED)
    selection = create_experiment_selection_decision(
        experiment=experiment,
        selected_candidate_id=candidate.candidate_id,
        candidates=read_model,
        objective_evaluation=objective,
        compatibility=compatibility,
        derived_strategy_version=derived,
        selection_method=ExperimentSelectionMethod.RESEARCHER_JUDGMENT,
        researcher_rationale=(
            "Researcher selected the fixed +3% / -4% candidate for E2E verification."
        ),
        created_at=CREATED_AT,
    )
    ExperimentSelectionRepository(database).create(selection)
    selection_decision, freeze = ExperimentSelectionHandoffService(
        database
    ).handoff_experiment_selection(experiment.experiment_id)
    assert freeze.strategy_version_id == derived.version_id
    assert freeze.strategy_version_content_hash == derived.content_hash
    assert selection_decision.selected_strategy_version_id == candidate.derived_strategy_version_id
    selected_audit = derived.materialization_provenance.binding_audit
    assert {
        item["parameter_name"]: item["parameter_value"] for item in selected_audit
    } == {"protective_threshold": -0.04, "risk_on_threshold": 0.03}
    assert tuple(item.transition_id for item in derived.configuration.transitions) == (
        "full-risk-on-to-protective",
        "recovery-to-full-risk-on",
    )

    oos_executions = OosExecutionRepository(database)
    spec = _oos_spec(protocol, selection_decision, freeze, derived)
    created = oos_executions.get_or_create_execution(spec, now=CREATED_AT)
    claimed = oos_executions.claim_execution(
        created.execution_id,
        "regime-grid-e2e",
        CREATED_AT,
        CREATED_AT + timedelta(minutes=10),
    )
    oos_outcome = OosExecutionService(
        protocol_repository=protocols,
        execution_repository=oos_executions,
        strategy_repository=strategies,
        data_service=_LocalDataService(),
        clock=lambda: CREATED_AT,
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
        backtest_repository=backtests,
        strategy_repository=strategies,
        clock=lambda: CREATED_AT,
    ).finalize_oos_observation(
        protocol_id=protocol.protocol_id,
        execution_id=claimed.execution_id,
        lease_token=claimed.lease_token or "",
        outcome=oos_outcome,
    )
    assert official.strategy_version_id == freeze.strategy_version_id == derived.version_id
    assert (
        official.strategy_content_hash
        == freeze.strategy_version_content_hash
        == derived.content_hash
    )
    assert official.oos_start == OOS_START and official.oos_end == OOS_END
    oos_strategy = strategies.get_any_version(official.strategy_version_id)
    assert oos_strategy is not None
    assert {
        item["parameter_name"]: item["parameter_value"]
        for item in oos_strategy.materialization_provenance.binding_audit
    } == {"protective_threshold": -0.04, "risk_on_threshold": 0.03}
    official_run = backtests.get(official.backtest_run_id)
    assert official_run is not None
    official_meta = next(
        item
        for item in backtests.list_metadata(limit=20).items
        if item.backtest_run_id == official.backtest_run_id
    )
    assert official_meta.run_type == "OFFICIAL_OOS"
    assert official_run.backtest_result.start_date == OOS_START
    assert official_run.backtest_result.end_date == OOS_END
    assert IS_END < OOS_START

    view_service = OosResearchViewService(
        protocols=protocols,
        results=OosResultRepository(database),
        backtests=backtests,
        strategies=strategies,
    )
    view = view_service.get(protocol.protocol_id)
    assert view["read_only"] is True
    assert view["oos_result"]["strategy_version_id"] == derived.version_id

    candidate_run_id = persisted_results[0].backtest_run_id
    assert backtests.trash(candidate_run_id) == "trashed"
    candidate_dependencies = backtests.permanently_delete(candidate_run_id)
    assert isinstance(candidate_dependencies, tuple)
    assert any(item.kind == "EXPERIMENT_CANDIDATE" for item in candidate_dependencies)
    assert backtests.restore(candidate_run_id) == "restored"
    assert (
        ExperimentResultsReadService(
            experiment_repository=experiments,
            candidate_execution_repository=executions,
            experiment_result_repository=results,
            backtest_repository=backtests,
            strategy_repository=strategies,
        )
        .get(experiment.experiment_id, protocol_id=protocol.protocol_id)
        .candidates
    )

    assert backtests.trash(official.backtest_run_id) == "trashed"
    oos_dependencies = backtests.permanently_delete(official.backtest_run_id)
    assert isinstance(oos_dependencies, tuple)
    assert any(item.kind == "OFFICIAL_OOS" for item in oos_dependencies)
    assert backtests.restore(official.backtest_run_id) == "restored"
    assert (
        view_service.get(protocol.protocol_id)["oos_result"]["oos_result_id"]
        == official.oos_result_id
    )
