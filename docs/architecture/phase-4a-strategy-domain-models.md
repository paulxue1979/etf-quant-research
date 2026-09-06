# PHASE 4A Strategy Domain Models

**Status:** Implemented
**Date:** 2026-09-06
**Scope:** Immutable Strategy Engine domain models only

## Implemented boundary

The `strategies` package provides structural, immutable and JSON-compatible
models for:

- asset references and asset-qualified price/MA/EMA operands;
- the five comparison operators and signed relative or future absolute
  thresholds;
- recursive AND/OR rule groups;
- target allocations, allocation rules and explicit fallback allocations;
- rebalance policies;
- strategy definitions and immutable version snapshots with canonical content
  hashes.

Each model performs only field-level validation needed to construct a valid
domain object. It does not evaluate conditions, calculate indicators, resolve
allocations, enforce cross-field allocation rules, generate signals, persist
versions, expose an API or implement frontend behavior.

## Data contract

`Operand` carries an asset identity, operand type, optional indicator period
and optional `PriceField`. `StrategyDefinition.price_field` is the explicit
strategy-level price convention. A later evaluator or adapter must enforce
consistency between those fields and the supplied market/indicator data; this
phase does not silently select raw versus adjusted prices.

`Threshold.value` is a signed decimal number. Relative thresholds use values
such as `0.04` and `-0.03`. Absolute thresholds are serializable as a future
extension, but no absolute-threshold evaluation exists in PHASE 4A.

## Deliberately deferred validation and behavior

Allocation sums, duplicate allocation symbols, priority conflicts, fallback
completeness, indicator availability, division-by-zero handling, look-ahead
protection and rebalance execution belong to later phases. Models preserve
the data needed by those phases without implementing their business logic.

`StrategyVersion` computes a deterministic SHA-256 hash from the canonical
serialized `StrategyDefinition`; it does not write to a database or provide
version persistence.
