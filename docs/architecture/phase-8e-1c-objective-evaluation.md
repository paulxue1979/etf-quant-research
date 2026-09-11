# PHASE 8E-1C — Objective & Hard Constraint Evaluation

## Scope

PHASE 8E-1C interprets the frozen objective attached to an `Experiment` and
evaluates that objective's hard constraints against one persisted
`ExperimentCandidateView`. It answers only whether that candidate satisfies
each pre-declared constraint. It does not compare candidates, rank them, score
them, select a winner, or create a selection decision.

## Frozen objective and provenance

The evaluator resolves `experiment.objective_specification` server-side. A
caller cannot provide a replacement metric, direction, threshold, or result
hash. The objective content hash is recomputed before evaluation and must
match the candidate's `objective_spec_hash`. Every returned evaluation retains
the experiment ID, candidate ID, objective hash, experiment result ID, and
result hash.

The current domain model represents hard constraints as a mapping from a
stable analytics metric identifier to a numeric threshold, together with a
`MetricDirection`. Until the domain adds an operator field, directions map to
the only unambiguous operators supported by this phase:

- `maximize` means `metric >= threshold`;
- `minimize` means `metric <= threshold`.

No expression language, `eval`, dynamic code, or caller-supplied formula is
accepted.

## Metrics and states

Metric resolution uses the canonical keys emitted by
`PerformanceAnalysisResult`, including `total_return`, `cagr`,
`annualized_volatility`, `sharpe_ratio`, `sortino_ratio`, `max_drawdown`,
`max_drawdown_duration`, `recovery_duration`, `calmar_ratio`, and the stable
trade metric keys. Unknown metrics raise the structured
`OBJECTIVE_METRIC_UNSUPPORTED` error; they are never ignored.

Each constraint has an immutable `PASS`, `FAIL`, or `NOT_EVALUABLE` state and a
stable reason code. A metric represented by `MetricValue.NOT_EVALUABLE`
remains `NOT_EVALUABLE`, including its safe reason. Missing or failed results
are not converted into constraint failures. Aggregate state is deterministic:

1. any `FAIL` produces overall `FAIL`;
2. otherwise, any `NOT_EVALUABLE` produces overall `NOT_EVALUABLE`;
3. otherwise, all constraints produce `PASS`.

An objective with no hard constraints produces an empty constraint result with
overall `PASS`; there is no implicit score or selection meaning.

## IS-only boundary

Evaluation consumes only the persisted candidate result projection, whose
performance summary is produced by the existing IS backtest and analytics
chain. It does not load OOS data, OOS equity, OOS trades, or OOS metrics.
Warmup dates are provenance on the result and do not change the IS statistics
or the evaluation period. The evaluator also does not recalculate analytics.

Compatibility diagnostics from PHASE 8E-1B remain a separate concern. An
individual candidate's hard constraints may be evaluated even when cross-
candidate compatibility is not available; incompatibility is not silently
turned into `FAIL`.

## Immutability and security

`ConstraintEvaluation` and `CandidateObjectiveEvaluation` are frozen domain
records. Their canonical JSON is deterministic and contains no rank, score,
winner, recommendation, or OOS field. Thresholds and metric values must be
finite. Invalid hashes, malformed metric payloads, unsafe reason text, and
non-finite persisted values produce structured errors or are rejected before
serialization. Sensitive values are never included in error or reason text.

## API and persistence

This phase intentionally adds no endpoint and no database table. The evaluator
is a pure read-only domain operation over the existing read model. A later API
may expose it using only protocol, experiment, and candidate identifiers;
objective and metric inputs must remain server-resolved. Selection persistence
and researcher rationale are out of scope.

## Verification

The focused suite covers successful constraints, single and multiple failures,
`NOT_EVALUABLE` precedence, failed candidates, canonical metric mappings,
unsupported and non-finite inputs, objective/result hash binding,
deterministic serialization, parameter variation, IS-only OOS exclusion, and
security rejection. Existing PHASE 8E-1A/1B and broader research/backtest
regression suites remain required before the phase gate.
