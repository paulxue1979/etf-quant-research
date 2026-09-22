# PHASE 11D: Deterministic Regime State Machine

## Scope

PHASE 11D adds a generic, strategy-defined regime state machine to the existing
strategy evaluator. It does not define QQQ-specific states, value bands,
recovery rules, position constraints, or UI markers. Those remain outside this
phase.

## Evaluation Modes

`StrategyEvaluationMode.RULE_BASED` remains the default and preserves the
legacy rule, fallback, and `HOLD_PREVIOUS_ALLOCATION` paths. A strategy using
`REGIME_STATE_MACHINE` must use schema `2.0`, declare an explicit
`initial_regime`, at least one `RegimeDefinition`, and valid transitions.

Each regime contains a stable `state_id`, a display-only `display_name`, a
complete `AllocationSpecification`, and optional string metadata. Each
`RegimeTransitionDefinition` contains a stable `transition_id`, source and
target state IDs, an existing `Condition` or `RuleGroup`, a numeric priority,
and an optional description.

## Runtime Semantics

Runtime state is local to one evaluation run. The evaluator starts from
`initial_regime`, allows the initial state's outgoing transitions on the first
evaluation date, evaluates only the active state's outgoing edges, and selects
the matching edge with the lowest numeric priority. Priorities must be unique
per source state, so equal-priority ambiguity is rejected during validation.

At most one transition is selected per date. A state entered on date `T` is
not evaluated for another transition until the next evaluation date. With no
matching transition, the current state and its target allocation remain active
and no transition event is emitted. Self-transitions are rejected because
staying is the explicit no-transition semantic.

The state machine emits one target allocation. It never creates orders. The
existing backtest integration schedules that target for the next common
trading session, preserving the separation between evaluation/transition date
and execution date.

## Provenance

For every evaluable date, evaluation provenance records the active state, state
entry date, previous state, last transition, transition count, evaluated
outgoing evidence, and the final target allocation. A `RegimeTransitionEvent`
is emitted only when the state changes and includes evaluation date,
transition identity, source/target states, state entry date, condition evidence,
target allocation, strategy version ID/hash, and a transition definition hash.
Existing operand evidence retains timeframe and source date, including
completed weekly data semantics.

The regime provenance is carried by `StrategySignal` and therefore follows the
existing `StrategyBacktestResult.signal_records` and OOS provenance paths. No
second evaluator exists in Experiment or OOS execution; both reuse
`evaluate_strategy`. Independent OOS execution starts from the frozen
`initial_regime`, because the OOS window is an independent deterministic
evaluation boundary.

## Identity and Materialization

For schema `2.0`, canonical strategy serialization includes strategy mode,
initial regime, canonical state definitions, target allocations, transition
definitions, priorities, and condition timeframes. These fields are therefore
covered by the StrategyVersion content hash. The existing immutable
materialization path additionally permits safe bindings to regime transition
condition periods/thresholds and regime target weights; legacy binding paths
remain unchanged.

## Explicitly Deferred

Weekly value zones, proximity bands, hysteresis, Recovery Hold templates,
position management, turnover limits, optimization, UI markers, wealth
comparison, and all QQQ/TQQQ-specific business rules are deferred to later
phases.
