# PHASE 8E-0E Cross-Component Integrity Audit

PHASE 8E-0E is an audit-only gate. It adds no production behavior, no schema
change, and no new execution path. The audit tests verify that the already
implemented PHASE 8E-0A through PHASE 8E-0D components preserve their
cross-component contracts when used together.

## Audit Scope

The audited chain is:

```text
Frozen experiment and candidate
  -> deterministic derived StrategyVersion
  -> CandidateExecution
  -> Strategy Evaluation / Signal / Backtest / Analytics
  -> BacktestRun
  -> immutable ExperimentResult
  -> PHASE 7 SelectionDecision and StrategyFreezeRecord
```

The audit does not select a strategy, rank candidates, execute OOS, optimize
parameters, or add an API or frontend workflow.

## Cross-Component Contracts

### Identity and provenance

The derived version is persisted with its deterministic identity and content
hash. Candidate execution, backtest, experiment result, and later research
freeze records must agree on:

- base strategy version id and hash;
- parameter set hash and binding hash;
- derived strategy version id and hash;
- experiment, candidate, and candidate-set identity;
- frozen backtest configuration and price field.

An ordinary Strategy catalog counts only ordinary versions. Derived versions
remain globally addressable through the strategy repository without polluting
the ordinary version catalog.

### Backtest source of truth

`BacktestRun` remains the source of truth for orders, fills, positions, equity,
and accounting. `ExperimentResult` stores only immutable experiment identity,
provenance, the `backtest_run_id`, and the analytics summary. The audit checks
that no second accounting representation is introduced by experiment result
persistence.

### IS/OOS boundary and execution timing

Candidate execution requests market data from the warmup start through the IS
end date only. Warmup points before IS are available for indicator formation,
but backtest and analytics dates remain exactly the protocol IS range. The
audit also checks the existing no-lookahead rule: a signal on date `T` can only
produce a fill on a later trading date, and an IS-final-day signal cannot
create an out-of-range fill.

SelectionDecision accepts only IS backtest evidence. The selected version and
run must match the locked candidate set and exact content hash. The database
unique index on `research_selection_decisions(protocol_id)` prevents two
different concurrent decisions for one protocol.

### Lifecycle atomicity

The audit keeps two guarantees separate:

1. `ExperimentRepository.transition_status()` atomically updates a permitted
   non-terminal experiment status and appends its lifecycle event, with stale
   state and transition-key protection.
2. `ExperimentRepository.finalize_result()` atomically writes the immutable
   result, changes `RUNNING` to `COMPLETED`, and appends the terminal event.

Direct completion without a result is rejected. Result finalization is
idempotent across restart and concurrent retry. A result/event failure leaves
the experiment non-terminal and leaves no partial ExperimentResult, while the
already persisted BacktestRun remains the accounting artifact that can be
reconciled.

## Database and Security Checks

The audit inspects the existing protocol uniqueness index and exercises a
concurrent different-selection race. It does not create a migration or alter
schema. Immutable result and provenance paths reject non-finite values and
credential-like fields; no API key or Tiingo credential is part of the audit
fixtures or expected output.

## Out of Scope

- PHASE 8E-0F and PHASE 8E-1;
- OOS execution or observation;
- parameter optimization, ranking, or recommendation;
- dataset versioning or checksums;
- new persistence tables, migrations, APIs, or frontend changes;
- changes to Strategy, Backtest, Analytics, or Portfolio production logic.

The gate is `READY` only when this audit, the relevant regression suites, code
quality checks, compile/import checks, security scan, and Git diff validation
all pass.
