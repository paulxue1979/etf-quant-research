from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from typing import Any

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
from data.cache import DiskCache
from data.config import TiingoSettings
from data.exceptions import (
    TiingoConfigurationError,
    TiingoNetworkError,
    TiingoTimeoutError,
)
from data.models import HistoricalDataRequest, PriceField
from data.service import HistoricalDataService
from data.tiingo import TiingoClient
from research import (
    BindingValueType,
    Experiment,
    ExperimentMethod,
    ExperimentProvenance,
    ExperimentStatus,
    MetricDirection,
    ObjectiveSpecification,
    ParameterBinding,
    ParameterDefinition,
    ParameterSpace,
    ParameterType,
    generate_candidates,
)
from research.execution_service import ExperimentExecutionService
from research.experiment_selection import (
    ExperimentSelectionMethod,
    create_experiment_selection_decision,
)
from research.materialization import ParameterBindingSet
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
from strategies import (
    Allocation,
    AllocationRule,
    AssetReference,
    ComparisonOperator,
    Condition,
    FallbackAllocation,
    LogicalOperator,
    Operand,
    OperandType,
    RuleGroup,
    StrategyDefinition,
)
from strategies import (
    RebalanceFrequency as StrategyRebalanceFrequency,
)
from strategies import (
    RebalancePolicy as StrategyRebalancePolicy,
)

FIVE_ETFS = ("QQQ", "TQQQ", "SPY", "IWM", "SGOV")
PRICE_FIELD = PriceField.ADJUSTED_CLOSE
IS_START = date(2024, 1, 2)
IS_END = date(2024, 6, 28)
OOS_START = date(2024, 7, 1)
OOS_END = date(2024, 12, 31)
INDICATOR_PERIOD = 20
CREATED_AT = datetime(2026, 9, 14, 12, 0, tzinfo=UTC)


class _NetworkTrackingDataService:
    """Preserve external-network diagnostics across service error translation."""

    def __init__(self, delegate: Any) -> None:
        self._delegate = delegate
        self.network_error: Exception | None = None

    def get_history(self, request: HistoricalDataRequest):
        try:
            return self._delegate.get_history(request)
        except (TiingoNetworkError, TiingoTimeoutError) as exc:
            self.network_error = exc
            raise


def _settings() -> TiingoSettings:
    try:
        return TiingoSettings.from_environment()
    except TiingoConfigurationError as exc:
        pytest.skip(f"Tiingo environment is not configured: {exc}")


def _warmup_start(start: date) -> date:
    return start - timedelta(days=INDICATOR_PERIOD * 3 + 10)


def _strategy() -> StrategyDefinition:
    condition = Condition(
        Operand("QQQ", OperandType.PRICE, price_field=PRICE_FIELD),
        ComparisonOperator.GREATER_THAN,
        Operand("QQQ", OperandType.MA, period=INDICATOR_PERIOD, price_field=PRICE_FIELD),
    )
    return StrategyDefinition(
        strategy_id="five-etf-target-allocation",
        name="Five ETF Target Allocation",
        description="Generic multi-asset target allocation acceptance strategy.",
        assets=tuple(AssetReference(symbol) for symbol in FIVE_ETFS),
        price_field=PRICE_FIELD,
        rules=(
            AllocationRule(
                "risk-on",
                "Risk On",
                1,
                (
                    Allocation("QQQ", 0.30),
                    Allocation("TQQQ", 0.20),
                    Allocation("SPY", 0.20),
                    Allocation("IWM", 0.20),
                    Allocation("SGOV", 0.10),
                ),
                RuleGroup(LogicalOperator.AND, (condition,)),
            ),
        ),
        fallback=FallbackAllocation((Allocation("SGOV", 1.0),)),
        rebalance_policy=StrategyRebalancePolicy(StrategyRebalanceFrequency.DAILY),
    )


def _evaluation_config() -> ResearchEvaluationConfig:
    return ResearchEvaluationConfig(
        price_field_used=PRICE_FIELD.value,
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
        protocol_id="protocol-five-etf-v1",
        protocol_version=1,
        created_at=CREATED_AT,
        is_start_date=IS_START,
        is_end_date=IS_END,
        oos_start_date=OOS_START,
        oos_end_date=OOS_END,
        selection_rules=("researcher selects from frozen IS evidence",),
        allowed_metrics=("cagr", "max_drawdown"),
        forbidden_actions=("oos_back_selection", "oos_parameter_tuning"),
        evaluation_config=_evaluation_config(),
    )


def _experiment(version, protocol: ResearchProtocol) -> Experiment:
    space = ParameterSpace(
        parameters=(
            ParameterDefinition(
                "period",
                ParameterType.INTEGER,
                min=INDICATOR_PERIOD,
                max=INDICATOR_PERIOD,
                step=1,
            ),
        ),
        max_candidates=1,
    )
    objective = ObjectiveSpecification(
        primary_metric="cagr",
        metric_directions={"cagr": MetricDirection.MAXIMIZE},
    )
    backtest_config = BacktestConfig(
        strategy_version_id=version.version_id,
        start_date=IS_START,
        end_date=IS_END,
        initial_capital=10_000.0,
        price_field_used=PRICE_FIELD,
        rebalance_policy=RebalancePolicy(RebalanceFrequency.DAILY),
    )
    return Experiment(
        experiment_id="experiment-five-etf-v1",
        protocol_id=protocol.protocol_id,
        strategy_definition_id=version.strategy_id,
        base_strategy_version_id=version.version_id,
        base_strategy_version_hash=version.content_hash or "",
        parameter_space=space,
        objective_specification=objective,
        is_start_date=IS_START,
        is_end_date=IS_END,
        backtest_configuration=backtest_config,
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
            data_snapshot_reference={
                "source": "tiingo",
                "frequency": "daily",
                "symbols": list(FIVE_ETFS),
                "price_field": PRICE_FIELD.value,
            },
        ),
    )


def _binding() -> ParameterBinding:
    return ParameterBinding(
        "period",
        "rules[0].condition.children[0].right.period",
        BindingValueType.POSITIVE_INTEGER,
        "five-etf-acceptance-v1",
    )


def _oos_spec(protocol, selection, freeze, version) -> OosEvaluationSpec:
    identity = OosEvaluationIdentity(
        protocol_id=protocol.protocol_id,
        selection_decision_id=selection.decision_id,
        strategy_freeze_id=freeze.freeze_id,
        strategy_version_id=version.version_id,
        strategy_content_hash=version.content_hash or "",
    )
    warmup_start = _warmup_start(OOS_START)
    evaluation_range = OosEvaluationRange.from_protocol(protocol, warmup_start=warmup_start)
    configuration = OosEvaluationConfig.from_research_evaluation_config(
        protocol.evaluation_config,
        analytics_version="phase-4i.0",
    )
    provenance = OosDataProvenance(
        source="HistoricalDataService",
        data_reference={
            "symbols": list(FIVE_ETFS),
            "frequency": "daily",
            "price_field": PRICE_FIELD.value,
        },
        requested_start=OOS_START,
        requested_end=OOS_END,
        warmup_start=warmup_start,
        frequency="daily",
        price_field=PRICE_FIELD,
    )
    return OosEvaluationSpec(
        identity=identity,
        evaluation_range=evaluation_range,
        configuration=configuration,
        data_provenance=provenance,
    )


@pytest.mark.integration
def test_five_etf_real_data_acceptance(tmp_path) -> None:
    settings = _settings()
    cache = DiskCache()
    data_service = HistoricalDataService(TiingoClient(settings), cache)
    tracked_data_service = _NetworkTrackingDataService(data_service)
    full_request_data = {}
    warmup_start = _warmup_start(IS_START)
    request_end = OOS_END

    try:
        for symbol in FIVE_ETFS:
            request = HistoricalDataRequest(
                symbol=symbol,
                start_date=warmup_start,
                end_date=request_end,
                price_field_used=PRICE_FIELD,
            )
            first = tracked_data_service.get_history(request)
            cached = tracked_data_service.get_history(request)
            assert first.points
            assert first.source.value in {"api_fresh", "cache"}
            assert cached.source.value == "cache"
            assert first.price_field_used is PRICE_FIELD
            assert all(
                point.close is not None and point.adj_close is not None
                for point in first.points
            )
            assert all(
                point.high >= point.low and point.adj_high >= point.adj_low
                for point in first.points
            )
            full_request_data[symbol] = first
    except (TiingoNetworkError, TiingoTimeoutError) as exc:
        pytest.skip(f"Codex/local network cannot reach Tiingo: {type(exc).__name__}")

    common_dates = set.intersection(
        *(set(point.date for point in dataset.points) for dataset in full_request_data.values())
    )
    assert common_dates
    assert not any(
        day > OOS_END for day in common_dates
    )

    database = tmp_path / "research.db"
    protocols = ResearchProtocolRepository(database)
    strategies = StrategyRepository(database)
    experiments = ExperimentRepository(database)
    executions = CandidateExecutionRepository(database)
    protocol = protocols.create_protocol(_protocol())
    base_version = strategies.create(_strategy())
    candidate_set = CandidateSet(
        candidate_set_id="candidate-set-five-etf-v1",
        protocol_id=protocol.protocol_id,
        strategy_version_ids=(base_version.version_id,),
        strategy_version_content_hashes={base_version.version_id: base_version.content_hash or ""},
        created_at=CREATED_AT,
    )
    protocols.create_candidate_set(candidate_set)
    protocols.lock_candidate_set(candidate_set.candidate_set_id)
    protocols.transition_protocol(protocol.protocol_id, ProtocolStatus.FROZEN)

    experiment = _experiment(base_version, protocol)
    experiments.create(experiment, generate_candidates(experiment))
    experiments.transition_status(
        experiment.experiment_id,
        ExperimentStatus.SPACE_FROZEN,
        ExperimentStatus.CANDIDATES_GENERATED,
        "CANDIDATES_GENERATED",
        "five-etf-candidates-generated",
    )
    experiments.transition_status(
        experiment.experiment_id,
        ExperimentStatus.CANDIDATES_GENERATED,
        ExperimentStatus.RUNNING,
        "RUNNING",
        "five-etf-experiment-running",
    )

    execution_service = ExperimentExecutionService(
        experiment_repository=experiments,
        protocol_repository=protocols,
        strategy_repository=strategies,
        candidate_execution_repository=executions,
        data_service=tracked_data_service,
        clock=lambda: CREATED_AT,
    )
    outcome = execution_service.execute(
        experiment_id=experiment.experiment_id,
        candidate_index=0,
        parameter_bindings=ParameterBindingSet((_binding(),)),
    )
    if tracked_data_service.network_error is not None:
        pytest.skip(
            "Codex/local network cannot reach Tiingo: "
            f"{type(tracked_data_service.network_error).__name__}"
        )
    assert outcome.backtest_result is not None
    assert outcome.performance_analysis_result is not None
    assert outcome.backtest_result.backtest_result.start_date == IS_START
    assert outcome.backtest_result.backtest_result.end_date == IS_END
    assert outcome.performance_analysis_result.start_date == IS_START
    assert outcome.performance_analysis_result.end_date == IS_END
    assert outcome.warmup_start is not None and outcome.warmup_start < IS_START
    assert all(
        point.date <= IS_END
        for point in outcome.backtest_result.backtest_result.equity_curve
    )

    finalizer = ExperimentResultFinalizationService(
        backtest_repository=BacktestRepository(database),
        experiment_result_repository=ExperimentResultRepository(database),
        candidate_execution_repository=executions,
        experiment_repository=experiments,
        clock=lambda: CREATED_AT,
    )
    result = finalizer.finalize(outcome)
    assert result.is_start == IS_START
    assert result.is_end == IS_END
    assert result.price_field_used is PRICE_FIELD

    read_model = ExperimentResultsReadService(
        experiment_repository=experiments,
        candidate_execution_repository=executions,
        experiment_result_repository=ExperimentResultRepository(database),
        backtest_repository=BacktestRepository(database),
        strategy_repository=strategies,
    ).get(experiment.experiment_id, protocol_id=protocol.protocol_id)
    candidate = read_model.candidates[0]
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
            "Researcher selected the sole frozen candidate after reviewing IS evidence."
        ),
        created_at=CREATED_AT,
    )
    ExperimentSelectionRepository(database).create(selection)
    handoff = ExperimentSelectionHandoffService(
        experiment_repository=experiments,
        selection_repository=ExperimentSelectionRepository(database),
        result_repository=ExperimentResultRepository(database),
        execution_repository=executions,
        backtest_repository=BacktestRepository(database),
        strategy_repository=strategies,
        protocol_repository=protocols,
    )
    selection_decision, freeze = handoff.handoff_experiment_selection(experiment.experiment_id)
    assert selection_decision.protocol_id == protocol.protocol_id
    assert freeze.strategy_version_id == result.derived_strategy_version_id

    oos_executions = OosExecutionRepository(database)
    spec = _oos_spec(protocol, selection_decision, freeze, derived)
    oos_execution = oos_executions.get_or_create_execution(spec, now=CREATED_AT)
    claimed = oos_executions.claim_execution(
        oos_execution.execution_id,
        "five-etf-acceptance",
        CREATED_AT,
        CREATED_AT + timedelta(minutes=10),
    )
    oos_service = OosExecutionService(
        protocol_repository=protocols,
        execution_repository=oos_executions,
        strategy_repository=strategies,
        data_service=tracked_data_service,
        clock=lambda: CREATED_AT,
    )
    oos_outcome = oos_service.execute(
        protocol_id=protocol.protocol_id,
        execution_id=claimed.execution_id,
        lease_token=claimed.lease_token or "",
        spec=spec,
    )
    assert oos_outcome.backtest_result.start_date == OOS_START
    assert oos_outcome.backtest_result.end_date == OOS_END
    assert oos_outcome.performance_analysis.start_date == OOS_START
    assert oos_outcome.performance_analysis.end_date == OOS_END
    assert all(point.date <= OOS_END for point in oos_outcome.backtest_result.equity_curve)
    assert all(order.date <= OOS_END for order in oos_outcome.backtest_result.orders)

    oos_finalizer = OosFinalizationService(
        protocol_repository=protocols,
        execution_repository=oos_executions,
        result_repository=OosResultRepository(database),
        backtest_repository=BacktestRepository(database),
        strategy_repository=strategies,
        clock=lambda: CREATED_AT,
    )
    official = oos_finalizer.finalize_oos_observation(
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
    assert view["read_only"] is True
    assert view["oos_result"]["oos_result_id"] == official.oos_result_id

    assert spec.boundary_signal_policy.value == "oos_signals_only"
    common_dates_sorted = sorted(common_dates)
    for order in oos_outcome.backtest_result.orders:
        assert OOS_START <= order.signal_date <= OOS_END
        assert OOS_START <= order.date <= OOS_END
        assert order.date > order.signal_date
        following = [day for day in common_dates_sorted if day > order.signal_date]
        assert following and order.date == following[0]

    print(
        "acceptance_ids="
        f"strategy:{base_version.strategy_id} "
        f"strategy_version:{base_version.version_id} "
        f"backtest:{result.backtest_run_id} "
        f"experiment:{experiment.experiment_id} "
        f"candidate:{candidate.candidate_id} "
        f"selection:{selection_decision.decision_id} "
        f"freeze:{freeze.freeze_id} "
        f"protocol:{protocol.protocol_id} "
        f"oos_execution:{claimed.execution_id} "
        f"official_oos:{official.oos_result_id} "
        f"official_backtest:{official.backtest_run_id}"
    )
