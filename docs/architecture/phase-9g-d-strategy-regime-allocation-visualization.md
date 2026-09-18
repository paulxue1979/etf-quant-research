# PHASE 9G-D Strategy Regime, Signal/Execution, and Allocation Visualization

## Scope

PHASE 9G-D extends the versioned backtest report read model and the existing synchronized chart stack. It does not change strategy evaluation, signal generation, order sizing, fills, accounting, performance analytics, or OOS semantics.

## Canonical ownership

| Report concept | Canonical owner | Persisted/read source |
| --- | --- | --- |
| Strategy regime and target intent | Strategy evaluation | BacktestRun.strategy_provenance.records |
| Signal date and resolved rule | Strategy evaluation | StrategyBacktestAllocation.signal projection |
| Execution date and cause | Backtest execution | BacktestResult.orders and BacktestResult.fills |
| Target allocation | Strategy evaluation | strategy_provenance.records[].target_allocation |
| Actual allocation | Portfolio accounting | BacktestResult.allocation_history |
| Cash weight | Portfolio ledger | EquityPoint.cash / EquityPoint.total_equity |

Regime is never inferred from orders, fills, positions, or actual allocation. hold_previous is continuity provenance and does not create a signal marker. Meaningful signal markers are emitted only when the canonical rule/source/target decision changes. Execution markers require real persisted orders; contribution-caused orders remain execution events and are not promoted to strategy signals.

## Report contract

GET /research/backtests/{backtest_run_id}/report retains schema version 1.0 and adds backward-compatible detail under:

- strategy_provenance.records: normalized daily decision provenance.
- strategy_provenance.markers: transition signals and actual executions.
- allocations.target.timeline: resolved strategy intent.
- allocations.actual.timeline: portfolio reality after execution.
- allocations.cash_semantics: explicit separation of ledger Cash from SGOV.

Legacy runs continue to report strategy provenance and target allocation as not_available. Actual allocation remains available when its persisted history exists.

## Visualization

The Strategy Regime strip uses the existing Lightweight Charts stack and the same ChartSyncController as portfolio value, TWR, and drawdown. Target and actual allocations are separate charts:

- Target uses step lines to communicate strategy intent.
- Actual uses continuous lines to communicate portfolio state.
- Symbols and legends are generated from report data.
- Cash is a separate category; SGOV remains an invested security.
- Signal markers are placed on signal dates at the close.
- Execution markers are placed on persisted execution dates at the next open.
- Omitted last-day signals retain a signal marker and explicitly state that execution did not occur.

All synchronization is date-keyed. Visible ranges and crosshairs are shared across the three core charts, regime strip, target allocation, and actual allocation.

## Deferred

Holding-period analysis, MFE/MAE, contribution timelines, DCA detail, XIRR explanation, attribution, exports, optimization, walk-forward analysis, and Monte Carlo remain outside PHASE 9G-D.

## Verification boundary

Automated tests cover provenance filtering, HOLD continuity, signal/execution dates, omitted execution, contribution-only rebalance, multi-asset allocation, Cash/SGOV separation, dynamic legends, API serialization, chart adapters, component composition, and shared synchronization.

Real browser visual QA and real Tiingo visual acceptance are separate manual gates and are not claimed by this phase.
