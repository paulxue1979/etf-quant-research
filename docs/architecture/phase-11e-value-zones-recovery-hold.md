# PHASE 11E — Weekly Value Zones and Recovery Hold

## Scope

PHASE 11E adds generic, versioned value-zone evidence to the existing regime
state machine. A value zone is a market-evidence definition, not a position or
order instruction. It references one declared signal asset, one daily or
completed-weekly price series, and one MA or EMA series.

The zone distance is deterministic:

```text
reference_price / indicator_value - 1
```

The selected `PriceField`, timeframe, indicator kind, period, thresholds,
comparators, priority, and metadata are serialized in `StrategyDefinition` and
therefore included in the immutable `StrategyVersion` content hash.

## Resolution and Hysteresis

Zones are evaluated through the existing `Operand` and `evaluate_operand`
contract. The resolver emits evidence for every configured zone, including the
evaluation date, source dates, reference price, indicator value, distance,
threshold matches, and current-zone marker.

Resolution is sorted by explicit numeric priority and then `zone_id`. Only one
zone is active. A current zone is retained until its exit band matches. A
higher-priority matching nested zone may take over deterministically. No
condition or transition ordering is used as an implicit zone priority.

`RegimeTransitionDefinition` can consume zone evidence with `MATCH`, `ENTER`,
or `EXIT`. Existing condition groups remain required and are evaluated by the
existing rule-group evaluator. This keeps value evidence separate from regime
semantics while allowing one transition per date.

## Recovery Hold

Recovery Hold is represented by an explicit regime and runtime state history.
For example, a deep-value exit can use a transition tagged `EXIT` for the deep
zone and enter a Recovery regime. The next transition can require a daily
confirmation condition before entering full risk. A later deterioration can
route back to Recovery using an `ENTER` or `MATCH` zone transition. The engine
does not infer Recovery from a single weekly comparison and does not emit
orders directly.

The runtime carries `current_zone_id` by replacement. The strategy definition
and version remain immutable. Each evaluation emits regime provenance and, when
zones are configured, the complete value-zone resolution. Signal and execution
date separation remains owned by the existing backtest integration.

## Timeframe and No-Lookahead Rules

Weekly prices use `DerivedWeeklyDataSet` and weekly indicators use the same
completed-week series prepared by `prepare_strategy_inputs`. A weekly evidence
record carries the completed bar's `available_on` source date. A caller may
evaluate on a later daily date, but never receives a future weekly bar.

Warmup is derived from the union of rule, transition, and value-zone indicator
requirements. Experiment IS and controlled OOS use the same preparation and
strategy evaluator; OOS starts from its declared `initial_regime` and does not
reuse prior live or IS runtime state.

## Materialization and Provenance

The PHASE 8D whitelist supports value-zone period, entry-threshold, and
exit-threshold bindings. Materialization constructs a new immutable derived
strategy version and preserves the base version, parameter-set, binding, and
derived hashes. The base strategy is never mutated.

## Deliberate Non-Goals

This phase does not add position policies, minimum allocation, turnover or cash
limits, chart markers, heatmaps, Pareto views, walk-forward evaluation, AI
optimization, history remediation, or live trading behavior. Those concerns
remain outside the value-zone and Recovery Hold domain.
