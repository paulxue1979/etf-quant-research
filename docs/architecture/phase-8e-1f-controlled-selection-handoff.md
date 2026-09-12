# PHASE 8E-1F Controlled Experiment Selection Handoff

## Scope

PHASE 8E-1F transfers one already-persisted `ExperimentSelectionDecision` into
the PHASE 7 research-governance records. It does not select a candidate,
materialize a strategy, run a backtest, calculate metrics, rank results, make a
recommendation, or access OOS data.

## Input And Server-Side Resolution

The service accepts only `experiment_id`. The caller cannot provide or override
the selected candidate, derived strategy version, rationale, result, backtest
run, metrics, or provenance.

The service resolves the persisted experiment selection and its evidence from
the repositories for experiments, selections, candidates, experiment results,
candidate executions, backtest runs, strategy versions, and research
protocols. Missing or inconsistent records cause the handoff to fail before
any PHASE 7 write.

## Selection Translation

The persisted `ExperimentSelectionDecision` is translated into one PHASE 7
`SelectionDecision`. The PHASE 7 decision retains the researcher rationale,
the selected IS backtest run, allowed IS metrics, and immutable source
provenance. Its source is `experiment_selection_handoff`.

The experiment-selection record and PHASE 7 selection remain separate records:
the former records the researcher's experiment decision; the latter is the
official protocol-level governance decision.

## Exact Derived Strategy Binding

The selected result, completed candidate execution, backtest run, and persisted
derived `StrategyVersion` must agree on the exact derived version identity and
content hash. The service recomputes the materialization semantic hash and
derived version ID from the persisted configuration and provenance. It also
verifies the base strategy version and hash against the locked PHASE 7
candidate set.

The resulting PHASE 7 `SelectionDecision` and `StrategyFreezeRecord` reference
the same exact derived strategy version. The freeze records its content hash
and the ID of the selection decision that caused the freeze.

## Protocol And IS Boundary

The protocol must be `IS_EVALUATED` for a new handoff. Its candidate set must
be locked, its evaluation configuration must match the persisted backtest, and
the backtest and performance-analysis dates must exactly match the protocol IS
range. Price field, engine version, analysis version, data provenance, and
frozen backtest configuration are validated from persisted records.

Warm-up or OOS execution is not performed here. Provenance containing OOS or
sensitive credential fields is rejected. The service neither reads nor writes
OOS observations, results, metrics, or decisions.

## Atomic Persistence And Lifecycle

`ResearchProtocolRepository.persist_selection_and_freeze_atomic()` uses one
SQLite `BEGIN IMMEDIATE` transaction for:

1. The PHASE 7 `SelectionDecision`.
2. The `selection_recorded` protocol lifecycle event.
3. The `StrategyFreezeRecord`.

All protocol, candidate-set, strategy, backtest, and provenance bindings are
revalidated inside the transaction before insertion. Any validation,
constraint, serialization, or database failure rolls back the transaction, so
no partial official selection or freeze becomes visible.

## Idempotency And Conflicts

The decision, freeze, and lifecycle event IDs are deterministic from the
persisted experiment selection hash. Retrying an identical completed handoff
returns the existing selection and freeze and does not append another lifecycle
event.

The database-level protocol selection uniqueness constraint remains the final
concurrency guard. A different selection for a protocol that already has an
official selection is rejected as a conflict and cannot overwrite the existing
selection or freeze. An incomplete or internally inconsistent prior handoff is
reported as an integrity error rather than repaired implicitly.

## Current Limitations

- The handoff is an internal application service; PHASE 8E-1F adds no public API
  or frontend workflow.
- Only one official selection is permitted per research protocol.
- Candidate ranking, recommendation, automatic winner selection, and OOS
  evaluation remain outside this phase.
- Recovery is deterministic retry against persisted SQLite state; no external
  queue or distributed transaction coordinator is introduced.
