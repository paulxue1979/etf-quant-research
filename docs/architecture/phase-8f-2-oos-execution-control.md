# PHASE 8F-2 — OOS Claim / Retry / Recovery

## Scope

PHASE 8F-2 adds the control plane for one controlled OOS execution lineage per
frozen research protocol. It does not evaluate market data, indicators,
strategies, signals, backtests, analytics, or official OOS results.

The persisted execution identity is derived from the immutable
`OosEvaluationSpec` and records the protocol, selection decision, strategy
freeze, exact strategy content hash, OOS range, warmup boundary, and frozen
configuration hash.

## Lifecycle

```text
PENDING -> RUNNING -> FAILED -> RUNNING -> ...
   |          |
   |          +-> PENDING (expired lease recovery)
   |
   +-> RUNNING

RUNNING -> BLOCKED
```

`COMPLETED` is reserved for a later executor/finalization phase and cannot be
created by this repository. A retryable worker failure becomes `FAILED`; a
non-retryable failure becomes terminal `BLOCKED`. A `BLOCKED` execution cannot
be claimed again.

## Claim and Lease Rules

- `protocol_id` is unique in `research_oos_executions`.
- Repeating the same spec is idempotent and returns the existing execution.
- Reusing a protocol with a different spec is an identity conflict.
- Claims run under SQLite `BEGIN IMMEDIATE`; only one active worker lease can
  be created.
- Each claim creates a fresh opaque lease token and increments `attempt_count`.
- Renewal and failure reporting require the current live lease token.
- An expired `RUNNING` lease is reclaimed to `PENDING` before another worker
  can claim it.
- Old workers cannot mutate a reclaimed execution.

## Transaction and Audit Guarantees

Each state mutation and its corresponding append-only event are written in the
same SQLite transaction. If either write fails, both are rolled back. Events
contain the actor, attempt number, state transition, timestamp, and a safe
canonical JSON payload. Stored execution and event payloads are revalidated on
read against canonical JSON, identity hashes, and relational columns.

The repository is deliberately separate from the existing research protocol
state machine. It never advances a protocol to `OOS_EVALUATED`, and it never
creates an official OOS observation. Claims and retries are rejected once an
official observation is `OBSERVED` or `SEALED`.

## Recovery and Restart

`recover_stale_executions()` scans `RUNNING` rows whose lease has expired,
reclaims them in one transaction, and appends a `LEASE_EXPIRED` event. The
operation is safe to repeat. A process restart therefore resumes from the
persisted execution state instead of recreating an execution identity.

## Safety Boundaries

Failure messages and event payloads reject credentials, authorization data,
private keys, filesystem paths, non-finite numbers, and non-JSON values. The
repository does not store tracebacks or secrets. OOS data provenance remains a
reference only; dataset versioning is out of scope.

## Database Tables

The minimal control-plane schema adds:

- `research_oos_executions`: one immutable execution identity with mutable
  lease/lifecycle metadata.
- `research_oos_execution_events`: append-only execution audit events.

No external queue, worker service, cache, or new database engine is required.

## Out of Scope

- OOS data loading or Tiingo integration
- Strategy, indicator, signal, backtest, and analytics execution
- Official OOS result persistence
- Protocol state advancement
- API, frontend, ranking, reselection, optimization, walk-forward, or live
  trading
