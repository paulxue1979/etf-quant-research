# PHASE 13L Data Availability and V1.4 Release Candidate

## Purpose

PHASE 13L exposes the existing market-data and indicator preparation contract
before Backtest, Grid, and controlled research execution. It is a read-only
projection. It does not run a strategy, rank candidates, or alter accounting.

## Canonical Availability Contract

The API endpoint `POST /research/data-availability` accepts an immutable
`strategy_id`, `strategy_version_id`, requested dates, and the explicit price
field. It returns:

- requested and expected effective dates;
- the authoritative warm-up request start;
- active-asset observed bounds and bounded snapshot references;
- daily/weekly indicator requirements and first valid dates;
- `INFO`, warning, and blocking issues;
- the strategy version identity, price field, rebalance policy, and execution rule.

The projection reuses `required_indicators`, `required_weekly_assets`,
`prepare_strategy_inputs`, completed-week aggregation, and the existing
requested/effective date boundary validator. It never fabricates pre-inception
prices, forward-fills missing sessions, treats an inactive fallback as an
active asset, or changes the strategy/backtest/OOS semantics.

Natural-calendar boundaries are informational. Effective evidence remains
inside the requested range:

```text
effective_start >= requested_start
effective_end <= requested_end
effective_start <= effective_end
```

## UI Surfaces

Backtest Lab and Experiment Builder display the same availability panel before
execution or immutable experiment creation. The panel distinguishes Requested
Research Range from Expected Effective Trading Range, shows Warm-up Request
Start, Adjusted Close/Raw Close, active-asset observed bounds, timeframe
requirements, warnings, and blocking issues.

## Release Safety Notes

- `PERSISTENCE_CANARY_V14_HOLD` remains persisted as
  `PERSISTENCE_CANARY_V14_HOLD-v5-99a03142359b` with unchanged content hash.
- `HOLD_PREVIOUS_ALLOCATION` with empty fallback remains valid.
- `USE_FALLBACK` with empty fallback remains invalid.
- Adjusted-price dividend handling remains unchanged and is not duplicated by
  the availability projection.
- External Tiingo availability remains an environment prerequisite for local
  real-data acceptance; no mock or synthetic acceptance data is used.
