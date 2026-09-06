# PHASE 3 - Backtest Engine

## Scope

PHASE 3 implements deterministic portfolio execution from a precomputed
`TargetAllocation` sequence. Strategy evaluation, performance analytics,
optimization, and frontend workflows remain outside this phase.

## Input Contract

`BacktestEngine.run(data, target_allocations, config)` accepts a mapping of
ticker to validated `HistoricalDataSet`, date-ordered `TargetAllocation`
objects, and a `BacktestConfig` with an explicit `PriceField`, date range,
initial capital, commission, slippage, execution rule, and rebalance policy.

Target weights are non-negative and total at most `1.0`. Unallocated weight
remains cash. Margin, borrowing, and fractional shares are not supported.

## Execution and Accounting

The only execution rule is:

```text
Signal at T close -> next trading day T+1 open
```

The engine never substitutes T close or T+1 close when T+1 open is absent.
Orders are deterministic, sell orders execute before buy orders, and buy
quantities use `floor(target_value / execution_price)`. Commission and
slippage are applied explicitly. An unaffordable buy is reduced to the
largest fundable integer quantity, preserving residual cash without borrowing.

Each day is marked to the selected raw or adjusted close and satisfies:

```text
Equity = Cash + sum(asset market values)
```

Completed trades use FIFO lots and retain entry/exit dates, prices, quantity,
P&L, P&L percentage, and holding period.

## Rebalance Policy

Supported frequencies are `daily`, `weekly`, `monthly`, and
`on_signal_change`. Weekly and monthly policies use the first supplied target
date in each ISO week or calendar month. `threshold`, when configured, is an
absolute portfolio-weight difference; a cycle is skipped when every asset is
below that threshold.

## Result and Provenance

`BacktestResult` contains daily equity and cash history, portfolio snapshots,
allocation history, orders, fills, completed trades, the strategy version ID,
the full configuration snapshot, data source/request references, and the
engine version. Raw and adjusted price use is always recorded as
`price_field_used`.

## Boundary

The engine does not implement MA/EMA calculation, condition evaluation,
strategy rules, trading signals, performance metrics, optimization, or UI.
