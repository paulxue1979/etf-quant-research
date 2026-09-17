# PHASE 9F Capital Contribution / DCA

## Scope

PHASE 9F adds deterministic positive USD capital contributions to the existing
target-allocation backtest pipeline. Contributions are backtest configuration,
not strategy configuration: the same immutable `StrategyVersion` can be run
with or without a contribution schedule while producing distinct run and
experiment configuration identities.

V1.1 supports one-time contributions and monthly contributions requested on
calendar month start. Withdrawals, arbitrary monthly days, fractional shares,
multi-currency cash flows, taxes, broker integration, and report redesign are
out of scope.

## Execution Contract

Each requested contribution date is normalized to the next validated common
trading date. One-time requests before the investment range and events whose
effective date is after the range are excluded. Monthly month-start requests
may normalize to the first common trading date in that month. No market data
is filled or synthesized.

On an effective date the engine applies external cash before the open. A
pending target generated on T therefore sizes against the updated cash at the
T+1 open. When no target is pending, a contribution may independently
rebalance toward the current resolved target. It does not create a strategy
signal, change stateful strategy state, or select a security. Existing
threshold, cost, integer-share, and next-open execution rules remain in force.

Orders record whether the rebalance was caused by a normal target or a
contribution. A contribution that cannot purchase an integer share retains
cash without weakening the insufficient-cash checks for normal target orders.

## Accounting and Analytics

`BacktestResult` preserves requested and effective contribution events,
external cash flows, cumulative contributions, total invested capital, and
investment profit. Raw equity remains the account value and therefore rises
when external cash arrives.

Performance analytics use a time-weighted wealth index. Because contributions
arrive before the open, each period uses
`V_t / (V_(t-1) + CF_t) - 1`; the first observation uses initial capital plus
its same-day flow. Total return, CAGR, volatility, Sharpe, Sortino, drawdown,
and Calmar derive from this flow-adjusted series. Trade statistics continue to
derive only from executed trades. With no contribution, the calculation is
compatible with the existing equity-return series.

## Provenance and Research Integrity

Contribution schedules are canonically serialized with decimal strings so
equivalent values such as `1000` and `1000.000000` have the same identity.
The schedule is included in the backtest snapshot, experiment compatibility
dimensions, frozen research evaluation configuration, OOS configuration hash,
and OOS backtest reconstruction. Exact configuration validation rejects
No-DCA IS paired with DCA OOS and the reverse.

## User Interface

Backtest Lab exposes an enable control, one-time/monthly frequency, positive
USD amount, and a requested date for one-time contributions. Monthly mode
states the month-start rule. Results show initial capital, cumulative
contributions, total invested capital, ending value, and investment profit.
The UI does not ask for an asset because a contribution enters cash rather
than directly creating a purchase.
