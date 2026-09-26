# PHASE 13D Backtest Run Management

## Contract

Backtest artifacts remain immutable. Management state is stored additively in
`backtest_run_management`, keyed by `backtest_run_id`, with `ACTIVE` and
`TRASHED` lifecycle states, `deleted_at`, `is_protected`, and a provenance-based
run type (`MANUAL`, `GRID_CANDIDATE`, or `OFFICIAL_OOS`). Legacy rows are
backfilled as active manual runs without reading their large JSON payloads on
history requests.

Moving a run to Trash is the default delete operation. Restore preserves the
original run identity, timestamps, canonical result, and StrategyVersion
provenance. Permanent deletion requires the run to be trashed, unprotected,
and free of experiment-result or official-OOS references. It is performed in
one SQLite transaction and cascades only run-owned metadata.

## Dependencies

The dependency read model checks canonical `research_experiment_results` and
`research_oos_results` references. A referenced run may be trashed, but
permanent deletion returns typed dependency descriptors and does not mutate
experiment truth, selection, freeze, or OOS records.

## UI

Backtest History provides Active, Trash, and All views; strategy/version/run
type/status/search filters; run type badges; selection for comparison and bulk
Trash; View, Restore, Protect/Unprotect, Delete, and destructive permanent
delete controls. Permanent delete requires typing `DELETE`. Protected runs are
skipped by bulk Trash and require explicit unprotection first.
