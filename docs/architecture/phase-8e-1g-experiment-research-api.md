# PHASE 8E-1G — Experiment Research API

## Scope

This phase exposes the existing IS-only experiment research capabilities over
HTTP. It is an adapter/orchestration layer only. It does not execute a
backtest, calculate analytics, rank candidates, recommend a strategy, or read
or evaluate OOS data.

## Endpoints

All endpoints are under `/research/protocols/{protocol_id}/experiments/{experiment_id}`:

- `GET /results` returns the persisted `ExperimentResultsReadModel`, preserving
  canonical `candidate_index` order and exposing completed, failed, missing,
  and `NOT_EVALUABLE` candidates.
- `GET /comparison` delegates to `ExperimentCompatibilityService` and answers
  only whether results are comparable. It has no winner, rank, score, or best
  candidate field.
- `GET /objective-evaluation` delegates each candidate to the frozen objective
  evaluator. It preserves `PASS`, `FAIL`, and `NOT_EVALUABLE`; it never selects.
- `POST /selection` accepts only `selected_candidate_id`, a human-directed
  `selection_method`, and `researcher_rationale`. All hashes, strategy IDs,
  result IDs, dates, configuration, and provenance are resolved server-side.
- `GET /selection` reads the immutable experiment selection.
- `POST /handoff` accepts an empty body and delegates to
  `ExperimentSelectionHandoffService`.
- `GET /handoff` verifies the persisted experiment selection, PHASE 7 selection,
  and strategy freeze identity chain.

Request models reject unknown fields. Selection writes are immutable and the
existing repository supplies idempotency for an identical retry and conflict
for a different selection.

## Data and integrity boundaries

The path protocol is checked against the persisted experiment before every
operation. BacktestRun and persisted experiment results remain the source of
truth; the API does not create a second accounting or analytics model. All
experiment endpoints are IS-only and do not accept OOS overrides or transition
OOS state. Handoff creates only the already-defined PHASE 7 selection/freeze
prerequisite; it does not observe OOS.

The API returns stable structured errors without tracebacks, database paths,
credentials, API keys, or internal filesystem details. The frontend is not
modified in this phase. Full dataset versioning remains out of scope; existing
experiment/backtest provenance is reused.
