# PHASE 4C Operand & Condition Evaluator

**Status:** Implemented
**Date:** 2026-09-07
**Scope:** Pure Operand and Condition evaluation only

## Boundary

PHASE 4C converts caller-prepared market and indicator data into an
explainable `ConditionResult`. The evaluator does not call Tiingo, access a
database or filesystem, calculate indicators, evaluate rule groups, resolve
allocations, generate signals or run backtests.

## Evaluation context

`EvaluationContext` stores:

- asset-keyed `HistoricalDataSet` values for prices;
- asset-qualified `IndicatorSeries` values keyed by `IndicatorKey`.

`IndicatorKey` includes asset, indicator kind, period and `PriceField`, so MA
and EMA series from different assets or price conventions cannot be confused.
The context is frozen and wraps its mappings in read-only proxies.

## Operand semantics

- `PRICE` reads the requested asset and explicit `RAW_CLOSE` or
  `ADJUSTED_CLOSE` value on the requested date.
- `MA` and `EMA` read the exact asset, period, price field and date from a
  precomputed PHASE 2 series.
- `CONSTANT` returns its finite model value and does not read market data;
  its asset field, retained for domain compatibility, is ignored.

Missing assets, dates, indicators and early indicator points raise explicit
evaluation errors. No fill, nearest-date lookup or silent fallback is used.

## Comparison and threshold semantics

All five comparison operators are supported for values without a threshold.
Relative thresholds use the existing signed model:

```text
effective_right = right * (1 + threshold.value)
```

`==` with a relative threshold is rejected as
`INVALID_CONDITION_CONFIGURATION`; exact equality remains available without
a threshold. Absolute thresholds are modeled but reserved for a later phase.
If the relative reference is zero, evaluation raises
`ZERO_REFERENCE_VALUE` rather than emitting `NaN`, `Infinity` or a boolean.

Both operands are resolved for the same requested date. The evaluator never
uses future dates or centered windows. Prefix consistency is tested by
comparing a full context with a context truncated at the evaluation date.

## Explainability and errors

`OperandValue` records value, asset, operand type, price field and date.
`ConditionResult` records the condition ID, both operand results, operator,
threshold, effective right-hand value, pass/fail status and explanation.
Evaluation exception classes expose stable `code` values for callers while
their messages contain no credentials or external service response bodies.

Rule-group composition, AND/OR execution, strategy evaluation, allocation
resolution, signals and backtesting remain outside this phase.
