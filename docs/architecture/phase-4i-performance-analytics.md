# PHASE 4I: Backtest Performance Analytics

## Purpose and boundary

PHASE 4I is a pure, deterministic analysis layer:

```text
BacktestResult
    -> Performance Analytics
    -> PerformanceAnalysisResult
```

`analytics.analyze_backtest()` consumes a completed `BacktestResult`. It does not execute a
strategy, create orders or fills, recalculate portfolio accounting, modify positions or cash,
download market data, access Tiingo, use a database, or expose an API or frontend.

The portfolio equity curve is the only authoritative source for portfolio-return metrics. This
preserves the effect of multi-asset weights, cash, rebalancing, commissions, slippage, and integer
share constraints already applied by PHASE 3.

## Input contract and provenance

The analysis input must be a valid immutable `BacktestResult` with a non-empty, date-ascending,
unique `equity_curve`. Each `EquityPoint` must have finite, non-negative cash, asset values, and
total equity, and must satisfy:

```text
total_equity = cash + sum(asset_values)
```

The first and last equity points must agree with `initial_capital` and `final_equity`. Input trades
must be `Trade` records with finite `pnl` and `pnl_pct`; their declared `holding_period` must match
the PHASE 3 calendar-day difference between entry and exit dates.

`PerformanceAnalysisResult` preserves:

- `strategy_version_id` from `BacktestResult`;
- `price_field_used` from `configuration_snapshot` without changing RAW versus ADJUSTED semantics;
- rebalance frequency from `configuration_snapshot`;
- optional caller-supplied `backtest_run_id` and `strategy_id`.

It also returns a compact provenance mapping that records the equity, return, and trade sources.

## Result status

Every metric is represented as `MetricValue`:

- `available`: a finite numeric value;
- `not_evaluable`: `value=None` and a non-empty reason.

NaN and positive or negative infinity are never returned as metric values. Invalid source data is
rejected with `AnalyticsInputError`; mathematically undefined metrics are returned as
`not_evaluable` instead of inventing a number.

## Configuration and annualization

`PerformanceAnalyticsConfig` explicitly controls:

- `risk_free_rate`: annual effective risk-free rate, default `0.0`;
- `minimum_acceptable_return`: annual effective MAR for Sortino, default `0.0`;
- `periods_per_year`: default `252.0` for daily observations;
- `observation_frequency`: provenance label, default `daily`.

Annual hurdle rates are converted before use in periodic returns:

```text
periodic_rate = (1 + annual_rate) ** (1 / periods_per_year) - 1
```

`periods_per_year` remains an explicit configuration value so later weekly or monthly observation
contracts can choose their own annualization convention without changing metric definitions.

## Portfolio metrics

For equity series `E_t`, periodic returns are:

```text
R_t = E_t / E_(t-1) - 1
```

Returns require a strictly positive prior equity value. The implementation uses sample standard
deviation for volatility and Sharpe calculations.

| Metric | Definition | Not-evaluable condition |
| --- | --- | --- |
| Total Return | `final_equity / initial_capital - 1` | Input is invalid. |
| CAGR | `(final / initial) ** (1 / years) - 1`, where `years = calendar_days / 365` | No positive elapsed calendar period. |
| Annualized Volatility | `sample_std(R) * sqrt(periods_per_year)` | Fewer than two periodic returns. |
| Sharpe Ratio | `mean(R - Rf) / sample_std(R - Rf) * sqrt(periods_per_year)` | Fewer than two returns or zero excess-return volatility. |
| Sortino Ratio | `mean(R - MAR) / downside_deviation * sqrt(periods_per_year)` | No returns or zero downside deviation. Downside deviation is `sqrt(mean(min(R - MAR, 0)^2))` over all returns. |
| Maximum Drawdown | `min(E_t / running_peak_t - 1)` | Input is invalid. A zero drawdown is available as `0.0`. |
| Drawdown Duration | Peak-to-recovery period count for the maximum drawdown; unrecovered uses peak-to-end | Input is invalid. Unit: trading periods in the supplied equity curve. |
| Recovery Duration | Trough-to-recovery period count for the maximum drawdown | Maximum drawdown has not recovered. |
| Calmar Ratio | `CAGR / abs(max_drawdown)` | CAGR/drawdown unavailable or maximum drawdown is zero. |

For a single equity point, Total Return and Maximum Drawdown remain available, while CAGR and
return-series metrics that need elapsed time or returns are `not_evaluable` as appropriate.

## Trade metrics

Trade metrics read only `BacktestResult.trades`, which contains completed PHASE 3 round trips.
Open positions are not synthesized as trades.

| Metric | Definition | Not-evaluable condition |
| --- | --- | --- |
| Number of Closed Trades | Count of `Trade` records | Never; zero is valid. |
| Win Rate | Winning trades (`pnl_pct > 0`) / all closed trades | No closed trades. |
| Profit Factor | Gross positive `pnl` / absolute gross negative `pnl` | No closed trades or gross loss is zero. |
| Average Trade Return | Arithmetic mean of `pnl_pct` | No closed trades. |
| Best / Worst Trade | Maximum / minimum `pnl_pct` | No closed trades. |
| Average Holding Period | Arithmetic mean of `Trade.holding_period` | No closed trades. Unit: PHASE 3 calendar days. |
| Turnover | Not implemented in PHASE 4I | `BacktestResult` has no canonical turnover contract. |

Best, worst, and average trade metrics are returns, not currency P&L. Profit Factor uses trade
currency P&L by definition.

## Future extension points

Benchmark and buy-and-hold analytics are intentionally not implemented: PHASE 4I does not fetch
data or create benchmark portfolio accounting. Future phases may accept explicit benchmark results
or equity series through a separate contract.

This module provides descriptive statistics only. It does not rank a "best" strategy. Robust
strategy evaluation must eventually combine return, drawdown, volatility, risk-adjusted metrics,
trade quality, turnover, stability, out-of-sample testing, and walk-forward validation.
