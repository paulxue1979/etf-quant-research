from __future__ import annotations

from dataclasses import replace
from datetime import timedelta

import pytest

from backend.app.strategy_repository import StrategyRepository
from backtest import (
    BacktestConfig,
    CommissionPolicy,
    run_strategy_backtest,
    target_allocations_from_timeline,
)
from backtest import RebalanceFrequency as BacktestRebalanceFrequency
from backtest import RebalancePolicy as BacktestRebalancePolicy
from data.models import PriceField
from indicators.peak_drawdown import peak_drawdown
from indicators.preparation import prepare_strategy_inputs
from research.data_availability import project_data_availability, warmup_start_for_strategy
from research.experiments import ParameterSet
from research.semantic_bindings import (
    discover_semantic_bindings,
    materialize_semantic_strategy_version,
)
from strategies import (
    Allocation,
    AllocationRule,
    AllocationSpecification,
    ComparisonOperator,
    Condition,
    EvaluationContext,
    LogicalOperator,
    Operand,
    OperandType,
    RegimeDefinition,
    RuleGroup,
    StrategyEvaluationMode,
    evaluate_strategy,
    required_indicators,
    validate_strategy,
)
from tests.unit.test_regime_state_machine import (
    FIELD,
    START,
    _dataset,
    _definition,
    _transition,
    _version,
)


def _peak_condition(threshold: float, field: PriceField = FIELD) -> Condition:
    return Condition(
        Operand("SPY", OperandType.PEAK_DRAWDOWN, price_field=field),
        ComparisonOperator.LESS_OR_EQUAL,
        Operand("SPY", OperandType.CONSTANT, value=threshold),
    )


def _entry_version():
    original_false = Condition(
        Operand("SPY", OperandType.PRICE, price_field=FIELD),
        ComparisonOperator.GREATER_THAN,
        Operand("SPY", OperandType.CONSTANT, value=1000),
    )
    value = _transition(
        "idle-to-value",
        "idle",
        "VALUE",
        1,
        condition=RuleGroup(LogicalOperator.OR, (original_false, _peak_condition(-0.2))),
    )
    deep = _transition(
        "value-to-deep",
        "VALUE",
        "DEEP_VALUE",
        1,
        condition=RuleGroup(LogicalOperator.OR, (original_false, _peak_condition(-0.3))),
    )
    exit_edge = _transition(
        "deep-to-recovery", "DEEP_VALUE", "RECOVERY_HOLD", 1, condition=original_false
    )
    base = _definition(())
    allocations = AllocationSpecification((Allocation("SGOV", 1.0),))
    definition = replace(
        base,
        regimes=tuple(
            RegimeDefinition(state, state, allocations)
            for state in ("idle", "VALUE", "DEEP_VALUE", "RECOVERY_HOLD")
        ),
        transitions=(value, deep, exit_edge),
    )
    return _version(definition)


def test_peak_drawdown_uses_running_close_peak_and_resets_at_new_high() -> None:
    data = _dataset("SPY", (100, 90, 80, 70, 110, 99))
    series = peak_drawdown(data, price_field=FIELD)
    assert [point.value for point in series.points] == pytest.approx([0, -0.1, -0.2, -0.3, 0, -0.1])


def test_peak_drawdown_inherits_price_field_and_never_reads_future() -> None:
    data = _dataset("SPY", (100, 80, 70, 60))
    adjusted = replace(
        data,
        points=tuple(
            replace(
                point,
                adj_open=point.open * 2,
                adj_high=point.high * 2,
                adj_low=point.low * 2,
                adj_close=point.close * 2,
            )
            for point in data.points
        ),
    )
    original = peak_drawdown(adjusted, price_field=FIELD)
    changed = replace(
        adjusted,
        points=adjusted.points[:-1] + (replace(adjusted.points[-1], adj_high=200, adj_close=200),),
    )
    assert original.points[:3] == peak_drawdown(changed, price_field=FIELD).points[:3]
    assert original.points[2].value == pytest.approx(-0.3)
    raw_request = replace(adjusted.request, price_field_used=PriceField.RAW_CLOSE)
    raw = replace(adjusted, request=raw_request)
    assert peak_drawdown(raw, price_field=PriceField.RAW_CLOSE).points[2].value == pytest.approx(
        -0.3
    )
    different_adjusted = replace(
        raw,
        request=adjusted.request,
        points=raw.points[:1]
        + (replace(raw.points[1], adj_open=150, adj_high=150, adj_low=150, adj_close=150),)
        + raw.points[2:],
    )
    assert peak_drawdown(different_adjusted, price_field=FIELD).points[1].value == pytest.approx(
        -0.25
    )
    assert peak_drawdown(raw, price_field=PriceField.RAW_CLOSE).points[1].value == pytest.approx(
        -0.2
    )
    with pytest.raises(ValueError, match="price field"):
        peak_drawdown(raw, price_field=FIELD)


def test_entry_or_condition_uses_history_before_backtest_start_and_one_transition_per_day() -> None:
    version = _entry_version()
    assert validate_strategy(version.configuration).is_valid
    spy = _dataset("SPY", (100, 100, 80, 70, 85))
    data = {symbol: _dataset(symbol, (100, 100, 80, 70, 85)) for symbol in ("SPY", "UPRO", "SGOV")}
    data["SPY"] = spy
    prepared = prepare_strategy_inputs(version, data, price_field=FIELD)
    context = EvaluationContext.from_components(data, prepared.indicators)
    timeline = evaluate_strategy(
        version, context, START + timedelta(days=2), START + timedelta(days=4)
    )
    assert timeline.evaluations[0].regime_provenance["active_state_id"] == "VALUE"
    assert timeline.evaluations[1].regime_provenance["active_state_id"] == "DEEP_VALUE"
    assert timeline.evaluations[2].regime_provenance["active_state_id"] == "DEEP_VALUE"
    assert warmup_start_for_strategy(START + timedelta(days=2), version).year == 1900


def test_peak_entry_keeps_signal_on_close_and_execution_on_next_open() -> None:
    version = _entry_version()
    data = {symbol: _dataset(symbol, (100, 100, 80, 70, 85)) for symbol in ("SPY", "UPRO", "SGOV")}
    prepared = prepare_strategy_inputs(version, data, price_field=FIELD)
    start = START + timedelta(days=2)
    end = START + timedelta(days=4)
    timeline = evaluate_strategy(
        version, EvaluationContext.from_components(data, prepared.indicators), start, end
    )
    config = BacktestConfig(
        strategy_version_id=version.version_id,
        start_date=start,
        end_date=end,
        initial_capital=100_000,
        price_field_used=FIELD,
        commission=CommissionPolicy(),
        rebalance_policy=BacktestRebalancePolicy(BacktestRebalanceFrequency.DAILY),
    )
    records = target_allocations_from_timeline(version, timeline, data, config)
    result = run_strategy_backtest(version, timeline, data, config)
    assert records[0].signal.date == start
    assert records[0].execution_date == start + timedelta(days=1)
    assert result.backtest_result is not None


def test_peak_drawdown_rejected_for_rule_or_exit() -> None:
    version = _entry_version()
    exit_edge = replace(version.configuration.transitions[2], condition=_peak_condition(-0.2))
    invalid = replace(
        version.configuration, transitions=version.configuration.transitions[:2] + (exit_edge,)
    )
    assert any(issue.code == "InvalidCondition" for issue in validate_strategy(invalid).errors)
    rule = replace(
        version.configuration,
        strategy_mode=StrategyEvaluationMode.RULE_BASED,
        rules=(
            AllocationRule(
                "bad-peak",
                "Bad peak",
                1,
                (Allocation("SGOV", 1.0),),
                _peak_condition(-0.2),
            ),
        ),
    )
    assert any(issue.code == "InvalidCondition" for issue in validate_strategy(rule).errors)


def test_peak_drawdown_cannot_turn_deep_value_exit_into_value_entry() -> None:
    definition = _entry_version().configuration
    exit_edge = replace(
        definition.transitions[2],
        to_state="VALUE",
        condition=_peak_condition(-0.2),
    )
    invalid = replace(
        definition,
        transitions=definition.transitions[:2] + (exit_edge,),
    )
    assert any(issue.code == "InvalidCondition" for issue in validate_strategy(invalid).errors)


@pytest.mark.parametrize("initial_state", ["idle", "VALUE"])
@pytest.mark.parametrize("original_threshold,expected_entry", [(50, True), (1000, False)])
def test_original_or_peak_entry_truth_table_without_drawdown(
    initial_state: str, original_threshold: float, expected_entry: bool
) -> None:
    base = _entry_version()
    edge_index = 0 if initial_state == "idle" else 1
    edge = base.configuration.transitions[edge_index]
    original = edge.condition.children[0]
    changed_original = replace(
        original, right=Operand("SPY", OperandType.CONSTANT, value=original_threshold)
    )
    changed_edge = replace(
        edge,
        condition=replace(edge.condition, children=(changed_original, edge.condition.children[1])),
    )
    transitions = tuple(
        changed_edge if index == edge_index else item
        for index, item in enumerate(base.configuration.transitions)
    )
    definition = replace(base.configuration, initial_regime=initial_state, transitions=transitions)
    version = _version(definition)
    data = {symbol: _dataset(symbol, (100, 100)) for symbol in ("SPY", "UPRO", "SGOV")}
    prepared = prepare_strategy_inputs(version, data, price_field=FIELD)
    context = EvaluationContext.from_components(data, prepared.indicators)
    result = evaluate_strategy(version, context, START, START).evaluations[0]
    expected_state = (
        ("VALUE" if initial_state == "idle" else "DEEP_VALUE") if expected_entry else initial_state
    )
    assert result.regime_provenance["active_state_id"] == expected_state


def test_deep_threshold_shallower_than_value_is_warning_only() -> None:
    base = _entry_version().configuration
    deep = base.transitions[1]
    peak = deep.condition.children[1]
    changed = replace(peak, right=Operand("SPY", OperandType.CONSTANT, value=-0.1))
    deep_condition = replace(deep.condition, children=(deep.condition.children[0], changed))
    definition = replace(
        base,
        transitions=(
            base.transitions[0],
            replace(deep, condition=deep_condition),
            base.transitions[2],
        ),
    )
    result = validate_strategy(definition)
    assert result.is_valid
    assert any("shallower" in issue.message for issue in result.warnings)


def test_peak_threshold_is_discovered_and_materialized_without_changing_base() -> None:
    version = _entry_version()
    catalog = discover_semantic_bindings(version)
    peak = [
        binding
        for binding in catalog.supported
        if binding.descriptor.target_kind == "peak_drawdown_threshold"
    ]
    assert len(peak) == 2
    selected = peak[0]
    derived = materialize_semantic_strategy_version(
        version, ParameterSet({selected.parameter_name: -0.18}), (selected,)
    )
    assert derived.content_hash != version.content_hash
    assert version.configuration.transitions[0].condition.children[1].right.value == -0.2
    target = next(
        transition
        for transition in derived.configuration.transitions
        if transition.transition_id == selected.descriptor.transition_id
    )
    assert target.condition.children[1].right.value == -0.18


def test_peak_requirement_ignores_inactive_rule_draft() -> None:
    version = _entry_version()
    inactive = AllocationRule(
        "inactive-peak",
        "Inactive peak",
        1,
        (Allocation("SGOV", 1.0),),
        Condition(
            Operand("UPRO", OperandType.PEAK_DRAWDOWN, price_field=FIELD),
            ComparisonOperator.LESS_OR_EQUAL,
            Operand("UPRO", OperandType.CONSTANT, value=-0.1),
        ),
    )
    configuration = replace(version.configuration, rules=(inactive,))
    assert validate_strategy(configuration).is_valid
    requirements = required_indicators(_version(configuration))
    assert [item.symbol for item in requirements] == ["SPY"]


def test_peak_availability_uses_pre_range_history_without_fixed_bar_warmup() -> None:
    version = _entry_version()
    data = {symbol: _dataset(symbol, (100, 100, 80, 70, 85)) for symbol in ("SPY", "UPRO", "SGOV")}

    class Calendar:
        calendar_id = "PEAK-TEST"

        def sessions(self, start, end):
            return tuple(
                START + timedelta(days=index)
                for index in range(5)
                if start <= START + timedelta(days=index) <= end
            )

    projected = project_data_availability(
        version,
        requested_start=START + timedelta(days=2),
        requested_end=START + timedelta(days=4),
        data_by_symbol=data,
        calendar=Calendar(),
    )
    requirement = projected["indicator_requirements"][0]
    assert requirement["kind"] == "peak_drawdown"
    assert requirement["required_bars"] is None
    assert requirement["peak_mode"] == "all_time_to_date"
    assert projected["warmup_start"] == "1900-01-01"
    assert projected["status"] in {"READY", "READY_WITH_WARNINGS"}


def test_peak_strategy_draft_and_version_survive_repository_restart(tmp_path) -> None:
    version = _entry_version()
    definition = version.configuration
    path = tmp_path / "peak-strategy.db"
    repository = StrategyRepository(path)
    repository.create_strategy(definition.strategy_id, definition.name, definition.description)
    draft = repository.save_draft(
        strategy_id=definition.strategy_id,
        expected_revision=0,
        active_mode="regime_state_machine",
        rule_based_draft={"assets": ["SPY"]},
        regime_draft={"assets": ["SPY"], "editor_state": definition.to_dict()},
        active_strategy_payload=definition.to_dict(),
        source_metadata={"price_field": FIELD.value},
        name=definition.name,
        description=definition.description,
    )
    saved = repository.create_version_from_draft(
        strategy_id=definition.strategy_id,
        expected_revision=draft.draft_revision,
        definition=definition,
    )
    restarted = StrategyRepository(path)
    assert restarted.get_draft(definition.strategy_id).draft_hash == draft.draft_hash
    restored = restarted.get(definition.strategy_id, saved.version_id)
    assert restored.content_hash == saved.content_hash
    assert restored.configuration.to_dict() == definition.to_dict()
