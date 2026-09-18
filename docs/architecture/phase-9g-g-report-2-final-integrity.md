# PHASE 9G-G - Report 2.0 Final Integrity Gate

## Status

- Baseline: `ca6c58a35e41e9d66a7e031cbd330761f66425b8`
- Branch: `main`
- Automated implementation gates: PASS
- Browser QA: PASS against a cache-backed real-market-data run
- Fresh Tiingo network verification in Codex: BLOCKED by external DNS
- Release decision: local fresh-data acceptance remains required

## Canonical Data Flow

```text
HistoricalDataSet / common trading timeline
  -> indicator engine
  -> immutable StrategyVersion evaluation
  -> TargetAllocation and SignalRecord
  -> next-trading-day-open BacktestEngine execution
  -> orders, fills, FIFO lots, trades, positions, cash and equity ledger
  -> PerformanceAnalysis / BenchmarkEvaluation / HoldingSegment
  -> immutable BacktestRun
  -> BacktestReportProjectionService
  -> /research/backtests/{id}/report, /series and /holdings
  -> Backtest Lab Report 2.0 views
```

Canonical owners:

| Concept | Canonical owner |
| --- | --- |
| Market prices and price field | `HistoricalDataSet` and `HistoricalDataRequest` |
| Strategy identity and rules | immutable `StrategyVersion` |
| Regime, signal and target allocation | strategy evaluation provenance / `SignalRecord` |
| Execution, cash, positions and FIFO accounting | `BacktestResult` |
| Raw equity and invested capital | `BacktestResult.equity_curve` |
| TWR, XIRR, drawdown, exposure and turnover | `PerformanceAnalysis` |
| Fair buy-and-hold comparison | `BenchmarkEvaluation` using `BacktestEngine` |
| Holding periods | canonical FIFO holding projection from fills/trades |
| Contribution schedule and effective events | `BacktestResult.contribution_*` |
| API availability semantics | `BacktestReportProjectionService` |

The frontend formats and visualizes these contracts. It does not infer returns,
regimes, fills, holdings, benchmark wealth, or contribution deployment.

## Integrity Rules

- Raw equity is not TWR; capital invested is not ending value; profit is not return.
- TWR removes external cash-flow timing; XIRR represents the investor cash-flow experience.
- Signal date and execution date remain separate; a final-day signal does not create a fill.
- `HOLD_PREVIOUS` preserves allocation continuity and does not create a signal marker.
- Contribution cash is external principal, while contribution-triggered fills are traded notional.
- Target allocation and actual allocation remain separate.
- Cash is ledger cash; SGOV is an asset and is never classified as cash.
- Trades, positions, FIFO lots and holding segments remain distinct concepts.
- StrategyVersion identity is unaffected by report, DCA, or benchmark configuration.

## Verified Defects And Minimal Fixes

1. Turnover excluded fills whose rebalance cause was `CONTRIBUTION`. Turnover now
   includes every canonical fill joined to an order and exposes a cause breakdown;
   the external contribution principal itself remains excluded.
2. Benchmark evaluation could silently use a shorter calendar than the owner run.
   It now requires exact owner dates, rejects missing dates, and records equal capital,
   cash-flow, commission, slippage, integer-share, price-field and execution provenance.
3. Backtest Lab could not request a benchmark and one report endpoint failure hid both
   summary and chart data. It now submits an optional benchmark symbol and isolates
   summary and series failures with `Promise.allSettled`.
4. Persisted immutable benchmark provenance contains read-only mappings that FastAPI
   could not serialize. The report summary now sanitizes the nested artifact.
5. Persisted benchmark curves are tuples, while the series projection accepted only
   lists. It now accepts both immutable tuples and lists and sanitizes each point.

Each defect has a focused regression test. No Strategy, Signal, Backtest, Experiment,
Selection, Freeze, Protocol, or OOS production semantics were changed.

## Acceptance Matrix

| Capability | Automated | Cached real-data browser | Fresh Tiingo | Status |
| --- | --- | --- | --- | --- |
| Raw equity / capital / profit | PASS | PASS | BLOCKED | READY pending local data gate |
| TWR / XIRR / drawdown | PASS | PASS | BLOCKED | READY pending local data gate |
| Fair benchmark and excess return | PASS | PASS | BLOCKED | READY pending local data gate |
| Regime / signal / execution | PASS | PASS | BLOCKED | READY pending local data gate |
| Target / actual allocation | PASS | PASS | BLOCKED | READY pending local data gate |
| Exposure / turnover | PASS | PASS | BLOCKED | READY pending local data gate |
| FIFO holdings | PASS | PASS | BLOCKED | READY pending local data gate |
| Contribution timeline | PASS | PASS | BLOCKED | READY pending local data gate |
| Dynamic multi-asset / Cash vs SGOV | PASS | PASS | BLOCKED | READY pending local data gate |
| Legacy partial availability | PASS | PASS | N/A | PASS |
| Experiment / Selection / Freeze / OOS | PASS | N/A | N/A | PASS |

## Golden And Regression Coverage

- Flat-market DCA: ending equity equals invested capital, profit/TWR/XIRR are zero,
  capital/TWR/drawdown summary-series identities hold, and no trade or holding exists.
- No DCA: initial capital remains the capital basis and the contribution artifact is
  available with an empty event list.
- Monthly DCA: requested/effective dates, cumulative capital, markers, XIRR, benchmark,
  and contribution pagination are covered.
- Stateful SMA200: rule match, fallback, hold continuity, T+1 execution, final-day signal,
  and signal/execution separation are covered by existing stateful tests.
- Multi-asset: QQQ/TQQQ/SGOV target and actual weights are dynamic, with cash separate.
- FIFO: partial exits, multiple lots, open lots and canonical trade/holding P&L are covered.
- Error isolation: unavailable benchmark, legacy provenance, series failure, not-evaluable
  XIRR and partial report availability remain explicit and do not crash other sections.
- Date and price-field tests preserve `YYYY-MM-DD`, requested/effective boundaries, and
  consistent raw/adjusted selection across strategy, backtest, benchmark and report.

## Browser And Performance Evidence

The in-app browser exercised a 2020-01-01 through 2025-12-31, adjusted-close,
multi-asset monthly-DCA run with QQQ benchmark. The effective run began 2020-05-28.
The report showed 68 contributions, 196 holding segments and 116 trades. Portfolio,
capital, TWR, benchmark TWR, strategy/benchmark drawdown, regime, signal/execution,
target allocation and actual QQQ/SGOV/TQQQ/Cash series were available.

Wide (1440x900) and narrow (390x844) layouts had no page-level overflow. Range controls
1Y/3Y/5Y/MAX, synchronized crosshair values, contribution pages 1-25/26-50/51-68,
and holding page 26-50 were verified. Browser console contained no error or warning after
the fixes. A development StrictMode run may duplicate initial reads; no fetch loop,
subscription recursion, observer leak, or chart recreation loop was observed.

A synthetic 6,000-point projection produced an approximately 684,756-byte report
summary and 960,902-byte series payload. Projection took about 6.52 ms and series
projection about 5.87 ms in the local test environment. No obvious freeze or event lag
was observed. Payload pagination remains mandatory for holdings and contributions.

## Gate Results

- Backend focused Report 2.0: 47 passed; post-format subset: 18 passed.
- Backend full non-network: 765 passed, 4 deselected.
- OOS and research integrity selection: 114 passed.
- Frontend: 55 passed.
- TypeScript, ESLint, Vite build: PASS.
- Ruff check and changed-file format: PASS.
- Python compile, Git whitespace and changed-file secret scan: PASS.
- First full backend run exposed one pre-existing initialization timing failure;
  the isolated concurrency test passed 3/3 and the complete rerun passed.
- Five-ETF cached acceptance: PASS in Codex.
- QQQ/TQQQ/SPY fresh Tiingo requests: BLOCKED by Codex DNS, not a product failure.

## Security And Scope

No credential, `.env`, database, cache, `node_modules`, build output, telemetry, remote
logging, tag or push is included. Report provenance contains no Tiingo key or environment
secret. No optimizer, walk-forward, Monte Carlo, tax, withdrawal, fractional-share,
broker, or live-trading functionality was added.

## Known Limitations

- Positive USD contributions only.
- Integer shares only.
- Monthly month-start and one-time contribution schedules.
- No withdrawals, tax model, broker/live trading, optimizer, walk-forward or Monte Carlo.
- MFE/MAE and holding drawdown are unavailable.
- No true performance attribution or PDF export.
- Large series are returned as full JSON payloads; chart downsampling is deferred.

## Local Fresh-Data Acceptance

Run from the repository with `.venv` active:

```bash
pytest -q tests/integration/test_tiingo_real.py -m integration -s
pytest -q tests/integration/test_five_etf_real_data_acceptance.py -m integration -s
pytest -q tests/integration/test_report_2_0_integrity.py -s
```

For browser acceptance:

```bash
python -m uvicorn backend.app.main:app --reload
cd frontend && pnpm dev
```

Open `http://127.0.0.1:5173/` and repeat No-DCA, Monthly DCA, SMA200 hysteresis,
and QQQ/TQQQ/SGOV scenarios. Check charts, 1Y/3Y/5Y/MAX, zoom/pan, crosshair,
regime, signal/execution, target/actual allocation, holdings, contribution timeline,
TWR/XIRR, and the browser console.
