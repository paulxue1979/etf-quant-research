from __future__ import annotations

import json
from dataclasses import replace
from math import inf, nan

import pytest

from data.models import PriceField
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
    RemainingAllocation,
    RuleGroup,
    StrategyDefinition,
    Threshold,
    ValidationCode,
    validate_strategy,
)
from strategies.validation import StrategyValidator


def _price(
    symbol: str = "TQQQ", price_field: PriceField | None = PriceField.ADJUSTED_CLOSE
) -> Operand:
    return Operand(symbol, OperandType.PRICE, price_field=price_field)


def _ma(
    period: int,
    symbol: str = "TQQQ",
    price_field: PriceField | None = PriceField.ADJUSTED_CLOSE,
) -> Operand:
    return Operand(symbol, OperandType.MA, period, price_field)


def _constant(value: object, symbol: str = "TQQQ") -> Operand:
    return Operand(symbol, OperandType.CONSTANT, value=value)  # type: ignore[arg-type]


def _condition(
    left: Operand | None = None,
    right: Operand | None = None,
    threshold: Threshold | None = None,
) -> Condition:
    return Condition(
        left=left or _price(),
        operator=ComparisonOperator.GREATER_THAN,
        right=right or _ma(50),
        threshold=threshold,
    )


def _allocation(symbol: str, weight: float, **bounds: float) -> Allocation:
    return Allocation(symbol, weight, **bounds)


def _definition(
    *,
    assets: tuple[str, ...] = ("QQQ", "TQQQ", "SGOV"),
    rules: tuple[AllocationRule, ...] | None = None,
    fallback: FallbackAllocation | None = None,
    price_field: PriceField = PriceField.ADJUSTED_CLOSE,
) -> StrategyDefinition:
    if rules is None:
        rules = (
            AllocationRule(
                "risk-on",
                "Risk On",
                100,
                (_allocation("QQQ", 0.30), _allocation("TQQQ", 0.50), _allocation("SGOV", 0.20)),
                _condition(),
            ),
        )
    return StrategyDefinition(
        strategy_id="qqq-tqqq-sgov",
        name="Dynamic Allocation",
        description="Validation fixture",
        assets=tuple(AssetReference(symbol) for symbol in assets),
        price_field=price_field,
        rules=rules,
        fallback=fallback or FallbackAllocation((_allocation("SGOV", 1.0),)),
        rebalance_policy=RebalancePolicy(RebalanceFrequency.MONTHLY, 0.05),
    )


def _codes(result) -> set[str]:
    return {issue.code for issue in result.errors}


def test_valid_multi_asset_strategy_returns_structured_valid_result() -> None:
    result = validate_strategy(_definition())

    assert result.is_valid
    assert result.errors == ()
    assert result.to_dict() == {"is_valid": True, "errors": [], "warnings": []}


@pytest.mark.parametrize(
    ("payload", "expected_code"),
    [
        (
            {**_definition().to_dict(), "assets": []},
            ValidationCode.EMPTY_ASSETS,
        ),
        (
            {
                **_definition().to_dict(),
                "assets": [{"symbol": "QQQ"}, {"symbol": "QQQ"}],
            },
            ValidationCode.DUPLICATE_ASSET,
        ),
        ({"assets": [{"symbol": "QQQ"}]}, ValidationCode.MISSING_FALLBACK),
    ],
)
def test_payload_schema_errors_are_returned_without_raising(payload, expected_code) -> None:
    result = validate_strategy(payload)

    assert not result.is_valid
    assert expected_code in _codes(result)


def test_unknown_asset_in_operand_and_allocation_is_rejected() -> None:
    rule = AllocationRule(
        "bad-assets",
        "Bad Assets",
        100,
        (_allocation("SPY", 0.5),),
        _condition(left=_price("SPY")),
    )

    result = validate_strategy(_definition(rules=(rule,)))

    assert not result.is_valid
    assert ValidationCode.UNKNOWN_ASSET_REFERENCE in _codes(result)
    assert len(result.errors) >= 2


def test_duplicate_rule_ids_and_priorities_are_rejected_without_reordering() -> None:
    first = AllocationRule("same", "First", 100, (_allocation("QQQ", 1.0),))
    second = AllocationRule("same", "Second", 100, (_allocation("SGOV", 1.0),))
    result = validate_strategy(_definition(rules=(first, second)))

    assert not result.is_valid
    assert ValidationCode.DUPLICATE_RULE_ID in _codes(result)
    assert ValidationCode.PRIORITY_CONFLICT in _codes(result)
    assert [rule.priority for rule in _definition(rules=(first, second)).rules] == [100, 100]


def test_allocation_sum_must_not_exceed_one_but_cash_remainder_is_allowed() -> None:
    over = AllocationRule(
        "over",
        "Overweight",
        100,
        (_allocation("QQQ", 0.6), _allocation("TQQQ", 0.6)),
    )
    partial = AllocationRule(
        "partial",
        "Cash Remainder",
        90,
        (_allocation("QQQ", 0.4), _allocation("TQQQ", 0.4)),
    )

    over_result = validate_strategy(_definition(rules=(over,)))
    partial_result = validate_strategy(_definition(rules=(partial,)))

    assert ValidationCode.ALLOCATION_EXCEEDS_100_PERCENT in _codes(over_result)
    assert partial_result.is_valid


def test_remaining_allocation_requires_declared_non_explicit_recipient() -> None:
    valid = AllocationRule(
        "remaining",
        "Remaining",
        100,
        (_allocation("QQQ", 0.4), _allocation("TQQQ", 0.4)),
        remaining=RemainingAllocation("SGOV"),
    )
    unknown = replace(valid, remaining=RemainingAllocation("SPY"))
    duplicate = replace(valid, remaining=RemainingAllocation("QQQ"))

    assert validate_strategy(_definition(rules=(valid,))).is_valid
    assert ValidationCode.UNKNOWN_ASSET_REFERENCE in _codes(
        validate_strategy(_definition(rules=(unknown,)))
    )
    assert ValidationCode.INVALID_REMAINING_ALLOCATION in _codes(
        validate_strategy(_definition(rules=(duplicate,)))
    )


def test_minimum_and_maximum_weights_are_validated_without_clamping() -> None:
    invalid = AllocationRule(
        "bounds",
        "Bounds",
        100,
        (_allocation("QQQ", 0.3, minimum_weight=0.4, maximum_weight=0.2),),
    )

    result = validate_strategy(_definition(rules=(invalid,)))

    assert not result.is_valid
    assert ValidationCode.INVALID_MIN_MAX in _codes(result)
    assert invalid.allocations[0].target_weight == 0.3


def test_fallback_must_use_declared_assets() -> None:
    fallback = FallbackAllocation((_allocation("SPY", 1.0),))

    result = validate_strategy(_definition(fallback=fallback))

    assert not result.is_valid
    assert ValidationCode.UNKNOWN_ASSET_REFERENCE in _codes(result)


def test_nested_rule_groups_and_condition_operands_are_validated_recursively() -> None:
    nested = RuleGroup(
        LogicalOperator.OR,
        (
            RuleGroup(LogicalOperator.AND, (_condition(), _condition(right=_ma(20)))),
            _condition(left=_ma(20), right=_ma(50)),
        ),
    )
    rule = AllocationRule("nested", "Nested", 100, (_allocation("SGOV", 1.0),), nested)

    assert validate_strategy(_definition(rules=(rule,))).is_valid


def test_constant_operand_is_valid_and_preserves_strategy_validation_contract() -> None:
    condition = _condition(left=_constant(0), right=_constant(-1.25))
    rule = AllocationRule("constants", "Constants", 100, (_allocation("QQQ", 1.0),), condition)

    assert validate_strategy(_definition(rules=(rule,))).is_valid


@pytest.mark.parametrize("value", [None, nan, inf, -inf, "1.0", True])
def test_constant_operand_invalid_values_are_rejected_at_schema_boundary(value: object) -> None:
    payload = _definition().to_dict()
    payload["rules"][0]["condition"]["left"] = {
        "type": "constant",
        "asset": "TQQQ",
        "value": value,
    }

    result = validate_strategy(payload)

    assert not result.is_valid
    assert ValidationCode.INVALID_CONDITION in _codes(result)


def test_constant_operand_missing_value_is_rejected_at_schema_boundary() -> None:
    payload = _definition().to_dict()
    payload["rules"][0]["condition"]["left"] = {
        "type": "constant",
        "asset": "TQQQ",
    }

    result = validate_strategy(payload)

    assert not result.is_valid
    assert ValidationCode.INVALID_CONDITION in _codes(result)


def test_malformed_constant_operand_value_is_rejected_at_schema_boundary() -> None:
    payload = _definition().to_dict()
    payload["rules"][0]["condition"]["left"] = {
        "type": "constant",
        "asset": "TQQQ",
        "value": {"number": 1},
    }

    result = validate_strategy(payload)

    assert not result.is_valid
    assert ValidationCode.INVALID_CONDITION in _codes(result)


def test_constant_operand_must_not_carry_period_or_price_field() -> None:
    for extra in ({"period": 5}, {"price_field": "adjusted_close"}, {"value": 1}):
        payload = _definition().to_dict()
        constant = {"type": "constant", "asset": "TQQQ", "value": 1, **extra}
        payload["rules"][0]["condition"]["left"] = constant
        if extra == {"value": 1}:
            payload["rules"][0]["condition"]["right"] = {
                "type": "price",
                "asset": "TQQQ",
                "value": 1,
            }

        result = validate_strategy(payload)

        assert not result.is_valid
        assert ValidationCode.INVALID_CONDITION in _codes(result)


def test_constant_operand_payload_round_trips_through_strategy_validation() -> None:
    condition = _condition(left=_constant(3.5))
    rule = AllocationRule("constant", "Constant", 100, (_allocation("QQQ", 1.0),), condition)
    definition = _definition(rules=(rule,))

    restored = StrategyDefinition.from_dict(definition.to_dict())

    assert validate_strategy(restored).is_valid
    assert restored.rules[0].condition.left.value == 3.5  # type: ignore[union-attr]


def test_condition_threshold_must_be_finite_and_price_fields_must_match_strategy() -> None:
    wrong_field = _condition(left=_price(price_field=PriceField.RAW_CLOSE))
    nan_payload = _definition().to_dict()
    nan_payload["rules"][0]["condition"]["threshold"] = {
        "type": "relative",
        "value": nan,
    }

    nan_result = validate_strategy(nan_payload)
    wrong_field_result = validate_strategy(
        _definition(
            rules=(AllocationRule("field", "Field", 100, (_allocation("QQQ", 1.0),), wrong_field),)
        )
    )

    assert ValidationCode.INVALID_THRESHOLD in _codes(nan_result)
    assert ValidationCode.PRICE_FIELD_MISMATCH in _codes(wrong_field_result)


@pytest.mark.parametrize(
    ("field", "value", "expected_code"),
    [
        ("target_weight", -0.1, ValidationCode.INVALID_ALLOCATION),
        ("price_field", "unknown", ValidationCode.INVALID_PRICE_FIELD),
        ("rebalance_policy", {"frequency": "invalid"}, ValidationCode.INVALID_REBALANCE_POLICY),
    ],
)
def test_malformed_json_fields_return_specific_schema_error_codes(
    field: str, value, expected_code: ValidationCode
) -> None:
    payload = _definition().to_dict()
    if field == "target_weight":
        payload["rules"][0]["allocations"][0][field] = value
    elif field == "price_field":
        payload[field] = value
    else:
        payload[field] = value

    result = validate_strategy(payload)

    assert expected_code in _codes(result)


def test_missing_condition_operand_returns_invalid_condition() -> None:
    payload = _definition().to_dict()
    payload["rules"][0]["condition"]["left"] = None

    result = validate_strategy(payload)

    assert ValidationCode.INVALID_CONDITION in _codes(result)


@pytest.mark.parametrize(
    ("mutator", "expected_code"),
    [
        (
            lambda payload: payload.update({"fallback": []}),
            ValidationCode.INVALID_FALLBACK,
        ),
        (
            lambda payload: payload["rules"][0]["allocations"].__setitem__(0, None),
            ValidationCode.INVALID_ALLOCATION,
        ),
        (
            lambda payload: payload["rules"][0]["condition"].update({"right": []}),
            ValidationCode.INVALID_CONDITION,
        ),
        (
            lambda payload: payload["rules"][0]["condition"].update(
                {"type": "group", "operator": "and", "children": ["invalid"]}
            ),
            ValidationCode.INVALID_RULE_GROUP,
        ),
    ],
)
def test_malformed_nested_payloads_return_specific_error_codes(mutator, expected_code) -> None:
    payload = _definition().to_dict()
    mutator(payload)

    result = validate_strategy(payload)

    assert not result.is_valid
    assert expected_code in _codes(result)


def test_invalid_json_returns_schema_error_without_raising() -> None:
    result = validate_strategy('{"strategy_id":')

    assert not result.is_valid
    assert result.errors[0].code == ValidationCode.SCHEMA_ERROR
    assert result.errors[0].path == "$"


def test_validate_or_raise_returns_definition_or_structured_code() -> None:
    validator = StrategyValidator()
    definition = _definition()

    assert validator.validate_or_raise(definition) is definition
    assert validator.validate_or_raise(definition.to_json()) == definition

    with pytest.raises(ValueError, match="AllocationExceeds100Percent"):
        validator.validate_or_raise(
            _definition(
                rules=(
                    AllocationRule(
                        "over",
                        "Overweight",
                        100,
                        (_allocation("QQQ", 0.6), _allocation("TQQQ", 0.6)),
                    ),
                )
            )
        )


@pytest.mark.parametrize("price_field", [PriceField.RAW_CLOSE, PriceField.ADJUSTED_CLOSE])
def test_both_supported_price_fields_are_valid(price_field: PriceField) -> None:
    definition = _definition(
        price_field=price_field,
        rules=(
            AllocationRule(
                "field",
                "Field",
                100,
                (_allocation("QQQ", 1.0),),
                _condition(
                    left=_price(price_field=price_field),
                    right=_ma(50, price_field=price_field),
                ),
            ),
        ),
    )

    assert validate_strategy(definition).is_valid


@pytest.mark.parametrize("threshold", [nan, inf, -inf, 2.0, -0.1])
def test_invalid_rebalance_threshold_is_rejected_at_schema_boundary(threshold: float) -> None:
    payload = _definition().to_dict()
    payload["rebalance_policy"]["threshold"] = threshold

    result = validate_strategy(payload)

    assert not result.is_valid
    assert ValidationCode.INVALID_REBALANCE_POLICY in _codes(
        result
    ) or ValidationCode.INVALID_THRESHOLD in _codes(result)


def test_validator_accepts_json_payload_and_does_not_evaluate_conditions() -> None:
    definition = _definition()
    payload = json.dumps(definition.to_dict())

    result = validate_strategy(payload)

    assert result.is_valid
