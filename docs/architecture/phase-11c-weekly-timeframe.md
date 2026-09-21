# PHASE 11C: Completed Weekly Timeframe Support

PHASE 11C derives weekly market data from bounded canonical daily data.  It does
not request a second weekly source from Tiingo and does not change portfolio
accounting or execution semantics.

## Calendar and completion

The runtime uses the version-pinned `exchange-calendars` XNYS regular-session
calendar.  A week is an ISO calendar week grouped by exchange sessions.  A
derived bar is published only after the calendar's final session for that week
is at or before the effective data cutoff.  The final session can therefore be
Thursday during a Friday holiday, while a Thursday holiday with an open Friday
remains incomplete until Friday.

Missing sessions in a completed week raise a data-quality error.  A partial
request-boundary week is omitted.  Rows after the effective cutoff are ignored,
so a cache containing future rows cannot affect an earlier backtest or OOS run.

## Derived data and indicators

`DerivedWeeklyBar` retains raw and adjusted OHLCV independently, plus
`period_start`, `period_end`, `available_on`, calendar identity, source
frequency, and `aggregation_version`.  Adjusted OHLC fields are copied from
the normalized daily adjusted fields and aggregated directly; no adjusted field
is synthesized from adjusted close.

Daily and weekly MA/EMA preparation uses the same existing formula functions.
Weekly series contain only completed observations.  Weekly operands resolve the
latest point whose `available_on` is no later than the daily evaluation date;
they never recalculate or fill the current incomplete week.  Operand evidence
includes timeframe and source date.

## Runtime integration

Backtest, IS experiment execution, and controlled OOS execution all call the
same `prepare_strategy_inputs` path.  Their evaluation clock remains the daily
common trading timeline.  Existing integration continues to map a signal at
`T` to the next common trading session open, including holiday transitions.
Signal-source history can begin earlier for indicator warmup; execution assets
are never backfilled or given synthetic prices.
