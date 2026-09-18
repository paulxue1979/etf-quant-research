# PHASE 9G-E Canonical Holding Period Report

## Scope

PHASE 9G-E adds an immutable FIFO holding artifact, a paginated report projection, and a read-only holding table. It does not change order sizing, execution timing, cash accounting, FIFO matching, trade P&L, portfolio positions, performance analytics, or OOS behavior.

## Canonical ownership

| Concept | Canonical owner | Persisted source |
| --- | --- | --- |
| Entry and exit execution | Backtest engine | Filled order identity and execution date |
| FIFO lot basis | Backtest ledger | BacktestResult.holding_segments |
| Closed holding P&L | Existing FIFO close path | HoldingSegment.realized_pnl and matching Trade.pnl |
| Open holding valuation | End-of-run mark to market | HoldingSegment ending price, market value, and unrealized P&L |
| Signal context | Strategy evaluation provenance | BacktestRun.strategy_provenance.records |
| Report view | Read-only projection | BacktestReportProjectionService.holdings |

`Trade` remains the realized close-fragment view. `Position` remains an aggregate portfolio snapshot. Neither is used to reconstruct historical or open FIFO lots.

## Holding segment contract

A holding segment represents a positive integer quantity from one real BUY lot. A CLOSED segment terminates at a matching FIFO SELL fill. An OPEN segment terminates at the effective report end for valuation only and has no exit execution.

Identity is deterministic:

- Lot identity hashes the symbol and entry order identity.
- Closed segment identity hashes the lot, exit order, and close-fragment sequence.
- Open segment identity hashes the lot and an OPEN marker.

These identities are scoped by the enclosing immutable backtest run; the report's canonical row identity is `(backtest_run_id, holding_id)`.

Execution dates own holding duration. Signal dates are optional context and never replace entry or exit execution dates. Duration is calendar days between the entry execution date and the exit execution date or report end.

## Accounting semantics

The entry basis uses the existing executed BUY price plus allocated BUY commission. That execution price already contains configured slippage. Closed holding P&L and return reuse the existing FIFO close calculation:

`realized_pnl = net_sell_proceeds - entry_basis`

`holding_return = realized_pnl / entry_basis`

Open holdings use the canonical report-end price selected by the backtest price field:

`unrealized_pnl = ending_market_value - entry_basis`

`holding_return = unrealized_pnl / entry_basis`

No cost is deducted twice. Cash is excluded from holding segments; SGOV and every other supplied security remain ordinary assets.

## Signal and contribution provenance

Rule and allocation-source labels are joined only through persisted strategy provenance for the exact signal date. Target-caused entry and exit executions require a signal date. Contribution-caused execution may have no strategy signal and is shown as `N/A`; no signal is synthesized from an execution date.

## Report API

`GET /research/backtests/{backtest_run_id}/report/holdings` supports:

- `status=ALL|OPEN|CLOSED`
- case-normalized `symbol`
- bounded `limit` and non-negative `offset`
- sorting by entry date, exit date, symbol, holding return, P&L, or duration
- ascending or descending order

The response remains under report schema version 1.0 because the endpoint and root holdings summary are backward-compatible additions. Runs created before canonical lot persistence return `not_available`; the service never reconstructs lots from aggregate positions.

## Metric availability

MFE, MAE, and holding drawdown are explicitly `not_available`. The immutable result does not persist a lot-level market price path with sufficient provenance to compute those metrics without rereading or inferring data. Portfolio drawdown is not substituted for holding drawdown, and missing values are never represented as zero, NaN, or Infinity.

## Frontend

The holding report is loaded from the backend projection. The frontend does not perform FIFO matching. It displays OPEN and CLOSED segments, contribution executions with `N/A` signal context, dynamic symbols, server-side filtering and sorting, and bounded pagination. Row-to-chart zoom is deferred because it is not required for the canonical holding contract.

## Verification boundary

Automated tests cover closed and open segments, partial sales, multiple BUY lots, FIFO scale-in/scale-out behavior, deterministic identities, signal/execution separation, contribution execution without a signal, commission/slippage parity with Trade, multi-asset behavior, Cash/SGOV separation, persistence, legacy degradation, API pagination/filtering/sorting, finite values, and frontend table states.

Real browser visual QA and real Tiingo holding acceptance are separate manual gates and are not claimed by this phase.
