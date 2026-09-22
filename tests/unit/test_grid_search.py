from __future__ import annotations

from dataclasses import replace

import pytest

from backend.app.backtest_repository import BacktestRepository
from backend.app.candidate_execution_repository import CandidateExecutionRepository
from backend.app.experiment_repository import ExperimentRepository
from backend.app.experiment_result_repository import ExperimentResultRepository
from backend.app.grid_search_repository import GridSearchRepository
from backend.app.research_protocol import CandidateSet, ProtocolStatus, ResearchProtocolRepository
from backend.app.strategy_repository import StrategyRepository
from research import (
    BacktestBindingValueType,
    BacktestParameterBinding,
    BoundConstraint,
    BudgetConstraint,
    ConstraintOperator,
    GridPreflightStatus,
    GridSearchDefinition,
    MonotonicConstraint,
    OrderingConstraint,
    ParameterBinding,
    ParameterDefinition,
    ParameterSet,
    ParameterSpace,
    ParameterType,
    SumConstraint,
    constraint_from_dict,
    materialize_backtest_config,
    preflight_grid_search,
)
from research.canonical import canonical_json
from research.enums import ExperimentStatus
from research.execution import CandidateExecutionStatus
from research.execution_service import ExperimentExecutionService
from research.grid_search_service import GridSearchService
from research.result_finalization_service import ExperimentResultFinalizationService
from tests.unit.test_experiment_execution_service import (
    NOW,
    _binding,
    _definition,
    _experiment,
    _LocalDataService,
    _protocol,
)


def _backtest_binding(
    name: str = "minimum_change", field: str = "minimum_allocation_change"
) -> BacktestParameterBinding:
    value_type = (
        BacktestBindingValueType.UNIT_INTERVAL
        if field == "minimum_cash_reserve"
        else BacktestBindingValueType.OPTIONAL_NON_NEGATIVE_FLOAT
    )
    return BacktestParameterBinding(
        name,
        f"position_rebalance_policy.{field}",
        value_type,
    )


def _replace_space(experiment, space: ParameterSpace):
    provenance = replace(
        experiment.provenance,
        parameter_space_hash=space.content_hash,
    )
    return replace(experiment, parameter_space=space, provenance=provenance)


def _grid_definition(experiment, *, strategy=(), backtest=(), constraints=(), maximum=100):
    return GridSearchDefinition(
        experiment_id=experiment.experiment_id,
        experiment_hash=experiment.content_hash,
        strategy_bindings=tuple(strategy),
        backtest_bindings=tuple(backtest),
        fixed_parameters={
            "signal_asset": "QQQ",
            "execution_assets": ["QQQ", "TQQQ"],
            "price_field": "adjusted_close",
        },
        constraints=tuple(constraints),
        max_candidates=maximum,
    )


def _base_experiment(tmp_path, *, space: ParameterSpace | None = None):
    version = StrategyRepository(tmp_path / "grid.db").create(_definition())
    experiment = _experiment(version)
    if space is not None:
        experiment = _replace_space(experiment, space)
    return version, experiment


def test_single_and_multiple_parameter_expansion_is_deterministic(tmp_path) -> None:
    space = ParameterSpace(
        parameters=(
            ParameterDefinition("period", ParameterType.INTEGER, min=2, max=3, step=1),
            ParameterDefinition(
                "minimum_change", ParameterType.DISCRETE, allowed_values=(0.05, 0.03)
            ),
        ),
        max_candidates=4,
    )
    version, experiment = _base_experiment(tmp_path, space=space)
    definition = _grid_definition(
        experiment, strategy=(_binding(),), backtest=(_backtest_binding(),)
    )

    first = preflight_grid_search(experiment, definition, version)
    second = preflight_grid_search(experiment, definition, version)

    assert first.status is GridPreflightStatus.READY
    assert first.theoretical_count == first.valid_count == first.executable_count == 4
    assert first.to_dict() == second.to_dict()
    assert tuple(plan.parameter_set.values for plan in first.plans) == (
        {"minimum_change": 0.03, "period": 2},
        {"minimum_change": 0.03, "period": 3},
        {"minimum_change": 0.05, "period": 2},
        {"minimum_change": 0.05, "period": 3},
    )


def test_parameter_input_order_and_float_expansion_have_stable_identity(tmp_path) -> None:
    first_space = ParameterSpace(
        parameters=(
            ParameterDefinition("period", ParameterType.INTEGER, min=2, max=2, step=1),
            ParameterDefinition(
                "minimum_change",
                ParameterType.FLOAT,
                min=0.1,
                max=0.3,
                step=0.1,
                precision=1,
            ),
        ),
        max_candidates=3,
    )
    second_space = ParameterSpace(
        parameters=tuple(reversed(first_space.parameters)), max_candidates=3
    )
    version, first_experiment = _base_experiment(tmp_path, space=first_space)
    second_experiment = _replace_space(first_experiment, second_space)
    first_definition = _grid_definition(
        first_experiment, strategy=(_binding(),), backtest=(_backtest_binding(),)
    )
    second_definition = _grid_definition(
        second_experiment, strategy=(_binding(),), backtest=(_backtest_binding(),)
    )

    first = preflight_grid_search(first_experiment, first_definition, version)
    second = preflight_grid_search(second_experiment, second_definition, version)

    assert [item.parameter_set.content_hash for item in first.plans] == [
        item.parameter_set.content_hash for item in second.plans
    ]
    assert [item.parameter_set.values["minimum_change"] for item in first.plans] == [
        0.1,
        0.2,
        0.3,
    ]


def test_structured_constraints_prune_before_candidate_limit(tmp_path) -> None:
    space = ParameterSpace(
        parameters=(
            ParameterDefinition("qqq", ParameterType.DISCRETE, allowed_values=(0.4, 0.8)),
            ParameterDefinition("tqqq", ParameterType.DISCRETE, allowed_values=(0.2, 0.6)),
            ParameterDefinition("reserve", ParameterType.DISCRETE, allowed_values=(0.0, 0.2)),
        ),
        max_candidates=8,
    )
    version, experiment = _base_experiment(tmp_path, space=space)
    bindings = (
        ParameterBinding("qqq", "fallback.allocations[0].target_weight", "allocation_weight", "v1"),
        ParameterBinding(
            "tqqq", "rules[0].allocations[0].target_weight", "allocation_weight", "v1"
        ),
    )
    definition = _grid_definition(
        experiment,
        strategy=bindings,
        backtest=(_backtest_binding("reserve", "minimum_cash_reserve"),),
        constraints=(
            BoundConstraint("reserve", minimum=0.0, maximum=0.2),
            SumConstraint(("qqq", "tqqq"), ConstraintOperator.LESS_OR_EQUAL, 1.0),
            BudgetConstraint(("qqq", "tqqq"), reserve_parameter="reserve"),
        ),
        maximum=4,
    )

    result = preflight_grid_search(experiment, definition, version)

    assert result.theoretical_count == 8
    assert result.pruned_count == 4
    assert result.valid_count == result.executable_count == 4
    assert result.can_execute
    assert result.pruning_reasons == {"BUDGET_CONSTRAINT_FAILED": 4}


@pytest.mark.parametrize(
    ("constraint", "expected"),
    [
        (OrderingConstraint("deep", ConstraintOperator.GREATER_OR_EQUAL, "value"), True),
        (MonotonicConstraint(("approaching", "value", "deep")), True),
        (OrderingConstraint("approaching", ConstraintOperator.GREATER_THAN, "deep"), False),
    ],
)
def test_ordering_and_monotonic_constraints(constraint, expected: bool) -> None:
    values = {"approaching": 0.2, "value": 0.3, "deep": 0.4}
    assert constraint.evaluate(values) is expected
    assert "expression" not in canonical_json(constraint.to_dict())


def test_semantic_duplicate_is_not_executed_twice(tmp_path) -> None:
    space = ParameterSpace(
        parameters=(
            ParameterDefinition("minimum_change", ParameterType.DISCRETE, allowed_values=(0, 0.0)),
        ),
        max_candidates=2,
    )
    version, experiment = _base_experiment(tmp_path, space=space)
    definition = _grid_definition(experiment, backtest=(_backtest_binding(),), maximum=2)

    result = preflight_grid_search(experiment, definition, version)

    assert result.theoretical_count == 2
    assert result.valid_count == 2
    assert result.duplicate_count == 1
    assert result.executable_count == 1
    assert result.duplicate_samples[0]["canonical_candidate_index"] == 0


def test_over_limit_fails_without_silent_truncation(tmp_path) -> None:
    space = ParameterSpace(
        parameters=(ParameterDefinition("period", ParameterType.INTEGER, min=2, max=4, step=1),),
        max_candidates=3,
    )
    version, experiment = _base_experiment(tmp_path, space=space)
    definition = _grid_definition(experiment, strategy=(_binding(),), maximum=2)

    result = preflight_grid_search(experiment, definition, version)

    assert not result.can_execute
    assert result.executable_count == 3
    assert result.issues == ("VALID_CANDIDATE_COUNT_EXCEEDS_LIMIT",)


def test_theoretical_explosion_fails_before_enumeration(tmp_path) -> None:
    definitions = tuple(
        ParameterDefinition(
            f"parameter_{index}", ParameterType.DISCRETE, allowed_values=tuple(range(10))
        )
        for index in range(10)
    )
    space = ParameterSpace(parameters=definitions, max_candidates=1000)
    version, experiment = _base_experiment(tmp_path, space=space)
    bindings = tuple(
        BacktestParameterBinding(
            item.name,
            # Repeated paths are rejected before preflight, so use the domain only
            # to prove the theoretical guard through a deliberately partial definition.
            "position_rebalance_policy.minimum_allocation_change",
            BacktestBindingValueType.OPTIONAL_NON_NEGATIVE_FLOAT,
        )
        for item in definitions[:1]
    )
    definition = _grid_definition(experiment, backtest=bindings, maximum=1000)

    # Binding coverage is checked only after the constant-time theoretical guard.
    result = preflight_grid_search(experiment, definition, version)

    assert result.theoretical_count == 10_000_000_000
    assert result.issues == ("THEORETICAL_COUNT_EXCEEDS_GUARD",)
    assert not result.plans


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("minimum_allocation_change", 0.03),
        ("drift_threshold", 0.04),
        ("maximum_turnover", 0.25),
        ("minimum_cash_reserve", 0.1),
    ],
)
def test_backtest_policy_materialization_is_immutable(tmp_path, field: str, value: float) -> None:
    version, experiment = _base_experiment(tmp_path)
    binding = _backtest_binding("policy_value", field)
    derived = materialize_backtest_config(
        experiment.backtest_configuration,
        ParameterSet({"policy_value": value}),
        (binding,),
    )

    assert getattr(derived.position_rebalance_policy, field) == value
    assert getattr(experiment.backtest_configuration.position_rebalance_policy, field) in {
        None,
        0.0,
    }


def _service_setup(tmp_path, data_service=None):
    database = tmp_path / "grid-service.db"
    protocols = ResearchProtocolRepository(database)
    strategies = StrategyRepository(database)
    experiments = ExperimentRepository(database)
    executions = CandidateExecutionRepository(database)
    results = ExperimentResultRepository(database)
    grids = GridSearchRepository(database)
    backtests = BacktestRepository(database)
    protocol = _protocol()
    protocols.create_protocol(protocol)
    version = strategies.create(_definition())
    phase7_candidates = CandidateSet(
        candidate_set_id="protocol-candidates-11h",
        protocol_id=protocol.protocol_id,
        strategy_version_ids=(version.version_id,),
        strategy_version_content_hashes={version.version_id: version.content_hash or ""},
        created_at=NOW,
    )
    protocols.create_candidate_set(phase7_candidates)
    protocols.lock_candidate_set(phase7_candidates.candidate_set_id)
    protocols.transition_protocol(protocol.protocol_id, ProtocolStatus.FROZEN)
    experiment = _experiment(version, parameter_max=3)
    experiments.create(experiment)
    executor = ExperimentExecutionService(
        experiment_repository=experiments,
        protocol_repository=protocols,
        strategy_repository=strategies,
        candidate_execution_repository=executions,
        data_service=data_service or _LocalDataService(),
        clock=lambda: NOW,
    )
    finalizer = ExperimentResultFinalizationService(
        backtest_repository=backtests,
        experiment_result_repository=results,
        candidate_execution_repository=executions,
        experiment_repository=experiments,
        clock=lambda: NOW,
    )
    service = GridSearchService(
        experiment_repository=experiments,
        strategy_repository=strategies,
        execution_repository=executions,
        result_repository=results,
        grid_repository=grids,
        execution_service=executor,
        finalization_service=finalizer,
        clock=lambda: NOW,
    )
    definition = _grid_definition(experiment, strategy=(_binding(),), maximum=2)
    return service, experiments, executions, results, grids, backtests, experiment, definition


def test_small_grid_executes_is_only_persists_runs_metadata_and_summary(tmp_path) -> None:
    service, experiments, executions, results, grids, backtests, experiment, definition = (
        _service_setup(tmp_path)
    )

    prepared = service.prepare(experiment.experiment_id, definition)
    completed = service.execute(experiment.experiment_id)

    assert prepared["theoretical_count"] == prepared["executable_count"] == 2
    assert completed["status"] == "completed"
    assert completed["completed"] == 2
    assert completed["progress_percentage"] == 100.0
    assert experiments.get(experiment.experiment_id).status is ExperimentStatus.COMPLETED
    assert len(results.list(experiment.experiment_id)) == 2
    assert all(
        executions.get_execution_by_candidate(experiment.experiment_id, index).status
        is CandidateExecutionStatus.COMPLETED
        for index in range(2)
    )
    assert len(backtests.list_records()) == 2
    assert backtests.list_metadata(limit=10, offset=0).total == 2
    assert results.list_summaries(experiment.experiment_id)[0] == 2
    assert grids.progress(experiment.experiment_id)["completed"] == 2

    replay = service.execute(experiment.experiment_id)
    assert replay == completed
    assert len(backtests.list_records()) == 2


def test_retryable_candidate_failure_isolated_and_recovered(tmp_path) -> None:
    data = _LocalDataService(timeout_once=True)
    service, experiments, _, results, _, _, experiment, definition = _service_setup(tmp_path, data)
    service.prepare(experiment.experiment_id, definition)

    first = service.execute(experiment.experiment_id)

    assert first["failed"] == 0
    assert first["retryable"] == 1
    assert first["pending"] == 0
    assert first["progress_percentage"] == 50.0
    assert first["completed"] == 1
    assert experiments.get(experiment.experiment_id).status is ExperimentStatus.RUNNING
    second = service.execute(experiment.experiment_id)
    assert second["status"] == "completed"
    assert second["completed"] == 2
    assert len(results.list(experiment.experiment_id)) == 2


def test_cancel_before_execution_preserves_candidates_and_prevents_restart(tmp_path) -> None:
    service, experiments, _, results, _, _, experiment, definition = _service_setup(tmp_path)
    service.prepare(experiment.experiment_id, definition)

    cancelled = service.cancel(experiment.experiment_id)
    replay = service.execute(experiment.experiment_id)

    assert cancelled["status"] == "cancelled"
    assert cancelled["cancelled"] == 2
    assert replay["status"] == "cancelled"
    assert experiments.get(experiment.experiment_id).status is ExperimentStatus.CANCELLED
    assert results.list(experiment.experiment_id) == ()


def test_grid_definition_rejects_arbitrary_backtest_path_and_nonfinite_values() -> None:
    with pytest.raises(Exception, match="whitelist"):
        BacktestParameterBinding(
            "unsafe", "__class__.__mro__", BacktestBindingValueType.NON_NEGATIVE_FLOAT
        )
    with pytest.raises(Exception, match="finite"):
        ParameterSet({"unsafe": float("nan")})
    with pytest.raises(Exception, match="executable"):
        constraint_from_dict({"constraint_type": "sum", "expression": "a + b <= 1"})


def test_generic_spy_upro_sgov_regime_candidate_is_ticker_agnostic() -> None:
    from tests.unit.test_regime_state_machine import _service_definition, _version

    version = _version(_service_definition())
    base = _experiment(version)
    space = ParameterSpace(
        parameters=(ParameterDefinition("period", ParameterType.INTEGER, min=2, max=3, step=1),),
        max_candidates=2,
    )
    experiment = _replace_space(base, space)
    binding = ParameterBinding(
        "period",
        "rules[0].condition.children[0].right.period",
        "positive_integer",
        "phase-11h-v1",
    )
    definition = GridSearchDefinition(
        experiment.experiment_id,
        experiment.content_hash,
        strategy_bindings=(binding,),
        fixed_parameters={
            "signal_asset": "SPY",
            "execution_assets": ["SPY", "UPRO", "SGOV"],
        },
        max_candidates=2,
    )

    result = preflight_grid_search(experiment, definition, version)

    assert result.can_execute
    assert result.executable_count == 2
    assert {asset.symbol for asset in version.configuration.assets} == {"SPY", "UPRO", "SGOV"}


def test_value_zone_threshold_candidate_reuses_state_machine_materializer() -> None:
    from tests.unit.test_value_zones import _strategy, _version

    version = _version(_strategy("SPY", "UPRO", "SGOV"))
    base = _experiment(version)
    space = ParameterSpace(
        parameters=(
            ParameterDefinition("value_entry", ParameterType.DISCRETE, allowed_values=(0.04, 0.05)),
        ),
        max_candidates=2,
    )
    experiment = _replace_space(base, space)
    binding = ParameterBinding(
        "value_entry",
        "value_zones[2].entry_threshold",
        "relative_threshold",
        "phase-11h-v1",
    )
    definition = GridSearchDefinition(
        experiment.experiment_id,
        experiment.content_hash,
        strategy_bindings=(binding,),
        fixed_parameters={"strategy_mode": "regime_state_machine"},
        max_candidates=2,
    )

    result = preflight_grid_search(experiment, definition, version)

    assert result.can_execute
    assert result.executable_count == 2


class _NonRetryableFailureOnceData(_LocalDataService):
    def __init__(self) -> None:
        super().__init__()
        self._failed = False

    def get_history(self, request):
        if not self._failed:
            self._failed = True
            raise LookupError("candidate-local fixture failure")
        return super().get_history(request)


def test_nonretryable_candidate_failure_does_not_discard_or_block_later_result(tmp_path) -> None:
    service, experiments, _, results, _, _, experiment, definition = _service_setup(
        tmp_path, _NonRetryableFailureOnceData()
    )
    service.prepare(experiment.experiment_id, definition)

    progress = service.execute(experiment.experiment_id)

    assert progress["failed"] == 1
    assert progress["completed"] == 1
    assert progress["status"] == "completed"
    assert experiments.get(experiment.experiment_id).status is ExperimentStatus.COMPLETED
    assert len(results.list(experiment.experiment_id)) == 1


def test_completed_execution_without_result_is_recovered_without_duplicate_run(tmp_path) -> None:
    service, experiments, executions, results, _, backtests, experiment, definition = (
        _service_setup(tmp_path)
    )
    service.prepare(experiment.experiment_id, definition)
    experiments.transition_status(
        experiment.experiment_id,
        ExperimentStatus.CANDIDATES_GENERATED,
        ExperimentStatus.RUNNING,
        "TEST_GRID_STARTED",
        "test-grid-started",
    )
    orphaned = service._executor.execute(
        experiment_id=experiment.experiment_id,
        candidate_index=0,
        parameter_bindings=definition.strategy_bindings,
        claimed_by="crash-window-fixture",
    )
    assert orphaned.succeeded
    assert results.list(experiment.experiment_id) == ()

    progress = service.execute(experiment.experiment_id)

    assert progress["completed"] == 2
    assert len(backtests.list_records()) == 2
    events = executions.list_events(orphaned.candidate_execution_id or "")
    assert "EXECUTION_UNFINALIZED_RECOVERED" in [item.event_type for item in events]


def test_cancel_after_first_candidate_finishes_starts_no_second_candidate(tmp_path) -> None:
    service, experiments, executions, results, grids, _, experiment, definition = _service_setup(
        tmp_path
    )
    service.prepare(experiment.experiment_id, definition)
    real_executor = service._executor

    class _CancelAfterExecute:
        def execute(self, **kwargs):
            outcome = real_executor.execute(**kwargs)
            grids.request_cancel(experiment.experiment_id)
            return outcome

    service._executor = _CancelAfterExecute()
    progress = service.execute(experiment.experiment_id)

    assert progress["status"] == "cancelled"
    assert progress["completed"] == 1
    assert progress["cancelled"] == 1
    assert len(results.list(experiment.experiment_id)) == 1
    assert executions.get_execution_by_candidate(experiment.experiment_id, 1) is None
    assert experiments.get(experiment.experiment_id).status is ExperimentStatus.CANCELLED
