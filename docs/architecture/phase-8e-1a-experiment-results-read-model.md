# PHASE 8E-1A: Experiment Results Read Model

This phase adds a read-only composition service over the existing experiment,
candidate execution, immutable result, backtest run, and strategy repositories.
It does not add a database table or a second accounting or analytics source of
truth.

## Composition Contract

`ExperimentResultsReadService` loads one frozen experiment and emits an
immutable `ExperimentResultsReadModel`. Candidate projections retain the
persisted `candidate_index` order. Each projection includes the parameter and
provenance identity needed to audit the candidate without copying trade,
order, fill, equity, or drawdown ledgers.

The service re-resolves the persisted derived strategy version and backtest run
and checks their identity against the experiment result and candidate
execution. Existing backend performance analytics are passed through; no
metrics are recalculated.

## Status and Completeness

Candidate views distinguish `COMPLETED`, `FAILED`, `NOT_EVALUABLE`,
`MISSING_RESULT`, and `INCONSISTENT`. Failed candidates remain visible with a
safe failure code and summary. An experiment that is not completed can produce
a partial view. A completed experiment with incomplete candidate outcomes is
rejected as an integrity error rather than presented as complete.

Warmup bounds are exposed as provenance metadata. The persisted IS dates and
backend performance summary remain the only performance range; warmup data is
not included in result statistics.

## Boundaries

The read model does not read protocol OOS evaluations, rank candidates,
evaluate objectives, persist selections, or mutate any source repository.
There is no read-model persistence schema and no frontend/API write path in
PHASE 8E-1A.
