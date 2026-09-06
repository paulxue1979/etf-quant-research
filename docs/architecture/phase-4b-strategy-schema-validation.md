# PHASE 4B Strategy Schema & Validation

**Status:** Implemented
**Date:** 2026-09-06
**Scope:** Strategy schema consistency validation only

## Validation boundary

`strategies.validation.StrategyValidator` accepts an existing
`StrategyDefinition`, a JSON-compatible mapping, or a JSON string and returns
an immutable `ValidationResult`. The result contains `is_valid`, structured
`errors`, and `warnings`; each issue has a stable code, field path and safe
message.

PHASE 4A models continue to own field-level construction checks. The validator
owns cross-object and cross-field checks such as declared asset membership,
duplicate rule IDs and priorities, allocation totals, min/max consistency,
fallback membership, price-field consistency and recursive rule-group
validation.

## Implemented rules

- Assets must be non-empty and unique.
- Operands and allocations may reference only declared strategy assets.
- Allocation symbols cannot repeat within one rule or fallback.
- Explicit allocation weights must sum to no more than `1.0`.
- Optional `RemainingAllocation` must name a declared asset that is not already
  explicitly allocated; an omitted recipient leaves an explicit cash remainder.
- Minimum and maximum weights must be ordered and must contain the target
  weight. Values are rejected, never clamped.
- Rule IDs and priorities are unique within a strategy.
- Conditions and nested `AND`/`OR` groups are checked structurally, without
  evaluating market data or thresholds.
- Strategy and operand price fields are restricted to and checked against the
  explicit `RAW_CLOSE` or `ADJUSTED_CLOSE` selection.
- Rebalance frequency and optional threshold use the PHASE 4A contract.
- Explicit fallback allocation is required and validated like any other
  allocation set.

## Deferred behavior

This phase does not calculate MA/EMA, evaluate conditions, calculate
deviation, resolve dynamic allocations, generate signals, place orders,
perform portfolio accounting, run backtests, persist versions, expose an API,
or implement frontend behavior.
