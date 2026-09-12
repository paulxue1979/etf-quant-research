from __future__ import annotations

from dataclasses import replace
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import pytest

from backend.app.candidate_execution_repository import CandidateExecutionRepository
from backend.app.experiment_repository import ExperimentRepository
from backend.app.research_protocol import (
    CandidateSet,
    ProtocolStatus,
    ResearchEvaluationConfig,
    ResearchProtocol,
    ResearchProtocolRepository,
)
from backend.app.strategy_repository import StrategyRepository
from backtest import BacktestConfig
from backtest import RebalanceFrequency as BacktestRebalanceFrequency
from backtest import RebalancePolicy as BacktestRebalancePolicy
from data.exceptions import TiingoTimeoutError
from data.models import (
    DataSource,
    HistoricalDataRequest,
    HistoricalDataSet,
    MarketDataPoint,
    PriceField,
)
from research import (
    BindingValueType,
    CandidateExecution,
    Experiment,
    ExperimentMethod,
    ExperimentProvenance,
    ExperimentStatus,
    MetricDirection,
    ObjectiveSpecification,
    ParameterBinding,
    ParameterBindingSet,
    ParameterDefinition,
    ParameterSpace,
    ParameterType,
    candidate_id_for,
    generate_candidates,
    materialize_strategy_version,
)
from research.execution import CandidateExecutionStatus
from research.execution_outcome import ExperimentExecutionOutcomeStatus
from research.execution_service import ExperimentExecutionService
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
    RebalanceFrequency,
    RebalancePolicy,
    RuleGroup,
    StrategyDefinition,
)

NOW = datetime(2026, 9, 9, 12, tzinfo=UTC)
IS_START = date(2020, 1, 13)
IS_END = date(2020, 1, 17)
OOS_START = date(2020, 1, 20)
OOS_END = date(2020, 1, 24)


class _LocalDataService:
    """Deterministic local data fixture; it never represents Tiingo validation."""

    def __init__(
        self,
        *,
        extra_oos_point: bool = False,
        missing_symbol: str | None = None,
        returned_price_field: PriceField | None = None,
        timeout_once: bool = False,
    ) -> None:
        self.requests: list[HistoricalDataRequest] = []
        self.extra_oos_point = extra_oos_point
        self.missing_symbol = missing_symbol
        self.returned_price_field = returned_price_field
        self.timeout_once = timeout_once

    def get_history(self, request: HistoricalDataRequest) -> HistoricalDataSet:
        self.requests.append(request)
        if self.timeout_once:
            self.timeout_once = False
            raise TiingoTimeoutError("transient timeout")
        if request.symbol == self.missing_symbol:
            raise LookupError("fixture asset absent")
        final_day = (
            request.end_date + timedelta(days=1) if self.extra_oos_point else request.end_date
        )
        points = tuple(
            _point(day, request.start_date, request.symbol)
            for day in _dates(request.start_date, final_day)
        )
        default_dataset = HistoricalDataSet(
            request=request,
            points=points,
            source=DataSource.API_FRESH,
        ).with_source(DataSource.API_FRESH)
        if self.returned_price_field is None:
            return default_dataset
        return HistoricalDataSet(
            request=replace(request, price_field_used=self.returned_price_field),
            points=points,
            source=DataSource.API_FRESH,
        )


def _dates(start: date, end: date) -> tuple[date, ...]:
    return tuple(start + timedelta(days=index) for index in range((end - start).days + 1))


def _point(day: date, start: date, symbol: str) -> MarketDataPoint:
    offset = (day - start).days
    base = 100.0 + offset + (5.0 if symbol == "TQQQ" else 0.0)
    return MarketDataPoint(
        date=day,
        open=base,
        high=base + 1.0,
        low=base - 1.0,
        close=base,
        volume=1_000.0,
        adj_open=base,
        adj_high=base + 1.0,
        adj_low=base - 1.0,
        adj_close=base,
        adj_volume=1_000.0,
        div_cash=0.0,
        split_factor=1.0,
    )


def _definition() -> StrategyDefinition:
    condition = Condition(
        Operand("QQQ", OperandType.PRICE, price_field=PriceField.ADJUSTED_CLOSE),
        ComparisonOperator.GREATER_THAN,
        Operand("QQQ", OperandType.MA, 2, PriceField.ADJUSTED_CLOSE),
    )
    return StrategyDefinition(
        strategy_id="execution-service",
        name="Execution service fixture",
        description="Deterministic PHASE 8D-3 fixture",
        assets=(AssetReference("QQQ"), AssetReference("TQQQ")),
        price_field=PriceField.ADJUSTED_CLOSE,
        rules=(
            AllocationRule(
                "risk-on",
                "Risk On",
                1,
                (Allocation("TQQQ", 1.0),),
                condition=RuleGroup(LogicalOperator.AND, (condition,)),
            ),
        ),
        fallback=FallbackAllocation((Allocation("QQQ", 1.0),)),
        rebalance_policy=RebalancePolicy(RebalanceFrequency.DAILY),
    )


def _evaluation_config() -> ResearchEvaluationConfig:
    return ResearchEvaluationConfig(
        price_field_used=PriceField.ADJUSTED_CLOSE.value,
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
        protocol_id="protocol-8d3",
        protocol_version=1,
        created_at=NOW,
        is_start_date=IS_START,
        is_end_date=IS_END,
        oos_start_date=OOS_START,
        oos_end_date=OOS_END,
        selection_rules=("human IS review",),
        allowed_metrics=("cagr",),
        forbidden_actions=("oos_back_selection",),
        evaluation_config=_evaluation_config(),
    )


def _experiment(
    version,
    *,
    status: ExperimentStatus = ExperimentStatus.SPACE_FROZEN,
    parameter_max: int = 3,
) -> Experiment:
    space = ParameterSpace(
        parameters=(
            ParameterDefinition("period", ParameterType.INTEGER, min=2, max=parameter_max, step=1),
        ),
        max_candidates=parameter_max - 1,
    )
    objective = ObjectiveSpecification(
        primary_metric="cagr", metric_directions={"cagr": MetricDirection.MAXIMIZE}
    )
    config = BacktestConfig(
        strategy_version_id=version.version_id,
        start_date=IS_START,
        end_date=IS_END,
        initial_capital=10_000.0,
        price_field_used=PriceField.ADJUSTED_CLOSE,
        rebalance_policy=BacktestRebalancePolicy(BacktestRebalanceFrequency.DAILY),
    )
    return Experiment(
        experiment_id="experiment-8d3",
        protocol_id="protocol-8d3",
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
        status=status,
        created_at=NOW,
        method=ExperimentMethod.GRID,
        provenance=ExperimentProvenance(
            protocol_id="protocol-8d3",
            strategy_definition_id=version.strategy_id,
            base_strategy_version_id=version.version_id,
            base_strategy_version_hash=version.content_hash or "",
            parameter_space_hash=space.content_hash,
            objective_spec_hash=objective.content_hash,
            is_start_date=IS_START,
            is_end_date=IS_END,
            price_field_used=PriceField.ADJUSTED_CLOSE,
            initial_capital=10_000.0,
            commission={"rate": 0.0, "per_order": 0.0},
            slippage=0.0,
            execution_rule="next_trading_day_open",
            fractional_shares=False,
            rebalance_policy={"frequency": "daily", "threshold": None},
            engine_version="phase-3.0",
            analysis_version="phase-4i.0",
            data_snapshot_reference={"frequency": "daily", "source": "local-test"},
        ),
    )


def _binding() -> ParameterBinding:
    return ParameterBinding(
        "period",
        "rules[0].condition.children[0].right.period",
        BindingValueType.POSITIVE_INTEGER,
        "phase-8d-1-v1",
    )


def _setup(
    tmp_path: Path,
    data: _LocalDataService | None = None,
    *,
    parameter_max: int = 3,
):
    database = tmp_path / "research.db"
    protocols = ResearchProtocolRepository(database)
    strategies = StrategyRepository(database)
    experiments = ExperimentRepository(database)
    executions = CandidateExecutionRepository(database)
    protocol = _protocol()
    protocols.create_protocol(protocol)
    version = strategies.create(_definition())
    candidate_set = CandidateSet(
        candidate_set_id="protocol-candidates-8d3",
        protocol_id=protocol.protocol_id,
        strategy_version_ids=(version.version_id,),
        strategy_version_content_hashes={version.version_id: version.content_hash or ""},
        created_at=NOW,
    )
    protocols.create_candidate_set(candidate_set)
    protocols.lock_candidate_set(candidate_set.candidate_set_id)
    protocols.transition_protocol(protocol.protocol_id, ProtocolStatus.FROZEN)
    experiment = _experiment(version, parameter_max=parameter_max)
    experiments.create(experiment, generate_candidates(experiment))
    service = ExperimentExecutionService(
        experiment_repository=experiments,
        protocol_repository=protocols,
        strategy_repository=strategies,
        candidate_execution_repository=executions,
        data_service=data or _LocalDataService(),
        clock=lambda: NOW,
    )
    return service, experiments, protocols, executions, data, experiment


def test_executes_one_candidate_with_is_only_warmup_and_complete_provenance(tmp_path) -> None:
    data = _LocalDataService()
    service, _, _, executions, _, experiment = _setup(tmp_path, data)

    outcome = service.execute(
        experiment_id=experiment.experiment_id,
        candidate_index=0,
        parameter_bindings=(_binding(),),
    )

    assert outcome.status is ExperimentExecutionOutcomeStatus.COMPLETED
    assert outcome.backtest_result is not None
    assert outcome.performance_analysis_result is not None
    assert outcome.warmup_start is not None and outcome.warmup_start < IS_START
    assert outcome.warmup_end == IS_START - timedelta(days=1)
    assert outcome.backtest_result.backtest_result.start_date == IS_START
    assert outcome.backtest_result.backtest_result.end_date == IS_END
    assert outcome.performance_analysis_result.start_date == IS_START
    assert outcome.performance_analysis_result.end_date == IS_END
    assert all(request.end_date == IS_END for request in data.requests)
    assert all(request.start_date <= IS_START for request in data.requests)
    assert outcome.backtest_result.signal_records[-1].signal.date == IS_END
    assert outcome.backtest_result.signal_records[-1].submitted_to_backtest is False
    assert outcome.provenance["is_evaluation_range"]["end_date"] == IS_END.isoformat()
    persisted = StrategyRepository(tmp_path / "research.db").get_any_version(
        outcome.derived_strategy_version_id or ""
    )
    assert persisted is not None
    assert outcome.derived_strategy_version_id == persisted.version_id
    assert outcome.derived_strategy_version_hash == persisted.content_hash
    assert persisted.materialization_provenance is not None
    assert (
        outcome.provenance["warmup_range"]["end_date"] == (IS_START - timedelta(days=1)).isoformat()
    )
    events = executions.list_events(outcome.candidate_execution_id or "")
    assert [event.event_type for event in events] == [
        "EXECUTION_CREATED",
        "EXECUTION_CLAIMED",
        "EXECUTION_COMPLETED",
    ]


@pytest.mark.parametrize(
    ("candidate_index", "expected_code"),
    [(99, "INVALID_PARAMETER_SET"), (-1, "INVALID_PARAMETER_SET")],
)
def test_rejects_candidate_outside_experiment(
    tmp_path, candidate_index: int, expected_code: str
) -> None:
    service, _, _, _, _, experiment = _setup(tmp_path)

    outcome = service.execute(
        experiment_id=experiment.experiment_id,
        candidate_index=candidate_index,
        parameter_bindings=(_binding(),),
    )

    assert outcome.status is ExperimentExecutionOutcomeStatus.FAILED
    assert outcome.failure_code == expected_code


def test_rejects_unfrozen_protocol(tmp_path) -> None:
    database = tmp_path / "unfrozen.db"
    protocols = ResearchProtocolRepository(database)
    strategies = StrategyRepository(database)
    experiments = ExperimentRepository(database)
    protocol = _protocol()
    protocols.create_protocol(protocol)
    version = strategies.create(_definition())
    experiment = _experiment(version)
    experiments.create(experiment, generate_candidates(experiment))
    service = ExperimentExecutionService(
        experiment_repository=experiments,
        protocol_repository=protocols,
        strategy_repository=strategies,
        candidate_execution_repository=CandidateExecutionRepository(database),
        data_service=_LocalDataService(),
        clock=lambda: NOW,
    )

    outcome = service.execute(
        experiment_id=experiment.experiment_id,
        candidate_index=0,
        parameter_bindings=(_binding(),),
    )

    assert outcome.failure_code == "INVALID_EXPERIMENT"


def test_rejects_invalid_binding_and_materialization_failure(tmp_path) -> None:
    service, _, _, executions, _, experiment = _setup(tmp_path)
    malformed = object()
    invalid_binding = ParameterBinding(
        "period",
        "rules[0].condition.children[0].threshold.value",
        BindingValueType.RELATIVE_THRESHOLD,
        "phase-8d-1-v1",
    )

    malformed_outcome = service.execute(
        experiment_id=experiment.experiment_id,
        candidate_index=0,
        parameter_bindings=malformed,  # type: ignore[arg-type]
    )
    materialized_outcome = service.execute(
        experiment_id=experiment.experiment_id,
        candidate_index=0,
        parameter_bindings=(invalid_binding,),
    )

    assert malformed_outcome.failure_code == "INVALID_BINDING"
    assert materialized_outcome.failure_code == "INVALID_MATERIALIZED_STRATEGY"
    execution = executions.get_execution(materialized_outcome.candidate_execution_id or "")
    assert execution is not None and execution.status is CandidateExecutionStatus.FAILED
    assert execution.failure_retryable is False
    assert [event.event_type for event in executions.list_events(execution.execution_id)] == [
        "EXECUTION_CREATED",
        "EXECUTION_CLAIMED",
        "EXECUTION_FAILED",
    ]


def test_rejects_oos_data_price_field_mismatch_and_missing_asset(tmp_path) -> None:
    for name, data, code in (
        ("oos", _LocalDataService(extra_oos_point=True), "OOS_DATA_REQUEST"),
        (
            "price-field",
            _LocalDataService(returned_price_field=PriceField.RAW_CLOSE),
            "PRICE_FIELD_MISMATCH",
        ),
        ("missing", _LocalDataService(missing_symbol="TQQQ"), "MISSING_MARKET_DATA"),
    ):
        service, _, _, executions, _, experiment = _setup(tmp_path / name, data)
        outcome = service.execute(
            experiment_id=experiment.experiment_id,
            candidate_index=0,
            parameter_bindings=(_binding(),),
        )
        execution = executions.get_execution(outcome.candidate_execution_id or "")
        assert outcome.failure_code == code
        assert execution is not None and execution.status is CandidateExecutionStatus.FAILED
        assert execution.failure_retryable is False


def test_transient_data_failure_is_retryable_and_reuses_candidate_identity(tmp_path) -> None:
    data = _LocalDataService(timeout_once=True)
    service, _, _, executions, _, experiment = _setup(tmp_path, data)

    first = service.execute(
        experiment_id=experiment.experiment_id,
        candidate_index=0,
        parameter_bindings=(_binding(),),
    )
    second = service.execute(
        experiment_id=experiment.experiment_id,
        candidate_index=0,
        parameter_bindings=(_binding(),),
    )

    assert first.failure_code == "MISSING_MARKET_DATA"
    assert second.status is ExperimentExecutionOutcomeStatus.COMPLETED
    assert first.candidate_execution_id == second.candidate_execution_id
    execution = executions.get_execution(first.candidate_execution_id or "")
    assert execution is not None and execution.status is CandidateExecutionStatus.COMPLETED
    assert execution.retry_count == 1


def test_recovers_expired_candidate_lease_before_running_the_candidate(tmp_path) -> None:
    service, experiments, _, executions, _, experiment = _setup(tmp_path)
    candidate_set = experiments.get_candidate_set(experiment.experiment_id)
    assert candidate_set is not None
    candidate = experiments.list_candidates(experiment.experiment_id)[0]
    parameter_set = candidate_set.candidates[0]
    base = StrategyRepository(tmp_path / "research.db").get(
        experiment.strategy_definition_id,
        experiment.base_strategy_version_id,
    )
    assert base is not None
    bindings = (_binding(),)
    derived = materialize_strategy_version(base, parameter_set, bindings)
    stale = CandidateExecution.pending(
        experiment_id=experiment.experiment_id,
        experiment_hash=experiment.content_hash,
        candidate_id=candidate_id_for(
            experiment.experiment_id,
            candidate.candidate_index,
            parameter_set.content_hash,
        ),
        candidate_index=candidate.candidate_index,
        parameter_set_hash=parameter_set.content_hash,
        parameter_binding_hash=ParameterBindingSet(bindings).binding_hash,
        base_strategy_version_id=base.version_id,
        base_strategy_version_hash=base.content_hash or "",
        derived_strategy_version_id=derived.version_id,
        derived_strategy_version_hash=derived.content_hash,
        created_at=NOW - timedelta(minutes=10),
    )
    executions.create_execution(stale)
    executions.claim_candidate(
        experiment.experiment_id,
        candidate.candidate_index,
        "abandoned-worker",
        NOW - timedelta(minutes=10),
        NOW - timedelta(minutes=1),
    )

    outcome = service.execute(
        experiment_id=experiment.experiment_id,
        candidate_index=0,
        parameter_bindings=bindings,
    )

    assert outcome.succeeded
    events = executions.list_events(outcome.candidate_execution_id or "")
    assert [event.event_type for event in events] == [
        "EXECUTION_CREATED",
        "EXECUTION_CLAIMED",
        "LEASE_RECOVERED",
        "EXECUTION_CLAIMED",
        "EXECUTION_COMPLETED",
    ]


def test_fresh_deterministic_runs_are_identical_except_execution_lifecycle(tmp_path) -> None:
    first_service, _, _, _, _, first_experiment = _setup(tmp_path / "first")
    second_service, _, _, _, _, second_experiment = _setup(tmp_path / "second")

    first = first_service.execute(
        experiment_id=first_experiment.experiment_id,
        candidate_index=0,
        parameter_bindings=(_binding(),),
    )
    second = second_service.execute(
        experiment_id=second_experiment.experiment_id,
        candidate_index=0,
        parameter_bindings=(_binding(),),
    )

    assert first.succeeded and second.succeeded
    assert first.binding_hash == second.binding_hash
    assert _backtest_business_projection(first) == _backtest_business_projection(second)
    assert _analytics_business_projection(first) == _analytics_business_projection(second)


def test_outcome_does_not_expose_sensitive_values_or_dynamic_execution(tmp_path) -> None:
    source = Path("research/execution_service.py").read_text()
    assert "eval(" not in source
    assert "exec(" not in source
    assert "importlib" not in source
    assert "setattr(" not in source

    service, _, _, _, _, experiment = _setup(tmp_path)
    outcome = service.execute(
        experiment_id=experiment.experiment_id,
        candidate_index=0,
        parameter_bindings=(_binding(),),
    )
    rendered = repr(outcome).lower()
    assert "api_key" not in rendered
    assert "tiingo_api_key" not in rendered
    assert outcome.derived_strategy_version_hash is not None
    assert outcome.provenance["data_snapshot_reference"] == {
        "frequency": "daily",
        "source": "local-test",
    }


def _backtest_business_projection(outcome) -> dict[str, object]:
    """Compare deterministic mechanics while excluding database-specific IDs."""
    assert outcome.backtest_result is not None
    result = outcome.backtest_result.backtest_result
    return {
        "signals": tuple(
            (
                item.signal.date,
                item.signal.matched_rule_id,
                item.signal.allocation_source.value,
                tuple(sorted(item.target_allocation.as_mapping().items())),
                item.execution_date,
                item.submitted_to_backtest,
                item.omission_reason,
            )
            for item in outcome.backtest_result.signal_records
        ),
        "orders": tuple(
            (
                item.signal_date,
                item.date,
                item.symbol,
                item.side.value,
                item.quantity,
                item.requested_price,
                item.execution_price,
                item.commission,
                item.slippage,
                item.target_weight,
            )
            for item in result.orders
        ),
        "fills": tuple(
            (
                item.date,
                item.symbol,
                item.side.value,
                item.quantity,
                item.price,
                item.commission,
                item.slippage,
                item.cash_effect,
            )
            for item in result.fills
        ),
        "trades": tuple(
            (
                item.symbol,
                item.entry_date,
                item.exit_date,
                item.entry_price,
                item.exit_price,
                item.quantity,
                item.pnl,
                item.pnl_pct,
                item.holding_period,
            )
            for item in result.trades
        ),
        "equity_curve": tuple(
            (item.date, item.cash, tuple(sorted(item.asset_values.items())), item.total_equity)
            for item in result.equity_curve
        ),
        "final_equity": result.final_equity,
    }


def _analytics_business_projection(outcome) -> dict[str, object]:
    assert outcome.performance_analysis_result is not None
    analysis = outcome.performance_analysis_result
    return {
        "date_range": (analysis.start_date, analysis.end_date),
        "price_field_used": analysis.price_field_used,
        "initial_capital": analysis.initial_capital,
        "final_equity": analysis.final_equity,
        "total_return": analysis.total_return,
        "cagr": analysis.cagr,
        "annualized_volatility": analysis.annualized_volatility,
        "sharpe_ratio": analysis.sharpe_ratio,
        "sortino_ratio": analysis.sortino_ratio,
        "max_drawdown": analysis.max_drawdown,
        "max_drawdown_duration": analysis.max_drawdown_duration,
        "recovery_duration": analysis.recovery_duration,
        "calmar_ratio": analysis.calmar_ratio,
        "trade_metrics": analysis.trade_metrics,
        "drawdown_curve": analysis.drawdown_curve,
    }
