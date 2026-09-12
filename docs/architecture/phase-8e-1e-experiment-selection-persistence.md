# PHASE 8E-1E: Experiment Selection Atomic Persistence

## Scope

This phase persists the validated `ExperimentSelectionDecision` produced by
the PHASE 8E-1D domain model. It records one explicit researcher choice for a
completed experiment's IS candidate universe. It is separate from the PHASE 7
protocol `SelectionDecision` and does not perform OOS handoff, strategy
freezing, ranking, recommendation, or optimization.

## Storage Contract

Selections are stored in `research_experiment_selections`. The table has a
database-level unique constraint on `experiment_id`, so an experiment can have
one official selection only. The complete decision is retained as canonical
JSON, while identity fields are duplicated as queryable columns and checked
against the JSON on every read.

The repository recomputes the PHASE 8E-1D semantic hash from the reconstructed
decision. `created_at` remains outside that semantic hash, exactly as defined
by the domain model. Non-finite JSON, sensitive content, OOS markers, and
tampered column/payload combinations are rejected.

## Atomic Lifecycle

`ExperimentSelectionRepository.create()` uses one SQLite `BEGIN IMMEDIATE`
transaction. It verifies the frozen experiment, candidate set, completed
candidate execution, official `ExperimentResult`, exact derived
`StrategyVersion`, objective evidence, compatibility evidence, and IS
boundaries. It then inserts the immutable selection, transitions the experiment
from `COMPLETED` to `SELECTION_RECORDED`, and appends the existing experiment
lifecycle event with transition key
`experiment-selection:{experiment_id}` before committing.

Any validation, insert, state transition, or event failure rolls back all three
artifacts. A repeated identical semantic selection returns the persisted row
without a second event. A different selection is a structured conflict and
cannot overwrite the first record.

## Provenance and Boundaries

The stored record preserves candidate, result, derived strategy, objective,
candidate-set, parameter-space, configuration, engine, analysis, and data
provenance fields from the validated IS decision. Backtest and analytics remain
owned by existing repositories and services; this repository never recalculates
performance metrics and never reads or creates PHASE 7 OOS handoff records.

## Read API

The repository exposes `get(selection_id)`, `get_by_experiment_id()`, and
`list(protocol_id=None)`. There are intentionally no update, replace, or delete
operations. Restart recovery reconstructs the immutable domain decision and
validates both its canonical payload and semantic hash.

## Out of Scope

PHASE 8E-1F, PHASE 7 handoff, OOS evaluation, ranking, best-candidate logic,
recommendations, optimization, APIs, and frontend changes are not part of
this phase.
