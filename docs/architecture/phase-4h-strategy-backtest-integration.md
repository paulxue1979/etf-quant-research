# PHASE 4H Strategy / Backtest Integration

**Status:** Implemented
**Scope:** Connect PHASE 4G strategy evaluation to the PHASE 3 backtest engine

## Boundary

The integration entry point is:

```text
run_strategy_backtest(strategy_version, evaluation_timeline, data, config)
```

It validates the identity, price-field, rebalance-policy, date, and asset
contracts; converts evaluated `StrategySignal.target_allocation` values into
PHASE 3 `TargetAllocation` values; and calls the existing `BacktestEngine`.
It does not calculate order quantities, prices, fills, positions, cash,
trades, or equity.

```text
StrategyEvaluationTimeline
    -> StrategySignal
    -> TargetAllocation adapter
    -> BacktestEngine
    -> BacktestResult
```

## Time And Error Semantics

The adapter preserves `Signal(T) -> T+1 trading-day open`. An evaluated signal
on the final available trading day is retained in provenance but is marked
omitted because no execution date exists. No calendar date or price is
invented.

Leading `NOT_EVALUABLE` results are treated as indicator warm-up and produce
no allocation. A `NOT_EVALUABLE` result after an evaluated signal, or any
`ERROR` result, stops integration explicitly. Neither status becomes `FALSE`
or fallback.

## Provenance

`StrategyBacktestResult` wraps the existing `BacktestResult` and retains the
full evaluation timeline plus one `StrategyBacktestAllocation` record per
evaluated signal. Each record includes the original signal, converted target
allocation, execution date, submission decision, fallback/rule provenance,
condition explanation, price field, and source-data reference carried by the
signal.

The wrapper does not add a second accounting model. Portfolio accounting and
execution remain exclusively owned by PHASE 3.

## Rebalance And Allocation Semantics

The adapter passes every executable evaluated target to PHASE 3. Daily,
weekly, monthly, on-signal-change, absolute weight threshold, sell-first,
commission, slippage, integer-share, cash-buffer, and remaining-allocation
behavior therefore remain governed by the existing PHASE 3 implementation.

Strategy and backtest configurations must agree on rebalance frequency,
threshold, strategy version, and selected raw/adjusted price field. Market
data must contain exactly the strategy's declared assets and use that same
price field.
