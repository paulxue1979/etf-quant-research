# PHASE 8E-1H — Experiment Research Frontend

## Scope

PHASE 8E-1H adds a thin React read-and-selection surface over the completed PHASE 8E-1G research API. It does not change the backend, database, strategy engine, backtest engine, analytics, or protocol schema.

## Data flow

The page loads persisted results, compatibility diagnostics, frozen objective evaluation, and any existing selection/handoff through the API client in `frontend/src/research/api.ts`. The UI renders backend values and statuses; it does not recalculate performance metrics, trade logic, compatibility, constraints, hashes, or provenance.

## Candidate presentation

Candidates are rendered in `candidate_index` ascending order. Failed, missing, inconsistent, and `not_evaluable` candidates remain visible. `NOT_EVALUABLE` is an explicit state and is never converted into a numeric fallback. Parameter sets and backend hashes are displayed as read-only evidence.

## Researcher selection

Only candidates reported as completed with a passing frozen constraint evaluation are enabled for selection. Confirmation requires a backend-supported selection method and a non-empty researcher rationale. The POST payload contains only `selected_candidate_id`, `selection_method`, and `researcher_rationale`; all identity, evidence, configuration, and provenance are resolved by the backend. A persisted selection is rendered read-only and is reloaded through GET on page load.

## Protocol handoff

Handoff is unavailable until a persisted selection exists. The handoff request has no identity or configuration body. The response displays the backend-returned SelectionDecision, StrategyFreezeRecord, and strategy identity. The frontend does not calculate or compare hashes.

## Safety boundaries

The page exposes no automatic candidate choice, performance ordering, ranking controls, or research-outside-range execution controls. Errors are reduced to safe messages and stable codes; stack traces, filesystem paths, credentials, and secrets are not rendered.
