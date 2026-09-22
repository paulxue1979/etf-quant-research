# PHASE 11L System Acceptance

## Scope

PHASE 11L validates the V1.3 research system without adding product features. The
acceptance baseline is commit `a8e24d4217aab1fce573dc5f93857979d2575dfe` on
`main`.

The audit covers daily and weekly data boundaries, stateful strategy semantics,
position and contribution policies, deterministic Grid Search, optimization
analysis, Selection/Freeze/Official OOS integrity, wealth comparison, event
markers, long-history reads, browser behavior, performance, compatibility,
storage, and security.

## Environment

- macOS Darwin 25.5.0, arm64
- Python 3.12.14 in `.venv` (system Python 3.9.6)
- Node.js 26.8.2
- pnpm 12.4.1 installed; repository declares pnpm 11.19.0
- Git 2.50.1
- SQLite 3.53.1
- exchange-calendars 4.13.2
- Python dependency check: no broken requirements

The declared pnpm version could not be downloaded because the execution
environment cannot reach the npm registry. Frontend verification therefore used
the already-installed repository-local Vitest, TypeScript, ESLint, and Vite
binaries. No dependency was changed.

## Research Integrity

- Daily and weekly no-lookahead tests pass, including normal and shortened
  weeks, cutoff clipping, Friday-close/next-session-open timing, and a midweek
  OOS end with a future Friday present in the source payload.
- Regime evaluation preserves one active regime, one canonical target, and at
  most one transition per evaluation date. Value-zone priority, hysteresis,
  Recovery Hold, and no same-day cascade behavior pass.
- QQQ/TQQQ/SGOV/Cash and SPY/UPRO generic multi-asset scenarios pass. Ledger
  Cash remains distinct from SGOV.
- Rebalance suppression preserves the latest canonical target while actual
  allocation remains unchanged or partial. Contribution, regime change, drift,
  and schedule triggers aggregate into one decision and one order plan.
- Contributions are cash-first and do not create strategy signals. TWR, XIRR,
  capital invested, and investment profit regression tests pass.
- Deterministic Grid Search preflight, pruning, limits, candidate identities,
  failure isolation, cancellation, Heatmap, Pareto, Stability, and Sensitivity
  tests pass. There is no silent truncation, interpolation, or OOS input.
- A two-candidate system test executes Grid/IS, explicit researcher selection,
  SelectionDecision persistence, PHASE 7 handoff, StrategyFreezeRecord,
  Controlled OOS, atomic official result finalization, and the read-only OOS
  research view against one temporary database.
- OOS starts from the frozen strategy's `initial_regime`, which is the currently
  approved independent-window contract. Pre-OOS state warmup remains deferred.
- Frozen strategy identity, content hash, protocol interval, BacktestConfig,
  value zones, timeframes, asset roles, allocation, and rebalance policy are
  preserved. Historical identities are not rewritten.

## Real Data And Long History

- Existing Tiingo cache acceptance passes for QQQ, TQQQ, SPY, IWM, and SGOV.
  It covers adjusted-close validation, common timelines, the research chain,
  wealth, and markers.
- Fresh Tiingo requests are blocked only by DNS in the Codex environment. No
  mock or synthetic result is reported as a fresh-network pass.
- Existing 2000-to-2026 QQQ Buy-and-Hold and daily trend runs load successfully
  in the browser and provide report, comparison, wealth, and marker data.
- Generic SPY/UPRO/SGOV behavior is covered by deterministic backend tests.

## Browser Acceptance

Real browser acceptance was executed against the running local backend and
frontend at desktop (1440x900) and narrow (390x844) viewports.

- Strategy Lab, Backtest Lab, history pagination, long-history detail,
  multi-strategy TWR/drawdown/metrics, wealth comparison, report navigation, and
  marker filtering were exercised.
- `MAX`, `Fit All`, `Reset View`, wealth-series toggles, and marker toggles work.
- A real cached backtest completed through the UI and rendered analytics,
  capital, wealth, regime, target/actual allocation, holdings, and markers.
- No page-level horizontal overflow was observed at either viewport. Narrow
  chart end labels can be visually tight, but controls remain usable.
- Grid Search, Optimization Results, and OOS Research empty states were checked
  in the browser. Their populated workflows are covered by frontend tests, API
  tests, and the system E2E test because the main database intentionally has no
  persisted experiment or protocol rows.
- Final post-remediation browser console has no uncaught errors, React warnings,
  chart errors, or failed API requests.

## Performance And Storage

- The performance suite completes 100 tests in 3.40 seconds.
- 1,000-candidate preflight: 0.26 seconds.
- 1,000-candidate Pareto analysis: 0.15 seconds.
- 1,000-neighbor lookup: 0.01 seconds.
- 1,000-candidate persistence and summary: 2.53 seconds.
- Midweek OOS audit: 0.50 seconds.
- Full two-candidate Grid/Selection/OOS audit: 0.13 seconds.
- 1,999-marker projection: 0.02 seconds.
- Local API timing: 50-row history page 0.115 seconds, report 0.082 seconds,
  marker projection 0.099 seconds.
- `data/strategy.db` is approximately 528 MiB with 164 backtest runs and 164
  metadata rows after browser acceptance. `PRAGMA integrity_check` returns `ok`;
  no missing or orphan metadata exists. Main-database experiment and protocol
  tables remain empty by design.

## Findings And Remediation

Browser interaction found one HIGH issue: changing marker filters invoked the
parent callback from inside a child React state updater, producing a render-time
cross-component update warning. The callback now runs after the child state is
computed and scheduled. A focused regression test exercises a stateful parent
and asserts that the warning is absent.

The system audit also adds focused coverage for the complete frozen
Grid/Selection/OOS identity chain and midweek OOS weekly-data clipping. No
financial formula, selection rule, freeze contract, OOS semantic, or historical
identity was changed.

Post-remediation issue counts:

- CRITICAL: 0
- HIGH: 0
- MEDIUM: fresh Tiingo blocked by Codex DNS; populated Grid/Optimization/OOS
  browser flows not run against the intentionally empty main database; pnpm
  package-manager identity download blocked by network; repository-wide Ruff
  format has 37 pre-existing files; Vite retains its pre-existing >500 KiB chunk
  warning.
- LOW: two third-party deprecation warnings; narrow chart end labels can be
  truncated inside the chart without page overflow.

These deferred items do not affect research correctness, data integrity, the
tested primary workflows, or release safety.

## Security And Compatibility

- Ruff check, Python compilation/import, TypeScript, ESLint, Vite build, secret
  scan, and Git whitespace checks are required to pass before commit.
- No `.env`, token, database, cache, dependency directory, or build artifact is
  tracked by this phase.
- Production code contains no arbitrary `eval`/`exec`, formula execution, unsafe
  HTML addition, or unrestricted SQL construction introduced by this phase.
- Legacy Buy-and-Hold, SMA/EMA, HOLD_PREVIOUS, contribution, report, history,
  and V1.2 comparison behavior remains covered by the full regression suites.

## Gate

Final verification results:

- Backend non-network: 1,026 passed, 6 deselected, 2 third-party warnings.
- Frontend: 19 files and 136 tests passed.
- TypeScript, ESLint, Vite build, Ruff check, scoped Ruff format, Python
  compilation, secret scan, and Git whitespace checks: PASS.
- Real local-cache acceptance: 3 passed.
- Focused V1.3 semantic regression: 263 passed.
- Performance regression: 100 passed.
- PHASE 11L system acceptance: 2 passed.
- Desktop and narrow real-browser acceptance: PASS after remediation.
- CRITICAL: 0. HIGH: 0.

PHASE 11L is READY once this scoped audit/remediation change is committed and
the working tree is clean. The phase does not create a release tag, push
changes, or start PHASE 11M.
