# PHASE 4F Signal Engine

**Status:** Implemented
**Date:** 2026-09-07
**Scope:** Pure assembly of immutable strategy signals

## Responsibility

PHASE 4F assembles a `StrategySignal` from a `StrategyVersion`, a supplied
PHASE 4D `RuleGroupResult`, and a supplied PHASE 4E
`TargetAllocationResult`. It preserves upstream output and provenance. It does
not recalculate conditions, rule groups, priorities, fallbacks, remaining
weights or target weights.

## Signal contract

```text
date
strategy_version_id
matched_rule_id
allocation_source: rule_match | fallback
condition_results: complete RuleGroupResult tree
target_allocation: original TargetAllocationResult
price_field_used
explanation
source_data_reference: optional caller-provided metadata
```

`StrategySignal` is immutable. It retains the upstream immutable result
objects without mutating them. `to_dict()` returns a separate deterministic,
JSON-compatible audit snapshot.

## Provenance and consistency

The signal date must equal both upstream result dates. The version ID comes
from `StrategyVersion.version_id`. The strategy definition price field is
recorded as `price_field_used`, and every non-constant price field in the full
condition tree must agree with it. A normal matched rule must exist in the
strategy version, and a conditional matched rule requires a passing rule-group
result. Fallback is distinct: `allocation_source=fallback` and
`matched_rule_id=None`.

## Error and no-lookahead semantics

Upstream `EvaluationError` values become `SignalEvaluationPropagationError`.
They are never converted to false, no-match or fallback. Missing inputs,
inconsistent dates, price fields and invalid provenance produce explicit
errors. The engine only consumes supplied same-date results: it accesses no
market data, future dates, execution data, filesystem, network or global state.

## Scope boundary

PHASE 4F stops at `StrategySignal`. It does not orchestrate strategy evaluation
over dates, create orders, fills, positions or portfolio state, execute
rebalances, run backtests, persist data, expose an API, render a frontend,
optimize parameters, or perform OOS/walk-forward analysis.
