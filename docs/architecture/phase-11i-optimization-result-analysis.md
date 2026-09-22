# PHASE 11I Optimization Result Analysis

## Scope

PHASE 11I is a read-only, IS-only research layer over persisted grid-search
candidate results. It does not execute candidates, calculate new financial
metrics, select a candidate, freeze a strategy, or read official OOS results.

The analysis contract is versioned as `phase-11i.1` and supports at most 1,000
candidates per request.

## Lightweight Projection

`OptimizationResearchRepository` executes one experiment-scoped SQL query. It
joins candidate coordinates, execution state, and immutable result summaries.
The query extracts only allowlisted scalar analytics with SQLite JSON paths.
It does not select `result_json`, load `run_json`, or query BacktestRun records.

Each `OptimizationCandidateSummary` contains:

- experiment, candidate, parameter-set, and candidate-set identity;
- canonical parameter values;
- execution/result state and safe failure information;
- derived StrategyVersion and BacktestConfiguration identity;
- BacktestRun reference;
- exact IS dates and engine/analysis versions;
- projected canonical metrics and explicit availability.

The repository validates the frozen ParameterSpace, ParameterSet hash,
candidate identity, IS boundary, and engine/analysis versions before returning
the projection.

## Metric Registry

The allowlist is backend-owned. No arbitrary metric field or formula is
accepted.

| Metric | Direction | Source |
| --- | --- | --- |
| CAGR | MAXIMIZE | canonical analytics `cagr` |
| Total TWR Return | MAXIMIZE | `total_return` |
| Max Drawdown | MAXIMIZE | canonical negative `max_drawdown` |
| Sharpe Ratio | MAXIMIZE | `sharpe_ratio` |
| Sortino Ratio | MAXIMIZE | `sortino_ratio` |
| Calmar Ratio | MAXIMIZE | `calmar_ratio` |
| Annualized Volatility | MINIMIZE | `annualized_volatility` |
| Realized Turnover | MINIMIZE | top-level canonical `turnover` |
| Closed Trades | MAXIMIZE | `trade_metrics.number_of_closed_trades` |
| Average Holding Period | MAXIMIZE | `trade_metrics.average_holding_period` |
| Average Gross Exposure | MINIMIZE | `exposure_summary.average_gross_exposure` |
| Time Invested | MAXIMIZE | `exposure_summary.time_invested_fraction` |
| XIRR | MAXIMIZE | canonical `xirr`, investor-experience category |
| Final Portfolio Value | MAXIMIZE | `final_equity`, not Pareto-eligible by default |

Max Drawdown storage is unchanged. A canonical value of `-0.20` is better than
`-0.80`, so the research direction is MAXIMIZE. The maximum-drawdown filter is
expressed as an explicit magnitude and checks `abs(max_drawdown) <= limit`.

Turnover always uses analytics canonical realized turnover. It never uses the
PHASE 11F pre-trade turnover estimate or policy trade budget.

Metric availability is one of `AVAILABLE`, `UNAVAILABLE`, `NOT_APPLICABLE`, or
`FAILED`. Missing values are never converted to zero.

## Filters

Filters are typed, deterministic, backend-owned, and combined with AND
semantics. Supported predicates include status, candidate IDs, minimum closed
trades, maximum realized turnover, maximum drawdown magnitude, minimum CAGR,
minimum Sharpe, exposure bounds, and exact/range parameter predicates.

A filter only defines the visible research universe. It does not create a
SelectionDecision and does not modify neighbor topology.

## Heatmap

A heatmap request names two distinct axis parameters, one allowlisted metric,
and a fixed value for every remaining tunable parameter. Incomplete or extra
slice fields are rejected as `AMBIGUOUS_SLICE`. A cell that maps to multiple
candidates is rejected as `AMBIGUOUS_HEATMAP_CELL`.

Axis order comes from the frozen ParameterSpace domain. Numeric values retain
numeric grid order; enum/discrete values retain the canonical order used by
candidate generation. There is no aggregation and no interpolation.

Cells distinguish available, excluded, failed, unavailable/not-applicable, and
pruned/not-generated coordinates.

## Pareto Frontier

Pareto analysis accepts two to five unique, Pareto-eligible metrics. Candidate
A dominates B only when A is no worse on every objective according to each
registry direction and strictly better on at least one objective. Identical
objective vectors are all retained on the frontier. Failed candidates and
candidates missing any requested metric are excluded with explicit reasons.
Membership and deterministic candidate-index ordering are backend-owned.

## Neighbor Stability

The topology is the frozen ParameterSpace, not the current filter result. A
direct neighbor differs in exactly one parameter by exactly one canonical grid
step. Diagonal candidates are not direct neighbors. Missing constrained points
remain `PRUNED_OR_NOT_GENERATED`; failed points remain failed. A farther point
is never substituted.

The response reports expected/available counts, center value, neighbor median,
minimum, maximum, mean, population standard deviation, MAD, worst deterioration,
and median deterioration. Direction-aware deterioration is:

- MAXIMIZE: `center - neighbor`;
- MINIMIZE: `neighbor - center`.

A positive value therefore means the neighbor is worse.

## Sensitivity

Sensitivity fixes every parameter except one sweep parameter and projects one
or more existing metrics over the full canonical domain. Pruned, failed,
excluded, and unavailable points remain gaps. Neither the backend nor frontend
interpolates across a gap.

## Integrity Boundaries

- All source metrics are persisted IS analytics.
- OOS metric IDs are absent from the registry and cannot be requested.
- Official OOS repositories are not queried.
- No financial formula or BacktestResult is changed.
- No candidate is labeled Best, Winner, or Optimal.
- No SelectionDecision, StrategyFreezeRecord, or OOS execution is created.
- Full backtests are only available through existing explicit detail flows.

## Frontend

The Experiment Research page includes a compact Optimization Results Explorer
with tabs for results, heatmap, Pareto, stability, and sensitivity. Applied
filters and fixed slices remain visible. Pareto coordinates are rendered in the
browser, but frontier membership comes only from the backend. Sensitivity line
segments are drawn only between adjacent available observations.
