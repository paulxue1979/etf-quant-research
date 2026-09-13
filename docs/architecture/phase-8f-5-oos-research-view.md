# PHASE 8F-5: OOS Read API / Research View

## Purpose

PHASE 8F-5 exposes the official, finalized out-of-sample observation as a
read-only research view. It does not create a new research fact, rerun a
strategy, recalculate analytics, or select a candidate.

## Backend Composition

`GET /research/protocols/{protocol_id}/oos` composes persisted records from:

- `ResearchProtocolRepository` for protocol, selection, freeze, and observation state;
- `OosResultRepository` for the immutable official OOS summary and provenance;
- `BacktestRepository` for detailed accounting, orders, fills, trades, positions, and allocation history;
- `StrategyRepository` for the frozen strategy identity and content hash.

The view layer is an adapter/read model. It does not call the data service,
indicator engine, strategy evaluator, signal engine, backtest engine, or
performance analytics service.

## Finalization and Integrity Rules

Only protocols in the finalized OOS states are readable. The service rejects
missing or tampered official results, missing backtest runs, mismatched
strategy identities, mismatched hashes, mismatched OOS dates, mismatched price
fields, mismatched frozen configuration, and analytics summaries that differ
from the persisted backtest analysis.

The official result must reference an observation in `OBSERVED` or `SEALED`
state and the same persisted `BacktestRun`. This keeps the OOS result as the
summary/provenance source and the `BacktestRun` as the detailed accounting
source of truth.

## Data Boundaries

The response makes the research boundaries explicit:

- the requested IS range remains separate from the OOS range;
- warm-up begins before OOS only to initialize indicators and is excluded from
  OOS performance statistics;
- the OOS result contains OOS observations only;
- execution remains `Signal(T Close) -> T+1 Trading Day Open`;
- portfolio initialization is fresh capital and the signal policy is OOS-only.

The read API validates these boundaries but never extends the OOS range or
requests additional market data.

## Frontend Contract

The frontend renders backend-provided metrics, equity, drawdown, trades,
orders, fills, allocation history, positions, and provenance. It performs
formatting and visualization only. It does not recompute returns, risk
statistics, drawdown, trade statistics, or trading decisions.

The view intentionally has no controls for rerun, reselection, mutation,
recommendation, optimization, or winner selection. `NOT_EVALUABLE` metrics are
displayed as `Not Evaluable` without substituting a value.

## Error Contract

Expected failures are returned as structured API errors, including
`OOS_NOT_FINALIZED`, `OOS_RESULT_NOT_FOUND`, `OOS_BACKTEST_RUN_NOT_FOUND`, and
`OOS_PROVENANCE_INTEGRITY_ERROR`. Internal stack traces, credentials, and
filesystem details are not exposed to the frontend.

## Scope Boundary

This phase does not implement OOS execution, finalization, selection,
recommendation, optimization, or dataset versioning. PHASE 8F-6 remains
outside the current scope.
