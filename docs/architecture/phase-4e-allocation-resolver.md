# PHASE 4E Allocation Resolver

**Status:** Implemented  
**Date:** 2026-09-07  
**Scope:** Pure allocation resolution from supplied PHASE 4D results

## Responsibility

PHASE 4E converts a `StrategyDefinition` and caller-supplied
`RuleGroupResult` values into an immutable `TargetAllocationResult`. It does
not evaluate conditions or rule groups again. It does not access Tiingo,
network services, the filesystem, databases, backtests, signals, orders or
portfolio state.

## Resolution semantics

Rules are considered by descending integer priority. A conditional rule is a
candidate only when its supplied `RuleGroupResult` has the requested date and
`passed=True`. The highest-priority matching rule wins. Rules are never merged,
averaged, accumulated or selected from list order. Duplicate priorities and
rule IDs are defensive configuration errors even though PHASE 4B normally
rejects them first.

Unconditional rules are valid matching rules. The explicit fallback is used
only when no rule matches; it is not a competing rule and is never inferred as
100% cash when absent.

## Weight handling

Each explicit allocation must retain its configured weight in `[0, 1]`, and
the explicit sum must not exceed `1`. The resolver never clips, normalizes,
scales or otherwise changes configured weights. A `RemainingAllocation`
receives `1 - explicit_sum`, including the valid zero remainder. Without a
remaining recipient, that same unallocated amount is reported as
`cash_buffer`; it is not assigned to an ETF. Allocations may omit declared
assets, and all referenced assets must belong to the strategy asset set.

Minimum and maximum bounds are checked for consistency with each explicit
target weight. Malformed objects that bypass model construction are rejected
with `InvalidAllocationConfigurationError` rather than producing an invalid
target.

## Output and explainability

`TargetAllocationResult` contains the evaluation date, selected rule or
fallback identifier, fallback usage, deterministic asset ordering, remaining
weight, cash buffer and a compact explanation. Its `weights` view is read-only.
The result and all input domain models are immutable, and repeated resolution
with identical inputs is deterministic.

## Errors and no-lookahead boundary

Missing conditional results, mismatched result dates, malformed mappings and
upstream `EvaluationError` values are explicit errors. Upstream failures are
wrapped as `RuleEvaluationPropagationError`; they are never converted to
`FALSE` or silently routed to fallback. The resolver checks only the supplied
result for the requested date. It consumes a same-date PHASE 4D result and
does not inspect future market data or invoke any evaluator.

## Multi-asset behavior

The model supports single-, dual- and arbitrary multi-asset allocations. QQQ,
TQQQ, SPY and SGOV are test examples only; no ticker is hard-coded into the
resolver. Output ordering follows `StrategyDefinition.assets` for stable
serialization.

## Relationship to earlier phases

PHASE 4D owns recursive `AND`/`OR` rule-group evaluation and returns
`RuleGroupResult`. PHASE 4E owns only the next boundary: selecting an
`AllocationRule` and resolving its target weights. PHASE 3 remains responsible
for backtest portfolio accounting, rebalance execution, cash, positions,
trades, fees and slippage. PHASE 4E does not perform any of those operations.

## Current limitations

This phase does not implement strategy-wide evaluation orchestration, signal
generation, rebalance decisions, execution, portfolio state, persistence,
API, frontend, optimization, OOS or walk-forward analysis.
