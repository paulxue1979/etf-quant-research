# PHASE 10B Versioned Multi-Strategy Comparison Contract

## Boundary

`POST /research/comparisons` remains the only multi-run comparison route. The
canonical selection identity is `BacktestRun`; a strategy version is display
metadata and cannot substitute for a run's date range, capital configuration,
costs, data provenance, or execution context.

PHASE 10B is a read-only projection. It does not execute strategies, rerun
backtests, recompute analytics, align dates, or persist comparison records.

## Request

The request accepts 2 through 10 unique persisted run identifiers in the order
the caller wants them returned:

```json
{
  "backtest_run_ids": ["backtest-a", "backtest-b"],
  "include": {
    "twr": true,
    "drawdown": true,
    "portfolio_value": false,
    "metrics": true
  }
}
```

Unknown fields, blank or duplicate identifiers, and run counts outside the
bounded range are validation errors. Any unknown run causes a 404 rather than a
partial comparison. Raw portfolio values are excluded by default to keep the
usual 10-run response bounded.

## Versioning And Legacy Fields

The response declares `comparison_schema_version: "2.0"`. This version is
independent of Report 2.0's `report_schema_version`.

The PHASE 6 `comparable`, `incompatibility_reasons`, `runs`, and `series` fields
remain present. Within each legacy series item, `equity_curve` is populated only
when `include.portfolio_value` is true; `drawdown_curve` follows the drawdown
include flag. Consumers should migrate to the versioned `twr`, `drawdown`, and
`portfolio_value` capability objects.

## Canonical Series

TWR points come only from
`PerformanceAnalysisResult.twr_wealth_curve`. The projection rebases the first
persisted valid wealth point to 100:

`base_100 = canonical_wealth / first_canonical_wealth * 100`

The response records the source, first valid date and value, base value, and
`rebase_first_valid_point` method. This is a display normalization, not a TWR
recalculation. Drawdown points are copied verbatim from
`PerformanceAnalysisResult.drawdown_curve`. Portfolio values, when requested,
come directly from `BacktestResult.equity_curve.total_equity`.

Each run keeps its own real dates. The projection never forward-fills,
backfills, interpolates, or aligns observations by array index.

## Compatibility

Compatibility is reported independently for `twr`, `portfolio_value`, and
`investor_experience`. Each dimension has one of `COMPARABLE`, `WARNING`,
`INCOMPATIBLE`, or `UNKNOWN`, plus stable reason codes, human-readable reasons,
and the per-run values used by the decision.

TWR is strategy performance. Different initial capital or external cash flows
produce warnings rather than an automatic rejection because TWR removes cash
flow timing mathematically. The warning also records integer-share path
dependency: capital, contribution-funded rebalances, cash drag, and allocation
granularity can still alter the executed path.

Portfolio value is an account outcome. Different initial capital, cash-flow
schedule or effective dates, execution semantics, costs, price field, or date
range make raw-value comparison incompatible. The API may still return the
requested values, but explicitly does not call that a fair performance ranking.

XIRR is investor experience, not strategy performance. Different cash-flow
contexts are warnings, and unavailable XIRR is represented as unknown rather
than zero.

## Metrics And Provenance

Metrics are projections of persisted backend analytics. Missing values retain
their availability status and reason. The projection includes CAGR, total TWR
return, max drawdown, Sharpe, Sortino, Calmar, XIRR, exposure summary, canonical
portfolio turnover, closed trade count, and holding-period count when present.

Every run includes engine and analytics versions, price field, execution
semantics, contribution configuration and effective events, cost model, asset
universe, benchmark capability, and market-data references. Secrets are
redacted recursively. Since current artifacts do not always contain an
immutable dataset version or content hash, compatibility emits
`DATASET_VERSION_UNAVAILABLE` instead of claiming unconditional comparability.

## Determinism And Scale

Runs and series always follow request order. Optional unavailable metrics do not
fail the comparison; unreadable or missing run artifacts do. The contract is
tested with 10 runs containing 6,000 TWR and 6,000 drawdown observations each.
No downsampling is introduced in PHASE 10B.
