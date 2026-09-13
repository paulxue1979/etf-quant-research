# PHASE 8F-6 OOS End-to-End Integrity Gate

## Scope

PHASE 8F-6 is an integrity audit of the existing controlled OOS chain. It adds
no new OOS workflow, selection behavior, mutation endpoint, optimizer, or
dataset-versioning system.

The audited chain is:

```text
Frozen Research Protocol
  -> SelectionDecision
  -> StrategyFreezeRecord
  -> persisted StrategyVersion
  -> OosEvaluationSpec
  -> leased OosExecution
  -> OosExecutionOutcome
  -> official BacktestRun
  -> immutable OosEvaluationResult
  -> OOS_EVALUATED protocol
  -> read-only OOS research view
```

## Integrity Boundaries

### Identity and immutability

The OOS identity carries protocol, selection, freeze, strategy version, and
strategy content hash. Finalization validates every identity link before
publishing. Strategy versions, selections, freezes, OOS results, and Backtest
runs are persisted as immutable records; a changed strategy requires a new
version.

### Date firewall and warm-up

The protocol owns the inclusive OOS range. The executor requests data only for
`[warmup_start, oos_end]`, evaluates only the OOS interval, and passes an OOS
bounded `BacktestConfig` to the PHASE 3 integration. Returned datasets are
validated against the request and clipped to the request bounds before indicator
preparation. Warm-up rows are available only for indicator initialization and
cannot create OOS accounting records.

The final OOS signal is intentionally omitted when no next trading day exists
inside the bounded data set. Execution therefore remains:

```text
Signal(T) -> next valid trading-day open
```

### Accounting and analytics

`BacktestRun` is the accounting source of truth for orders, fills, trades,
positions, equity, allocations, and cash. `OosEvaluationResult` stores only
the immutable official summary and provenance. Analytics are produced by the
existing performance module and the read view never re-runs analytics.

### Atomic publication

Official finalization uses one SQLite `BEGIN IMMEDIATE` transaction for the
BacktestRun, OOS result, execution completion, official observation, and
terminal protocol events. Any injected write failure rolls back the complete
publication. Unique database constraints and exact retry comparison prevent a
second official result for the same protocol.

### Read-only research view

`GET /research/protocols/{protocol_id}/oos` reads and validates the persisted
protocol, selection, freeze, strategy, OOS result, and BacktestRun. It does not
call market-data, indicator, strategy, signal, backtest, or analytics engines,
and it exposes no OOS mutation route.

## Audit Coverage

The concentrated integration audit is in
`tests/integration/test_oos_end_to_end_integrity.py`. It verifies:

- future rows returned by a data source are clipped before calculation;
- concurrent finalization publishes one result, one BacktestRun, and one
  official observation;
- the read view does not mutate protocol, selection, freeze, execution, result,
  BacktestRun, or event rows;
- fresh-capital initialization and the no-back-selection boundary remain
  intact.

Existing PHASE 8F tests additionally cover exact identity, frozen ranges,
price/configuration equality, no-lookahead scheduling, lease recovery, stale
writers, retry idempotency, hash validation, rollback injection, canonical
serialization, and structured API errors.

## Security and Reproducibility

OOS provenance rejects credentials, secret sentinels, and local filesystem
paths. Safe failure records do not expose stack traces or credentials. The
current provenance records source, asset references, requested range, warm-up,
frequency, and price field. An immutable dataset snapshot/version store is out
of scope and remains a V1.1 enhancement.

Real Tiingo integration remains environment-dependent. DNS or network failure
is reported as an external integration block and is not converted into a
product pass or hidden by mocks.

## Gate Result

No CRITICAL or HIGH integrity defect was found in the audited chain. PHASE 8F-6
does not start PHASE 8F-7 or any release gate.
