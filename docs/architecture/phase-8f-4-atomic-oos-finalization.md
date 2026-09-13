# PHASE 8F-4 Atomic OOS Finalization

## Scope

PHASE 8F-4 publishes one already-calculated, claimed OOS outcome. It does
not fetch market data, evaluate conditions, generate signals, run a backtest,
calculate analytics, expose an API, or modify the frontend. Those operations
remain owned by the existing PHASE 3/4 and PHASE 8F-3 components.

## Finalization Flow

`OosFinalizationService.finalize_oos_observation()` opens one SQLite
`BEGIN IMMEDIATE` transaction and reloads the protocol, execution lease,
frozen selection, frozen strategy, and frozen evaluation configuration. It
validates the outcome's identity, hashes, dates, warm-up boundary, price
field, engine/analytics versions, and configuration before writing anything.

For a valid outcome, the transaction writes:

1. the immutable `BacktestRun`, whose result and analytics are the existing
   PHASE 3/4 objects;
2. the immutable `research_oos_results` row;
3. the execution transition from `RUNNING` to `COMPLETED` and its terminal
   event;
4. the official `research_oos_evaluations` observation with
   `untouched_oos: false`;
5. protocol terminal events that advance the observed protocol state to
   `OOS_EVALUATED`.

The transaction commits only after all writes succeed. Any failure rolls back
all writes, so a partial official result cannot be mistaken for a completed
OOS evaluation.

## Immutability and Idempotency

An official result is unique per `protocol_id`, `execution_id`, and
`backtest_run_id` at the database level. The result payload is canonical JSON
and carries a deterministic result hash. A retry with the same execution and
semantically identical outcome returns the stored result without creating a
second backtest or observation. A retry with a different outcome or execution
is rejected and cannot replace the official record.

The backtest run id is deterministic from the execution id and outcome hash.
The result id is deterministic from protocol, execution, and outcome
identity. The source of truth for accounting and analytics remains the
persisted `BacktestRun`; `OosEvaluationResult` is its immutable official OOS
summary and provenance envelope.

## Lease, Freeze, and Boundary Guards

- Finalization requires a live execution lease owned by the supplied token.
- The protocol must be `SELECTION_RECORDED`; the selected strategy freeze and
  exact persisted strategy version must still match the outcome.
- The OOS range must exactly equal the protocol's frozen OOS range.
- Data warm-up may begin before OOS start only for indicator preparation and is
  retained as provenance; performance dates remain strictly inside the OOS
  range.
- The frozen price field, engine version, analytics version, and evaluation
  configuration must match the calculated outcome.
- The existing no-lookahead contract remains unchanged:
  `Signal(T) -> T+1 Trading Day Open`.

An observed official result is never marked as untouched OOS. Once committed,
the execution is terminal and the protocol cannot be finalized a second time.

## Recovery and Failure Isolation

The database lock serializes competing finalizers. A stale or expired lease is
rejected before persistence. Constraint conflicts are treated as integrity
conflicts rather than overwrite requests. Calculation and data acquisition
remain outside this phase, so their retry policy stays in PHASE 8F-3; this
phase only retries the finalization call with the same immutable outcome.

Because the BacktestRun, result, execution terminal state, observation, and
protocol events share one transaction, process restart yields either the
pre-finalization state or the complete official state. No recovery job needs
to infer whether only one of those records was written.

## Database Changes

`research_oos_results` is an append-only table with database uniqueness for
protocol, execution, and backtest identities. `OosResultRepository` validates
canonical JSON and result hashes on read. `BacktestRepository` provides a
transaction-aware insert path and schema initialization that does not commit
the caller's transaction. Existing research protocol and OOS observation
tables are reused; no dataset versioning, migration framework, or new service
infrastructure is introduced.

## Tests and Out of Scope

Focused tests cover successful atomic finalization, deterministic idempotent
retry, stale lease rejection, rollback after result failure, rollback after
terminal-event failure, conflicting retry rejection, and canonical storage
round trips. Full regression and external Tiingo tests remain release-gate
checks; unavailable Tiingo DNS/network access is reported as an environment
blocker and is never mocked or converted into a pass.

PHASE 8F-5 and all selection, optimization, live trading, API, frontend,
dataset versioning, and alternative execution infrastructure are out of
scope.
