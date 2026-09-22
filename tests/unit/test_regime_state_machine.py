from __future__ import annotations

from dataclasses import replace
from datetime import UTC, date, datetime, timedelta

import pytest

from backend.app.candidate_execution_repository import CandidateExecutionRepository
from backend.app.experiment_repository import ExperimentRepository
from backend.app.research_protocol import CandidateSet, ProtocolStatus, ResearchProtocolRepository
from backend.app.strategy_execution_provenance import strategy_execution_provenance
from backend.app.strategy_repository import StrategyRepository
from backtest import (
    BacktestConfig,
    CommissionPolicy,
    run_strategy_backtest,
    target_allocations_from_timeline,
)
from backtest import RebalanceFrequency as BacktestRebalanceFrequency
from backtest import RebalancePolicy as BacktestRebalancePolicy
from backtest.integration import StrategyBacktestAllocation
from backtest.models import TargetAllocation
from data.derived import derive_completed_weekly
from data.models import (
    DataSource,
    HistoricalDataRequest,
    HistoricalDataSet,
    MarketDataPoint,
    PriceField,
    Timeframe,
)
from research import (
    BindingValueType,
    ParameterBinding,
    ParameterSet,
    generate_candidates,
    materialize_strategy_version,
)
from research.execution_outcome import ExperimentExecutionOutcomeStatus
from research.execution_service import ExperimentExecutionService
from strategies import (
    Allocation,
    AllocationRule,
    AllocationSpecification,
    AssetRole,
    ComparisonOperator,
    Condition,
    EvaluationContext,
    FallbackAllocation,
    LogicalOperator,
    Operand,
    OperandType,
    RebalanceFrequency,
    RebalancePolicy,
    RegimeDefinition,
    RegimeTransitionDefinition,
    RuleGroup,
    StrategyAssetReference,
    StrategyDefinition,
    StrategyEvaluationMode,
    StrategyEvaluationStatus,
    StrategyStatus,
    StrategyVersion,
    ValidationCode,
    evaluate_strategy,
    validate_strategy,
)
from tests.unit.test_experiment_execution_service import (
    NOW,
    _binding,
    _experiment,
    _LocalDataService,
    _protocol,
)
from tests.unit.test_oos_domain import OOS_END
from tests.unit.test_oos_execution_service import (
    FakeDataService,
)
from tests.unit.test_oos_execution_service import (
    _claimed as _claimed_oos,
)
from tests.unit.test_oos_execution_service import (
    _dataset as _oos_dataset,
)
from tests.unit.test_oos_execution_service import (
    _service as _oos_service,
)
from tests.unit.test_strategy_repository import _definition as repository_definition

START = date(2026, 1, 5)
FIELD = PriceField.ADJUSTED_CLOSE


class _StaticCalendar:
    calendar_id = "REGIME-TEST"

    def __init__(self, sessions: tuple[date, ...]) -> None:
        self._sessions = sessions

    def sessions(self, start: date, end: date) -> tuple[date, ...]:
        return tuple(day for day in self._sessions if start <= day <= end)


def _dataset(symbol: str, values: tuple[float, ...]) -> HistoricalDataSet:
    points = tuple(
        MarketDataPoint(
            date=START + timedelta(days=index),
            open=value,
            high=value,
            low=value,
            close=value,
            volume=100.0,
            adj_open=value,
            adj_high=value,
            adj_low=value,
            adj_close=value,
            adj_volume=100.0,
            div_cash=0.0,
            split_factor=1.0,
        )
        for index, value in enumerate(values)
    )
    return HistoricalDataSet(
        HistoricalDataRequest(symbol, points[0].date, points[-1].date, price_field_used=FIELD),
        points,
        DataSource.CACHE,
    )


def _condition(value: float = 100.0) -> Condition:
    return Condition(
        Operand("SPY", OperandType.PRICE, price_field=FIELD),
        ComparisonOperator.GREATER_OR_EQUAL,
        Operand("SPY", OperandType.CONSTANT, value=value),
    )


def _transition(
    transition_id: str,
    from_state: str,
    to_state: str,
    priority: int,
    *,
    condition: Condition | RuleGroup | None = None,
) -> RegimeTransitionDefinition:
    return RegimeTransitionDefinition(
        transition_id=transition_id,
        from_state=from_state,
        to_state=to_state,
        condition=condition or _condition(),
        priority=priority,
    )


def _definition(
    transitions: tuple[RegimeTransitionDefinition, ...],
    *,
    initial_regime: str = "idle",
    regime_order: tuple[str, ...] = ("idle", "momentum", "recovery"),
) -> StrategyDefinition:
    allocations = {
        "idle": AllocationSpecification((Allocation("SGOV", 1.0),)),
        "momentum": AllocationSpecification((Allocation("UPRO", 1.0),)),
        "recovery": AllocationSpecification((Allocation("SPY", 0.5),)),
    }
    display_names = {state_id: state_id.title() for state_id in allocations}
    return StrategyDefinition(
        strategy_id="generic-regime-test",
        name="Generic Regime Test",
        description="SPY signal and UPRO execution asset fixture",
        assets=(
            StrategyAssetReference("SPY", AssetRole.BOTH),
            StrategyAssetReference("UPRO", AssetRole.EXECUTION_ASSET),
            StrategyAssetReference("SGOV", AssetRole.EXECUTION_ASSET),
        ),
        price_field=FIELD,
        rules=(),
        fallback=FallbackAllocation((Allocation("SGOV", 1.0),)),
        rebalance_policy=RebalancePolicy(RebalanceFrequency.DAILY),
        strategy_schema_version="2.0",
        strategy_mode=StrategyEvaluationMode.REGIME_STATE_MACHINE,
        initial_regime=initial_regime,
        regimes=tuple(
            RegimeDefinition(state_id, display_names[state_id], allocations[state_id])
            for state_id in regime_order
        ),
        transitions=transitions,
    )


def _service_definition() -> StrategyDefinition:
    base = _definition(
        (
            _transition(
                "idle-to-momentum",
                "idle",
                "momentum",
                1,
                condition=RuleGroup(LogicalOperator.AND, (_condition(),)),
            ),
        )
    )
    transition_condition = RuleGroup(LogicalOperator.AND, (_condition(),))
    materialization_condition = RuleGroup(
        LogicalOperator.AND,
        (
            Condition(
                Operand("SPY", OperandType.PRICE, price_field=FIELD),
                ComparisonOperator.GREATER_THAN,
                Operand("SPY", OperandType.MA, period=2, price_field=FIELD),
            ),
        ),
    )
    return StrategyDefinition(
        **{
            **base.__dict__,
            "rules": (
                AllocationRule(
                    "materialization-anchor",
                    "Materialization Anchor",
                    1,
                    (Allocation("UPRO", 1.0),),
                    materialization_condition,
                ),
            ),
            "transitions": (
                _transition(
                    "idle-to-momentum",
                    "idle",
                    "momentum",
                    1,
                    condition=transition_condition,
                ),
            ),
        }
    )


def _version(definition: StrategyDefinition) -> StrategyVersion:
    return StrategyVersion(
        strategy_id=definition.strategy_id,
        version_id="generic-regime-test-v1",
        version_number=1,
        created_at=datetime(2026, 1, 1, tzinfo=UTC),
        configuration=definition,
        status=StrategyStatus.ACTIVE,
    )


def _context(*, spy: tuple[float, ...] = (110.0, 110.0, 90.0)) -> EvaluationContext:
    return EvaluationContext.from_components(
        {symbol: _dataset(symbol, spy) for symbol in ("SPY", "UPRO", "SGOV")}
    )


def _weekly_context() -> EvaluationContext:
    values = (110.0, 111.0, 112.0, 113.0, 114.0)
    datasets = {symbol: _dataset(symbol, values) for symbol in ("SPY", "UPRO", "SGOV")}
    sessions = tuple(point.date for point in datasets["SPY"].points)
    weekly = derive_completed_weekly(
        datasets["SPY"], calendar=_StaticCalendar(sessions), cutoff=sessions[-1]
    )
    return EvaluationContext.from_components(
        datasets,
        weekly_market_data={"SPY": weekly},
    )


def test_regime_evaluation_supports_initial_transition_and_one_hop_per_date() -> None:
    definition = _definition(
        (
            _transition("idle-to-momentum", "idle", "momentum", 10),
            _transition("momentum-to-recovery", "momentum", "recovery", 10),
        )
    )

    timeline = evaluate_strategy(_version(definition), _context(), START, START + timedelta(days=2))

    assert [item.status for item in timeline.evaluations] == [
        StrategyEvaluationStatus.EVALUATED,
        StrategyEvaluationStatus.EVALUATED,
        StrategyEvaluationStatus.EVALUATED,
    ]
    assert [item.regime_provenance["active_state_id"] for item in timeline.evaluations] == [
        "momentum",
        "recovery",
        "recovery",
    ]
    assert [item.regime_provenance["transition_count"] for item in timeline.evaluations] == [
        1,
        2,
        2,
    ]
    assert [
        item.regime_provenance["transition_event"]["transition_id"]
        if item.regime_provenance["transition_event"]
        else None
        for item in timeline.evaluations
    ] == ["idle-to-momentum", "momentum-to-recovery", None]
    assert timeline.evaluations[0].regime_provenance["transition_event"]["state_entry_date"] == (
        START.isoformat()
    )
    assert (
        timeline.evaluations[2].regime_provenance["state_entry_date"]
        == (START + timedelta(days=1)).isoformat()
    )
    assert timeline.evaluations[0].signal is not None
    assert timeline.evaluations[0].signal.regime_provenance is not None
    assert timeline.evaluations[0].signal.to_dict()["regime_provenance"]["active_state_id"] == (
        "momentum"
    )


def test_only_current_state_outgoing_edges_participate_in_priority_arbitration() -> None:
    definition = _definition(
        (
            _transition("idle-to-recovery", "idle", "recovery", 20),
            _transition("idle-to-momentum", "idle", "momentum", 10),
            _transition("momentum-to-recovery", "momentum", "recovery", 1),
        )
    )

    result = evaluate_strategy(
        _version(definition), _context(spy=(110.0, 110.0)), START, START + timedelta(days=1)
    ).evaluations

    assert result[0].regime_provenance["active_state_id"] == "momentum"
    assert result[0].regime_provenance["transition_event"]["transition_id"] == ("idle-to-momentum")
    assert result[1].regime_provenance["active_state_id"] == "recovery"
    assert result[1].regime_provenance["transition_event"]["transition_id"] == (
        "momentum-to-recovery"
    )


def test_state_transition_keeps_target_and_execution_on_separate_dates() -> None:
    version = _version(_definition((_transition("idle-to-momentum", "idle", "momentum", 1),)))
    data = {symbol: _dataset(symbol, (110.0, 110.0, 110.0)) for symbol in ("SPY", "UPRO", "SGOV")}
    timeline = evaluate_strategy(
        version, EvaluationContext.from_components(data), START, START + timedelta(days=2)
    )
    config = BacktestConfig(
        strategy_version_id=version.version_id,
        start_date=START,
        end_date=START + timedelta(days=2),
        initial_capital=1_000.0,
        price_field_used=FIELD,
        commission=CommissionPolicy(),
        rebalance_policy=BacktestRebalancePolicy(BacktestRebalanceFrequency.DAILY),
    )

    records = target_allocations_from_timeline(version, timeline, data, config)
    result = run_strategy_backtest(version, timeline, data, config)

    assert records[0].signal.date == START
    assert records[0].execution_date == START + timedelta(days=1)
    assert records[-1].submitted_to_backtest is False
    assert result.backtest_result.orders[0].signal_date == START
    assert result.backtest_result.orders[0].date == START + timedelta(days=1)


def test_state_change_with_identical_target_does_not_force_an_extra_order() -> None:
    base = _definition((_transition("idle-to-momentum", "idle", "momentum", 1),))
    same_target_regimes = tuple(
        RegimeDefinition(
            regime.state_id,
            regime.display_name,
            AllocationSpecification((Allocation("SGOV", 1.0),)),
        )
        for regime in base.regimes
    )
    version = _version(
        StrategyDefinition(
            **{
                **base.__dict__,
                "regimes": same_target_regimes,
            }
        )
    )
    data = {symbol: _dataset(symbol, (100.0, 100.0, 100.0)) for symbol in ("SPY", "UPRO", "SGOV")}
    timeline = evaluate_strategy(
        version, EvaluationContext.from_components(data), START, START + timedelta(days=2)
    )
    config = BacktestConfig(
        strategy_version_id=version.version_id,
        start_date=START,
        end_date=START + timedelta(days=2),
        initial_capital=1_000.0,
        price_field_used=FIELD,
        commission=CommissionPolicy(),
        rebalance_policy=BacktestRebalancePolicy(BacktestRebalanceFrequency.DAILY),
    )

    result = run_strategy_backtest(version, timeline, data, config)

    assert timeline.evaluations[0].regime_provenance["transition_event"] is not None
    assert [order.signal_date for order in result.backtest_result.orders] == [START]


def test_regime_transition_provenance_flows_into_persisted_execution_provenance() -> None:
    version = _version(_definition((_transition("idle-to-momentum", "idle", "momentum", 1),)))
    signal = evaluate_strategy(version, _context(), START, START).evaluations[0].signal
    assert signal is not None
    record = StrategyBacktestAllocation(
        signal=signal,
        target_allocation=TargetAllocation.from_weights(START, {"UPRO": 1.0}),
        execution_date=START + timedelta(days=1),
        submitted_to_backtest=True,
    )
    provenance = strategy_execution_provenance((record,))

    assert provenance["records"][0]["regime_provenance"]["active_state_id"] == "momentum"
    assert (
        provenance["records"][0]["regime_provenance"]["transition_event"]["strategy_version_id"]
        == version.version_id
    )


@pytest.mark.parametrize("operator", [LogicalOperator.AND, LogicalOperator.OR])
def test_daily_and_weekly_transition_evidence_is_no_lookahead_and_keeps_source_dates(
    operator: LogicalOperator,
) -> None:
    daily = _condition()
    weekly = Condition(
        Operand("SPY", OperandType.PRICE, price_field=FIELD, timeframe=Timeframe.WEEKLY),
        ComparisonOperator.GREATER_OR_EQUAL,
        Operand("SPY", OperandType.CONSTANT, value=100.0),
    )
    definition = _definition(
        (
            _transition(
                "confirmed-weekly-entry",
                "idle",
                "momentum",
                1,
                condition=RuleGroup(operator, (daily, weekly)),
            ),
        )
    )

    timeline = evaluate_strategy(
        _version(definition), _weekly_context(), START, START + timedelta(days=4)
    )

    assert all(
        item.status is StrategyEvaluationStatus.NOT_EVALUABLE for item in timeline.evaluations[:-1]
    )
    friday = timeline.evaluations[-1]
    assert friday.status is StrategyEvaluationStatus.EVALUATED
    event = friday.regime_provenance["transition_event"]
    assert event["transition_id"] == "confirmed-weekly-entry"
    conditions = event["evaluated_evidence"][0]["condition_evidence"]["child_results"]
    assert {item["left_operand_result"]["timeframe"] for item in conditions} == {
        Timeframe.DAILY.value,
        Timeframe.WEEKLY.value,
    }
    assert {item["left_operand_result"]["source_date"] for item in conditions} == {
        (START + timedelta(days=4)).isoformat()
    }
    assert all(item.regime_provenance is None for item in timeline.evaluations[:-1])


@pytest.mark.parametrize(
    ("mutator", "expected"),
    [
        (
            lambda definition: definition.__class__(
                **{
                    **definition.__dict__,
                    "initial_regime": "missing",
                }
            ),
            ValidationCode.MISSING_INITIAL_REGIME,
        ),
        (
            lambda definition: definition.__class__(
                **{
                    **definition.__dict__,
                    "transitions": (_transition("self", "idle", "idle", 1),),
                }
            ),
            ValidationCode.SELF_REGIME_TRANSITION,
        ),
        (
            lambda definition: definition.__class__(
                **{
                    **definition.__dict__,
                    "transitions": (
                        _transition("first", "idle", "momentum", 1),
                        _transition("second", "idle", "recovery", 1),
                    ),
                }
            ),
            ValidationCode.REGIME_PRIORITY_CONFLICT,
        ),
    ],
)
def test_regime_graph_validation_rejects_invalid_definitions(mutator, expected) -> None:
    result = validate_strategy(mutator(_definition(())))

    assert expected in {issue.code for issue in result.errors}


def test_regime_definition_order_is_canonical_and_affects_hash_only_when_semantics_change() -> None:
    transitions = (
        _transition("idle-to-momentum", "idle", "momentum", 10),
        _transition("momentum-to-recovery", "momentum", "recovery", 20),
    )
    first = _version(_definition(transitions))
    reordered = _version(
        _definition(tuple(reversed(transitions)), regime_order=("recovery", "momentum", "idle"))
    )
    changed = _version(
        _definition(
            (
                _transition("idle-to-momentum", "idle", "momentum", 11),
                _transition("momentum-to-recovery", "momentum", "recovery", 20),
            )
        )
    )

    assert first.content_hash == reordered.content_hash
    assert first.to_json() == reordered.to_json()
    assert first.content_hash != changed.content_hash


def test_materialization_preserves_regime_graph_and_can_bind_regime_allocation() -> None:
    base = _version(_definition((_transition("idle-to-momentum", "idle", "momentum", 10),)))
    binding = ParameterBinding(
        "momentum_weight",
        "regimes[1].target_allocation.allocations[0].target_weight",
        BindingValueType.ALLOCATION_WEIGHT,
        "phase-8d-1-v1",
    )

    derived = materialize_strategy_version(
        base, ParameterSet({"momentum_weight": 0.75}), (binding,)
    )

    assert derived is not base
    assert derived.configuration.strategy_mode is StrategyEvaluationMode.REGIME_STATE_MACHINE
    assert derived.configuration.initial_regime == base.configuration.initial_regime
    assert derived.configuration.transitions == base.configuration.transitions
    assert derived.configuration.regimes[0] == base.configuration.regimes[0]
    assert derived.configuration.regimes[1].target_allocation.allocations[0].target_weight == 0.75
    assert derived.content_hash != base.content_hash


def test_experiment_execution_reuses_regime_evaluator_and_persists_provenance(tmp_path) -> None:
    database = tmp_path / "regime-experiment.db"
    protocols = ResearchProtocolRepository(database)
    strategies = StrategyRepository(database)
    experiments = ExperimentRepository(database)
    executions = CandidateExecutionRepository(database)
    protocol = _protocol()
    protocols.create_protocol(protocol)
    version = strategies.create(_service_definition())
    candidate_set = CandidateSet(
        candidate_set_id="regime-candidates",
        protocol_id=protocol.protocol_id,
        strategy_version_ids=(version.version_id,),
        strategy_version_content_hashes={version.version_id: version.content_hash or ""},
        created_at=NOW,
    )
    protocols.create_candidate_set(candidate_set)
    protocols.lock_candidate_set(candidate_set.candidate_set_id)
    protocols.transition_protocol(protocol.protocol_id, ProtocolStatus.FROZEN)
    experiment = _experiment(version)
    experiments.create(experiment, generate_candidates(experiment))
    service = ExperimentExecutionService(
        experiment_repository=experiments,
        protocol_repository=protocols,
        strategy_repository=strategies,
        candidate_execution_repository=executions,
        data_service=_LocalDataService(),
        clock=lambda: NOW,
    )

    outcome = service.execute(
        experiment_id=experiment.experiment_id,
        candidate_index=0,
        parameter_bindings=(_binding(),),
    )

    assert outcome.status is ExperimentExecutionOutcomeStatus.COMPLETED
    assert outcome.backtest_result is not None
    evaluations = outcome.backtest_result.evaluation_timeline.evaluations
    assert evaluations[0].signal is not None
    assert evaluations[0].signal.regime_provenance["active_state_id"] == "momentum"
    assert outcome.backtest_result.signal_records[0].signal.regime_provenance is not None


def test_oos_execution_reuses_frozen_regime_evaluator_and_provenance(tmp_path) -> None:
    condition = Condition(
        Operand("QQQ", OperandType.PRICE, price_field=FIELD),
        ComparisonOperator.GREATER_OR_EQUAL,
        Operand("QQQ", OperandType.CONSTANT, value=100.0),
    )
    base = repository_definition("oos-strategy")
    definition = replace(
        base,
        strategy_schema_version="2.0",
        strategy_mode=StrategyEvaluationMode.REGIME_STATE_MACHINE,
        initial_regime="defensive",
        regimes=(
            RegimeDefinition(
                "defensive", "Defensive", AllocationSpecification((Allocation("QQQ", 1.0),))
            ),
            RegimeDefinition(
                "levered", "Levered", AllocationSpecification((Allocation("TQQQ", 1.0),))
            ),
        ),
        transitions=(
            RegimeTransitionDefinition(
                "defensive-to-levered", "defensive", "levered", condition, 1
            ),
        ),
    )
    version = StrategyVersion(
        strategy_id=definition.strategy_id,
        version_id="oos-regime-v1",
        version_number=1,
        created_at=datetime(2026, 9, 13, tzinfo=UTC),
        configuration=definition,
    )
    version, protocols, executions, spec, claimed = _claimed_oos(tmp_path, version=version)
    data = {
        asset.symbol: _oos_dataset(asset.symbol, end=OOS_END)
        for asset in version.configuration.assets
    }
    service = _oos_service(
        tmp_path,
        FakeDataService(data),
        execution_repository=executions,
        protocol_repository=protocols,
        version=version,
    )

    outcome = service.execute(
        protocol_id="protocol-8f1",
        execution_id=claimed.execution_id,
        lease_token=claimed.lease_token,
        spec=spec,
    )

    assert outcome.strategy_provenance is not None
    assert outcome.strategy_provenance["records"][0]["regime_provenance"] is not None
    assert (
        outcome.strategy_provenance["records"][0]["regime_provenance"]["active_state_id"]
        == "levered"
    )
