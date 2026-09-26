from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime

import pytest

from research import (
    BindingValueType,
    GridSearchDefinition,
    OperandSide,
    ParameterDefinition,
    ParameterSet,
    ParameterSpace,
    ParameterType,
    SemanticParameterBinding,
    SemanticTargetDescriptor,
    SemanticTargetKind,
    discover_semantic_bindings,
    materialize_semantic_strategy_version,
    preflight_grid_search,
    resolve_semantic_bindings,
)
from strategies import (
    ComparisonOperator,
    Condition,
    Operand,
    OperandType,
    RuleGroup,
    StrategyStatus,
    StrategyVersion,
    Threshold,
    ThresholdType,
)
from tests.unit.test_experiment_execution_service import _experiment
from tests.unit.test_value_zones import _strategy


def _version() -> StrategyVersion:
    definition = _strategy()
    first = definition.transitions[0]
    condition = RuleGroup(
        "and",
        (
            Condition(
                Operand("QQQ", OperandType.PRICE, price_field=definition.price_field),
                ComparisonOperator.GREATER_THAN,
                Operand("QQQ", OperandType.MA, period=20, price_field=definition.price_field),
                Threshold(ThresholdType.RELATIVE, 0.04),
            ),
            Condition(
                Operand("QQQ", OperandType.PRICE, price_field=definition.price_field),
                ComparisonOperator.GREATER_THAN,
                Operand("QQQ", OperandType.EMA, period=10, price_field=definition.price_field),
            ),
        ),
    )
    definition = replace(
        definition, transitions=(replace(first, condition=condition),) + definition.transitions[1:]
    )
    return StrategyVersion(
        definition.strategy_id,
        "semantic-fixture-v1",
        1,
        datetime(2026, 1, 1, tzinfo=UTC),
        definition,
        status=StrategyStatus.ACTIVE,
    )


def _binding(
    version: StrategyVersion, kind: SemanticTargetKind, **kwargs: object
) -> SemanticParameterBinding:
    return SemanticParameterBinding(
        "tunable",
        BindingValueType.POSITIVE_INTEGER
        if "period" in kind.value
        else BindingValueType.RELATIVE_THRESHOLD,
        "phase-13e-1-v1",
        SemanticTargetDescriptor(kind, version.version_id, version.content_hash or "", **kwargs),
    )


def test_semantic_nested_period_and_threshold_materialize_with_audit() -> None:
    version = _version()
    period = _binding(
        version,
        SemanticTargetKind.NESTED_CONDITION_PERIOD,
        transition_id="normal-approaching",
        condition_path=(1,),
        operand_side=OperandSide.RIGHT,
    )
    materialized = materialize_semantic_strategy_version(
        version, ParameterSet({"tunable": 30}), (period,)
    )

    transition = next(
        item
        for item in materialized.configuration.transitions
        if item.transition_id == "normal-approaching"
    )
    condition = transition.condition
    assert condition.children[1].right.period == 30  # type: ignore[union-attr]
    assert materialized.version_id != version.version_id
    audit = materialized.materialization_provenance.binding_audit  # type: ignore[union-attr]
    assert audit[0]["resolved_target_path"].endswith("children[1].right.period")
    assert audit[0]["parameter_value"] == 30


def test_semantic_state_and_zone_targets_resolve_by_identity() -> None:
    version = _version()
    state_binding = SemanticParameterBinding(
        "weight",
        BindingValueType.ALLOCATION_WEIGHT,
        "phase-13e-1-v1",
        SemanticTargetDescriptor(
            SemanticTargetKind.STATE_ALLOCATION_WEIGHT,
            version.version_id,
            version.content_hash or "",
            state_id="deep",
            symbol="TQQQ",
        ),
    )
    zone_binding = SemanticParameterBinding(
        "zone_period",
        BindingValueType.POSITIVE_INTEGER,
        "phase-13e-1-v1",
        SemanticTargetDescriptor(
            SemanticTargetKind.VALUE_ZONE_PERIOD,
            version.version_id,
            version.content_hash or "",
            zone_id="value",
        ),
    )
    resolved = resolve_semantic_bindings(version, (state_binding, zone_binding))
    assert "regimes[" in resolved[0].parameter_binding.target_path
    assert resolved[0].parameter_binding.target_path.endswith("target_weight")
    assert resolved[1].parameter_binding.target_path == "value_zones[2].period"


def test_semantic_binding_fails_closed_for_hash_or_deleted_target() -> None:
    version = _version()
    wrong_hash = replace(
        _binding(
            version,
            SemanticTargetKind.TRANSITION_THRESHOLD,
            transition_id="normal-approaching",
            condition_path=(0,),
        ),
        descriptor=SemanticTargetDescriptor(
            SemanticTargetKind.TRANSITION_THRESHOLD,
            version.version_id,
            "0" * 64,
            transition_id="normal-approaching",
            condition_path=(0,),
        ),
    )
    with pytest.raises(ValueError, match="identity"):
        resolve_semantic_bindings(version, (wrong_hash,))
    missing = _binding(version, SemanticTargetKind.VALUE_ZONE_PERIOD, zone_id="deleted-zone")
    with pytest.raises(ValueError, match="missing or ambiguous"):
        resolve_semantic_bindings(version, (missing,))


def test_discovery_exposes_supported_semantics_and_unsupported_structure() -> None:
    catalog = discover_semantic_bindings(_version())
    kinds = {item.descriptor.target_kind for item in catalog.supported}
    assert SemanticTargetKind.NESTED_CONDITION_PERIOD in kinds
    assert SemanticTargetKind.STATE_ALLOCATION_WEIGHT in kinds
    assert SemanticTargetKind.VALUE_ZONE_ENTRY_THRESHOLD in kinds
    assert any(item["reason"] == "TRANSITION_PRIORITY_NOT_TUNABLE" for item in catalog.unsupported)


def test_semantic_bindings_participate_in_deterministic_two_by_two_preflight() -> None:
    version = _version()
    experiment = _experiment(version)
    space = ParameterSpace(
        parameters=(
            ParameterDefinition("period", ParameterType.DISCRETE, allowed_values=(20, 30)),
            ParameterDefinition("threshold", ParameterType.DISCRETE, allowed_values=(0.02, 0.04)),
        ),
        max_candidates=4,
    )
    experiment = replace(
        experiment,
        parameter_space=space,
        provenance=replace(experiment.provenance, parameter_space_hash=space.content_hash),
    )
    bindings = (
        SemanticParameterBinding(
            "period",
            BindingValueType.POSITIVE_INTEGER,
            "phase-13e-1-v1",
            SemanticTargetDescriptor(
                SemanticTargetKind.NESTED_CONDITION_PERIOD,
                version.version_id,
                version.content_hash or "",
                transition_id="normal-approaching",
                condition_path=(1,),
                operand_side=OperandSide.RIGHT,
            ),
        ),
        SemanticParameterBinding(
            "threshold",
            BindingValueType.RELATIVE_THRESHOLD,
            "phase-13e-1-v1",
            SemanticTargetDescriptor(
                SemanticTargetKind.NESTED_CONDITION_THRESHOLD,
                version.version_id,
                version.content_hash or "",
                transition_id="normal-approaching",
                condition_path=(0,),
            ),
        ),
    )
    definition = GridSearchDefinition(
        experiment_id=experiment.experiment_id,
        experiment_hash=experiment.content_hash,
        semantic_bindings=bindings,
        fixed_parameters={"strategy_definition_id": version.strategy_id},
        max_candidates=4,
        theoretical_guard=4,
    )

    result = preflight_grid_search(experiment, definition, version)

    assert result.status.value == "ready"
    assert result.theoretical_count == result.executable_count == 4
    assert "semantic_bindings" in result.definition.to_dict()
