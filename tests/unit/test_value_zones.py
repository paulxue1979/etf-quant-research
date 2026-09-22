from __future__ import annotations

from dataclasses import replace
from datetime import UTC, date, datetime, timedelta
from math import inf, nan

import pytest

from data.derived import DerivedWeeklyBar, DerivedWeeklyDataSet
from data.models import (
    DataSource,
    HistoricalDataRequest,
    HistoricalDataSet,
    MarketDataPoint,
    PriceField,
    Timeframe,
)
from indicators.models import IndicatorKind, IndicatorPoint, IndicatorSeries
from research import (
    BindingValueType,
    ParameterBinding,
    ParameterSet,
    materialize_strategy_version,
)
from strategies import (
    Allocation,
    AllocationSpecification,
    AssetRole,
    ComparisonOperator,
    Condition,
    EvaluationContext,
    FallbackAllocation,
    Operand,
    OperandType,
    RebalanceFrequency,
    RebalancePolicy,
    RegimeDefinition,
    RegimeTransitionDefinition,
    StrategyAssetReference,
    StrategyDefinition,
    StrategyEvaluationMode,
    StrategyStatus,
    StrategyVersion,
    ValueZoneDefinition,
    ValueZoneTrigger,
    evaluate_strategy,
    required_indicators,
    required_weekly_assets,
    resolve_value_zones,
    validate_strategy,
)
from strategies.exceptions import InvalidEvaluationValueError, ZeroReferenceValueError

START = date(2026, 1, 2)
FIELD = PriceField.ADJUSTED_CLOSE


def _dates(count: int) -> tuple[date, ...]:
    return tuple(START + timedelta(days=index * 7) for index in range(count))


def _point(day: date, value: float) -> MarketDataPoint:
    return MarketDataPoint(
        date=day,
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


def _dataset(symbol: str, days: tuple[date, ...], values: tuple[float, ...]) -> HistoricalDataSet:
    request = HistoricalDataRequest(symbol, days[0], days[-1], price_field_used=FIELD)
    return HistoricalDataSet(
        request=request,
        points=tuple(_point(day, value) for day, value in zip(days, values, strict=True)),
        source=DataSource.CACHE,
    )


def _weekly_dataset(
    symbol: str, days: tuple[date, ...], values: tuple[float, ...]
) -> DerivedWeeklyDataSet:
    request = HistoricalDataRequest(symbol, days[0], days[-1], price_field_used=FIELD)
    return DerivedWeeklyDataSet(
        symbol=symbol,
        points=tuple(
            DerivedWeeklyBar(
                symbol=symbol,
                period_start=day - timedelta(days=4),
                period_end=day,
                available_on=day,
                open=value,
                high=value,
                low=value,
                close=value,
                volume=500.0,
                adj_open=value,
                adj_high=value,
                adj_low=value,
                adj_close=value,
                adj_volume=500.0,
                div_cash=0.0,
                split_factor=1.0,
            )
            for day, value in zip(days, values, strict=True)
        ),
        source_request=request,
        source=DataSource.CACHE,
        calendar_id="VALUE-ZONE-TEST",
        price_field_used=FIELD,
    )


def _context(
    signal_symbol: str,
    execution_symbols: tuple[str, str],
    prices: tuple[float, ...],
    *,
    indicator_values: tuple[float | None, ...] | None = None,
) -> EvaluationContext:
    days = _dates(len(prices))
    market_data = {
        signal_symbol: _dataset(signal_symbol, days, prices),
        execution_symbols[0]: _dataset(execution_symbols[0], days, prices),
        execution_symbols[1]: _dataset(execution_symbols[1], days, prices),
    }
    weekly = _weekly_dataset(signal_symbol, days, prices)
    values = indicator_values or tuple(100.0 for _ in prices)
    indicator = IndicatorSeries(
        kind=IndicatorKind.MOVING_AVERAGE,
        period=2,
        price_field_used=FIELD,
        points=tuple(IndicatorPoint(day, value) for day, value in zip(days, values, strict=True)),
        timeframe=Timeframe.WEEKLY,
    )
    daily_indicator = IndicatorSeries(
        kind=IndicatorKind.MOVING_AVERAGE,
        period=2,
        price_field_used=FIELD,
        points=tuple(IndicatorPoint(day, 110.0) for day in days),
        timeframe=Timeframe.DAILY,
    )
    return EvaluationContext.from_components(
        market_data,
        ((signal_symbol, indicator), (signal_symbol, daily_indicator)),
        weekly_market_data={signal_symbol: weekly},
    )


def _always_true(symbol: str) -> Condition:
    return Condition(
        Operand(symbol, OperandType.PRICE, price_field=FIELD),
        ComparisonOperator.GREATER_THAN,
        Operand(symbol, OperandType.CONSTANT, value=0.0),
    )


def _daily_confirmation(symbol: str, period: int = 2) -> Condition:
    return Condition(
        Operand(symbol, OperandType.PRICE, price_field=FIELD),
        ComparisonOperator.GREATER_OR_EQUAL,
        Operand(symbol, OperandType.MA, period=period, price_field=FIELD),
    )


def _zones(symbol: str) -> tuple[ValueZoneDefinition, ...]:
    return (
        ValueZoneDefinition(
            "approaching",
            "Approaching",
            symbol,
            Timeframe.WEEKLY,
            IndicatorKind.MOVING_AVERAGE,
            2,
            FIELD,
            0.10,
            0.12,
            priority=20,
        ),
        ValueZoneDefinition(
            "value",
            "Value",
            symbol,
            Timeframe.WEEKLY,
            IndicatorKind.MOVING_AVERAGE,
            2,
            FIELD,
            0.05,
            0.07,
            priority=10,
        ),
        ValueZoneDefinition(
            "deep",
            "Deep Value",
            symbol,
            Timeframe.WEEKLY,
            IndicatorKind.MOVING_AVERAGE,
            2,
            FIELD,
            0.0,
            0.02,
            priority=0,
        ),
    )


def _transition(
    transition_id: str,
    from_state: str,
    to_state: str,
    priority: int,
    signal_symbol: str,
    *,
    zone_id: str | None = None,
    trigger: ValueZoneTrigger = ValueZoneTrigger.MATCH,
    condition: Condition | None = None,
) -> RegimeTransitionDefinition:
    return RegimeTransitionDefinition(
        transition_id=transition_id,
        from_state=from_state,
        to_state=to_state,
        condition=condition or _always_true(signal_symbol),
        priority=priority,
        value_zone_id=zone_id,
        value_zone_trigger=trigger,
    )


def _strategy(
    signal_symbol: str = "QQQ",
    risk_symbol: str = "TQQQ",
    defensive_symbol: str = "SGOV",
    *,
    zones: tuple[ValueZoneDefinition, ...] | None = None,
) -> StrategyDefinition:
    zone_definitions = zones if zones is not None else _zones(signal_symbol)
    allocations = {
        "normal": AllocationSpecification((Allocation(defensive_symbol, 1.0),)),
        "approaching": AllocationSpecification((Allocation(signal_symbol, 0.5),)),
        "value": AllocationSpecification((Allocation(signal_symbol, 1.0),)),
        "deep": AllocationSpecification((Allocation(risk_symbol, 0.75),)),
        "recovery": AllocationSpecification(
            (
                Allocation(signal_symbol, 0.5),
                Allocation(risk_symbol, 0.25),
                Allocation(defensive_symbol, 0.25),
            )
        ),
        "full_risk": AllocationSpecification((Allocation(risk_symbol, 1.0),)),
    }
    transitions = (
        _transition(
            "normal-approaching",
            "normal",
            "approaching",
            10,
            signal_symbol,
            zone_id="approaching",
            trigger=ValueZoneTrigger.ENTER,
        ),
        _transition(
            "approaching-value",
            "approaching",
            "value",
            10,
            signal_symbol,
            zone_id="value",
            trigger=ValueZoneTrigger.ENTER,
        ),
        _transition(
            "value-deep",
            "value",
            "deep",
            10,
            signal_symbol,
            zone_id="deep",
            trigger=ValueZoneTrigger.ENTER,
        ),
        _transition(
            "deep-recovery",
            "deep",
            "recovery",
            10,
            signal_symbol,
            zone_id="deep",
            trigger=ValueZoneTrigger.EXIT,
        ),
        _transition(
            "recovery-full",
            "recovery",
            "full_risk",
            10,
            signal_symbol,
            condition=_daily_confirmation(signal_symbol),
        ),
        _transition(
            "full-recovery",
            "full_risk",
            "recovery",
            10,
            signal_symbol,
            zone_id="value",
            trigger=ValueZoneTrigger.MATCH,
            condition=Condition(
                Operand(signal_symbol, OperandType.PRICE, price_field=FIELD),
                ComparisonOperator.LESS_THAN,
                Operand(signal_symbol, OperandType.MA, period=2, price_field=FIELD),
            ),
        ),
    )
    return StrategyDefinition(
        strategy_id=f"{signal_symbol.lower()}-value-zones",
        name="Generic Value Zone Strategy",
        description="Generic weekly evidence with stateful recovery",
        assets=(
            StrategyAssetReference(signal_symbol, AssetRole.BOTH),
            StrategyAssetReference(risk_symbol, AssetRole.EXECUTION_ASSET),
            StrategyAssetReference(defensive_symbol, AssetRole.EXECUTION_ASSET),
        ),
        price_field=FIELD,
        rules=(),
        fallback=FallbackAllocation((Allocation(defensive_symbol, 1.0),)),
        rebalance_policy=RebalancePolicy(RebalanceFrequency.DAILY),
        strategy_schema_version="2.0",
        strategy_mode=StrategyEvaluationMode.REGIME_STATE_MACHINE,
        initial_regime="normal",
        regimes=tuple(
            RegimeDefinition(state_id, state_id.replace("_", " ").title(), allocation)
            for state_id, allocation in allocations.items()
        ),
        transitions=transitions,
        value_zones=zone_definitions,
    )


def _version(strategy: StrategyDefinition) -> StrategyVersion:
    return StrategyVersion(
        strategy.strategy_id,
        f"{strategy.strategy_id}-v1",
        1,
        datetime(2026, 1, 1, tzinfo=UTC),
        strategy,
        status=StrategyStatus.ACTIVE,
    )


@pytest.mark.parametrize(
    ("price", "expected"),
    [(110.0, 0.10), (100.0, 0.0), (95.0, -0.05)],
)
def test_value_zone_distance_is_price_over_indicator_minus_one(
    price: float, expected: float
) -> None:
    context = _context("QQQ", ("TQQQ", "SGOV"), (price,))

    resolution = resolve_value_zones(_strategy(), context, START)

    assert resolution.evidence[0].distance == pytest.approx(expected)
    assert all(item.source_date == START for item in resolution.evidence)


@pytest.mark.parametrize("invalid", [0.0, nan, inf])
def test_value_zone_rejects_zero_or_non_finite_indicator_values(invalid: float) -> None:
    context = _context(
        "QQQ",
        ("TQQQ", "SGOV"),
        (100.0,),
        indicator_values=(invalid,),
    )

    error = ZeroReferenceValueError if invalid == 0 else InvalidEvaluationValueError
    with pytest.raises(error):
        resolve_value_zones(_strategy(), context, START)


def test_nested_zone_resolution_is_priority_driven_and_order_independent() -> None:
    context = _context("QQQ", ("TQQQ", "SGOV"), (98.0,))
    first = _strategy()
    second = replace(first, value_zones=tuple(reversed(first.value_zones)))

    first_result = resolve_value_zones(first, context, START)
    second_result = resolve_value_zones(second, context, START)

    assert first_result.matched_zone_id == "deep"
    assert first_result.to_dict() == second_result.to_dict()
    assert [item.zone_id for item in first_result.evidence] == ["deep", "value", "approaching"]


def test_hysteresis_retains_zone_until_exit_then_resolves_remaining_band() -> None:
    strategy = _strategy()
    hold = resolve_value_zones(
        strategy,
        _context("QQQ", ("TQQQ", "SGOV"), (101.0,)),
        START,
        current_zone_id="deep",
    )
    exit_result = resolve_value_zones(
        strategy,
        _context("QQQ", ("TQQQ", "SGOV"), (103.0,)),
        START,
        current_zone_id="deep",
    )

    assert hold.matched_zone_id == "deep"
    assert hold.transition_type == "stay"
    assert exit_result.exited_zone_id == "deep"
    assert exit_result.matched_zone_id == "value"
    assert exit_result.transition_type == "enter"


@pytest.mark.parametrize(
    "kwargs",
    [
        {"entry_threshold": nan},
        {"entry_threshold": 0.05, "exit_threshold": 0.03},
        {"entry_operator": ComparisonOperator.EQUAL},
    ],
)
def test_value_zone_definition_rejects_invalid_threshold_contract(
    kwargs: dict[str, object],
) -> None:
    with pytest.raises(ValueError):
        zone_kwargs = {"entry_threshold": 0.05, **kwargs}
        ValueZoneDefinition(
            "invalid",
            "Invalid",
            "QQQ",
            Timeframe.WEEKLY,
            IndicatorKind.MOVING_AVERAGE,
            2,
            FIELD,
            **zone_kwargs,
        )


def test_weekly_evidence_uses_latest_completed_source_without_lookahead() -> None:
    days = (START, START + timedelta(days=3))
    market = {symbol: _dataset(symbol, days, (95.0, 97.0)) for symbol in ("QQQ", "TQQQ", "SGOV")}
    weekly = _weekly_dataset("QQQ", (START,), (95.0,))
    indicator = IndicatorSeries(
        IndicatorKind.MOVING_AVERAGE,
        2,
        FIELD,
        (IndicatorPoint(START, 100.0),),
        Timeframe.WEEKLY,
    )
    context = EvaluationContext.from_components(
        market,
        (("QQQ", indicator),),
        weekly_market_data={"QQQ": weekly},
    )

    result = resolve_value_zones(_strategy(), context, days[-1])

    assert result.evidence[0].evaluation_date == days[-1]
    assert result.evidence[0].source_date == START
    assert result.evidence[0].price_source_date == START
    assert result.evidence[0].indicator_source_date == START


def test_recovery_hold_requires_history_and_daily_confirmation_with_reentry() -> None:
    prices = (120.0, 108.0, 103.0, 98.0, 103.0, 111.0, 104.0, 111.0)
    strategy = _strategy()
    timeline = evaluate_strategy(
        _version(strategy),
        _context("QQQ", ("TQQQ", "SGOV"), prices),
        _dates(len(prices))[0],
        _dates(len(prices))[-1],
    )

    states = [item.regime_provenance["active_state_id"] for item in timeline.evaluations]
    assert states == [
        "normal",
        "approaching",
        "value",
        "deep",
        "recovery",
        "full_risk",
        "recovery",
        "full_risk",
    ]
    assert [item.regime_provenance["transition_count"] for item in timeline.evaluations] == [
        0,
        1,
        2,
        3,
        4,
        5,
        6,
        7,
    ]
    recovery = timeline.evaluations[4]
    assert recovery.regime_provenance["transition_event"]["transition_id"] == "deep-recovery"
    assert recovery.regime_provenance["value_zone"]["exited_zone_id"] == "deep"
    assert {
        item.symbol.symbol: item.target_weight for item in recovery.target_allocation.allocations
    } == {
        "QQQ": 0.5,
        "TQQQ": 0.25,
        "SGOV": 0.25,
    }
    assert timeline.evaluations[3].target_allocation.remaining_weight == pytest.approx(0.25)
    assert timeline.evaluations[6].regime_provenance["transition_event"]["transition_id"] == (
        "full-recovery"
    )
    assert all(
        item.regime_provenance["transition_event"] is None
        or item.regime_provenance["transition_event"]["from_state"]
        != item.regime_provenance["transition_event"]["to_state"]
        for item in timeline.evaluations
    )


def test_large_move_can_take_one_explicit_direct_transition_without_cascade() -> None:
    base = _strategy()
    direct = replace(
        base,
        transitions=(
            _transition(
                "normal-deep",
                "normal",
                "deep",
                1,
                "QQQ",
                zone_id="deep",
                trigger=ValueZoneTrigger.ENTER,
            ),
        ),
    )
    timeline = evaluate_strategy(
        _version(direct),
        _context("QQQ", ("TQQQ", "SGOV"), (94.0,)),
        START,
        START,
    )

    assert timeline.evaluations[0].regime_provenance["active_state_id"] == "deep"
    assert timeline.evaluations[0].regime_provenance["transition_count"] == 1


@pytest.mark.parametrize(
    ("signal_symbol", "risk_symbol", "defensive_symbol"),
    [("QQQ", "TQQQ", "SGOV"), ("SPY", "UPRO", "SGOV")],
)
def test_value_zone_strategy_is_generic_across_asset_role_sets(
    signal_symbol: str, risk_symbol: str, defensive_symbol: str
) -> None:
    strategy = _strategy(signal_symbol, risk_symbol, defensive_symbol)
    version = _version(strategy)

    assert validate_strategy(strategy).is_valid
    assert required_weekly_assets(version) == (signal_symbol,)
    requirements = required_indicators(version)
    assert [item.symbol for item in requirements] == [signal_symbol, signal_symbol]
    assert {item.timeframe for item in requirements} == {Timeframe.DAILY, Timeframe.WEEKLY}


def test_value_zone_definition_is_serializable_hash_covered_and_order_stable() -> None:
    strategy = _strategy()
    version = _version(strategy)
    round_trip = StrategyDefinition.from_dict(strategy.to_dict())
    reordered = replace(strategy, value_zones=tuple(reversed(strategy.value_zones)))
    changed = replace(
        strategy,
        value_zones=(replace(strategy.value_zones[0], period=3),) + strategy.value_zones[1:],
    )

    assert round_trip.to_dict() == strategy.to_dict()
    assert _version(reordered).content_hash == version.content_hash
    assert _version(changed).content_hash != version.content_hash
    assert "value_zones" in strategy.to_dict()


def test_zone_period_and_thresholds_can_be_materialized_without_mutating_base() -> None:
    strategy = replace(_strategy(), value_zones=(_zones("QQQ")[0],), transitions=())
    base = _version(strategy)
    bindings = (
        ParameterBinding(
            "period",
            "value_zones[0].period",
            BindingValueType.POSITIVE_INTEGER,
            "phase-8d-1-v1",
        ),
        ParameterBinding(
            "entry",
            "value_zones[0].entry_threshold",
            BindingValueType.RELATIVE_THRESHOLD,
            "phase-8d-1-v1",
        ),
        ParameterBinding(
            "exit",
            "value_zones[0].exit_threshold",
            BindingValueType.RELATIVE_THRESHOLD,
            "phase-8d-1-v1",
        ),
    )

    derived = materialize_strategy_version(
        base,
        ParameterSet({"period": 20, "entry": 0.08, "exit": 0.13}),
        bindings,
    )

    assert base.configuration.value_zones[0].period == 2
    assert derived.configuration.value_zones[0].period == 20
    assert derived.configuration.value_zones[0].entry_threshold == 0.08
    assert derived.configuration.value_zones[0].exit_threshold == 0.13
    assert derived.content_hash != base.content_hash


def test_value_zone_validation_rejects_ambiguous_priority_and_unknown_transition_reference() -> (
    None
):
    first = _zones("QQQ")[0]
    duplicate_priority = replace(_zones("QQQ")[1], priority=first.priority)
    strategy = replace(
        _strategy(),
        value_zones=(first, duplicate_priority),
        transitions=(
            _transition(
                "unknown-zone",
                "normal",
                "approaching",
                1,
                "QQQ",
                zone_id="missing",
            ),
        ),
    )

    codes = {item.code for item in validate_strategy(strategy).errors}

    assert "ValueZonePriorityConflict" in codes
    assert "UnknownValueZoneReference" in codes
