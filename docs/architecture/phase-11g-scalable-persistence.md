# PHASE 11G — Scalable History and Result Persistence

## Scope

PHASE 11G hardens persistence reads before any future candidate-generation or
optimization work. The immutable `BacktestRun.run_json` remains the canonical
artifact. The new `backtest_run_metadata` table is a rebuildable read
projection, not a second financial source of truth.

This phase does not implement grid search, ranking, candidate execution,
heatmaps, Pareto analysis, or distributed infrastructure.

## Baseline Audit

The workspace SQLite database contained 162 backtest runs and was approximately
527 MiB. `run_json` occupied 551,925,571 bytes in aggregate. Its average,
median, and maximum row sizes were 3,406,948, 2,812,518, and 7,601,074 bytes.
The pre-change `BacktestRepository.list_records()` decoded all 162 rows in
approximately 17.4 seconds on the local workspace database.

The equivalent scalar SQL page read was approximately 0.0004 seconds before
projection. The old history implementation still paid the JSON decode cost
because it selected every `run_json` before Python sorting and slicing.

After the projection and explicit backfill, the same 50-row pages measured
approximately 0.0067 seconds for creation-date ordering and 0.0087 seconds for
metric ordering on the workspace database. The one-time bounded backfill of
the 159 legacy rows took approximately 10.45 seconds and did not change any
canonical JSON row.

A repeated final measurement recorded 0.0055 seconds for a 50-row metadata
page, 0.000019 seconds for `COUNT(*)`, and 0.0689 seconds for one full detail
load. The old full-list decode remained 14.71 seconds. The bounded response was
170,513 bytes versus 161,863 bytes for the prior 50-item wire contract; the
small increase preserves legacy summary fields while removing the unbounded
full-artifact read cost.

For the 162-row workspace database, projection JSON totals approximately 0.42
MiB and the SQLite projection table occupies approximately 0.64 MiB. This is
small relative to the approximately 527 MiB canonical database and grows
roughly linearly with run count without duplicating curves or trades.

At the observed average canonical artifact size, 100, 500, and 1000 runs imply
approximately 325 MiB, 1.59 GiB, and 3.17 GiB of `run_json` respectively. The
temporary projection databases for the same row counts were approximately
0.32 MiB, 1.37 MiB, and 2.68 MiB. PHASE 11G addresses read amplification; it
does not solve long-term canonical artifact storage growth.

## Projection Contract

`BacktestRunMetadata` is produced from the already computed
`BacktestResult`/`PerformanceAnalysisResult` objects at write time. It stores
identity, dates, engine and analysis versions, capital/equity values, the
existing configuration/data/provenance snapshots, and the existing analytics
metric payloads. No metric is recomputed by the projection.

Projection version: `phase-11g.1`.

The projection intentionally excludes equity curves, orders, fills, trades,
positions, allocation history, and other large immutable artifact sections.

## Persistence and Compatibility

Canonical `backtest_runs` and its metadata projection are inserted in the same
caller transaction. A projection failure therefore prevents a partially
visible new run. Detail, report, and comparison paths continue to load
`run_json` only when their bounded/full-artifact contract requires it.

Schema changes are additive. `ensure_schema()` creates the projection table and
indexes with `CREATE TABLE/INDEX IF NOT EXISTS`; it does not drop, rewrite, or
reset the canonical table. Existing rows without a projection remain visible
to metadata history with `metadata_projection_status=missing` and unavailable
metrics. `BacktestRepository.backfill_metadata(limit=...)` is the explicit,
bounded, idempotent rebuild operation. History requests never invoke it and
therefore never parse every legacy JSON row.

## History Query

`GET /research/backtests` now executes a scalar projection query with database
`LIMIT/OFFSET`, a separate `COUNT(*)`, and deterministic ordering:

```text
created_at DESC|ASC, backtest_run_id DESC|ASC
```

Metric ordering uses the projected numeric value, places unavailable values
after available values, and uses the same creation/run-id tie-break. The
existing response fields remain available; projection status/version fields
are additive.

The strategy history endpoint uses the same metadata projection. The detail
endpoint remains lazy and loads the immutable run JSON for one requested run.

Indexes are additive and scoped to actual query patterns:

- `idx_backtest_runs_created`
- `idx_backtest_runs_strategy_created_v2`
- `idx_backtest_metadata_strategy_created`
- `idx_backtest_metadata_version_created`
- `idx_backtest_metadata_experiment_candidate`

`EXPLAIN QUERY PLAN` confirms the global history page uses
`idx_backtest_runs_created`; strategy-scoped reads use the strategy composite
index. The projection join uses the metadata primary-key index.

## Experiment Summary

`GET /research/protocols/{protocol_id}/experiments/{experiment_id}/result-summaries`
returns bounded candidate summaries ordered by `candidate_index ASC` without
selecting or decoding `result_json`. Analytics are copied from the canonical
`performance_summary_json`; no ranking or automatic selection is performed.

The endpoint is capped at 500 rows per request and exposes `total`, `limit`,
`offset`, and provenance stating that analytics were not recomputed and no
ranking was applied.

## Resource and Integrity Limits

- History page size: 1–100.
- Experiment result summary page size: 1–500.
- Comparison full-artifact loading remains bounded by its existing 2–10 run
  contract.
- Full run detail remains one run per request.
- Metadata JSON is strict JSON and rejects non-finite values.
- Canonical run JSON is unchanged and remains the integrity-checked source.
- No secrets, credentials, API keys, caches, or generated artifacts are stored
  in the projection.

## Validation

Focused repository/API tests cover atomic projection persistence, deterministic
metadata pagination, missing legacy projections, explicit backfill, detail
loading, and the existing research history contract. Performance validation
uses temporary synthetic databases at 100, 500, and 1000 metadata rows; it
does not create candidate runs or execute grid search.

Measured 50-row page times in those temporary databases were approximately
0.0033, 0.0026, and 0.0028 seconds. These are engineering observations rather
than cross-machine latency guarantees; the structural assertion is that each
query reads one bounded metadata page and never materializes all artifacts.
