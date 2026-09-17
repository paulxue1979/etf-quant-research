from __future__ import annotations

import hashlib
import json
import sqlite3
from datetime import UTC, datetime

import pytest

from backend.app.strategy_repository import StrategyRepository
from data.models import PriceField
from research import (
    BindingValueType,
    ParameterBinding,
    ParameterDefinition,
    ParameterSet,
    ParameterSpace,
    ParameterType,
    materialize_strategy_version,
)
from strategies import (
    Allocation,
    AllocationSpecification,
    FallbackAllocation,
    NoMatchBehavior,
    RebalanceFrequency,
    StrategyDefinition,
    StrategyVersion,
    validate_strategy,
)
from strategies.exceptions import InvalidAllocationError


def _definition(**changes: object) -> StrategyDefinition:
    payload = {
        "strategy_id": "qqq-stateful",
        "name": "QQQ SMA200 Hysteresis",
        "description": "Stateful strategy domain fixture",
        "assets": [{"symbol": "QQQ"}],
        "price_field": PriceField.ADJUSTED_CLOSE.value,
        "rules": [],
        "fallback": {
            "name": "cash fallback",
            "allocations": [{"symbol": "QQQ", "target_weight": 0}],
        },
        "rebalance_policy": {
            "frequency": RebalanceFrequency.ON_SIGNAL_CHANGE.value,
            "threshold": None,
        },
        "no_match_behavior": NoMatchBehavior.HOLD_PREVIOUS_ALLOCATION.value,
        "initial_allocation": AllocationSpecification(()).to_dict(),
    }
    payload.update(changes)
    return StrategyDefinition.from_dict(payload)


def _golden_definition() -> StrategyDefinition:
    return StrategyDefinition.from_dict(
        {
            "strategy_id": "qqq-sma200-hysteresis",
            "name": "QQQ SMA200 Hysteresis",
            "description": "Buy above 104%, sell below 97%, otherwise hold",
            "assets": [{"symbol": "QQQ"}],
            "price_field": "adjusted_close",
            "rules": [
                {
                    "rule_id": "buy",
                    "name": "Buy QQQ",
                    "priority": 100,
                    "condition": {
                        "type": "condition",
                        "left": {
                            "type": "price",
                            "asset": "QQQ",
                            "price_field": "adjusted_close",
                        },
                        "operator": "greater_than",
                        "right": {
                            "type": "ma",
                            "asset": "QQQ",
                            "period": 200,
                            "price_field": "adjusted_close",
                        },
                        "threshold": {"type": "relative", "value": 0.04},
                    },
                    "allocations": [{"symbol": "QQQ", "target_weight": 1.0}],
                    "remaining": None,
                },
                {
                    "rule_id": "sell",
                    "name": "Sell to Cash",
                    "priority": 90,
                    "condition": {
                        "type": "condition",
                        "left": {
                            "type": "price",
                            "asset": "QQQ",
                            "price_field": "adjusted_close",
                        },
                        "operator": "less_than",
                        "right": {
                            "type": "ma",
                            "asset": "QQQ",
                            "period": 200,
                            "price_field": "adjusted_close",
                        },
                        "threshold": {"type": "relative", "value": -0.03},
                    },
                    "allocations": [{"symbol": "QQQ", "target_weight": 0.0}],
                    "remaining": None,
                },
            ],
            "fallback": {
                "name": "legacy cash fallback",
                "allocations": [{"symbol": "QQQ", "target_weight": 0.0}],
            },
            "rebalance_policy": {"frequency": "on_signal_change", "threshold": None},
            "no_match_behavior": "hold_previous_allocation",
            "initial_allocation": {"allocations": []},
        }
    )


def test_no_match_behavior_round_trips_and_legacy_payload_defaults() -> None:
    definition = _definition()

    assert definition.no_match_behavior is NoMatchBehavior.HOLD_PREVIOUS_ALLOCATION
    assert StrategyDefinition.from_json(definition.to_json()) == definition

    legacy = definition.to_dict()
    legacy.pop("no_match_behavior")
    legacy.pop("initial_allocation")
    restored = StrategyDefinition.from_dict(legacy)
    assert restored.no_match_behavior is NoMatchBehavior.USE_FALLBACK
    assert restored.initial_allocation is None
    assert "no_match_behavior" not in restored.to_dict()
    assert "initial_allocation" not in restored.to_dict()


def test_invalid_no_match_behavior_is_rejected() -> None:
    with pytest.raises(ValueError, match="NoMatchBehavior"):
        _definition(no_match_behavior="invalid")


def test_invalid_stateful_wire_fields_use_specific_validation_codes() -> None:
    invalid_behavior = _definition().to_dict()
    invalid_behavior["no_match_behavior"] = "invalid"
    behavior_result = validate_strategy(invalid_behavior)

    invalid_initial = _definition().to_dict()
    invalid_initial["initial_allocation"] = []
    initial_result = validate_strategy(invalid_initial)

    assert [(issue.code, issue.path) for issue in behavior_result.errors] == [
        ("InvalidNoMatchBehavior", "no_match_behavior")
    ]
    assert [(issue.code, issue.path) for issue in initial_result.errors] == [
        ("InvalidInitialAllocation", "initial_allocation")
    ]


def test_allocation_specification_uses_empty_allocations_for_cash_only() -> None:
    cash = AllocationSpecification(())
    qqq = AllocationSpecification((Allocation("QQQ", 1.0),))

    assert cash.to_dict() == {"allocations": []}
    assert cash.cash_weight == 1.0
    assert qqq.cash_weight == 0.0
    assert AllocationSpecification.from_dict(cash.to_dict()) == cash


@pytest.mark.parametrize("weight", [-0.01, 1.01, float("nan"), float("inf")])
def test_allocation_specification_rejects_invalid_weights(weight: float) -> None:
    with pytest.raises(InvalidAllocationError):
        AllocationSpecification.from_dict(
            {"allocations": [{"symbol": "QQQ", "target_weight": weight}]}
        )


def test_initial_allocation_must_use_declared_assets_and_valid_weights() -> None:
    definition = _definition(
        initial_allocation={
            "allocations": [{"symbol": "SPY", "target_weight": 1.0}],
        }
    )
    result = validate_strategy(definition)

    assert not result.is_valid
    assert any(issue.code == "UnknownAssetReference" for issue in result.errors)


def test_hold_requires_initial_allocation_but_fallback_mode_remains_compatible() -> None:
    missing = _definition(initial_allocation=None)
    assert any(
        issue.code == "MissingInitialAllocation" for issue in validate_strategy(missing).errors
    )

    fallback = StrategyDefinition.from_dict(
        {
            **missing.to_dict(),
            "no_match_behavior": NoMatchBehavior.USE_FALLBACK.value,
            "initial_allocation": None,
        }
    )
    assert validate_strategy(fallback).is_valid


def test_stateful_fields_are_part_of_strategy_version_identity() -> None:
    fallback = _definition(
        no_match_behavior=NoMatchBehavior.USE_FALLBACK.value,
        initial_allocation=None,
    )
    hold = _definition()
    fallback_version = StrategyVersion(
        strategy_id=fallback.strategy_id,
        version_id="fallback-v1",
        version_number=1,
        created_at=datetime(2026, 9, 17, tzinfo=UTC),
        configuration=fallback,
    )
    hold_version = StrategyVersion(
        strategy_id=hold.strategy_id,
        version_id="hold-v1",
        version_number=1,
        created_at=datetime(2026, 9, 17, tzinfo=UTC),
        configuration=hold,
    )

    assert fallback_version.content_hash != hold_version.content_hash
    assert StrategyDefinition.from_json(hold.to_json()).to_json() == hold.to_json()

    qqq_initial = StrategyDefinition.from_dict(
        {
            **hold.to_dict(),
            "initial_allocation": {"allocations": [{"symbol": "QQQ", "target_weight": 1.0}]},
        }
    )
    qqq_version = StrategyVersion(
        strategy_id=qqq_initial.strategy_id,
        version_id="qqq-initial-v1",
        version_number=1,
        created_at=datetime(2026, 9, 17, tzinfo=UTC),
        configuration=qqq_initial,
    )
    assert qqq_version.content_hash != hold_version.content_hash


def test_initial_allocation_is_immutable_and_does_not_add_cash_asset() -> None:
    definition = _definition()

    assert definition.initial_allocation is not None
    assert definition.initial_allocation.cash_weight == 1.0
    assert "CASH" not in {asset.symbol for asset in definition.assets}
    assert isinstance(definition.fallback, FallbackAllocation)
    with pytest.raises(AttributeError):
        definition.initial_allocation.allocations = ()  # type: ignore[misc]


def test_golden_hysteresis_strategy_is_valid_and_canonical() -> None:
    definition = _golden_definition()
    restored = StrategyDefinition.from_json(definition.to_json())

    assert validate_strategy(definition).is_valid
    assert restored == definition
    assert restored.to_json() == definition.to_json()
    assert restored.rules[0].condition.threshold.value == 0.04  # type: ignore[union-attr]
    assert restored.rules[1].condition.threshold.value == -0.03  # type: ignore[union-attr]
    assert restored.rules[1].allocations[0].target_weight == 0.0
    assert restored.initial_allocation is not None
    assert restored.initial_allocation.cash_weight == 1.0


def test_stateful_strategy_repository_round_trip(tmp_path) -> None:
    repository = StrategyRepository(tmp_path / "strategy.db")
    definition = _golden_definition()

    created = repository.create(definition)
    restored = repository.get(created.strategy_id, created.version_id)

    assert restored == created
    assert repository.list(created.strategy_id) == (created,)
    assert repository.get_any_version(created.version_id) == created
    assert restored is not None
    assert restored.configuration.no_match_behavior is NoMatchBehavior.HOLD_PREVIOUS_ALLOCATION
    assert restored.configuration.initial_allocation == AllocationSpecification(())


def test_stateful_exact_materialized_version_persists_and_reloads(tmp_path) -> None:
    repository = StrategyRepository(tmp_path / "strategy.db")
    base = repository.create(_golden_definition())
    parameter_space = ParameterSpace(
        parameters=(
            ParameterDefinition(
                "period",
                ParameterType.INTEGER,
                min=100,
                max=300,
                step=1,
            ),
        )
    )
    parameter_set = ParameterSet({"period": 180}, parameter_space)
    binding = ParameterBinding(
        "period",
        "rules[0].condition.right.period",
        BindingValueType.POSITIVE_INTEGER,
        "phase-8d-1-v1",
    )
    derived = materialize_strategy_version(base, parameter_set, (binding,))

    persisted = repository.persist_exact_strategy_version(derived)
    restored = StrategyRepository(repository.db_path).get_any_version(derived.version_id)

    assert persisted == derived
    assert restored == derived
    assert restored is not None
    assert restored.configuration.no_match_behavior is NoMatchBehavior.HOLD_PREVIOUS_ALLOCATION
    assert restored.configuration.initial_allocation == AllocationSpecification(())
    assert restored.materialization_provenance == derived.materialization_provenance


def test_legacy_strategy_hash_and_repository_hydration_remain_compatible(tmp_path) -> None:
    legacy_payload = {
        "strategy_id": "legacy-v1",
        "name": "Legacy V1",
        "description": "Pre-PHASE 9C strategy",
        "assets": [{"symbol": "QQQ"}],
        "price_field": "adjusted_close",
        "rules": [],
        "fallback": {
            "name": "all QQQ",
            "allocations": [{"symbol": "QQQ", "target_weight": 1.0}],
        },
        "rebalance_policy": {"frequency": "weekly", "threshold": None},
    }
    canonical = json.dumps(
        legacy_payload,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    )
    legacy_hash = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    definition = StrategyDefinition.from_dict(legacy_payload)
    assert definition.to_json() == canonical

    database = tmp_path / "legacy.db"
    repository = StrategyRepository(database)
    created_at = datetime(2026, 9, 1, tzinfo=UTC).isoformat()
    with sqlite3.connect(database) as connection:
        connection.execute(
            """
            INSERT INTO strategies(
                strategy_id, name, description, created_at, latest_version_number
            ) VALUES (?, ?, ?, ?, ?)
            """,
            ("legacy-v1", "Legacy V1", "Pre-PHASE 9C strategy", created_at, 1),
        )
        connection.execute(
            """
            INSERT INTO strategy_versions(
                strategy_id, version_id, version_number, created_at,
                configuration_json, content_hash, status
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            ("legacy-v1", "legacy-v1-version", 1, created_at, canonical, legacy_hash, "draft"),
        )

    restored = repository.get("legacy-v1", "legacy-v1-version")
    assert restored is not None
    assert restored.content_hash == legacy_hash
    assert restored.configuration.no_match_behavior is NoMatchBehavior.USE_FALLBACK
    assert restored.configuration.initial_allocation is None
