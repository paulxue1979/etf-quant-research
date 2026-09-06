# PHASE 4D RuleGroup Evaluator

**Status:** Implemented
**Date:** 2026-09-07
**Scope:** Pure recursive RuleGroup composition only

## Responsibility

PHASE 4D composes caller-provided `Condition` values and nested `RuleGroup`
values. It delegates every condition to the PHASE 4C `evaluate_condition`
function and returns an immutable, explainable `RuleGroupResult` tree. It does
not access Tiingo, HTTP, a database or the filesystem, and does not resolve
allocations, signals, orders or backtests.

## Boolean semantics

`AND` passes only when every child passes. `OR` passes when at least one child
passes. A child evaluation error is distinct from `FALSE`: all children are
still evaluated, and any error causes `RuleGroupEvaluationError` to propagate
after the complete traversal. Errors are never converted to `TRUE` or
`FALSE`, ignored, or masked by another child.

## Full evaluation and recursion

The evaluator intentionally uses full evaluation rather than short-circuiting.
This preserves every successful child result and every nested error for audit
and debugging. Nested groups are evaluated recursively to any depth supported
by the domain model. Child condition IDs and nested group IDs are deterministic
paths below the caller-provided root `rule_group_id`.

## Result and explainability

`RuleGroupResult` records the group ID, evaluation date, operator, boolean
result, immutable child result tuple and a compact explanation. Child results
are either `ConditionResult` or another `RuleGroupResult`. A propagated
`RuleGroupEvaluationError` records the group ID, date, child errors and all
successfully evaluated child results collected before propagation.

## Data and correctness guarantees

All children receive the same `EvaluationContext` and evaluation date. Price,
indicator, relative-threshold, zero-reference, missing-data and finite-value
semantics remain exclusively owned by PHASE 4C. Cross-asset conditions are
allowed because asset identity remains inside each operand result. The
evaluator does not mutate the rule tree or context and is deterministic for
the same inputs. Prefix consistency at a date is inherited from PHASE 4C and
verified at the group level.

Empty groups and malformed operators remain invalid domain structures; the
evaluator does not invent identity values such as `empty AND = TRUE` or
`empty OR = FALSE`.

## Current limitations

This phase does not implement allocation, target weights, strategy evaluation,
signals, portfolio management, backtest integration, API, frontend, database,
Tiingo access, optimization, parameter sweeps, OOS or walk-forward analysis.
