# PHASE 13S Controlled Backtest Operator

## Permission boundary

Codex MCP can resolve and read immutable strategy versions, prepare a durable
configuration, and read a completed result. It has no approve, execute, reject,
delete, StrategyVersion creation, Grid, Selection, Freeze, or OOS tool. The
browser-facing decision endpoints require the same local Origin gate as Draft
ChangeSet approval. Preparing a configuration creates no BacktestRun or trade.

## Exact binding and approval

The configuration stores the exact StrategyVersion ID and content hash, the
validated BacktestRequest, the original requested dates, and a cache-only
Data Availability projection. Saying "latest" resolves the latest immutable
version at preparation time; execution never resolves it again. Date input to
the MCP tool is exact ISO `YYYY-MM-DD`; ambiguous natural-language dates must
be clarified before calling prepare. The browser shows the actual Backtest Lab
fields prefilled and locked for review. Manual form state is separate.

Approval reloads the persisted configuration, checks its content hash, reloads
the exact StrategyVersion, validates the BacktestRequest, and reruns Data
Availability. If the exact version or effective/data snapshot evidence has
changed, the plan becomes `STALE` and cannot execute. A conditional
`PROPOSED -> RUNNING` transition prevents two
approval requests from claiming the same configuration. The existing
BacktestService computes the run without writing it; the existing
BacktestRepository persists that run and the `COMPLETED` configuration in one
SQLite transaction. The run remains classified as `MANUAL` and carries
`prepared_by=CODEX_MCP`, `approved_by=HUMAN`, and `config_id` provenance.
Repeating approval returns the same completed configuration and run ID.
If run computation fails before that transaction, the configuration becomes
`FAILED` with a safe code. A response failure after commit cannot overwrite
the canonical `COMPLETED` state.

## Contribution contract

The current production engine supports only `one_time` and `monthly` external
cash-flow schedules. A monthly schedule spans the entire effective backtest
window and requests month starts, moving each request to the next common
trading date where necessary. The preparation tool rejects weekly frequency
and independent monthly start/end windows. It never silently approximates
them. Cash-first accounting, TWR, XIRR, and rebalance semantics remain in the
existing engine. No contribution is treated as a strategy signal.

## Data and operational limits

MCP preparation uses the existing read-only, cache-only Data Availability
projection. A cache miss blocks approval; it is not evidence that market data
does not exist. The browser displays the frozen projection, and approval
rechecks availability before execution. An interrupted `RUNNING` configuration
is not automatically replayed: replay could create a second run if execution
had reached an unknown external boundary. It requires explicit operational
inspection before a new configuration is prepared. No strategy Draft or
StrategyVersion is modified by this operator.
