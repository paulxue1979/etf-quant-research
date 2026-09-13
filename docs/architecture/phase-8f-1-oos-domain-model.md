# PHASE 8F-1 Controlled OOS Domain Model

PHASE 8F-1 defines the immutable domain contract for one controlled
out-of-sample (OOS) evaluation. It does not execute a backtest, persist an OOS
record, expose an API, or change the existing strategy, signal, backtest, or
analytics engines.

## Purpose and Boundary

The domain model makes the holdout boundary explicit before any future OOS
execution is allowed:

```text
Frozen ResearchProtocol
  -> SelectionDecision (IS evidence only)
  -> StrategyFreezeRecord
  -> exact persisted StrategyVersion
  -> OosEvaluationSpec
```

The model rejects an OOS specification unless the protocol is in
`selection_recorded` state, the selected strategy and freeze records belong to
that protocol, and the persisted strategy content hash matches every recorded
identity. A strategy change requires a new immutable StrategyVersion and a new
research protocol decision; an OOS result cannot mutate or replace the frozen
version.

## Domain Records

`research/oos.py` provides these frozen records:

- `OosEvaluationIdentity`: protocol, IS selection, strategy freeze, exact
  strategy version, and strategy content hash.
- `OosEvaluationRange`: inclusive OOS dates plus a warm-up start. The
  evaluation range must equal the protocol OOS range exactly.
- `OosEvaluationConfig`: result-affecting backtest settings copied from the
  frozen `BacktestConfig` or `ResearchEvaluationConfig`, including price field,
  commission, slippage, execution rule, rebalance policy, engine version, and
  analytics version.
- `OosDataProvenance`: minimal source, date, frequency, price-field, cache, and
  snapshot references. Credentials and local filesystem paths are rejected.
- `OosEvaluationSpec`: the complete immutable input contract for a future
  executor. V1 requires fresh capital, OOS-only boundary signals, and a
  one-shot evaluation.
- `OosEvaluationResult`: an immutable official observation that references the
  existing `backtest_run_id` and stores an integrity hash over its semantic
  payload. It does not create a second accounting model.

`OosPerformanceSummary` accepts the existing `MetricValue` representation and
can project `PerformanceAnalysisResult` into a transport-safe metric container.
Performance calculation remains owned by PHASE 4I analytics.

## IS/OOS Boundary

The protocol owns the inclusive split:

```text
IS:  is_start_date .. is_end_date
OOS: oos_start_date .. oos_end_date
```

The ranges must not overlap. A caller-provided OOS start or end that differs
from the frozen protocol is rejected. Warm-up data may begin before OOS start
so indicators can be initialized, but `warmup_start` is provenance only and is
never part of OOS performance statistics. The OOS result and its provenance
must both use the exact OOS start/end and the same warm-up boundary.

The future executor must continue to use the existing execution semantics:

```text
Signal(T) -> T+1 trading-day open
```

This phase records the execution rule in the frozen configuration; it does not
implement or alter order, fill, portfolio, or accounting behavior.

## Configuration Consistency

`OosEvaluationConfig` has adapters for the existing `BacktestConfig` and the
PHASE 7 `ResearchEvaluationConfig`. The adapter is a projection, not an
override mechanism. `validate_oos_configuration` compares canonical fields
exactly, including numeric values, price field, execution rule, rebalance
policy, engine version, and analytics version. There is no tolerance-based
matching and no caller-supplied fallback for a frozen value.

The configured price field must remain consistent through:

```text
StrategyVersion -> Indicator inputs -> Strategy evaluation
              -> BacktestConfig -> PerformanceAnalysisResult
```

Any later executor must fail closed on a mismatch rather than silently mixing
raw and adjusted prices.

## Provenance and Integrity

The result identity preserves:

- `protocol_id`
- `selection_decision_id`
- `strategy_freeze_id`
- `strategy_version_id`
- `strategy_content_hash`
- `backtest_run_id`
- OOS and warm-up dates
- configuration hash, engine version, and analytics version
- safe data provenance

Nested provenance values are recursively frozen. Non-finite numbers,
credential-like keys, known secret sentinels, and local path values are
rejected. `OosEvaluationResult` derives `result_hash` from its semantic payload;
`created_at` is intentionally excluded so an exact replay has the same result
identity. `validate_one_shot_result` permits only an exact replay of an
existing official result and rejects replacement observations.

## State and Future Execution Boundary

`OosExecutionStatus` reserves the future lifecycle vocabulary:

```text
PENDING -> RUNNING -> COMPLETED
                    -> FAILED / BLOCKED
```

The enum is a domain vocabulary only in PHASE 8F-1. There is no claim, lease,
worker, retry, persistence, API, or concurrency implementation in this phase.
Those concerns belong to a separately reviewed execution phase and must keep
the one-shot and immutable-result rules above.

## Explicitly Out of Scope

PHASE 8F-1 does not add:

- OOS execution, batch backtests, ranking, recommendation, or reselection;
- persistence, migrations, database uniqueness, claims, leases, or recovery;
- API endpoints, frontend changes, or UI workflows;
- parameter optimization, walk-forward, Monte Carlo, ML/AI, or live trading;
- dataset versioning, data warehouses, Redis, Kafka, Celery, Docker, or
  microservices;
- changes to the PHASE 3 accounting/order/fill logic or the no-lookahead rule.

The next phase may consume these contracts only after an independent execution
design review and must continue to treat the frozen protocol, strategy version,
configuration, and OOS boundary as the source of truth.
