from __future__ import annotations

from datetime import UTC, date, datetime

import pytest

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
    ParameterBindingSet,
    ParameterSet,
    materialize_strategy_version,
)
from strategies import (
    Allocation,
    AllocationRule,
    AssetRole,
    ComparisonOperator,
    Condition,
    FallbackAllocation,
    LogicalOperator,
    Operand,
    OperandType,
    RebalanceFrequency,
    RebalancePolicy,
    RuleGroup,
    StrategyAssetReference,
    StrategyDefinition,
    StrategyStatus,
    StrategyVersion,
    Threshold,
    ThresholdType,
    UnsupportedTimeframeError,
    ValidationCode,
    validate_strategy,
)
from strategies.evaluation import EvaluationContext
from strategies.strategy_evaluation import evaluate_strategy


def _definition(
    *,
    roles: tuple[tuple[str, AssetRole], ...] = (
        ("QQQ", AssetRole.SIGNAL_SOURCE),
        ("TQQQ", AssetRole.EXECUTION_ASSET),
        ("SGOV", AssetRole.EXECUTION_ASSET),
    ),
    timeframe: Timeframe = Timeframe.DAILY,
    schema: str = "2.0",
    signal_symbol: str = "QQQ",
    execution_symbol: str = "TQQQ",
    fallback_symbol: str = "SGOV",
) -> StrategyDefinition:
    condition = Condition(
        Operand(
            signal_symbol,
            OperandType.PRICE,
            price_field=PriceField.ADJUSTED_CLOSE,
            timeframe=timeframe,
        ),
        ComparisonOperator.GREATER_THAN,
        Operand(
            signal_symbol,
            OperandType.MA,
            2,
            PriceField.ADJUSTED_CLOSE,
            timeframe=timeframe,
        ),
        Threshold(ThresholdType.RELATIVE, 0.01),
    )
    return StrategyDefinition(
        strategy_id="v13-contract",
        name="V1.3 contract",
        description="contract fixture",
        assets=tuple(StrategyAssetReference(symbol, role) for symbol, role in roles),
        price_field=PriceField.ADJUSTED_CLOSE,
        rules=(
            AllocationRule(
                "risk-on",
                "Risk On",
                1,
                (Allocation(execution_symbol, 0.8),),
                RuleGroup(LogicalOperator.AND, (condition,)),
                None,
            ),
        ),
        fallback=FallbackAllocation((Allocation(fallback_symbol, 1.0),)),
        rebalance_policy=RebalancePolicy(RebalanceFrequency.MONTHLY),
        strategy_schema_version=schema,
    )


def _version(definition: StrategyDefinition) -> StrategyVersion:
    return StrategyVersion(
        strategy_id=definition.strategy_id,
        version_id="v13-contract-v1",
        version_number=1,
        created_at=datetime(2026, 9, 21, tzinfo=UTC),
        configuration=definition,
        status=StrategyStatus.ACTIVE,
    )


def _dataset(symbol: str) -> HistoricalDataSet:
    points = tuple(
        MarketDataPoint(
            date=date(2026, 1, day),
            open=100.0 + day,
            high=101.0 + day,
            low=99.0 + day,
            close=100.0 + day,
            volume=1000.0,
            adj_open=100.0 + day,
            adj_high=101.0 + day,
            adj_low=99.0 + day,
            adj_close=100.0 + day,
            adj_volume=1000.0,
            div_cash=0.0,
            split_factor=1.0,
        )
        for day in (1, 2, 3)
    )
    return HistoricalDataSet(
        request=HistoricalDataRequest(
            symbol=symbol,
            start_date=points[0].date,
            end_date=points[-1].date,
            price_field_used=PriceField.ADJUSTED_CLOSE,
        ),
        points=points,
        source=DataSource.CACHE,
    )


def test_roles_are_explicit_and_legacy_assets_are_both() -> None:
    definition = _definition()
    assert definition.signal_asset_symbols == {"QQQ"}
    assert definition.execution_asset_symbols == {"TQQQ", "SGOV"}

    legacy = StrategyDefinition.from_dict({**_definition(schema="1.0").to_dict()})
    assert legacy.signal_asset_symbols == {"QQQ", "TQQQ", "SGOV"}
    assert legacy.execution_asset_symbols == {"QQQ", "TQQQ", "SGOV"}


@pytest.mark.parametrize(
    ("roles", "expected"),
    [
        (
            (
                ("QQQ", AssetRole.SIGNAL_SOURCE),
                ("TQQQ", AssetRole.SIGNAL_SOURCE),
                ("SGOV", AssetRole.EXECUTION_ASSET),
            ),
            ValidationCode.EXECUTION_ASSET_REQUIRED,
        ),
        (
            (
                ("QQQ", AssetRole.EXECUTION_ASSET),
                ("TQQQ", AssetRole.EXECUTION_ASSET),
                ("SGOV", AssetRole.EXECUTION_ASSET),
            ),
            ValidationCode.SIGNAL_SOURCE_REQUIRED,
        ),
    ],
)
def test_role_capability_validation(roles, expected) -> None:
    assert expected in {issue.code for issue in validate_strategy(_definition(roles=roles)).errors}


def test_both_generic_assets_and_sgov_execution_are_valid_without_cash_ticker() -> None:
    definition = _definition(
        roles=(
            ("SPY", AssetRole.BOTH),
            ("UPRO", AssetRole.EXECUTION_ASSET),
            ("SGOV", AssetRole.EXECUTION_ASSET),
        ),
        signal_symbol="SPY",
        execution_symbol="UPRO",
        fallback_symbol="SGOV",
    )
    assert validate_strategy(definition).is_valid
    assert "CASH" not in definition.signal_asset_symbols | definition.execution_asset_symbols
    assert "SGOV" in definition.execution_asset_symbols


def test_duplicate_role_entries_are_rejected() -> None:
    with pytest.raises(ValueError, match="duplicate symbols"):
        _definition(
            roles=(
                ("QQQ", AssetRole.SIGNAL_SOURCE),
                ("QQQ", AssetRole.BOTH),
                ("TQQQ", AssetRole.EXECUTION_ASSET),
            )
        )


def test_schema2_serializes_roles_and_daily_timeframe_deterministically() -> None:
    definition = _definition()
    payload = definition.to_dict()
    assert payload["strategy_schema_version"] == "2.0"
    assert payload["assets"][0] == {"symbol": "QQQ", "role": "signal_source"}
    operand = payload["rules"][0]["condition"]["children"][0]["left"]
    assert operand["timeframe"] == "daily"
    assert StrategyDefinition.from_dict(payload).to_dict() == payload


def test_legacy_roundtrip_keeps_old_wire_shape_and_identity() -> None:
    definition = _definition(schema="1.0")
    restored = StrategyDefinition.from_dict(definition.to_dict())
    assert "strategy_schema_version" not in definition.to_dict()
    assert "timeframe" not in definition.to_dict()["rules"][0]["condition"]["children"][0]["left"]
    assert _version(definition).content_hash == _version(restored).content_hash


def test_daily_weekly_roles_and_schema_change_content_identity() -> None:
    daily = _version(_definition(timeframe=Timeframe.DAILY))
    weekly = _version(_definition(timeframe=Timeframe.WEEKLY))
    both = _version(
        _definition(
            roles=(
                ("QQQ", AssetRole.BOTH),
                ("TQQQ", AssetRole.EXECUTION_ASSET),
                ("SGOV", AssetRole.EXECUTION_ASSET),
            )
        )
    )
    assert daily.content_hash != weekly.content_hash
    assert daily.content_hash != both.content_hash


def test_unsupported_future_schema_is_controlled() -> None:
    with pytest.raises(ValueError, match="unsupported strategy schema version"):
        StrategyDefinition.from_dict({**_definition().to_dict(), "strategy_schema_version": "9.0"})


def test_weekly_runtime_is_rejected_before_daily_fallback() -> None:
    version = _version(_definition(timeframe=Timeframe.WEEKLY))
    data = {symbol: _dataset(symbol) for symbol in ("QQQ", "TQQQ", "SGOV")}
    with pytest.raises(UnsupportedTimeframeError, match="PHASE 11C"):
        evaluate_strategy(
            version,
            EvaluationContext.from_components(data),
            date(2026, 1, 1),
            date(2026, 1, 3),
        )


def test_materialization_preserves_schema_roles_and_timeframe() -> None:
    base = _version(_definition(timeframe=Timeframe.WEEKLY))
    binding = ParameterBinding(
        "period",
        "rules[0].condition.children[0].right.period",
        BindingValueType.POSITIVE_INTEGER,
        "phase-8d-1-v1",
    )
    derived = materialize_strategy_version(
        base,
        ParameterSet({"period": 3}),
        ParameterBindingSet((binding,)),
    )
    assert derived.configuration.to_dict()["strategy_schema_version"] == "2.0"
    assert derived.configuration.assets[0].role is AssetRole.SIGNAL_SOURCE
    assert derived.configuration.rules[0].condition.children[0].right.timeframe is Timeframe.WEEKLY
