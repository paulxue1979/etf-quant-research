# PHASE 4G Strategy Evaluation

**Status:** Implemented
**Date:** 2026-09-07
**Scope:** Deterministic strategy-evaluation orchestration only

## Goal

PHASE 4G evaluates one immutable `StrategyVersion` over a requested historical
date range and returns an immutable `StrategyEvaluationTimeline`. It composes
the existing PHASE 4C condition evaluator, PHASE 4D rule-group evaluator,
PHASE 4E allocation resolver, and PHASE 4F signal engine. It does not
reimplement their calculation or resolution logic.

```text
StrategyVersion + EvaluationContext + date range
    -> RuleGroup evaluation
    -> Allocation resolution
    -> Signal assembly
    -> StrategyEvaluationTimeline
```

## Scope And Non-Goals

The implementation is pure Python and receives already-prepared market data
and indicator series through `EvaluationContext`. It does not fetch Tiingo
data, write cache files, persist results, call an API, or use global state.

It does not create orders, fills, positions, cash balances, portfolio values,
trades, P&L, equity curves, performance analytics, optimization, OOS,
walk-forward runs, or live-trading behavior. Rebalance-policy metadata remains
inside the strategy version; PHASE 4G evaluates a daily strategy decision and
does not execute a rebalance.

## Input And Output

`evaluate_strategy(strategy_version, context, start_date, end_date, ...)`
requires an explicit inclusive range where `start_date <= end_date`.

The result contains:

- `StrategyEvaluationTimeline`: immutable version ID, requested bounds,
  sorted/unique evaluation results, and optional source-data reference.
- `StrategyEvaluationResult`: date, strategy-version ID, status, full
  per-rule `RuleGroupResult` values, target allocation, signal, explanation,
  optional safe failure summary, and source-data reference.
- `EvaluationFailure`: stable error code plus a concise message. It does not
  retain credentials or raw HTTP data.

Each result is deterministic and JSON-compatible through `to_dict()`.

## Date Alignment And Multi-Asset Policy

PHASE 4G requires validated market data for every asset declared by the
strategy. Its candidate dates are the strict intersection of those assets'
actual input dates, filtered by the requested range and sorted ascending.

No calendar dates are invented. There is no forward fill, backward fill,
nearest-date substitution, use of the next trading day, or implicit alignment.
This conservative policy prevents an evaluation for one strategy asset from
using a different date than another.

The policy is symbol-agnostic. QQQ, TQQQ and SGOV are covered by tests only;
the implementation contains no ticker-specific branches.

## No-Lookahead And Prefix Consistency

For each candidate date `T`, the existing evaluators receive exactly `T` and
look up price and indicator points by exact date. Indicators must already have
been computed by PHASE 2 using their own current-and-prior-only definitions.
PHASE 4G does not inspect values at `T+1` or later.

As a result, extending a data set with later observations cannot change an
earlier result. Evaluating a prefix produces the same corresponding prefix of
the full timeline.

## Price-Field Contract

The strategy definition's `price_field` is the source of truth. PHASE 4G
requires every declared market data request to carry the same
`price_field_used`; a raw/adjusted mismatch produces a per-date `ERROR` with
code `PRICE_FIELD_MISMATCH`. It never converts raw and adjusted values.

The existing PHASE 4C and PHASE 4F checks continue to enforce price-field
consistency across operands, indicators, conditions, and signals. If a required
indicator exists only under a different price field, PHASE 4G returns
`PRICE_FIELD_MISMATCH`; it does not misclassify that configuration error as a
missing indicator.

## Indicator Availability And Errors

PHASE 4G has only three timeline outcomes:

- `evaluated`: all required values were available; target allocation and
  `StrategySignal` are present. A fallback is a valid evaluated signal, not an
  error.
- `not_evaluable`: a required, configured indicator exists but its exact-date
  value is `None`, such as MA/EMA warm-up. No allocation or signal is emitted.
- `error`: missing indicator series/date, price-field mismatch, or any
  PHASE 4C–4F evaluation failure. It is recorded with an explicit failure
  code and never converted to `FALSE`, no-match, or fallback.

An indicator series with malformed ordering, duplicate dates, or non-finite
values is rejected before timeline generation as invalid evaluation input.

## Rule And Signal Explainability

Every evaluated date retains every conditional allocation rule's original
`RuleGroupResult`, including recursive condition results and explanations.
For PHASE 4F signal assembly, PHASE 4G creates a deterministic OR aggregate
of those already-computed results. It does not recompute conditions. The
aggregate means “at least one conditional allocation rule passed”; actual rule
selection still belongs to the PHASE 4E priority resolver.

For a strategy with only unconditional rules, there is no genuine rule-group
result to manufacture. The PHASE 4F signal contract therefore permits
`condition_results=None` only when the strategy has no conditional rules.
Existing conditional strategies still require a real rule-group result.

## Strategy Version, Determinism, And Immutability

Each result stores `StrategyVersion.version_id`; strategy updates cannot alter
an existing timeline result. Inputs and all PHASE 4G result models are frozen.
The engine does not mutate market data, indicator series, context, strategy
definition, strategy version, upstream result, or signal objects.

The implementation uses explicit strategy rule order, sorted date output, and
no network, current-time, random, filesystem, or mutable global input.

## Future Backtest Boundary

PHASE 4G ends with explainable target allocations and strategy signals. A
future Backtest or Portfolio phase may consume a timeline signal to model
execution on a defined later price, orders, fills, holdings, cash, transaction
costs, equity, and performance. None of that state or behavior exists here.
