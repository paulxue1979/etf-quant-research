# PHASE 11J Wealth Accumulation Comparison

## Scope

PHASE 11J extends the persisted multi-run comparison read model with three opt-in,
USD-denominated wealth capabilities:

- `portfolio_value`
- `capital_invested`
- `investment_profit`

It does not change the backtest engine, contribution execution, analytics formulas,
strategy evaluation, optimization, selection, freeze, or OOS behavior.

## Canonical sources

Portfolio Value is read directly from
`BacktestResult.equity_curve.total_equity`; it is never normalized or rebased.

At each canonical equity observation date `t`:

```text
CapitalInvested(t)
  = BacktestResult.initial_capital
  + sum(ContributionEvent.amount where ContributionEvent.effective_date <= t)

InvestmentProfit(t)
  = PortfolioValue(t) - CapitalInvested(t)
```

The initial capital is present at the first valuation observation. Contributions use
their effective trading date, not their requested calendar date. The projection reads
only canonical `ContributionEvent` records, so retries cannot duplicate capital and
dividends, interest, trade proceeds, SGOV distributions, or ledger transfers cannot be
classified as external capital. Cash remains ledger cash; SGOV remains a security.

Commission and slippage are already reflected in canonical Portfolio Value. Their
effect flows into Investment Profit through subtraction and is not deducted again.

## Shared projection

`backend.app.wealth_projection` is the single read-only wealth projection path. The
single-run report delegates its Capital Invested series to this helper, and comparison
projects all requested wealth capabilities in one pass per selected run. No database
query is performed per series.

Each run retains its own canonical dates. The backend and frontend perform no
interpolation, forward fill, back fill, or cross-run point alignment. Capital Invested
is rendered with a stepped line; Investment Profit supports negative USD values.

## Comparison contract 2.1

The wire contract advances from `2.0` to `2.1` because request and response schemas add
`capital_invested` and `investment_profit`, and run summaries add final invested-capital
and profit scalars. All wealth capabilities default to excluded, preserving old request
behavior and payload size. The frontend accepts both `2.0` and `2.1`; absent wealth
capabilities are treated as unavailable instead of causing a crash.

The Backtest Lab client explicitly opts into all three wealth capabilities. Comparison
remains bounded to 2 through 10 selected immutable runs and loads each full artifact
only once.

## Compatibility

The existing dimension-specific compatibility engine remains authoritative:

- TWR describes strategy performance and keeps cash-flow differences as context.
- Portfolio Value describes actual wealth and applies strict capital-path and execution
  compatibility.
- Investor Experience describes XIRR and retains cash-flow timing context.

New dimensions are `total_capital_invested` and
`effective_cash_flow_sequence`. Total capital is compared from the canonical final
scalar. Timing compares the initial-capital event plus sorted canonical effective
ContributionEvents; schedule labels are not used to infer actual dates.

New reason codes are:

- `DIFFERENT_TOTAL_CAPITAL_INVESTED`
- `TOTAL_CAPITAL_EQUAL_BUT_TIMING_DIFFERS`

The latter is emitted when final total capital matches but the effective cash-flow
sequence does not. Under the existing strict Portfolio Value rules, a lump-sum versus
DCA comparison with different initial capital remains `INCOMPATIBLE` for fair wealth
comparison, while Investor Experience is `WARNING`. The curves remain visible for
descriptive inspection, with the warning shown in the UI; they are not presented as a
pure strategy-performance conclusion.

Existing reasons for initial capital, contribution schedules, effective dates, costs,
execution timing, price field, integer-share behavior, and unavailable dataset versions
remain in force and retain deterministic ordering.

## Frontend semantics

The Wealth Accumulation section has explicit Portfolio Value, Capital Invested, and
Investment Profit modes. It consumes backend points verbatim, reuses the comparison
range and crosshair controller, and shares stable run colors, visibility, and focus.

Final summaries label:

- Portfolio Value as actual wealth
- Capital Invested as external capital
- Investment Profit as dollar gain or loss
- XIRR as investor experience

TWR remains in the independent Strategy Performance chart. The UI does not calculate
profit or XIRR, infer a preferred strategy, or display signal, execution, allocation,
regime, or contribution markers.

## Deferred

Dataset snapshot redesign, payload compression, benchmark wealth overlays, action
markers, regime markers, signal markers, execution markers, walk-forward analysis,
new optimization logic, broker integration, and live trading remain out of scope.
