# PHASE 11K Backtest Event Markers

## Scope

PHASE 11K adds a versioned, read-only event-marker projection for one immutable
backtest run. It annotates the single-run Portfolio Value and TWR charts without
changing strategy evaluation, signal generation, execution, accounting, performance
analytics, selection, freeze, or OOS behavior. Multi-run comparison markers remain
off and are deferred.

## Contract and ownership

`GET /research/backtests/{run_id}/report/markers` is an opt-in endpoint. Existing
report and series payloads remain unchanged. The marker schema version is `1.0`.
Markers are derived on demand and are not persisted; canonical backtest and strategy
events remain authoritative and can reproduce the projection.

The backend owns marker type, event date, canonical source, before/after values,
significance inputs, deterministic ordering, and same-day group membership. The
frontend owns icon, placement, category visibility, tooltip formatting, and display
density. It does not infer events from prices, positions, equity, or target changes.

Marker IDs are SHA-256 hashes of run ID, marker type, event date, and the canonical
source reference. Group IDs hash the ordered marker IDs for one event date. Current
time and random identifiers are never used.

## Canonical source mapping

| Marker type | Canonical source | Event date |
| --- | --- | --- |
| `SIGNAL` | `BacktestRun.strategy_provenance.records` persisted from `StrategyBacktestResult.signal_records` | signal/evaluation date |
| `EXECUTION` | `BacktestResult.fills`, enriched only by matching canonical `Order` identity | fill date |
| `REBALANCE_DECISION` | `BacktestResult.rebalance_decisions` | decision evaluation date |
| `CONTRIBUTION` | `BacktestResult.contribution_events` | effective contribution date |
| `TARGET_ALLOCATION_TRANSITION` | adjacent target allocations in strategy execution provenance | signal/evaluation date |
| `ACTUAL_ALLOCATION_TRANSITION` | `BacktestResult.allocation_history` on dates with canonical fills | execution/allocation date |
| `REGIME_TRANSITION` | persisted `RegimeTransitionEvent` inside strategy provenance | regime evaluation date |

Target and actual allocations are separate marker types. Target transitions never use
fills as their source. Actual transitions are emitted only when the canonical actual
allocation changes on a real fill date, so market drift alone does not create daily
markers. Ledger Cash is projected as `CASH`; SGOV remains a security.

## Signal and execution timing

`hold_previous` does not produce a signal marker. Repeated rule decisions with the
same rule and target do not produce a new marker. A state-machine transition does
produce a signal and regime marker even when its target is unchanged, but it does not
fabricate an allocation transition.

A final-day signal without a next trading session is retained with
`NO_EXECUTION_SESSION`. It never creates an execution marker. Friday evaluation and a
Tuesday fill after a Monday holiday therefore remain distinct event dates.

Execution markers are grouped by fill date, related signal date, and rebalance cause.
Their details retain every fill's side, symbol, quantity, price, notional, commission,
slippage, and order reference. An order without a fill is never presented as an
executed BUY or SELL.

## Rebalance and contribution semantics

`EXECUTE`, `PARTIAL`, and `SUPPRESS` rebalance decisions are projected directly.
Suppression reason, all decision reasons, canonical target/actual allocations,
target-change metric, drift metric, turnover estimate, contribution context, and
expected execution date remain available. A suppressed decision never creates an
execution marker.

Contribution markers use effective date and remain external cash flow, not strategy
signals. Requested date and cumulative invested capital are details. Contribution and
rebalance events on the same day retain separate identities.

## Regime and value-zone evidence

Only a persisted `RegimeTransitionEvent` creates a regime marker; a state-machine stay
does not. Transition ID, from/to states, target allocation, strategy identity, compact
transition evidence, and compact Value Zone evidence are projected. Value Zone detail
includes asset, timeframe, reference price, indicator, period, indicator value,
distance, thresholds, and source date when canonically available. The full provenance
tree is not copied into each marker.

## Grouping, filters, and density

Backend marker ordering is:

1. Contribution
2. Signal
3. Regime transition
4. Target allocation transition
5. Actual allocation transition
6. Rebalance decision
7. Execution

Same-day grouping is presentation-only. A group references every ordered underlying
marker ID; the marker list remains intact. Next-session fills stay in their later-date
group.

The endpoint allowlists marker types and supports validated `from`/`to` dates,
same-day grouping, and major-only filtering. Major-only requires an explicit threshold.
Regime transitions and contributions are always major display events. Other markers
use backend-provided significance metrics:

- target/signal/rebalance/actual allocation: `0.5 * sum(abs(after_i - before_i))`,
  including implicit Cash;
- execution: gross one-way fill notional divided by canonical same-date portfolio
  value.

The threshold comparison is `metric >= threshold`. This is display filtering, not a
financial assertion or analytics turnover. Major-only is off by default and the UI
shows its threshold. Category and major filters report hidden counts. No count limit or
silent truncation exists; same-day grouping is the default density protection.

## Frontend behavior

The Backtest Lab explicitly requests the marker endpoint. Portfolio Value and TWR use
the same marker stream and exact event dates. Default visible categories are Execution,
Regime Transition, and Target Allocation Transition. Signals, Rebalances,
Contributions, and Actual Allocations are opt-in. Multiple same-day events render as
one marker with all underlying event summaries and details.

Markers annotate TWR by time only; their vertical location does not imply that event
values are TWR values. Drawdown and multi-strategy comparison markers are deferred.
Labels are rendered as React text and never through unsafe HTML.

## Integrity boundaries

Marker projection performs no P&L, return, XIRR, TWR, drawdown, Sharpe, Sortino,
Calmar, exposure, canonical turnover, capital-invested, or investment-profit
calculation. It does not alter T+1, sell-first, FIFO, integer-share, cost, contribution,
rebalance, state-machine, Value Zone, selection, freeze, or OOS semantics.

The endpoint validates marker categories and dates through typed FastAPI inputs. It
does not evaluate expressions, execute code, query arbitrary paths, or expose secrets.
Payloads contain compact marker details and do not duplicate equity curves, positions,
order history, full strategy configuration, or full provenance trees.

## Acceptance coverage

The focused backend suite covers HOLD suppression, final-day unexecuted signals,
BUY/SELL fill grouping, contribution-without-signal semantics, execute/suppress
rebalance decisions, target versus actual allocation, Cash versus SGOV, deterministic
identity and ordering, category/date/major filters, same-day groups, a 6,000-observation
dense projection, and the path NORMAL → APPROACHING_VALUE → VALUE → DEEP_VALUE →
RECOVERY_HOLD → FULL_RISK_ON → RECOVERY_HOLD.

A local real-Tiingo-cache integration runs QQQ, TQQQ, SPY, IWM, and SGOV through the
existing generic multi-asset strategy, Daily evaluation, monthly contributions,
backtest fills, analytics, persisted strategy provenance, and marker projection. It
checks effective contribution dates, later execution dates, target/actual allocation,
and non-truncated output without making a network request or exposing credentials.

The final local backend projection benchmark used dense, distinct signal dates and
reported:

| Markers | Projection | Compact JSON payload |
| ---: | ---: | ---: |
| 100 | 1.586 ms | 112,536 bytes |
| 500 | 7.431 ms | 560,936 bytes |
| 1,000 | 15.109 ms | 1,121,440 bytes |
| 6,000 | 96.746 ms | 6,731,440 bytes |

The payload is intentionally not truncated. The frontend initially requests only its
three visible categories and fetches a newly selected category set on demand, so hidden
categories do not impose this worst-case payload by default. Same-day grouping and
explicit major/category filters control render density while retaining hidden counts.

Weekly Value Zone and real-browser Recovery Hold marker acceptance remain part of the
PHASE 11L release gate; synthetic canonical-event coverage in PHASE 11K does not claim
that those environment-dependent checks were executed.

## Deferred

Focused-run markers in multi-strategy comparison, price-chart markers, drawdown
markers, server-side visible-range pagination, persisted marker tables, and real-browser
long-history visual acceptance remain outside PHASE 11K and belong to PHASE 11L or a
later approved phase.
