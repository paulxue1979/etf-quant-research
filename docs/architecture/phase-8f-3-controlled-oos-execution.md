# PHASE 8F-3 Controlled OOS Execution

## Scope

PHASE 8F-3 calculates one claimed, frozen OOS evaluation in memory. It does
not publish an official OOS observation, persist a `BacktestRun`, write an OOS
result repository, advance the Research Protocol, expose an API, or modify the
frontend. Official result finalization remains PHASE 8F-4.

## Data Flow

The service resolves the `SELECTION_RECORDED` protocol, official selection,
strategy freeze, exact persisted `StrategyVersion`, frozen `OosEvaluationSpec`,
and a `RUNNING` execution lease. It then requests each declared asset from
`warmup_start` through frozen `oos_end`, prepares existing indicators, calls
`evaluate_strategy()` only for `[oos_start, oos_end]`, calls
`run_strategy_backtest()`, and passes that result to `analyze_backtest()`.

The returned `OosExecutionOutcome` is immutable and in-memory only. The
execution control record remains `RUNNING`; calculation completion is not an
official OOS observation.

## Boundary and Integrity Rules

- Every data request is constructed server-side as `[warmup_start, oos_end]`.
- No request is open-ended and no `oos_end + 1` data is requested.
- Warm-up data is used only by indicator preparation. Timeline, backtest,
  equity, trades, and analytics are bounded to `[oos_start, oos_end]`.
- The existing integration owns `Signal(T close) -> T+1 trading-day open`.
  A final-day signal with no in-range next date is omitted by that integration.
- All declared strategy assets are fetched and validated without filling,
  forward-filling, back-filling, or synthetic prices.
- Strategy, indicators, data, backtest, and analytics use the frozen price
  field and configuration. Identity, range, and configuration hashes must
  match the claimed execution.

## Failure and Recovery

Lease, protocol, official-observation, identity, range, and configuration
checks happen before market-data access. Data transport failures are marked
retryable with a safe summary; validation, identity, calculation, and range
failures are non-retryable. Failure recording is lease-bound and best-effort
if the worker loses ownership. A successful calculation does not mark the
execution complete, allowing PHASE 8F-4 to decide how the outcome is consumed.

## Provenance and Security

The internal outcome records execution/spec/strategy identity, frozen OOS
dates, actual request warm-up start, price field, data source labels, row
counts, engine and analytics versions, and deterministic hashes. Its repr
contains identifiers only; metrics, equity, trades, credentials, traceback,
filesystem paths, and raw provider responses are not logged or persisted.

No dataset versioning is introduced. Existing cache/API source labels are
retained as minimal provenance.

## Tests

Focused tests cover exact warm-up and OOS boundaries, lease rejection before
data access, multi-asset request bounds, deterministic outcome hashing, and
the invariant that execution remains `RUNNING`. Full regression remains a
release gate, with external Tiingo tests reported separately when DNS/network
access is unavailable.
