# PHASE 8D Final Research Integrity Audit

## Scope

This audit covers the completed PHASE 8D execution path only:

```text
Frozen Experiment -> Candidate Execution -> IS Backtest -> Performance Analysis
                   -> Immutable ExperimentResult
```

It does not add candidate ranking, OOS selection, optimization, walk-forward,
frontend experiment UI, or dataset versioning.

## Audit Matrix

| Area | Control | Verification |
| --- | --- | --- |
| Strategy materialization | Parameter bindings use allowlisted paths and produce a new immutable derived version | PHASE 8D-1 tests and materialization provenance |
| Candidate identity | Candidate id is derived from experiment id, candidate index, and parameter-set hash | PHASE 8D-2 tests and repository constraints |
| State transitions | Candidate lifecycle is `PENDING -> RUNNING -> COMPLETED/FAILED`; leases are recoverable | PHASE 8D-2 tests, including concurrent claim and expired lease recovery |
| IS boundary | Requests may include pre-IS warmup only; backtest and analytics end at IS end | PHASE 8D-3 and 8D-4 tests plus finalizer range checks |
| No lookahead | Existing backtest integration keeps `Signal(T) -> T+1 trading day open` | PHASE 4H regression coverage |
| Frozen provenance | Finalization re-reads Experiment and CandidateSet from SQLite and compares content, parameter-space, candidate-set, candidate, strategy, and configuration hashes | `tests/audit/test_phase_8d_integrity.py` |
| Immutable result | Result identity and result hash are deterministic; `(experiment_id, candidate_id)` is unique | PHASE 8D-4 tests and concurrent finalization audit |
| Recovery | Deterministic BacktestRun identity and immutable result identity make partial retries convergent | Restart and idempotency tests |
| Secrets | Domain and repository serializers reject API-key, credential, authorization, and private-key sentinels | PHASE 8C/8D repository tests |

## Persistence Boundary

BacktestRun, ExperimentResult, and candidate lifecycle updates are persisted by
separate repositories and therefore are not one SQLite transaction. Recovery is
explicitly idempotent: deterministic backtest identity, immutable result
identity, unique candidate/result constraints, and lease recovery prevent a
retry from creating a second official result. A future schema-level transaction
may reduce the partial-commit window, but it is outside PHASE 8D.

## Final Audit Conclusion

The finalization provenance gap was closed by validating persisted Experiment
and CandidateSet records before creating an ExperimentResult. A result cannot
be finalized from an outcome whose frozen experiment, candidate set, parameter
set, or IS configuration hashes disagree with the durable source of truth.

PHASE 8E remains out of scope.
