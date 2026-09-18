# PHASE 9G-F Capital, Contribution, and DCA Research UX

## Scope

PHASE 9G-F adds a capital and contribution read model plus research-facing presentation. It does not change contribution normalization, ledger cash, target allocation, order sizing, execution timing, integer-share behavior, TWR, XIRR, benchmark evaluation, or OOS semantics.

## Canonical ownership

| Concept | Canonical owner | Report source |
| --- | --- | --- |
| Initial capital | Backtest configuration | BacktestResult.initial_capital |
| Requested and effective contribution dates | Contribution normalization | BacktestResult.contribution_events |
| External cash flows | Backtest ledger boundary | BacktestResult.external_cash_flows |
| Cumulative contributions | Backtest result integrity | BacktestResult.cumulative_contributions |
| Total capital invested | Backtest result integrity | BacktestResult.total_capital_invested |
| Ending portfolio value | Portfolio accounting | BacktestResult.final_equity |
| Investment profit | Backtest result integrity | BacktestResult.investment_profit |
| Strategy performance | Performance analytics | PerformanceAnalysisResult.total_return and twr_wealth_curve |
| Investor experience | Performance analytics | PerformanceAnalysisResult.xirr |
| Contribution-triggered execution | Backtest execution | Orders and fills with RebalanceCause.CONTRIBUTION |

The frontend formats and presents these values. It does not sum contributions, calculate profit, solve XIRR, calculate TWR, normalize requested dates, or infer executions.

## Five-concept separation

- Account Value is current portfolio equity.
- Capital Invested is initial capital plus effective external contributions.
- Investment Profit is the canonical dollar difference between account value and invested capital.
- TWR is strategy performance adjusted for external cash-flow timing.
- XIRR is annualized investor experience based on dated cash flows.

Investment Profit is not labeled as return. TWR and XIRR are presented in separate semantic panels.

## Contribution report contract

The version 1.0 report adds a backward-compatible `contribution_report` section with:

- availability status and legacy reason;
- configuration schedule summary;
- deterministic event sequence;
- requested and effective dates;
- canonical amount, currency, and schedule frequency;
- explicit `strategy_signal: false`;
- contribution-caused order/fill counts and symbols;
- event, external-flow, and cumulative-total consistency state.

Multiple events sharing an effective date remain separate rows. New runs with no configured contributions are `available` with an empty event list. Runs predating contribution provenance are `not_available`; they are never reported as known no-DCA runs.

## Execution semantics

A contribution event is an external cash flow, not a BUY signal. `rebalance_executed` means real orders with `RebalanceCause.CONTRIBUTION` exist on that effective date. `no_contribution_rebalance_execution` makes no claim that a separate target-caused execution did or did not occur that day.

This distinction supports Cash100, threshold, and integer-share cases without inventing signals or fills.

## Capital series and chart markers

Portfolio Value continues to use raw equity. Capital Invested continues to use the backend capital series. Same-date Dollar Profit is display-only subtraction in the chart tooltip and is never labeled TWR or XIRR.

Contribution markers use effective trading dates and preserve requested dates in tooltip details. Markers are opt-in so 20-25 years of monthly DCA does not obscure the portfolio chart. They reuse the existing synchronized chart and range controller.

## Frontend states

The capital research panel handles loading, error, available, available-empty, not-available, and not-evaluable states. Contribution events are paginated at 25 rows. The layout keeps labels and text as the primary status channel and supports horizontal table scrolling on narrow viewports.

## Deferred

Performance attribution, MFE/MAE, holding drawdown, withdrawals, tax, fractional shares, optimization, walk-forward analysis, Monte Carlo, broker integration, PDF, and export remain outside PHASE 9G-F.

## Verification boundary

Automated tests cover DCA and no-DCA capital summaries, positive and negative dollar profit, contribution-only TWR/XIRR, weekend date normalization display, same-effective-date provenance, monthly and one-time schedule presentation, contribution execution without a strategy signal, no-execution context, legacy availability, XIRR not-evaluable state, non-finite display protection, 300-event pagination, contribution marker semantics, and marker opt-in behavior.

Real browser visual QA and real Tiingo DCA visual acceptance are separate manual gates and are not claimed by this phase.
