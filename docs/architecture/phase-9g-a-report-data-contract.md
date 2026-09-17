# PHASE 9G-A Report Data Contract

## Scope

PHASE 9G-A adds a versioned, backend-owned report read model. It does not
alter backtest accounting, strategy evaluation, T+1 execution, contribution
timing, FIFO trade accounting, analytics formulas, or research/OOS semantics.

The contract version is `1.0`. It is independent of `BacktestRun` identity,
strategy version identity, and engine version.

## Data Ownership

`BacktestResult` remains execution truth. Account value is projected from
`equity_curve.total_equity`; capital is projected from initial capital and
effective contribution events; investment profit is projected from the
persisted `investment_profit` field. `PerformanceAnalysisResult` remains the
sole source for TWR summary metrics, risk metrics, trade statistics, and the
flow-adjusted drawdown curve. The report layer does not recalculate them.

`BacktestRun.strategy_provenance` is an optional immutable JSON-safe payload.
New normal, experiment, and OOS runs persist compact records from
`StrategyBacktestResult.signal_records`. Each record retains signal date,
matched rule identity, allocation source, resolved target allocation, execution
date/status, and any omission reason. `hold_previous` is continuity provenance,
not a synthetic signal, order, fill, or trade. Legacy runs return an explicit
`not_available` state rather than inferring regimes from execution artifacts.

Open FIFO lot / holding population is not persisted canonically. The report
therefore exposes holdings as `not_available`; it does not infer holdings from
orders, fills, trades, or final positions. Holding segments and related
analytics remain deferred to a later report phase.

## Read APIs

- `GET /research/backtests/{run_id}/report` returns bounded summary sections.
- `GET /research/backtests/{run_id}/report/series` returns only requested
  series using `include`, `from`, and `to` query parameters.

Supported series names are `equity`, `capital`, `twr`, `drawdown`, and
`benchmark`. Date filters only window returned points; they never change the
full-period summary metrics. Equity, capital, and persisted drawdown are
available where their canonical source exists. Normalized TWR wealth and
benchmark series are explicit `not_available` items deferred to PHASE 9G-B.
XIRR is likewise explicit `not_available` in the investor-experience section.

## Integrity and Security

Report responses are deterministic JSON and reject non-finite persisted
strategy provenance. The projection sanitizes sensitive-looking provenance and
configuration keys (`api_key`, `secret`, `token`, and `password`) before they
leave the backend. The report routes are read-only: they do not run a backtest,
fetch market data, or mutate SQLite state.

The existing `/backtests/{run_id}` response remains unchanged. The frontend
only gains TypeScript contract/client methods; no Report 2.0 UI or chart stack
is introduced in this phase.
